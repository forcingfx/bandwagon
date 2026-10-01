"""GTD data model and the clarify rules from David Allen's workflow diagram.

Every function here is pure over a Doc, so the TUI and the tests share one
set of rules. The book's "hard edges" between lists are the Section enum.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum


class Section(str, Enum):
    IN = "In"
    NEXT = "Next Actions"
    PROJECTS = "Projects"
    WAITING = "Waiting For"
    SOMEDAY = "Someday/Maybe"
    DONE = "Done"


CONTEXTS = ["@calls", "@computer", "@office", "@home", "@errands", "@agendas"]
DONE_RETENTION_DAYS = 14


@dataclass
class Item:
    text: str
    done: bool = False
    context: str | None = None  # one or more contexts, space-separated: "@calls @errands"
    project: str | None = None
    waiting: str | None = None
    src: str | None = None      # where an automated proposal came from (a URL, or "process")
    every: str | None = None    # recurrence: daily, weekly, monthly, yearly, or N-days/weeks/months/years
    start: date | None = None   # hidden until this day (Allen's tickler); rolls with due
    due: date | None = None     # a real deadline (home only by convention)
    since: date | None = None
    added: date | None = None
    done_on: date | None = None
    raw: str | None = None  # a line the parser could not read, kept verbatim
    area: str | None = field(default=None, compare=False)  # which file it lives in; never written

    @property
    def contexts(self) -> list[str]:
        return self.context.split() if self.context else []

    def line(self) -> str:
        if self.raw is not None:
            return self.raw
        parts = [f"- [{'x' if self.done else ' '}] {self.text}"]
        if self.context:
            parts.append(self.context)
        if self.project:
            parts.append(f"+{self.project}")
        for key in ("waiting", "src", "every", "start", "due", "since", "added"):
            value = getattr(self, key)
            if value:
                parts.append(f"{key}:{value}")
        if self.done_on:
            parts.append(f"done:{self.done_on}")
        return " ".join(parts)


@dataclass
class Doc:
    sections: dict[str, list[Item]] = field(default_factory=dict)
    last_review: date | None = None
    prune_manual: bool = False  # True: Done is pruned only on demand, not on every write
    preamble: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> Doc:
        return cls(sections={s.value: [] for s in Section})

    def items(self, section: Section) -> list[Item]:
        return self.sections.setdefault(section.value, [])

    def remove(self, item: Item | None) -> None:
        if item is None:
            return
        for items in self.sections.values():
            for i, candidate in enumerate(items):
                if candidate is item:
                    del items[i]
                    return


# Links are written the way Obsidian reads them, so they work in the app and in Obsidian:
# [[note|label]] (or [[note]]) for a vault note, [label](url) or a bare URL for anything else.
LINK = re.compile(r"\[\[(?P<note>[^\]|]+?)(?:\|(?P<alias>[^\]]+))?\]\]"
                  r"|\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)\)"
                  r"|(?P<bare>https?://[^\s)\]]+)")


def _link(m: re.Match) -> tuple[str, str, str]:
    if m["note"]:
        return m["alias"] or m["note"].split("#")[0].split("/")[-1], "note", m["note"]
    if m["url"]:
        return m["label"], "url", m["url"]
    return m["bare"].split("://", 1)[1], "url", m["bare"]


def links(text: str) -> list[tuple[str, str, str]]:
    """Every link in the text as (label, "note" or "url", target)."""
    return [_link(m) for m in LINK.finditer(text)]


def split_trailing_links(text: str) -> tuple[str, list[str]]:
    """The action text and the links written at its end (which the app shows as chips)."""
    trailing: list[str] = []
    rest = text.rstrip()
    while True:
        last = None
        for m in LINK.finditer(rest):
            last = m
        if not last or last.end() != len(rest) or not rest[:last.start()].strip():
            break
        trailing.insert(0, last.group(0))
        rest = rest[:last.start()].rstrip()
    return rest, trailing


def links_last(text: str, project: str | None = None) -> str:
    """Rewrite "[[note|Label]]: action" as "Action [[note|Label]]": the action first, links
    after. A leading link to the item's own project is dropped, because +project carries it."""
    m = re.match(r"\s*(" + LINK.pattern + r")\s*:?\s*", text)
    if not m or not text[m.end():].strip():
        return text
    link, rest = m.group(1), text[m.end():].strip()
    rest = rest[0].upper() + rest[1:]
    note = LINK.match(link)["note"]
    if note and project and note.split("#")[0].split("/")[-1] == project:
        return rest
    return f"{rest} {link}"


def link_segments(text: str) -> list[tuple[str, str | None]]:
    """The text split for display: plain runs, and link labels with their target."""
    out, pos = [], 0
    for m in LINK.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], None))
        label, _, target = _link(m)
        out.append((label, target))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], None))
    return out


def find(doc: Doc, section: Section, line: str) -> Item | None:
    return next((i for i in doc.items(section) if i.line() == line), None)


EVERY = re.compile(r"^(daily|weekly|monthly|yearly|(\d+)-(days|weeks|months|years))$")


def valid_every(value: str) -> bool:
    return bool(EVERY.match(value))


def _add_months(day: date, months: int) -> date:
    y, m = divmod(day.month - 1 + months, 12)
    year, month = day.year + y, m + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def next_due(due: date | None, every: str, today: date) -> date:
    """The next due date: the interval added to the later of the old due date and today."""
    base = max(due or today, today)
    m = EVERY.match(every)
    if not m:
        raise ValueError(f"bad recurrence: {every}")
    word, n, unit = m.group(1), int(m.group(2) or 1), m.group(3) or m.group(1)
    unit = {"daily": "days", "weekly": "weeks", "monthly": "months", "yearly": "years"}.get(word, unit)
    if unit == "days":
        return base + timedelta(days=n)
    if unit == "weeks":
        return base + timedelta(weeks=n)
    return _add_months(base, n * (12 if unit == "years" else 1))


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return "-".join(slug.split("-")[:6]) or "project"


# Capture

def capture(doc: Doc, text: str, today: date) -> Item:
    item = Item(text=text.strip(), added=today)
    doc.items(Section.IN).append(item)
    return item


def propose(doc: Doc, text: str, src: str, today: date) -> Item:
    """An automated capture: lands in In, marked with where it came from."""
    item = Item(text=text.strip(), src=src, added=today)
    doc.items(Section.IN).append(item)
    return item


def expire_proposals(doc: Doc, today: date, days: int = 7) -> list[Item]:
    """Drop proposals left in In for `days` or more; Arjun's own captures never expire."""
    cutoff = today - timedelta(days=days)
    gone = [i for i in doc.items(Section.IN) if i.src and i.added and i.added <= cutoff]
    doc.sections[Section.IN.value] = [i for i in doc.items(Section.IN) if i not in gone or i.raw]
    return gone


# Clarify: not actionable -> trash, incubate (Someday/Maybe) or reference

def trash(doc: Doc, item: Item) -> None:
    doc.remove(item)


def move(doc: Doc, item: Item, section: Section) -> None:
    doc.remove(item)
    item.done = section is Section.DONE
    doc.items(section).append(item)


# Clarify: actionable -> project if more than one step, then do, delegate or defer

def make_project(doc: Doc, source: Item | None, outcome: str, today: date) -> str:
    taken = {p.project for p in doc.items(Section.PROJECTS)}
    base = slug = slugify(outcome)
    n = 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    doc.remove(source)
    doc.items(Section.PROJECTS).append(Item(text=outcome.strip(), project=slug, added=today))
    return slug


def do_now(doc: Doc, source: Item | None, action: str, project: str | None, today: date) -> None:
    """The two-minute rule: done the moment it is defined."""
    doc.remove(source)
    doc.items(Section.DONE).append(Item(text=action.strip(), done=True, project=project, done_on=today))


def delegate(doc: Doc, source: Item | None, action: str, who: str, project: str | None, today: date,
             due: date | None = None, start: date | None = None) -> None:
    doc.remove(source)
    item = Item(text=action.strip(), project=project, waiting=who.strip().lower(), start=start, due=due, since=today)
    doc.items(Section.WAITING).append(item)
    return item


def defer(doc: Doc, source: Item | None, action: str, context: str | None, project: str | None, today: date,
          due: date | None = None, start: date | None = None) -> None:
    doc.remove(source)
    item = Item(text=action.strip(), context=context, project=project, start=start, due=due, added=today)
    doc.items(Section.NEXT).append(item)
    return item


# Organize and reflect

def complete(doc: Doc, item: Item, today: date) -> date | None:
    """Tick. A recurring item stays where it is with its due date rolled forward, and the
    due date it was ticked for is returned; anything else moves to Done."""
    if item.every:
        was = item.due or today
        item.due = next_due(item.due, item.every, today)
        if item.start:  # keep the same lead time before the new due date
            item.start = item.start + (item.due - was)
        return was
    doc.remove(item)
    item.done, item.done_on = True, today
    doc.items(Section.DONE).append(item)
    return None


def uncomplete(doc: Doc, item: Item, section: Section, index: int) -> None:
    """Untick: take the item out of Done and put it back at the given position."""
    doc.remove(item)
    item.done, item.done_on = False, None
    items = doc.items(section)
    items.insert(min(index, len(items)), item)


def reopen_target(item: Item) -> Section:
    """Where a Done item belongs if reopened, read from its own tags."""
    if item.waiting:
        return Section.WAITING
    if item.context:
        return Section.NEXT
    if item.project:
        return Section.PROJECTS
    return Section.IN


def starts_later(item: Item, today: date) -> bool:
    return bool(item.start and item.start > today and not item.done)


def stalled_projects(doc: Doc) -> list[Item]:
    """Projects with no open next action or waiting-for: the weekly-review check."""
    moving = {i.project for s in (Section.NEXT, Section.WAITING) for i in doc.items(s) if i.project}
    return [p for p in doc.items(Section.PROJECTS) if p.raw is None and p.project not in moving]


def prune_done(doc: Doc, today: date, days: int = DONE_RETENTION_DAYS) -> None:
    cutoff = today - timedelta(days=days)
    doc.sections[Section.DONE.value] = [
        i for i in doc.items(Section.DONE) if i.raw is not None or i.done_on is None or i.done_on >= cutoff]
