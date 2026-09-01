"""Tests for the iCalendar reader. No network; feeds are built by hand.

Run with:  python3 -m unittest discover -s . -v
"""

import unittest
from datetime import date, datetime

import ics

DAY = date(2026, 8, 31)          # a Monday
BERLIN = 7200                    # +02:00, as Open-Meteo reports it


def feed(*events: str) -> str:
    body = "".join(f"BEGIN:VEVENT\r\n{e.strip()}\r\nEND:VEVENT\r\n" for e in events)
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + body + "END:VCALENDAR\r\n"


def titles(text, day=DAY, tz=BERLIN):
    return [e["title"] for e in ics.parse(text, day, tz)]


class TestTimes(unittest.TestCase):
    def test_utc_is_converted_to_local(self):
        got = ics.parse(feed("""
UID:a
DTSTART:20260831T140000Z
DTEND:20260831T150000Z
SUMMARY:test
"""), DAY, BERLIN)
        self.assertEqual(got[0]["start"], datetime(2026, 8, 31, 16, 0))
        self.assertEqual(got[0]["end"], datetime(2026, 8, 31, 17, 0))
        self.assertFalse(got[0]["all_day"])

    def test_tzid_time_is_taken_as_local(self):
        got = ics.parse(feed("""
UID:a
DTSTART;TZID=Europe/Berlin:20260831T090000
DTEND;TZID=Europe/Berlin:20260831T093000
SUMMARY:Standup
"""), DAY, BERLIN)
        self.assertEqual(got[0]["start"], datetime(2026, 8, 31, 9, 0))

    def test_all_day_event(self):
        got = ics.parse(feed("""
UID:a
DTSTART;VALUE=DATE:20260831
DTEND;VALUE=DATE:20260901
SUMMARY:Holiday
"""), DAY, BERLIN)
        self.assertTrue(got[0]["all_day"])
        self.assertEqual(got[0]["start"], datetime(2026, 8, 31, 0, 0))

    def test_missing_dtend_gets_an_hour(self):
        got = ics.parse(feed("UID:a\r\nDTSTART:20260831T060000Z\r\nSUMMARY:x"), DAY, BERLIN)
        self.assertEqual(got[0]["end"] - got[0]["start"], datetime(2026, 1, 1, 1) - datetime(2026, 1, 1, 0))

    def test_other_days_are_not_returned(self):
        self.assertEqual(titles(feed("UID:a\r\nDTSTART:20260901T080000Z\r\nSUMMARY:tomorrow")), [])

    def test_event_running_across_midnight_into_today(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260830T220000Z
DTEND:20260831T040000Z
SUMMARY:Night shift
""")), ["Night shift"])

    def test_sorted_by_start(self):
        self.assertEqual(titles(feed(
            "UID:a\r\nDTSTART:20260831T160000Z\r\nSUMMARY:later",
            "UID:b\r\nDTSTART:20260831T060000Z\r\nSUMMARY:earlier",
        )), ["earlier", "later"])


class TestParsing(unittest.TestCase):
    def test_folded_line_is_rejoined(self):
        self.assertEqual(titles(feed(
            "UID:a\r\nDTSTART:20260831T080000Z\r\nSUMMARY:Design review with\r\n  the platform team")),
            ["Design review with the platform team"])

    def test_escapes_are_unescaped(self):
        self.assertEqual(titles(feed(
            "UID:a\r\nDTSTART:20260831T080000Z\r\nSUMMARY:Dentist\\, then\\nlunch")),
            ["Dentist, then lunch"])

    def test_untitled_event_still_shows(self):
        self.assertEqual(titles(feed("UID:a\r\nDTSTART:20260831T080000Z")), ["(no title)"])

    def test_cancelled_is_skipped(self):
        self.assertEqual(titles(feed(
            "UID:a\r\nDTSTART:20260831T080000Z\r\nSUMMARY:off\r\nSTATUS:CANCELLED")), [])

    def test_event_without_dtstart_is_ignored(self):
        self.assertEqual(titles(feed("UID:a\r\nSUMMARY:nonsense")), [])

    def test_junk_feed_yields_nothing(self):
        self.assertEqual(ics.parse("not a calendar at all", DAY, BERLIN), [])

    def test_lf_only_line_endings(self):
        self.assertEqual(titles(
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a\nDTSTART:20260831T080000Z\n"
            "SUMMARY:unix\nEND:VEVENT\nEND:VCALENDAR"), ["unix"])


class TestRecurrence(unittest.TestCase):
    def test_daily(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260801T060000Z
SUMMARY:Pills
RRULE:FREQ=DAILY
""")), ["Pills"])

    def test_daily_with_interval_misses(self):
        # Started 1 Aug, every other day: 31 Aug is day 30, an odd offset.
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260802T060000Z
SUMMARY:Bins
RRULE:FREQ=DAILY;INTERVAL=2
""")), [])

    def test_weekly_byday_matches_monday(self):
        got = ics.parse(feed("""
UID:a
DTSTART:20260805T073000Z
SUMMARY:Gym
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
"""), DAY, BERLIN)
        self.assertEqual([e["title"] for e in got], ["Gym"])
        self.assertEqual(got[0]["start"], datetime(2026, 8, 31, 9, 30))

    def test_weekly_byday_without_today(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260805T073000Z
SUMMARY:Gym
RRULE:FREQ=WEEKLY;BYDAY=TU,TH
""")), [])

    def test_weekly_without_byday_uses_dtstart_weekday(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260803T090000Z
SUMMARY:Sync
RRULE:FREQ=WEEKLY
""")), ["Sync"])

    def test_monthly(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260131T090000Z
SUMMARY:Rent
RRULE:FREQ=MONTHLY
""")), ["Rent"])

    def test_yearly(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20200831T000000Z
SUMMARY:Birthday
RRULE:FREQ=YEARLY
""")), ["Birthday"])

    def test_until_in_the_past_stops_it(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260801T060000Z
SUMMARY:Old habit
RRULE:FREQ=DAILY;UNTIL=20260810T000000Z
""")), [])

    def test_count_exhausted(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260801T060000Z
SUMMARY:Three days only
RRULE:FREQ=DAILY;COUNT=3
""")), [])

    def test_exdate_removes_todays_instance(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260801T060000Z
SUMMARY:Pills
RRULE:FREQ=DAILY
EXDATE:20260831T060000Z
""")), [])

    def test_override_moves_the_time(self):
        got = ics.parse(feed("""
UID:a
DTSTART:20260801T060000Z
SUMMARY:Standup
RRULE:FREQ=DAILY
""", """
UID:a
RECURRENCE-ID:20260831T060000Z
DTSTART:20260831T100000Z
DTEND:20260831T103000Z
SUMMARY:Standup (late)
"""), DAY, BERLIN)
        self.assertEqual([(e["title"], e["start"].hour) for e in got], [("Standup (late)", 12)])

    def test_override_moved_to_another_day_disappears(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260801T060000Z
SUMMARY:Standup
RRULE:FREQ=DAILY
""", """
UID:a
RECURRENCE-ID:20260831T060000Z
DTSTART:20260902T100000Z
SUMMARY:Standup (moved)
""")), [])

    def test_cancelled_instance_of_a_series(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260801T060000Z
SUMMARY:Standup
RRULE:FREQ=DAILY
""", """
UID:a
RECURRENCE-ID:20260831T060000Z
DTSTART:20260831T060000Z
STATUS:CANCELLED
SUMMARY:Standup
""")), [])

    def test_real_google_biweekly_rule(self):
        """The shape Google actually writes: WKST, a DATE-only UNTIL, INTERVAL.

        An all-day event first held Thu 27 Aug 2026, every second Thursday.
        """
        cal = feed("""
UID:a
DTSTART;VALUE=DATE:20260827
DTEND;VALUE=DATE:20260828
RRULE:FREQ=WEEKLY;WKST=SU;UNTIL=20270806;INTERVAL=2;BYDAY=TH
SUMMARY:Ur - Far Sporthalle
""")
        fires = [d for d in (date(2026, 8, 27), date(2026, 9, 3), date(2026, 9, 10),
                             date(2026, 9, 17), date(2026, 9, 24), date(2027, 8, 12))
                 if titles(cal, d)]
        self.assertEqual(fires, [date(2026, 8, 27), date(2026, 9, 10), date(2026, 9, 24)])
        got = ics.parse(cal, date(2026, 9, 10), BERLIN)[0]
        self.assertTrue(got["all_day"])
        self.assertEqual((got["start"], got["end"]),
                         (datetime(2026, 9, 10), datetime(2026, 9, 11)))

    def test_wkst_decides_which_week_a_sunday_belongs_to(self):
        # Sun 6 + Tue 8 Sep 2026, fortnightly. Under WKST=SU they are the same
        # week, so both fire; under WKST=MO the Sunday starts a week of its own
        # and lands in a skipped one.
        rule = "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SU,TU;WKST=%s"
        for wkst, expected in (("SU", ["Class"]), ("MO", [])):
            with self.subTest(wkst=wkst):
                cal = feed(f"UID:a\r\nDTSTART:20260908T080000Z\r\nSUMMARY:Class\r\n{rule % wkst}")
                self.assertEqual(titles(cal, date(2026, 9, 20)), expected)

    def test_unknown_freq_degrades_to_one_off(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260831T060000Z
SUMMARY:Odd rule
RRULE:FREQ=HOURLY
""")), ["Odd rule"])

    def test_recurrence_starting_after_today_is_not_drawn(self):
        self.assertEqual(titles(feed("""
UID:a
DTSTART:20260901T060000Z
SUMMARY:Future habit
RRULE:FREQ=DAILY
""")), [])


if __name__ == "__main__":
    unittest.main()
