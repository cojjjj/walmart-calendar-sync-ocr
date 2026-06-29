# walmart-calendar-sync v2

Safely import Walmart work shifts into Google Calendar from either a CSV file or a local schedule screenshot.

This app does **not** scrape, automate, or log into Me@Walmart. Screenshot import only reads an image file you choose. Every import goes through a review table before Google Calendar events are created.

## Features

- Windows desktop GUI built with Tkinter
- CSV import from `shifts.csv`
- Screenshot import for PNG, JPG, and JPEG files
- EasyOCR shift detection
- Background OCR thread so the UI stays responsive
- Progress bar while OCR or Google import is running
- Review table before import
- OCR confidence display
- Warning output for OCR lines that could not be parsed
- Google Calendar OAuth import
- Custom `CALENDAR_ID`, including a different Google Calendar email
- `America/New_York` timezone
- Duplicate protection with deterministic Google Calendar event IDs
- CLI kept for CSV preview/import

## Project Files

```text
walmart-calendar-sync/
  walmart_calendar_sync/
    __init__.py
    __main__.py
    cli.py
    gui.py
    ocr_import.py
  tests/
    test_event_ids.py
    test_ocr_import.py
  .env.example
  .gitignore
  README.md
  requirements.txt
  shifts.csv
```

## Setup

Prerequisite: Python 3.10.7 or newer.

Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

EasyOCR may download model files the first time screenshot OCR runs. That first preview can take longer than later previews.

## Google Calendar Setup

Follow Google's official Python Calendar API quickstart:

[Google Calendar API Python quickstart](https://developers.google.com/workspace/calendar/api/quickstart/python)

High-level steps:

1. Create or select a Google Cloud project.
2. Enable the Google Calendar API.
3. Configure the OAuth consent screen.
4. Create OAuth credentials for a desktop app.
5. Download the JSON file and save it in this project as `credentials.json`.

`credentials.json` and `token.json` are ignored by git.

## Desktop GUI

Launch the v2 app:

```powershell
python -m walmart_calendar_sync --gui
```

CSV workflow:

1. Pick `shifts.csv`.
2. Click **Preview CSV**.
3. Review the shifts in the table.
4. Click **Import to Google Calendar**.

Screenshot workflow:

1. Pick a PNG, JPG, or JPEG schedule screenshot.
2. Click **Preview Screenshot**.
3. Wait for the progress bar to finish.
4. Enter Schedule Month and Schedule Year if the cropped screenshot does not show them.
5. Review detected shifts, OCR confidence, and warning lines.
6. Click **Import to Google Calendar**.

The import button always imports only the shifts currently shown in the review table.

## CSV Format

Use these exact columns:

```csv
date,start,end,title
2026-07-01,09:00,17:00,Walmart Shift
2026-07-03,2:00 PM,10:30 PM,Walmart Shift
```

Supported date format:

- `YYYY-MM-DD`

Supported time formats:

- `09:00`
- `2:00 PM`
- `2 PM`

If a shift ends earlier than it starts, the tool treats it as an overnight shift.

## Screenshot OCR Tips

For cropped Me@Walmart Full Schedule screenshots, the parser looks for weekday lines such as `Wed`, a nearby day number such as `1`, and time tokens in that weekday block. It supports split OCR tokens like `7.OOam` and `4.OOpm`, normalizes them to `7:00am` and `4:00pm`, then uses the earliest AM time and latest PM time as the work shift. Meal times are ignored by that selection.

EasyOCR works best when:

- the screenshot clearly shows both dates and shift times
- times look like `9:00 AM - 5:00 PM`, `9 AM - 5 PM`, or `2 PM - 10:30 PM`
- dates look like `July 1`, `Jul 1`, `7/1`, or `2026-07-01`

The app shows OCR confidence for each detected shift and highlights lines it could not turn into shifts. If parsing fails, the raw OCR text is included in the error so you can see exactly what EasyOCR read.

For raw OCR troubleshooting, start the app with `WALMART_SYNC_OCR_DEBUG=1` in your environment before clicking **Preview Screenshot**.

## Command Line

Preview CSV shifts:

```powershell
python -m walmart_calendar_sync --csv shifts.csv
```

Import CSV shifts to your primary calendar:

```powershell
python -m walmart_calendar_sync --csv shifts.csv --commit
```

Import CSV shifts to another calendar:

```powershell
python -m walmart_calendar_sync --csv shifts.csv --calendar-id your-calendar@example.com --commit
```

You can also use `.env`:

```text
CALENDAR_ID=your-calendar@example.com
CREDENTIALS_FILE=credentials.json
TOKEN_FILE=token.json
```

## Duplicate Protection

Each shift is converted into a deterministic event ID using:

- calendar ID
- shift date
- shift start time
- shift end time
- title

If you import the same reviewed shifts again, Google Calendar rejects the duplicate ID and the app logs the duplicate as skipped.

## Tests

```powershell
python -m unittest discover -s tests
```







