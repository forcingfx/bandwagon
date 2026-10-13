from datetime import date

import pytest

from gtd import dates

WED = date(2026, 9, 30)  # a Wednesday
D = date


def rows(text, today=WED, **kw):
    return [(s.label, s.day) for s in dates.suggest(text, today, **kw)]


@pytest.mark.parametrize("text, expected", [
    # empty: the Superhuman defaults, with "no date" first so Enter skips
    ("", [("no date", None), ("tomorrow", D(2026, 10, 1)), ("next week", D(2026, 10, 5)),
          ("this weekend", D(2026, 10, 3))]),
    # numbers: in N days / weekdays / weeks / months, no repeated dates
    ("2", [("in 2 days", D(2026, 10, 2)), ("in 2 weeks", D(2026, 10, 14)), ("in 2 months", D(2026, 11, 30))]),
    ("in 2", [("in 2 days", D(2026, 10, 2)), ("in 2 weeks", D(2026, 10, 14)), ("in 2 months", D(2026, 11, 30))]),
    ("3", [("in 3 days", D(2026, 10, 3)), ("in 3 weekdays", D(2026, 10, 5)), ("in 3 weeks", D(2026, 10, 21)),
           ("in 3 months", D(2026, 12, 30))]),
    ("1", [("in 1 day", D(2026, 10, 1)), ("in 1 week", D(2026, 10, 7)), ("in 1 month", D(2026, 10, 30))]),
    ("3w", [("in 3 weeks", D(2026, 10, 21))]),
    ("3 d", [("in 3 days", D(2026, 10, 3))]),
    ("3 wee", [("in 3 weekdays", D(2026, 10, 5)), ("in 3 weeks", D(2026, 10, 21))]),
    ("3 months", [("in 3 months", D(2026, 12, 30))]),
    # words, nearest date first
    ("wed", [("on Wednesday", D(2026, 10, 7))]),
    ("on fri", [("on Friday", D(2026, 10, 2))]),
    ("we", [("this weekend", D(2026, 10, 3)), ("on Wednesday", D(2026, 10, 7))]),
    ("t", [("today", WED), ("tomorrow", D(2026, 10, 1)), ("this weekend", D(2026, 10, 3)),
           ("on Tuesday", D(2026, 10, 6))]),
    ("n", [("next month", D(2026, 10, 1)), ("next week", D(2026, 10, 5)), ("on November 1st", D(2026, 11, 1)),
           ("on November 2nd", D(2026, 11, 2)), ("on November 3rd", D(2026, 11, 3))]),
    ("e", [("end of month", WED), ("end of week", D(2026, 10, 2))]),      # Sep 30 is the end of the month
    # months: the first three days of the first match, the 1st of the others
    ("dec", [("on December 1st", D(2026, 12, 1)), ("on December 2nd", D(2026, 12, 2)),
             ("on December 3rd", D(2026, 12, 3))]),
    ("j", [("on January 1st", D(2027, 1, 1)), ("on January 2nd", D(2027, 1, 2)), ("on January 3rd", D(2027, 1, 3)),
           ("on June 1st", D(2027, 6, 1)), ("on July 1st", D(2027, 7, 1))]),
    ("ja", [("on January 1st", D(2027, 1, 1)), ("on January 2nd", D(2027, 1, 2)),
            ("on January 3rd", D(2027, 1, 3))]),
    # explicit dates; a day already past this year rolls to next year
    ("dec 5", [("on December 5th", D(2026, 12, 5))]),
    ("5 dec", [("on December 5th", D(2026, 12, 5))]),
    ("december 5th", [("on December 5th", D(2026, 12, 5))]),
    ("sep 1", [("on September 1st", D(2027, 9, 1))]),
    ("sep 30", [("on September 30th", WED)]),
    ("2026-10-05", [("on October 5th", D(2026, 10, 5))]),
    ("xyz", []),
])
def test_suggest_table(text, expected):
    assert rows(text) == expected


def test_last_used_and_start_relative_rows_and_cap():
    assert rows("", last=D(2026, 10, 7)) == [
        ("no date", None), ("last used", D(2026, 10, 7)), ("tomorrow", D(2026, 10, 1)),
        ("next week", D(2026, 10, 5)), ("this weekend", D(2026, 10, 3))]
    assert rows("", field="start", due=D(2026, 10, 10), last=D(2026, 10, 7))[:3] == [
        ("no date", None), ("3 days before due", D(2026, 10, 7)), ("tomorrow", D(2026, 10, 1))]
    assert len(dates.suggest("", WED, field="start", due=D(2026, 10, 10), last=D(2026, 10, 8))) == 5


def test_before_due_needs_a_due_date():
    assert rows("-3", field="start", due=D(2026, 10, 10)) == [
        ("3 days before due", D(2026, 10, 7)), ("3 weeks before due", D(2026, 9, 19))]
    assert rows("-3", field="start") == []


def test_edges():
    assert rows("1 month", today=D(2027, 1, 31)) == [("in 1 month", D(2027, 2, 28))]
    friday = D(2026, 10, 2)
    assert ("in 2 weekdays", D(2026, 10, 6)) in rows("2", today=friday)
    assert rows("wed", today=WED) == [("on Wednesday", D(2026, 10, 7))]   # never today


def test_best_and_fmt():
    assert dates.best("fri", WED) == D(2026, 10, 2)
    assert dates.best("", WED) is None
    assert dates.best("nonsense", WED) == "invalid"
    assert dates.best("-3", WED, field="start", due=D(2026, 10, 10)) == D(2026, 10, 7)
    assert dates.fmt(D(2026, 10, 7)) == "WED, OCT 07"


def test_last_used_round_trips(tmp_path):
    state = tmp_path / "last-used.json"
    assert dates.last_used("due", state) is None
    dates.remember("due", D(2026, 10, 7), state)
    assert dates.last_used("due", state) == D(2026, 10, 7)
    state.write_text("not json")
    assert dates.last_used("due", state) is None


def test_start_default_never_offers_a_past_date():
    assert ("3 days before due", D(2026, 9, 29)) not in rows("", field="start", due=D(2026, 10, 2))
