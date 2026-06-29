import unittest
from datetime import date, time

from walmart_calendar_sync.cli import Shift, deterministic_event_id


class EventIdTests(unittest.TestCase):
    def test_event_id_is_deterministic_and_google_safe(self):
        shift = Shift(
            work_date=date(2026, 7, 1),
            start_time=time(9, 0),
            end_time=time(17, 0),
            title="Walmart Shift",
        )

        first = deterministic_event_id(shift, "primary")
        second = deterministic_event_id(shift, "primary")

        self.assertEqual(first, second)
        self.assertLessEqual(set(first), set("0123456789abcdefghijklmnopqrstuv"))

    def test_event_id_changes_when_shift_changes(self):
        morning = Shift(
            work_date=date(2026, 7, 1),
            start_time=time(9, 0),
            end_time=time(17, 0),
            title="Walmart Shift",
        )
        evening = Shift(
            work_date=date(2026, 7, 1),
            start_time=time(14, 0),
            end_time=time(22, 0),
            title="Walmart Shift",
        )

        self.assertNotEqual(
            deterministic_event_id(morning, "primary"),
            deterministic_event_id(evening, "primary"),
        )


if __name__ == "__main__":
    unittest.main()
