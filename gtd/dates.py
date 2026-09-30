"""Dates typed the way Superhuman's "Remind me" takes them: a few characters, and a list of
suggestions each resolved to a day. Dates only, no times.

    ""         no date · tomorrow · next week · this weekend   (plus last used, before due)
    "2"        in 2 days · in 2 weekdays · in 2 weeks · in 2 months
    "3w"       in 3 weeks
    "wed"      on Wednesday (the next one, never today)
    "t"        today · tomorrow · this weekend · on Tuesday …
    "dec"      on December 1st · 2nd · 3rd
    "j"        on January 1st · 2nd · 3rd · on June 1st · on July 1st
    "dec 5"    on December 5th   (also "5 dec", "december 5th", "2026-10-05")
    "-3"       3 days before due · 3 weeks before due   (start field, when a due date is set)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .model import _add_months

MAX = 5
STATE = Path("~/.local/state/gtd/last-used.json").expanduser()
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
UNITS = ["days", "weekdays", "weeks", "months"]            # the order number rows appear in
UNIT_ALIASES = {"d": "days", "day": "days", "wd": "weekdays", "w": "weeks", "wk": "weeks", "wks": "weeks",
                "week": "weeks", "m": "months", "mo": "months", "mos": "months", "month": "months",
                "weekday": "weekdays"}


@dataclass(frozen=True)
class Suggestion:
    label: str
    day: date | None


def fmt(day: date) -> str:
    """The right-hand column: WED, OCT 07."""
    return day.strftime("%a, %b %d").upper()


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# The building blocks

def next_weekday(today: date, weekday: int) -> date:
    """The next given weekday, never today."""
    return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)


def add_weekdays(today: date, n: int) -> date:
    day = today
    while n > 0:
        day += timedelta(days=1)
        n -= day.weekday() < 5
    return day


def in_units(today: date, n: int, unit: str) -> date:
    if unit == "days":
        return today + timedelta(days=n)
    if unit == "weekdays":
        return add_weekdays(today, n)
    if unit == "weeks":
        return today + timedelta(weeks=n)
    return _add_months(today, n)


def next_occurrence(today: date, month: int, day: int) -> date | None:
    """That month and day this year, or next year if it has already passed."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= today:
            return candidate
    return None


def month_day(today: date, month: int, day: int) -> Suggestion | None:
    found = next_occurrence(today, month, day)
    return Suggestion(f"on {MONTHS[month - 1].title()} {ordinal(day)}", found) if found else None


def words(today: date) -> list[tuple[list[str], Suggestion]]:
    """The fixed vocabulary: (the phrases it answers to, what it means today)."""
    end_of_month = _add_months(date(today.year, today.month, 1), 1) - timedelta(days=1)
    vocab = [
        (["today"], Suggestion("today", today)),
        (["tomorrow"], Suggestion("tomorrow", today + timedelta(days=1))),
        (["this weekend", "weekend"], Suggestion("this weekend", next_weekday(today, 5))),
        (["end of week", "eow"], Suggestion("end of week", next_weekday(today, 4))),
        (["next week"], Suggestion("next week", next_weekday(today, 0))),
        (["next month"], Suggestion("next month", _add_months(date(today.year, today.month, 1), 1))),
        (["end of month", "eom"], Suggestion("end of month", end_of_month)),
    ]
    vocab += [([name], Suggestion(f"on {name.title()}", next_weekday(today, n))) for n, name in enumerate(WEEKDAYS)]
    return vocab


# The engine

def ranked(items: list[Suggestion], keep_order: bool = False) -> list[Suggestion]:
    """No repeated dates, nearest first unless the order is given, at most MAX."""
    out, seen = [], set()
    for s in (items if keep_order else sorted(items, key=lambda s: (s.day is not None, s.day or date.min))):
        if s.day not in seen:
            seen.add(s.day)
            out.append(s)
    return out[:MAX]


def number_rows(text: str, today: date) -> list[Suggestion] | None:
    m = re.fullmatch(r"(\d+)\s*([a-z]*)", text)
    if not m:
        return None
    n, unit = int(m[1]), m[2]
    units = UNITS if not unit else ([UNIT_ALIASES[unit]] if unit in UNIT_ALIASES
                                    else [u for u in UNITS if u.startswith(unit)])
    if not units:  # "5 dec" is a date, not a number of units: let the date rules have it
        return None
    rows = [Suggestion(f"in {n} {u[:-1] if n == 1 else u}", in_units(today, n, u)) for u in units]
    return ranked(rows, keep_order=True)


def before_due_rows(text: str, due: date | None) -> list[Suggestion] | None:
    m = re.fullmatch(r"-\s*(\d+)\s*([a-z]*)", text)
    if not m:
        return None
    if not due:
        return []
    n, unit = int(m[1]), m[2]
    rows = []
    for u, days in (("days", 1), ("weeks", 7)):
        if not unit or u.startswith(unit) or UNIT_ALIASES.get(unit) == u:
            rows.append(Suggestion(f"{n} {u[:-1] if n == 1 else u} before due", due - timedelta(days=n * days)))
    return ranked(rows, keep_order=True)


def explicit_rows(text: str, today: date) -> list[Suggestion] | None:
    try:
        day = date.fromisoformat(text)
        return [Suggestion(f"on {MONTHS[day.month - 1].title()} {ordinal(day.day)}", day)]
    except ValueError:
        pass
    m = (re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?", text)
         or re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)", text))
    if not m:
        return None
    name, day = (m[1], m[2]) if m[1].isalpha() else (m[2], m[1])
    months = [n for n, month in enumerate(MONTHS, 1) if month.startswith(name) and len(name) >= 3]
    rows = [s for s in (month_day(today, month, int(day)) for month in months) if s]
    return ranked(rows)


def word_rows(text: str, today: date) -> list[Suggestion]:
    rows = [s for phrases, s in words(today) if any(p.startswith(text) for p in phrases)]
    months = sorted((n for n, month in enumerate(MONTHS, 1) if month.startswith(text)),
                    key=lambda n: next_occurrence(today, n, 1))
    for i, month in enumerate(months):  # the first month offers three days, the others the 1st
        rows += [s for s in (month_day(today, month, d) for d in ((1, 2, 3) if i == 0 else (1,))) if s]
    return ranked(rows)


def suggest(text: str, today: date, *, field: str = "due", due: date | None = None,
            last: date | None = None) -> list[Suggestion]:
    """What the date field offers for what has been typed so far."""
    text = re.sub(r"^(in|on)\s+", "", text.strip().lower())
    if not text:
        rows = [Suggestion("no date", None)]
        if field == "start" and due and due - timedelta(days=3) >= today:  # never offer a past start
            rows.append(Suggestion("3 days before due", due - timedelta(days=3)))
        if last:
            rows.append(Suggestion("last used", last))
        rows += [Suggestion("tomorrow", today + timedelta(days=1)), Suggestion("next week", next_weekday(today, 0)),
                 Suggestion("this weekend", next_weekday(today, 5))]
        return ranked(rows, keep_order=True)
    for rule in (lambda: before_due_rows(text, due if field == "start" else None),
                 lambda: number_rows(text, today), lambda: explicit_rows(text, today)):
        if (found := rule()) is not None:
            return found
    return word_rows(text, today)


def best(text: str, today: date, **kw) -> date | None | str:
    """The top suggestion's day: what Enter would take. "invalid" when nothing matches."""
    found = suggest(text, today, **kw)
    return found[0].day if found else "invalid"


# Last used, per field

def last_used(field: str, state: Path = STATE) -> date | None:
    try:
        return date.fromisoformat(json.loads(state.read_text())[field])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def remember(field: str, day: date, state: Path = STATE) -> None:
    try:
        data = json.loads(state.read_text())
    except (OSError, ValueError):
        data = {}
    data[field] = str(day)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(data) + "\n")
