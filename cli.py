from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import os
from base64 import b32hexencode
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable

TIME_ZONE = "America/New_York"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
REQUIRED_COLUMNS = {"date", "start", "end", "title"}

logger = logging.getLogger("walmart-calendar-sync")


@dataclass(frozen=True)
class Shift:
    work_date: date
    start_time: time
    end_time: time
    title: str

    @property
    def starts_at(self) -> datetime:
        return datetime.combine(self.work_date, self.start_time)

    @property
    def ends_at(self) -> datetime:
        end_date = self.work_date
        if self.end_time <= self.start_time:
            end_date += timedelta(days=1)
        return datetime.combine(end_date, self.end_time)


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from exc


def parse_time(value: str) -> time:
    cleaned = value.strip().upper()
    formats = ("%H:%M", "%I:%M %p", "%I %p")
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"time must be HH:MM, H:MM AM/PM, or H AM/PM, got {value!r}")


def read_shifts(csv_path: Path) -> list[Shift]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    shifts: list[Shift] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise ValueError(f"CSV is missing required column(s): {missing_list}")

        for row_number, row in enumerate(reader, start=2):
            try:
                shift = Shift(
                    work_date=parse_date(row["date"]),
                    start_time=parse_time(row["start"]),
                    end_time=parse_time(row["end"]),
                    title=row["title"].strip() or "Walmart Shift",
                )
            except ValueError as exc:
                raise ValueError(f"Row {row_number}: {exc}") from exc

            shifts.append(shift)

    return shifts


def deterministic_event_id(shift: Shift, calendar_id: str) -> str:
    canonical = "|".join(
        [
            calendar_id.strip().lower(),
            TIME_ZONE,
            shift.work_date.isoformat(),
            shift.start_time.isoformat(timespec="minutes"),
            shift.end_time.isoformat(timespec="minutes"),
            shift.title.strip().lower(),
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    # Google Calendar event IDs allow digits and lowercase letters a-v.
    return "s" + b32hexencode(digest).decode("ascii").lower().rstrip("=")


def build_event_body(shift: Shift, calendar_id: str) -> dict:
    return {
        "id": deterministic_event_id(shift, calendar_id),
        "summary": shift.title,
        "description": "Imported from walmart-calendar-sync v2.",
        "start": {
            "dateTime": shift.starts_at.isoformat(timespec="seconds"),
            "timeZone": TIME_ZONE,
        },
        "end": {
            "dateTime": shift.ends_at.isoformat(timespec="seconds"),
            "timeZone": TIME_ZONE,
        },
    }


def get_calendar_service(credentials_path: Path, token_path: Path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Calendar dependencies are not installed. Run: pip install -r requirements.txt"
        ) from exc

    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        logger.info("Refreshing Google OAuth token.")
        creds.refresh(Request())

    if not creds or not creds.valid:
        if not credentials_path.exists():
            raise FileNotFoundError(
                f"Google OAuth credentials file not found: {credentials_path}"
            )
        logger.info("Opening Google OAuth consent flow in your browser.")
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds)


def format_shift(shift: Shift) -> str:
    return (
        f"{shift.title} | "
        f"{shift.starts_at.strftime('%Y-%m-%d %I:%M %p')} {TIME_ZONE} to "
        f"{shift.ends_at.strftime('%Y-%m-%d %I:%M %p')} {TIME_ZONE}"
    )


def log_preview(shifts: Iterable[Shift], calendar_id: str) -> None:
    for shift in shifts:
        event_id = deterministic_event_id(shift, calendar_id)
        logger.info("Preview: %s | event id %s", format_shift(shift), event_id)


def sync_to_calendar(service, shifts: Iterable[Shift], calendar_id: str) -> tuple[int, int]:
    created = 0
    skipped = 0

    for shift in shifts:
        body = build_event_body(shift, calendar_id)
        try:
            service.events().insert(calendarId=calendar_id, body=body).execute()
            created += 1
            logger.info("Created: %s on %s", shift.title, shift.starts_at.date())
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if exc.__class__.__name__ == "HttpError" and status == 409:
                skipped += 1
                logger.info(
                    "Skipped duplicate: %s on %s",
                    shift.title,
                    shift.starts_at.date(),
                )
                continue
            raise

    return created, skipped


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Walmart work shifts from shifts.csv into Google Calendar.",
        epilog="Desktop app: python -m walmart_calendar_sync --gui",
    )
    parser.add_argument(
        "--csv",
        default="shifts.csv",
        help="Path to CSV file with columns: date,start,end,title",
    )
    parser.add_argument(
        "--calendar-id",
        default=None,
        help='Google Calendar ID. Defaults to CALENDAR_ID env var, then "primary".',
    )
    parser.add_argument(
        "--credentials",
        default=None,
        help='Path to Google OAuth credentials JSON. Defaults to CREDENTIALS_FILE env var, then "credentials.json".',
    )
    parser.add_argument(
        "--token",
        default=None,
        help='Path to saved OAuth token JSON. Defaults to TOKEN_FILE env var, then "token.json".',
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually create events. Without this, the tool only previews.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show extra logging while running.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    csv_path = Path(args.csv)
    calendar_id = args.calendar_id or os.getenv("CALENDAR_ID") or "primary"
    credentials_path = Path(
        args.credentials or os.getenv("CREDENTIALS_FILE") or "credentials.json"
    )
    token_path = Path(args.token or os.getenv("TOKEN_FILE") or "token.json")

    try:
        shifts = read_shifts(csv_path)
        if not shifts:
            logger.warning("No shifts found in %s.", csv_path)
            return 0

        logger.info("Loaded %s shift(s) from %s.", len(shifts), csv_path)
        logger.info("Target calendar: %s", calendar_id)

        if not args.commit:
            logger.info("Dry run only. Add --commit to create Google Calendar events.")
            log_preview(shifts, calendar_id)
            return 0

        service = get_calendar_service(credentials_path, token_path)
        created, skipped = sync_to_calendar(service, shifts, calendar_id)
        logger.info("Done. Created %s event(s), skipped %s duplicate(s).", created, skipped)
        return 0
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("Unexpected error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
