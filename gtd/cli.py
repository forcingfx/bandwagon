"""The automation surface: every script and skill reads and writes gtd.md through these
commands, never by hand, so every write goes through the parser and the atomic write.

    gtd                                   open the app
    gtd add "text" [--area home]          capture to In
    gtd propose "text" --src URL [--area work] [--caller process]
                                          an automated proposal; capped per day where the
                                          area sets proposal_cap, extras parked in overflow;
                                          proposal_reserve keeps slots of the cap for a caller
    gtd done "title words"                tick the one open item matching (recurring: roll);
                                          in a terminal, several matches open a picker
    gtd find "words"                      show matching items, open and done, in every area
                                          (every word, any order, in the text or the tags:
                                          +project, @context, a person); in a terminal, a
                                          picker over the open ones to tick and star
    gtd list [--area a] [--section s]     every item as JSON, for skills and views
    gtd expire [--days 7]                 drop proposals left in In for 7 days into a log
    gtd sweep                             file checkboxes ticked in Obsidian: one-off items go
                                          to Done, recurring ones roll their due date forward
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from . import config, dates, helptext, model, store
from .model import Section

SECTIONS = {s.name.lower(): s for s in Section} | {s.value.lower(): s for s in Section}
OVERFLOW_KEEP_DAYS = 7
EXPIRED_KEEP_DAYS = 28


def item_json(area: str, section: Section, item: model.Item) -> dict:
    return {
        "area": area, "section": section.value, "text": item.text, "done": item.done,
        "context": item.context, "contexts": item.contexts, "project": item.project, "waiting": item.waiting, "src": item.src,
        "every": item.every, "start": item.start and str(item.start), "due": item.due and str(item.due), "since": item.since and str(item.since),
        "added": item.added and str(item.added), "done_on": item.done_on and str(item.done_on),
        "line": item.line(),
    }


def all_items(areas: dict[str, config.Area], area: str | None = None):
    for name, spec in areas.items():
        if area and name != area:
            continue
        doc = store.load(spec.file)
        for section in Section:
            for item in doc.items(section):
                if item.raw is None:
                    yield name, section, item


def is_open(section: Section, item: model.Item) -> bool:
    return not (item.done or section is Section.DONE)


def matches(areas, words: str, open_only: bool = False):
    """Every word appears in the text or tags, in any order, as in the app's / palette."""
    return [(a, s, i) for a, s, i in all_items(areas)
            if model.task_matches(words, i) and not (open_only and not is_open(s, i))]


def keep_recent_lines(path: Path, today: date, days: int, header: list[str]) -> list[str]:
    """The dated '- YYYY-MM-DD …' lines of a log newer than `days`, under its header."""
    lines = path.read_text().splitlines() if path.exists() else []
    cutoff = str(today - timedelta(days=days))
    return header + [ln for ln in lines if ln.startswith("- ") and ln[2:12] >= cutoff]


def append_log(path: Path, today: date, days: int, header: list[str], new: list[str]) -> None:
    store.write_text(path, "\n".join(keep_recent_lines(path, today, days, header) + new) + "\n")


def build_item(text: str, today: date, **tags) -> model.Item:
    """An item from free text, reading any tags already at the end of the text, then
    applying explicit flags on top, so the written line is always in canonical order."""
    item = store.parse_item(f"- [ ] {text.strip()}")
    if item.raw is not None:  # tags in the text were out of order: keep the whole text as text
        item = model.Item(text=text.strip())
    for key, value in tags.items():
        if value:
            if key in ("due", "start"):  # the same quick forms as the app: 2026-10-05, fri, +3d, dec 5
                parsed = dates.best(value, today, field=key, due=tags.get("due") and dates.best(tags["due"], today))
                if parsed in (None, "invalid"):
                    raise SystemExit(f"Can't read --{key} {value!r} as a date (try 2026-10-05, fri, 3d, dec 5)")
                value = parsed
            elif key == "since":
                value = date.fromisoformat(value)
            elif key == "context":
                names = value if isinstance(value, list) else [value]
                value = " ".join(c if c.startswith("@") else f"@{c}"
                                 for name in names for c in name.replace(",", " ").split())
            elif key == "every" and not model.valid_every(value):
                raise SystemExit(f"Bad --every {value!r}: use daily, weekly, monthly, yearly or N-days/weeks/months/years")
            setattr(item, key, value)
    item.added = item.added or today
    return item


def place(section: str | None) -> Section:
    return SECTIONS[section] if section else Section.IN


# Commands

def cmd_add(areas, area: str, text: str, today: date, section: str | None = None, **tags) -> str:
    item, where = build_item(text, today, **tags), place(section)
    store.mutate(areas[area].file, lambda d: d.items(where).append(item), today)
    return f"Captured to {area} {where.value}: {item.text}"


def cmd_propose(areas, area: str, text: str, src: str, today: date, caller: str | None = None, **tags) -> str:
    spec, caller = areas[area], caller or ""
    if spec.proposal_cap is not None and spec.state_dir:
        counts_file = spec.state_dir / "gtd-proposals.json"
        counts = json.loads(counts_file.read_text()) if counts_file.exists() else {}
        by = counts.get(str(today), {})
        by = dict(by) if isinstance(by, dict) else {"": by}  # a bare number: written before callers were counted
        made = sum(by.values())
        # Slots other callers have reserved and not used yet are closed to this one.
        held = sum(max(0, n - by.get(name, 0)) for name, n in spec.proposal_reserve.items() if name != caller)
        if made + held >= spec.proposal_cap:
            append_log(spec.state_dir / "task-overflow.md", today, OVERFLOW_KEEP_DAYS,
                       ["# Task overflow", "",
                        "<!-- Written by `gtd propose`. Proposals past the daily cap. Not tasks. "
                        "Re-propose one tomorrow or capture it by hand; lines older than 7 days drop. -->", ""],
                       [f"- {today} {text} [source]({src})"])
            if made >= spec.proposal_cap:
                return f"cap: {spec.proposal_cap} proposals already made today; parked to overflow"
            keepers = ", ".join(name for name in spec.proposal_reserve if name != caller)
            return (f"cap: {made} of {spec.proposal_cap} proposals made today and the other {held} "
                    f"reserved for {keepers}; parked to overflow")
        by[caller] = by.get(caller, 0) + 1
        store.write_text(counts_file, json.dumps({str(today): by}) + "\n")
    item = build_item(text, today, src=src, **tags)
    store.mutate(spec.file, lambda d: d.items(Section.IN).append(item), today)
    return f"Proposed to {area} In: {item.text}"


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def cmd_done(areas, words: str, today: date, picker=None) -> str:
    """Ticks the one open item matching. On several, `picker(words)` opens on them when
    given (a terminal); on none, or with no picker (scripts), it refuses with the listing."""
    found = matches(areas, words, open_only=True)
    if len(found) != 1:
        if picker is None or not found:
            listing = "\n".join(f"  {a} · {s.value} · {i.text}" for a, s, i in found)
            raise SystemExit(f"{len(found)} open items match {words!r}; need exactly one.\n{listing}".rstrip())
        return "\n".join(picker(words)) or "Nothing changed."
    return tick_item(areas, *found[0], today)


def tick_item(areas, area: str, section: Section, item: model.Item, today: date) -> str:
    line = item.line()
    rolled: list = []

    def tick(d):
        it = model.find(d, section, line)
        if (was := model.complete(d, it, today)) is not None:
            rolled.append((it.due, was))
    store.mutate(areas[area].file, tick, today)
    log_completions(areas[area], today, [(item.text, rolled[0][1] if rolled else item.due, item.every or "once")])
    if rolled:
        return f"Ticked in {area}: {item.text} (next due {rolled[0][0]})"
    return f"Ticked in {area}: {item.text}"


def star_item(areas, area: str, section: Section, item: model.Item, today: date) -> str:
    """Star for Focus, or unstar a starred item, as s does in the app."""
    star, line = None if item.star else today, item.line()
    store.mutate(areas[area].file, lambda d: setattr(model.find(d, section, line), "star", star), today)
    return f"{'Starred' if star else 'Unstarred'} in {area}: {item.text}"


def open_rows(areas) -> list:
    return [(a, s, i) for a, s, i in all_items(areas) if is_open(s, i)]


def log_completions(spec: config.Area, today: date, rows: list[tuple[str, object, str]]) -> None:
    if rows and spec.completions_log and spec.completions_log.exists():
        with spec.completions_log.open("a") as f:
            f.writelines(f"| {today} | {text.replace('|', chr(92) + '|')} | {due or ''} | {every} |\n"
                         for text, due, every in rows)


def cmd_sweep(areas, today: date, area: str | None = None) -> list[str]:
    report = []
    for name, spec in areas.items():
        if area and name != area:
            continue
        rows: list = []

        def sweep(d):
            for section in Section:
                if section is Section.DONE:
                    continue
                for item in [i for i in d.items(section) if i.done and i.raw is None]:
                    if item.every:
                        item.done, was = False, item.due
                        model.complete(d, item, today)
                        report.append(f"Rolled in {name}: {item.text} (next due {item.due})")
                        rows.append((item.text, was, item.every))
                    else:
                        item.done = False
                        model.complete(d, item, today)
                        report.append(f"Done in {name}: {item.text}")
                        rows.append((item.text, item.due, "once"))
        store.mutate(spec.file, sweep, today)
        log_completions(spec, today, rows)
    return report


def cmd_expire(areas, today: date, days: int, area: str | None = None) -> list[str]:
    report = []
    for name, spec in areas.items():
        if area and name != area:
            continue
        gone: list[model.Item] = []
        store.mutate(spec.file, lambda d: gone.extend(model.expire_proposals(d, today, days)), today)
        if gone and spec.state_dir:
            append_log(spec.state_dir / "gtd-expired.md", today, EXPIRED_KEEP_DAYS,
                       ["# Expired proposals", "",
                        f"<!-- Written by `gtd expire`. Automated proposals left in In for {days} days. "
                        "Lines older than 28 days drop. -->", ""],
                       [f"- {today} {i.text} [source]({i.src})" for i in gone])
        report += [f"Expired from {name} In: {i.text}" for i in gone]
    return report


def tag_flags(p) -> None:
    p.add_argument("--context", action="append", help="e.g. @calls; repeat or comma-separate for several")
    p.add_argument("--project", help="project slug, without +")
    p.add_argument("--waiting", help="who it is waiting on")
    p.add_argument("--every", help="repeat: daily, weekly, monthly, yearly, 2-weeks")
    p.add_argument("--due", help="due date: 2026-10-05, fri, 3d, dec 5")
    p.add_argument("--start", help="hide in the app until this date (same forms)")


def main(argv: list[str] | None = None, today: date | None = None) -> None:
    parser = helptext.parser()
    parser.add_argument("--file", type=Path, help="work on one file instead of the configured areas")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("add", help="capture to In (or --section)")
    p.add_argument("text", nargs="+")
    p.add_argument("--area")
    p.add_argument("--section", choices=sorted(SECTIONS))
    tag_flags(p)
    p = sub.add_parser("propose", help="an automated proposal to In")
    p.add_argument("text", nargs="+")
    p.add_argument("--src", required=True)
    p.add_argument("--area")
    p.add_argument("--caller", help="who is proposing (default: $GTD_CALLER), for the area's proposal_reserve")
    tag_flags(p)
    p = sub.add_parser("done", help="tick the one open item matching; a picker in a terminal when several")
    p.add_argument("words", nargs="+")
    p = sub.add_parser("find", help="show matching items; in a terminal, a picker to work through them")
    p.add_argument("words", nargs="*")
    p = sub.add_parser("list", help="every item as JSON")
    p.add_argument("--area")
    p.add_argument("--section", choices=sorted(SECTIONS))
    p = sub.add_parser("expire", help="drop old proposals from In")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--area")
    p = sub.add_parser("sweep", help="file checkboxes ticked in Obsidian")
    p.add_argument("--area")
    helptext.finish(sub)
    args = parser.parse_args(helptext.expand(sys.argv[1:] if argv is None else argv))
    today = today or date.today()

    if args.file:
        areas, capture = config.single(args.file), "main"
    else:
        areas, capture = config.load()
    area = getattr(args, "area", None) or capture
    if area not in areas:
        raise SystemExit(f"Unknown area {area!r}; configured: {', '.join(areas)}")

    tags = {k: getattr(args, k, None) for k in ("context", "project", "waiting", "every", "start", "due")}
    if args.command == "add":
        print(cmd_add(areas, area, " ".join(args.text), today, section=args.section, **tags))
    elif args.command == "propose":
        print(cmd_propose(areas, area, " ".join(args.text), args.src, today,
                          caller=args.caller or os.environ.get("GTD_CALLER"), **tags))
    elif args.command == "done":
        picker = None
        if interactive():
            from .pick import pick
            picker = lambda words: pick(areas, words, today)  # noqa: E731
        print(cmd_done(areas, " ".join(args.words), today, picker))
    elif args.command == "find" and interactive():
        from .pick import pick
        print("\n".join(pick(areas, " ".join(args.words), today)) or "Nothing changed.")
    elif args.command == "find":
        found = matches(areas, " ".join(args.words))
        for a, s, i in found:
            print(f"{a} · {s.value} · {'done' if i.done else 'open'} · {i.text}")
        sys.exit(0 if found else 1)
    elif args.command == "list":
        section = SECTIONS[args.section] if args.section else None
        print(json.dumps([item_json(a, s, i) for a, s, i in all_items(areas, getattr(args, "area", None))
                          if section is None or s is section], indent=1))
    elif args.command == "sweep":
        print("\n".join(cmd_sweep(areas, today, getattr(args, "area", None))) or "Nothing ticked.")
    elif args.command == "expire":
        print("\n".join(cmd_expire(areas, today, args.days, getattr(args, "area", None))) or "Nothing to expire.")
    else:
        from .app import GTDApp
        GTDApp(areas, capture=capture, contexts=config.contexts(), config_path=config.CONFIG).run()
