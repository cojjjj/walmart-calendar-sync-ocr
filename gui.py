from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from walmart_calendar_sync.cli import (
    Shift,
    format_shift,
    get_calendar_service,
    read_shifts,
    sync_to_calendar,
)
from walmart_calendar_sync.ocr_import import (
    OcrImportError,
    OcrImportResult,
    read_ocr_result_from_image,
)


@dataclass(frozen=True)
class ReviewedShift:
    shift: Shift
    source: str
    confidence: float | None = None


class QueueLogHandler(logging.Handler):
    def __init__(self, events: queue.Queue[tuple]) -> None:
        super().__init__()
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        self.events.put(("LOG", self.format(record), "info"))


class WalmartCalendarSyncV2:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Walmart Calendar Sync v2")
        self.root.geometry("1040x740")
        self.root.minsize(900, 640)

        today = date.today()
        self.csv_path = tk.StringVar(value="shifts.csv")
        self.screenshot_path = tk.StringVar(value="")
        self.schedule_month = tk.StringVar(value=str(today.month))
        self.schedule_year = tk.StringVar(value=str(today.year))
        self.calendar_id = tk.StringVar(value="primary")
        self.credentials_path = tk.StringVar(value="credentials.json")
        self.token_path = tk.StringVar(value="token.json")
        self.status_text = tk.StringVar(value="Ready")

        self.events: queue.Queue[tuple] = queue.Queue()
        self.reviewed_shifts: list[ReviewedShift] = []
        self.busy = False

        self._configure_style()
        self._configure_logging()
        self._build_layout()
        self._poll_events()

    def _configure_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def _configure_logging(self) -> None:
        handler = QueueLogHandler(self.events)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger = logging.getLogger("walmart-calendar-sync")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        shell = ttk.Frame(self.root, padding=16)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Walmart Calendar Sync v2", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, text="v2").grid(row=0, column=1, sticky="e")

        sidebar = ttk.Frame(shell)
        sidebar.grid(row=1, column=0, sticky="nsw", padx=(0, 14))
        sidebar.columnconfigure(0, weight=1)

        content = ttk.Frame(shell)
        content.grid(row=1, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=3)
        content.rowconfigure(1, weight=2)

        self._build_csv_section(sidebar)
        self._build_screenshot_section(sidebar)
        self._build_google_section(sidebar)
        self._build_action_section(sidebar)
        self._build_review_table(content)
        self._build_log_panel(content)
        self._build_status_bar(shell)

    def _build_csv_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="CSV Import", style="Section.TLabelframe")
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="shifts.csv").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        ttk.Entry(frame, textvariable=self.csv_path, width=36).grid(
            row=1, column=0, sticky="ew", padx=10, pady=(0, 8)
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Browse CSV", command=self.browse_csv).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(buttons, text="Preview CSV", command=self.preview_csv).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

    def _build_screenshot_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Screenshot Import", style="Section.TLabelframe")
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="PNG, JPG, or JPEG").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        ttk.Entry(frame, textvariable=self.screenshot_path, width=36).grid(
            row=1, column=0, sticky="ew", padx=10, pady=(0, 8)
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Browse Screenshot", command=self.browse_screenshot).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(buttons, text="Preview Screenshot", command=self.preview_screenshot).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

        date_frame = ttk.Frame(frame)
        date_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        date_frame.columnconfigure(1, weight=1)
        date_frame.columnconfigure(3, weight=1)
        ttk.Label(date_frame, text="Schedule Month").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(date_frame, textvariable=self.schedule_month, width=6).grid(
            row=0, column=1, sticky="ew", padx=(0, 10)
        )
        ttk.Label(date_frame, text="Schedule Year").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(date_frame, textvariable=self.schedule_year, width=8).grid(row=0, column=3, sticky="ew")

    def _build_google_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Google Calendar", style="Section.TLabelframe")
        frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="Calendar ID").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        ttk.Entry(frame, textvariable=self.calendar_id, width=36).grid(
            row=1, column=0, sticky="ew", padx=10, pady=(0, 8)
        )
        ttk.Label(frame, text="credentials.json").grid(row=2, column=0, sticky="w", padx=10, pady=(0, 4))
        ttk.Entry(frame, textvariable=self.credentials_path, width=36).grid(
            row=3, column=0, sticky="ew", padx=10, pady=(0, 8)
        )
        ttk.Button(frame, text="Browse Credentials", command=self.browse_credentials).grid(
            row=4, column=0, sticky="ew", padx=10, pady=(0, 10)
        )

    def _build_action_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Actions", style="Section.TLabelframe")
        frame.grid(row=3, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self.import_button = ttk.Button(
            frame,
            text="Import to Google Calendar",
            command=self.import_reviewed_shifts,
            style="Primary.TButton",
        )
        self.import_button.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        self.clear_button = ttk.Button(frame, text="Clear Review", command=self.clear_review)
        self.clear_button.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

    def _build_review_table(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Review Detected Shifts", style="Section.TLabelframe")
        frame.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("date", "start", "end", "title", "source", "confidence")
        self.review_table = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        headings = {
            "date": "Date",
            "start": "Start",
            "end": "End",
            "title": "Title",
            "source": "Source",
            "confidence": "OCR Confidence",
        }
        widths = {"date": 100, "start": 90, "end": 90, "title": 170, "source": 120, "confidence": 110}
        for column in columns:
            self.review_table.heading(column, text=headings[column])
            self.review_table.column(column, width=widths[column], anchor="w")

        self.review_table.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.review_table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=10, padx=(0, 10))
        self.review_table.configure(yscrollcommand=scrollbar.set)

    def _build_log_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Output and OCR Warnings", style="Section.TLabelframe")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.output = tk.Text(frame, wrap="word", height=10, state="disabled")
        self.output.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        self.output.tag_configure("info", foreground="#1f2937")
        self.output.tag_configure("success", foreground="#0f7a3b")
        self.output.tag_configure("warning", foreground="#9a5b00")
        self.output.tag_configure("error", foreground="#b00020")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.output.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 10), pady=10)
        self.output.configure(yscrollcommand=scrollbar.set)

    def _build_status_bar(self, parent: ttk.Frame) -> None:
        status = ttk.Frame(parent)
        status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_text).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=220)
        self.progress.grid(row=0, column=1, sticky="e")

    def browse_csv(self) -> None:
        path = filedialog.askopenfilename(title="Choose shifts.csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.csv_path.set(path)

    def browse_screenshot(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose schedule screenshot",
            filetypes=[("Image files", "*.png *.jpg *.jpeg"), ("PNG files", "*.png"), ("JPEG files", "*.jpg *.jpeg"), ("All files", "*.*")],
        )
        if path:
            self.screenshot_path.set(path)

    def browse_credentials(self) -> None:
        path = filedialog.askopenfilename(title="Choose credentials.json", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.credentials_path.set(path)

    def preview_csv(self) -> None:
        if self.busy:
            return
        try:
            csv_path = self.required_path(self.csv_path.get(), "CSV file")
            shifts = read_shifts(csv_path)
        except FileNotFoundError as exc:
            self.show_error("Missing CSV", str(exc))
            return
        except ValueError as exc:
            self.show_error("Bad CSV", str(exc))
            return

        reviewed = [ReviewedShift(shift=shift, source="CSV") for shift in shifts]
        self.load_review(reviewed, f"CSV {csv_path}")
        self.write_output(f"Loaded {len(reviewed)} CSV shift(s).", "success")

    def preview_screenshot(self) -> None:
        if self.busy:
            return
        try:
            image_path = self.required_path(self.screenshot_path.get(), "Screenshot")
            schedule_month, schedule_year = self.get_schedule_month_year()
        except FileNotFoundError as exc:
            self.show_error("Missing Screenshot", str(exc))
            return
        except ValueError as exc:
            self.show_error("Bad Schedule Date", str(exc))
            return

        self.clear_review()
        self.write_output("Running EasyOCR. The first run may take a little while.", "info")
        self.set_busy(True, "Reading screenshot with EasyOCR...")
        worker = threading.Thread(
            target=self._ocr_worker,
            args=(image_path, schedule_month, schedule_year),
            daemon=True,
        )
        worker.start()

    def _ocr_worker(self, image_path: Path, schedule_month: int, schedule_year: int) -> None:
        try:
            result = read_ocr_result_from_image(
                image_path,
                default_month=schedule_month,
                default_year=schedule_year,
            )
            self.events.put(("OCR_DONE", result, image_path))
        except FileNotFoundError as exc:
            self.events.put(("ERROR", "Missing Screenshot", str(exc)))
        except OcrImportError as exc:
            self.events.put(("ERROR", "OCR Could Not Detect Shifts", str(exc)))
        except Exception as exc:
            self.events.put(("ERROR", "OCR Error", str(exc)))
        finally:
            self.events.put(("BUSY_DONE",))

    def import_reviewed_shifts(self) -> None:
        if self.busy:
            return
        if not self.reviewed_shifts:
            self.show_error("Preview Required", "Preview CSV shifts or screenshot shifts before importing.")
            return

        try:
            credentials_path = self.required_path(self.credentials_path.get(), "Google OAuth credentials file")
        except FileNotFoundError as exc:
            self.show_error("Missing Credentials", str(exc))
            return

        calendar_id = self.calendar_id.get().strip() or "primary"
        token_path = Path(self.token_path.get().strip() or "token.json")
        shifts = [reviewed.shift for reviewed in self.reviewed_shifts]

        self.write_output(f"Importing {len(shifts)} reviewed shift(s) to {calendar_id}...", "info")
        self.set_busy(True, "Importing to Google Calendar...")
        worker = threading.Thread(
            target=self._import_worker,
            args=(credentials_path, token_path, shifts, calendar_id),
            daemon=True,
        )
        worker.start()

    def _import_worker(self, credentials_path: Path, token_path: Path, shifts: list[Shift], calendar_id: str) -> None:
        try:
            service = get_calendar_service(credentials_path, token_path)
            created, skipped = sync_to_calendar(service, shifts, calendar_id)
            self.events.put(("IMPORT_DONE", created, skipped))
        except FileNotFoundError as exc:
            self.events.put(("ERROR", "Missing Credentials", str(exc)))
        except RuntimeError as exc:
            self.events.put(("ERROR", "Google Auth Error", str(exc)))
        except Exception as exc:
            self.events.put(("ERROR", "Google Calendar Error", str(exc)))
        finally:
            self.events.put(("BUSY_DONE",))

    def load_review(self, reviewed: list[ReviewedShift], source_label: str) -> None:
        self.reviewed_shifts = reviewed
        self.review_table.delete(*self.review_table.get_children())
        for index, reviewed_shift in enumerate(reviewed):
            shift = reviewed_shift.shift
            confidence = ""
            if reviewed_shift.confidence is not None:
                confidence = f"{reviewed_shift.confidence * 100:.0f}%"
            self.review_table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    shift.work_date.isoformat(),
                    shift.start_time.strftime("%I:%M %p"),
                    shift.end_time.strftime("%I:%M %p"),
                    shift.title,
                    reviewed_shift.source,
                    confidence,
                ),
            )
        self.status_text.set(f"Reviewing {len(reviewed)} shift(s) from {source_label}")

    def show_ocr_result(self, result: OcrImportResult, image_path: Path) -> None:
        reviewed = [
            ReviewedShift(shift=parsed.shift, source="Screenshot", confidence=parsed.confidence)
            for parsed in result.parsed_shifts
        ]
        self.load_review(reviewed, f"screenshot {image_path}")
        self.write_output(f"Detected {len(reviewed)} shift(s) from screenshot.", "success")
        for parsed in result.parsed_shifts:
            self.write_output(f"{format_shift(parsed.shift)} | OCR confidence {parsed.confidence * 100:.0f}%", "info")

        if result.unparsed_lines:
            self.write_output("EasyOCR lines that could not be parsed:", "warning")
            for line in result.unparsed_lines:
                self.write_output(f"Could not parse ({line.confidence * 100:.0f}%): {line.text}", "warning")

    def clear_review(self) -> None:
        self.reviewed_shifts = []
        self.review_table.delete(*self.review_table.get_children())
        self.clear_output()
        self.status_text.set("Ready")

    def set_busy(self, busy: bool, status: str = "") -> None:
        self.busy = busy
        button_state = "disabled" if busy else "normal"
        for child in self.root.winfo_children():
            self._set_child_state(child, button_state)
        if busy:
            self.status_text.set(status)
            self.progress.start(12)
        else:
            if status:
                self.status_text.set(status)
            self.progress.stop()

    def _set_child_state(self, widget: tk.Widget, state: str) -> None:
        if isinstance(widget, ttk.Button):
            widget.configure(state=state)
        for child in widget.winfo_children():
            self._set_child_state(child, state)

    def get_schedule_month_year(self) -> tuple[int, int]:
        try:
            month = int(self.schedule_month.get().strip())
            year = int(self.schedule_year.get().strip())
        except ValueError as exc:
            raise ValueError("Schedule Month and Schedule Year must be numbers.") from exc
        if month < 1 or month > 12:
            raise ValueError("Schedule Month must be between 1 and 12.")
        if year < 2000 or year > 2100:
            raise ValueError("Schedule Year must be a four-digit year.")
        return month, year

    def required_path(self, value: str, label: str) -> Path:
        text = value.strip()
        if not text:
            raise FileNotFoundError(f"{label} is required.")
        path = Path(text)
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")
        return path

    def clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", tk.END)
        self.output.configure(state="disabled")

    def write_output(self, message: str, tag: str = "info") -> None:
        self.output.configure(state="normal")
        self.output.insert(tk.END, message + "\n", tag)
        self.output.see(tk.END)
        self.output.configure(state="disabled")

    def show_error(self, title: str, message: str) -> None:
        self.write_output(f"ERROR: {message}", "error")
        messagebox.showerror(title, message)

    def _poll_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "OCR_DONE":
                _, result, image_path = event
                self.clear_output()
                self.show_ocr_result(result, image_path)
            elif kind == "IMPORT_DONE":
                _, created, skipped = event
                self.write_output(f"Done. Created {created} event(s), skipped {skipped} duplicate(s).", "success")
                self.status_text.set(f"Import complete: {created} created, {skipped} duplicate(s) skipped")
            elif kind == "ERROR":
                _, title, message = event
                self.status_text.set("Error")
                self.show_error(title, message)
            elif kind == "LOG":
                _, message, tag = event
                self.write_output(message, tag)
            elif kind == "BUSY_DONE":
                self.set_busy(False)

        self.root.after(100, self._poll_events)


def main() -> int:
    root = tk.Tk()
    WalmartCalendarSyncV2(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
