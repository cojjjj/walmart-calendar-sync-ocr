from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Iterable, Sequence

from walmart_calendar_sync.cli import Shift, parse_time

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
WEEKDAY_ALIASES = {
    "mon": "mon",
    "monday": "mon",
    "tue": "tue",
    "tues": "tue",
    "tuesday": "tue",
    "tve": "tue",
    "wed": "wed",
    "wednesday": "wed",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "thursday": "thu",
    "fri": "fri",
    "friday": "fri",
    "sat": "sat",
    "saturday": "sat",
    "sun": "sun",
    "sunday": "sun",
}
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
IGNORE_TIME_WORDS = ("meal", "break", "lunch", "site", "total", "hours", "option")
logger = logging.getLogger("walmart-calendar-sync")


class OcrImportError(ValueError):
    """Raised when OCR cannot read usable shifts from an image."""


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    left: float = 0.0
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(frozen=True)
class ParsedOcrShift:
    shift: Shift
    confidence: float
    date_text: str
    time_text: str


@dataclass(frozen=True)
class OcrImportResult:
    shifts: list[Shift]
    parsed_shifts: list[ParsedOcrShift]
    unparsed_lines: list[OcrLine]
    raw_lines: list[OcrLine]


@dataclass(frozen=True)
class TimeRangeMatch:
    start_text: str
    end_text: str
    raw_text: str


@dataclass(frozen=True)
class TimeToken:
    text: str
    value: time
    line: OcrLine
    index: int

    @property
    def is_am(self) -> bool:
        return self.text.upper().endswith("AM")

    @property
    def is_pm(self) -> bool:
        return self.text.upper().endswith("PM")


@dataclass(frozen=True)
class WeekdayBlock:
    weekday_line: OcrLine
    weekday_index: int
    lines: list[OcrLine]
    start_index: int
    end_index: int
    day_number: int | None = None
    day_line: OcrLine | None = None


def read_shifts_from_image(
    image_path: Path,
    default_month: int | None = None,
    default_year: int | None = None,
) -> list[Shift]:
    return read_ocr_result_from_image(
        image_path,
        default_month=default_month,
        default_year=default_year,
    ).shifts


def read_ocr_result_from_image(
    image_path: Path,
    default_month: int | None = None,
    default_year: int | None = None,
    debug: bool | None = None,
) -> OcrImportResult:
    lines = extract_ocr_lines_from_image(image_path)
    return parse_ocr_lines(
        lines,
        default_month=default_month,
        default_year=default_year,
        debug=debug,
    )


def extract_ocr_lines_from_image(image_path: Path) -> list[OcrLine]:
    validate_image_path(image_path)

    try:
        import easyocr
    except ImportError as exc:
        raise OcrImportError(
            "EasyOCR is not installed. Run: pip install -r requirements.txt"
        ) from exc

    try:
        reader = easyocr.Reader(["en"], gpu=False)
        results = reader.readtext(str(image_path), detail=1, paragraph=False)
    except Exception as exc:
        raise OcrImportError(f"Could not read screenshot with EasyOCR: {exc}") from exc

    lines = [ocr_result_to_line(result) for result in results]
    lines = [line for line in lines if line.text]
    lines.sort(key=lambda line: (line.top, line.left))

    if not lines:
        raise OcrImportError("OCR did not find readable text in this screenshot.")
    return lines


def ocr_result_to_line(result: tuple) -> OcrLine:
    box, text, confidence = result
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    return OcrLine(
        text=clean_line(str(text)),
        confidence=float(confidence),
        left=min(xs),
        top=min(ys),
        right=max(xs),
        bottom=max(ys),
    )


def validate_image_path(image_path: Path) -> None:
    if not image_path.exists():
        raise FileNotFoundError(f"Screenshot file not found: {image_path}")
    if image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        formats = ", ".join(sorted(SUPPORTED_IMAGE_SUFFIXES))
        raise OcrImportError(f"Unsupported image type. Use one of: {formats}")


def parse_shifts_from_text(
    text: str,
    default_year: int | None = None,
    default_month: int | None = None,
) -> list[Shift]:
    lines = [
        OcrLine(
            text=clean_line(line),
            confidence=1.0,
            top=float(index * 30),
            bottom=float(index * 30 + 18),
        )
        for index, line in enumerate(text.splitlines())
        if clean_line(line)
    ]
    return parse_ocr_lines(
        lines,
        default_year=default_year,
        default_month=default_month,
    ).shifts


def parse_ocr_lines(
    lines: list[OcrLine],
    default_year: int | None = None,
    default_month: int | None = None,
    debug: bool | None = None,
) -> OcrImportResult:
    if not lines:
        raise OcrImportError("OCR did not find readable text in this screenshot.")

    debug_enabled = is_debug_enabled(debug)
    raw_lines = sorted(lines, key=lambda line: (line.top, line.left))
    if debug_enabled:
        log_raw_ocr(raw_lines)

    rows = group_lines_into_rows(raw_lines)
    row_lines = [merge_row(row) for row in rows]
    year = default_year or infer_year(row_lines) or date.today().year
    month = default_month or infer_month(row_lines) or date.today().month
    result = parse_weekday_blocks(row_lines, month, year, raw_lines)

    if result.parsed_shifts:
        return result

    raw_text = raw_ocr_text(raw_lines)
    logger.error("OCR parsing failed. Raw OCR text:\n%s", raw_text)
    raise OcrImportError(
        "OCR could not detect shifts from this screenshot. Raw OCR text:\n" + raw_text
    )


def parse_weekday_blocks(
    rows: list[OcrLine],
    default_month: int,
    default_year: int,
    raw_lines: list[OcrLine],
) -> OcrImportResult:
    blocks = build_weekday_blocks(rows)
    blocks = fill_missing_day_numbers(blocks)

    parsed: list[ParsedOcrShift] = []
    unparsed: list[OcrLine] = []
    used_row_ids: set[int] = set()
    current_month = default_month
    current_year = default_year
    previous_day: int | None = None

    for block in blocks:
        if block.day_number is None:
            unparsed.extend(block.lines)
            continue

        if previous_day is not None and block.day_number < previous_day:
            current_month = 1 if current_month == 12 else current_month + 1
            if current_month == 1:
                current_year += 1
        previous_day = block.day_number

        start_token, end_token = find_work_shift_tokens(block)
        if not start_token or not end_token:
            unparsed.extend(block.lines)
            continue

        shift = Shift(
            work_date=date(current_year, current_month, block.day_number),
            start_time=start_token.value,
            end_time=end_token.value,
            title="Walmart Shift",
        )
        confidence_lines = [block.weekday_line, start_token.line, end_token.line]
        if block.day_line:
            confidence_lines.append(block.day_line)
        confidence = average_confidence(*confidence_lines)
        parsed.append(
            ParsedOcrShift(
                shift=shift,
                confidence=confidence,
                date_text=f"{block.weekday_line.text} {block.day_number}",
                time_text=f"{start_token.text} - {end_token.text}",
            )
        )
        used_row_ids.update(id(row) for row in block.lines)

    for row in rows:
        if id(row) in used_row_ids:
            continue
        if looks_like_schedule_noise(row.text):
            continue
        unparsed.append(row)

    return OcrImportResult(
        shifts=[item.shift for item in parsed],
        parsed_shifts=parsed,
        unparsed_lines=dedupe_lines(unparsed),
        raw_lines=raw_lines,
    )


def build_weekday_blocks(rows: list[OcrLine]) -> list[WeekdayBlock]:
    weekday_indexes = [index for index, row in enumerate(rows) if extract_weekday(row.text)]
    blocks: list[WeekdayBlock] = []
    for position, start_index in enumerate(weekday_indexes):
        end_index = weekday_indexes[position + 1] if position + 1 < len(weekday_indexes) else len(rows)
        block_lines = rows[start_index:end_index]
        day_number, day_line = find_day_number_in_block(block_lines)
        blocks.append(
            WeekdayBlock(
                weekday_line=rows[start_index],
                weekday_index=start_index,
                lines=block_lines,
                start_index=start_index,
                end_index=end_index,
                day_number=day_number,
                day_line=day_line,
            )
        )
    return blocks


def fill_missing_day_numbers(blocks: list[WeekdayBlock]) -> list[WeekdayBlock]:
    filled = list(blocks)
    for index, block in enumerate(filled):
        if block.day_number is not None:
            continue

        inferred_day = infer_missing_day_from_neighbors(filled, index)
        if inferred_day is None:
            continue
        filled[index] = WeekdayBlock(
            weekday_line=block.weekday_line,
            weekday_index=block.weekday_index,
            lines=block.lines,
            start_index=block.start_index,
            end_index=block.end_index,
            day_number=inferred_day,
            day_line=None,
        )
    return filled


def infer_missing_day_from_neighbors(blocks: list[WeekdayBlock], index: int) -> int | None:
    for next_index in range(index + 1, len(blocks)):
        next_day = blocks[next_index].day_number
        if next_day is not None:
            inferred = next_day - (next_index - index)
            if 1 <= inferred <= 31:
                return inferred
            break

    for previous_index in range(index - 1, -1, -1):
        previous_day = blocks[previous_index].day_number
        if previous_day is not None:
            inferred = previous_day + (index - previous_index)
            if 1 <= inferred <= 31:
                return inferred
            break
    return None


def find_day_number_in_block(lines: list[OcrLine]) -> tuple[int | None, OcrLine | None]:
    for line in lines[1:6]:
        day = extract_day_number(line.text)
        if day is not None:
            return day, line
    same_line_day = extract_day_number(lines[0].text, allow_weekday_line=True)
    if same_line_day is not None:
        return same_line_day, lines[0]
    return None, None


def find_work_shift_tokens(block: WeekdayBlock) -> tuple[TimeToken | None, TimeToken | None]:
    tokens: list[TimeToken] = []
    for index, line in enumerate(block.lines):
        lowered = normalize_ocr_text(line.text).lower()
        if any(word in lowered for word in IGNORE_TIME_WORDS):
            continue
        for token_text in extract_time_token_texts(line.text):
            try:
                tokens.append(
                    TimeToken(
                        text=token_text,
                        value=parse_time(token_text),
                        line=line,
                        index=index,
                    )
                )
            except ValueError:
                continue

    am_tokens = [token for token in tokens if token.is_am]
    pm_tokens = [token for token in tokens if token.is_pm]
    if am_tokens and pm_tokens:
        return min(am_tokens, key=lambda token: token.value), max(pm_tokens, key=lambda token: token.value)
    if len(pm_tokens) >= 2:
        return min(pm_tokens, key=lambda token: token.value), max(pm_tokens, key=lambda token: token.value)
    if len(tokens) >= 2:
        ordered = sorted(tokens, key=lambda token: token.value)
        return ordered[0], ordered[-1]
    return None, None


def extract_time_token_texts(text: str) -> list[str]:
    normalized = normalize_time_line(text)
    if not re.search(r"(?:am|pm)\b", normalized, flags=re.IGNORECASE):
        return []
    token_pattern = r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b"
    return [normalize_time_text(match.group(0)) for match in re.finditer(token_pattern, normalized, flags=re.IGNORECASE)]


def extract_weekday(text: str) -> str | None:
    normalized = normalize_ocr_text(text).lower()
    tokens = re.findall(r"[a-z]+", normalized)
    for token in tokens:
        if token in WEEKDAY_ALIASES:
            return WEEKDAY_ALIASES[token]
    return None


def extract_day_number(text: str, allow_weekday_line: bool = False) -> int | None:
    normalized = normalize_ocr_text(text).lower()
    if not allow_weekday_line and extract_weekday(normalized):
        return None
    if extract_time_token_texts(normalized) or "site" in normalized or normalized.startswith("#"):
        return None
    if re.search(r"\b(?:h|hr|hrs|hours)\b", normalized):
        return None
    numbers = re.findall(r"\b\d{1,2}\b", normalized)
    if len(numbers) != 1 and not allow_weekday_line:
        return None
    for number in numbers:
        day = int(number)
        if 1 <= day <= 31:
            return day
    return None


def group_lines_into_rows(lines: list[OcrLine]) -> list[list[OcrLine]]:
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda item: (item.center_y, item.left)):
        if not rows:
            rows.append([line])
            continue
        current = rows[-1]
        current_center = sum(item.center_y for item in current) / len(current)
        tolerance = max(14.0, median_height(lines) * 0.8)
        if abs(line.center_y - current_center) <= tolerance:
            current.append(line)
        else:
            rows.append([line])
    return [sorted(row, key=lambda item: item.left) for row in rows]


def merge_row(row: list[OcrLine]) -> OcrLine:
    text = clean_line(" ".join(item.text for item in row))
    confidence = sum(item.confidence for item in row) / len(row)
    return OcrLine(
        text=text,
        confidence=confidence,
        left=min(item.left for item in row),
        top=min(item.top for item in row),
        right=max(item.right for item in row),
        bottom=max(item.bottom for item in row),
    )


def infer_month(lines: Sequence[OcrLine]) -> int | None:
    for line in lines:
        normalized = normalize_ocr_text(line.text).lower()
        for name, month in MONTHS.items():
            if re.search(rf"\b{re.escape(name)}\b", normalized):
                return month
    return None


def infer_year(lines: Sequence[OcrLine]) -> int | None:
    for line in lines:
        match = re.search(r"\b(20\d{2})\b", normalize_ocr_text(line.text))
        if match:
            return int(match.group(1))
    return None


def normalize_ocr_text(text: str) -> str:
    cleaned = clean_line(text)
    cleaned = cleaned.replace("|", "1")
    cleaned = re.sub(r"(?<=\d)[Oo](?=\d|\s*(?:am|pm)\b)", "0", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b[Oo](?=\d)", "0", cleaned)
    return cleaned


def normalize_time_line(text: str) -> str:
    cleaned = clean_line(text).lower()
    if re.search(r"[ap]\.?m\.?", cleaned):
        cleaned = re.sub(r"[oO]", "0", cleaned)
    cleaned = cleaned.replace("—", "-").replace("–", "-")
    cleaned = re.sub(r"(?<=\d)[.:](?=\d{2}\s*(?:a\.?m\.?|p\.?m\.?)\b)", ":", cleaned)
    cleaned = re.sub(r"a\.?m\.?\b", "am", cleaned)
    cleaned = re.sub(r"p\.?m\.?\b", "pm", cleaned)
    cleaned = re.sub(r"(?<=\d)\s*(am|pm)\b", r"\1", cleaned)
    return cleaned


def normalize_time_text(value: str) -> str:
    cleaned = normalize_time_line(value).upper()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"(\d)(AM|PM)$", r"\1 \2", cleaned)
    return cleaned.strip()


def clean_line(line: str) -> str:
    cleaned = line.replace("\u2013", "-").replace("\u2014", "-")
    cleaned = cleaned.replace("\u00a0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def looks_like_schedule_noise(text: str) -> bool:
    lowered = normalize_ocr_text(text).lower()
    noise_words = (
        "full schedule",
        "meal",
        "break",
        "lunch",
        "site",
        "shift options",
        "total",
        "hours",
        "location",
        "cashier",
        "stocking",
        "associate",
        "digital personal shopper",
        "personal shopper",
    )
    return any(word in lowered for word in noise_words)


def average_confidence(*lines: OcrLine) -> float:
    return sum(line.confidence for line in lines) / len(lines)


def dedupe_lines(lines: Iterable[OcrLine]) -> list[OcrLine]:
    seen: set[tuple[str, int, int]] = set()
    unique: list[OcrLine] = []
    for line in lines:
        key = (line.text, round(line.top), round(line.left))
        if key in seen:
            continue
        seen.add(key)
        unique.append(line)
    return unique


def median_height(lines: list[OcrLine]) -> float:
    heights = sorted(max(1.0, line.bottom - line.top) for line in lines)
    if not heights:
        return 18.0
    middle = len(heights) // 2
    if len(heights) % 2:
        return heights[middle]
    return (heights[middle - 1] + heights[middle]) / 2


def raw_ocr_text(lines: Sequence[OcrLine]) -> str:
    return "\n".join(line.text for line in sorted(lines, key=lambda line: (line.top, line.left)))


def is_debug_enabled(debug: bool | None) -> bool:
    if debug is not None:
        return debug
    return os.getenv("WALMART_SYNC_OCR_DEBUG", "").lower() in {"1", "true", "yes", "on"}


def log_raw_ocr(lines: list[OcrLine]) -> None:
    logger.info("Raw EasyOCR output:")
    for line in lines:
        logger.info(
            "OCR %.0f%% box=(%.0f, %.0f, %.0f, %.0f): %s",
            line.confidence * 100,
            line.left,
            line.top,
            line.right,
            line.bottom,
            line.text,
        )

