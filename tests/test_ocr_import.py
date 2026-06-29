import unittest
from datetime import date, time

from walmart_calendar_sync.ocr_import import (
    OcrImportError,
    OcrLine,
    parse_ocr_lines,
    parse_shifts_from_text,
)


def box_line(text, x, y, confidence=0.95, width=120, height=20):
    return OcrLine(
        text=text,
        confidence=confidence,
        left=float(x),
        top=float(y),
        right=float(x + width),
        bottom=float(y + height),
    )


class MeAtWalmartOcrParserTests(unittest.TestCase):
    def test_groups_full_schedule_cards_and_ignores_meal_breaks(self):
        lines = [
            box_line("Full Schedule", 20, 10),
            box_line("Mon", 20, 80),
            box_line("1", 90, 80),
            box_line("Stocking TA", 20, 115),
            box_line("7:30am - 4:30pm", 20, 145),
            box_line("Meal Break 12:00pm - 1:00pm", 20, 175),
            box_line("Store #1234", 20, 205),
            box_line("Tue", 20, 270),
            box_line("2", 90, 270),
            box_line("Frontend Checkout TA", 20, 305),
            box_line("1:00pm - 10:00pm", 20, 335),
            box_line("Meal Break 5:00pm - 6:00pm", 20, 365),
            box_line("8.00 hours", 20, 395),
        ]

        result = parse_ocr_lines(lines, default_year=2026, default_month=7)

        self.assertEqual(len(result.shifts), 2)
        self.assertEqual(result.shifts[0].work_date, date(2026, 7, 1))
        self.assertEqual(result.shifts[0].start_time, time(7, 30))
        self.assertEqual(result.shifts[0].end_time, time(16, 30))
        self.assertEqual(result.shifts[1].work_date, date(2026, 7, 2))
        self.assertEqual(result.shifts[1].start_time, time(13, 0))
        self.assertEqual(result.shifts[1].end_time, time(22, 0))
        self.assertEqual(result.shifts[0].title, "Walmart Shift")

    def test_tolerates_ocr_mistakes_in_weekday_and_times(self):
        lines = [
            box_line("Tve", 20, 80, 0.80),
            box_line("3", 90, 80, 0.96),
            box_line("Digital Shopper", 20, 115),
            box_line("7:3Oam - 4:3Opm", 20, 145, 0.76),
            box_line("Meal Break 12:OOpm - 1:OOpm", 20, 175, 0.70),
        ]

        result = parse_ocr_lines(lines, default_year=2026, default_month=7)

        self.assertEqual(len(result.shifts), 1)
        self.assertEqual(result.shifts[0].work_date, date(2026, 7, 3))
        self.assertEqual(result.shifts[0].start_time, time(7, 30))
        self.assertEqual(result.shifts[0].end_time, time(16, 30))
        self.assertLess(result.parsed_shifts[0].confidence, 0.90)


    def test_cropped_me_at_walmart_layout_wed_1_and_thu_2(self):
        lines = [
            box_line("Wed", 20, 20),
            box_line("1", 20, 48),
            box_line("Digital Personal Shopper", 20, 82),
            box_line("7:00am – 4:00pm", 20, 116),
            box_line("12:30pm – 1:30pm", 20, 150),
            box_line("Site 2159", 20, 184),
            box_line("Thu", 20, 240),
            box_line("2", 20, 268),
            box_line("Digital Personal Shopper", 20, 302),
            box_line("7:00am – 4:00pm", 20, 336),
            box_line("11:00am – 12:00pm", 20, 370),
            box_line("Site 2159", 20, 404),
        ]

        result = parse_ocr_lines(lines, default_year=2026, default_month=7)

        self.assertEqual(len(result.shifts), 2)
        self.assertEqual(result.shifts[0].work_date, date(2026, 7, 1))
        self.assertEqual(result.shifts[0].start_time, time(7, 0))
        self.assertEqual(result.shifts[0].end_time, time(16, 0))
        self.assertEqual(result.shifts[1].work_date, date(2026, 7, 2))
        self.assertEqual(result.shifts[1].start_time, time(7, 0))
        self.assertEqual(result.shifts[1].end_time, time(16, 0))


    def test_real_raw_ocr_split_time_tokens(self):
        text = """
        Wed
        Digital Personal Shopper
        8.00 h
        4.00pm
        7.OOam
        #4 12.3Opm
        1.30pm
        Site 2159
        Shift options
        Thu
        Digital Personal Shopper
        8.00 h
        2
        9
        7.OOam
        4.OOpm
        #4
        12.OOpm
        11.OOam
        Site 2159
        """

        shifts = parse_shifts_from_text(text, default_year=2026, default_month=7)

        self.assertEqual(len(shifts), 2)
        self.assertEqual(shifts[0].work_date, date(2026, 7, 1))
        self.assertEqual(shifts[0].start_time, time(7, 0))
        self.assertEqual(shifts[0].end_time, time(16, 0))
        self.assertEqual(shifts[1].work_date, date(2026, 7, 2))
        self.assertEqual(shifts[1].start_time, time(7, 0))
        self.assertEqual(shifts[1].end_time, time(16, 0))

    def test_failure_message_includes_raw_ocr_text(self):
        lines = [
            box_line("Wed", 20, 20),
            box_line("Digital Personal Shopper", 20, 52),
            box_line("Site 2159", 20, 84),
        ]

        with self.assertRaisesRegex(OcrImportError, "Raw OCR text"):
            parse_ocr_lines(lines, default_year=2026, default_month=7)

    def test_raises_when_no_schedule_cards_have_work_shift_times(self):
        lines = [
            box_line("Full Schedule", 20, 10),
            box_line("Shift options", 20, 40),
            box_line("Meal Break 12:00pm - 1:00pm", 20, 70),
        ]

        with self.assertRaises(OcrImportError):
            parse_ocr_lines(lines, default_year=2026, default_month=7)

    def test_legacy_text_parser_still_returns_shift_objects(self):
        text = """
        Wed 8
        Salesfloor TA
        9:00am - 6:00pm
        Meal Break 1:00pm - 2:00pm
        """

        shifts = parse_shifts_from_text(text, default_year=2026)

        self.assertEqual(len(shifts), 1)
        self.assertEqual(shifts[0].start_time, time(9, 0))
        self.assertEqual(shifts[0].end_time, time(18, 0))


if __name__ == "__main__":
    unittest.main()


