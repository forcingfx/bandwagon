"""Read and write gtd.md: one `##` section per GTD list, one `- [ ]` line per item.

Tags are parsed only from the end of a line, so an `@` or `+` inside the
sentence stays text. Lines the parser cannot read are kept verbatim.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .model import Doc, Item, Section, prune_done, valid_every

HEADER = re.compile(r"<!-- gtd v1(?: · last-review: (\d{4}-\d{2}-\d{2}))?( · prune: manual)? -->")
VERSION = re.compile(r"<!--\s*gtd v(\d+)")   # any version, to tell a newer file apart before loading it
MAX_BYTES = 5_000_000   # a task file bigger than this is something else
ITEM = re.compile(r"^- \[( |x)\] (.+)$")
DATE_TAGS = {"since", "added", "done", "due", "start", "star"}
TEXT_TAGS = {"waiting", "src", "every"}


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_item(line: str) -> Item:
    m = ITEM.match(line)
    if not m:
        return Item(text=line, raw=line)
    item = Item(text="", done=m.group(1) == "x")
    words = m.group(2).split(" ")
    while len(words) > 1:
        word = words[-1]
        key, _, value = word.partition(":")
        if word.startswith("@") and len(word) > 1:  # several contexts sit side by side
            item.context = word if item.context is None else f"{word} {item.context}"
        elif word.startswith("+") and len(word) > 1 and item.project is None:
            item.project = word[1:]
        elif key in TEXT_TAGS and value and getattr(item, key) is None:
            if key == "every" and not valid_every(value):
                break
            setattr(item, key, value)
        elif key in DATE_TAGS and _date(value):
            setattr(item, "done_on" if key == "done" else key, _date(value))
        else:
            break
        words.pop()
    item.text = " ".join(words)
    if item.line() != line:  # tags out of canonical order: keep the line exactly as written
        return Item(text=line, raw=line)
    return item


def parse(text: str) -> Doc:
    doc = Doc(sections={})
    current: list[Item] | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if (m := HEADER.fullmatch(line.strip())) and current is None:
            doc.last_review = _date(m.group(1)) if m.group(1) else None
            doc.prune_manual = bool(m.group(2))
        elif line.startswith("## "):
            current = doc.sections.setdefault(line[3:].strip(), [])
        elif current is None:
            doc.preamble.append(line)
        else:
            current.append(parse_item(line))
    for s in Section:
        doc.items(s)
    known = [s.value for s in Section]
    doc.sections = {k: doc.sections[k] for k in known + [k for k in doc.sections if k not in known]}
    return doc


def serialize(doc: Doc) -> str:
    review = f" · last-review: {doc.last_review}" if doc.last_review else ""
    prune = " · prune: manual" if doc.prune_manual else ""
    out = [f"<!-- gtd v1{review}{prune} -->", *doc.preamble]
    for name, items in doc.sections.items():
        out += ["", f"## {name}", *(i.line() for i in items)]
    return "\n".join(out) + "\n"


def load(path: Path) -> Doc:
    path = Path(path)
    return parse(path.read_text()) if path.exists() else Doc.empty()


def save(path: Path, doc: Doc) -> None:
    write_text(path, serialize(doc))


def write_text(path: Path, text: str) -> None:
    """Write through a temp file and an atomic rename, making the folder if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


LOCKS = Path("~/.cache/gtd/locks").expanduser()


@contextmanager
def locked(path: Path):
    """Hold an exclusive lock for this file while it is read, changed and written, so the
    app, the CLI and the vault timers queue instead of overwriting each other. The lock
    file lives outside the vaults, so the hourly git push never picks it up."""
    LOCKS.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:16]
    with open(LOCKS / f"{key}.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def mutate_with_before(path: Path, change: Callable[[Doc], object],
                       today: date | None = None) -> tuple[str | None, Doc]:
    """Under the lock: re-read the file, apply one change, prune old Done items (unless
    set to prune on demand), and write it back. Returns the text as it was, for undo."""
    path = Path(path)
    with locked(path):
        before = path.read_text() if path.exists() else None
        doc = parse(before) if before is not None else Doc.empty()
        change(doc)
        if not doc.prune_manual:
            prune_done(doc, today or date.today())
        save(path, doc)
    return before, doc


def mutate(path: Path, change: Callable[[Doc], object], today: date | None = None) -> Doc:
    return mutate_with_before(path, change, today)[1]


# Looking at a file before loading it: the Files screen and `gtd init` ask first

@dataclass
class Report:
    """What `inspect` found: ok, missing, partial (gtd-shaped with oddities), foreign (not a
    gtd file), newer (a later format) or unreadable. Nothing has been loaded or written."""
    status: str
    version: int | None = None
    items: int = 0
    raw_lines: int = 0                 # lines inside the lists that aren't tasks; kept, hidden
    unknown_sections: list[str] = field(default_factory=list)
    other_lines: int = 0               # a foreign file's own content
    detail: str = ""

    @property
    def refused(self) -> bool:
        return self.status in ("newer", "unreadable")

    @property
    def needs_consent(self) -> bool:
        return self.status in ("foreign", "partial")

    def sentence(self) -> str:
        """One plain sentence for the person deciding."""
        n = self.items
        if self.status == "ok":
            return f"A gtd file with {n} task{'s' if n != 1 else ''}."
        if self.status == "missing":
            return "Not there yet: it will be created with the six lists."
        if self.status == "newer":
            return f"Written by a newer gtd (v{self.version}); this version can't open it. Upgrade gtd."
        if self.status == "unreadable":
            return f"Can't read it: {self.detail}."
        if self.status == "foreign":
            return (f"Not a gtd file: no gtd header and none of the six lists. Adopting it appends "
                    f"the lists; its {self.other_lines} line{'s' if self.other_lines != 1 else ''} stay as they are.")
        parts = []
        if self.version is None:
            parts.append("No gtd header; one is added on the first save.")
        if self.raw_lines:
            parts.append(f"{self.raw_lines} line{'s' if self.raw_lines != 1 else ''} in the lists aren't tasks; "
                         "they stay in the file but are hidden here.")
        if self.unknown_sections:
            parts.append(f"Extra list{'s' if len(self.unknown_sections) != 1 else ''} kept as written: "
                         + ", ".join(self.unknown_sections) + ".")
        return " ".join(parts)

    @property
    def chip(self) -> str:
        """A few words for a list row."""
        if self.status == "ok":
            return f"{self.items} task{'s' if self.items != 1 else ''}"
        if self.status == "missing":
            return "missing: made on first save"
        if self.status == "newer":
            return f"newer gtd (v{self.version}): can't open"
        if self.status == "unreadable":
            return f"can't read: {self.detail}"
        if self.status == "foreign":
            return "not a gtd file"
        bits = [f"{self.items} tasks"]
        if self.raw_lines:
            bits.append(f"{self.raw_lines} hidden line{'s' if self.raw_lines != 1 else ''}")
        if self.version is None:
            bits.append("no header")
        return ", ".join(bits)


def inspect(path: Path) -> Report:
    """Classify a file without loading it into the app and without writing anything."""
    path = Path(path)
    if not path.exists():
        return Report("missing")
    try:
        if path.is_dir():
            return Report("unreadable", detail="it is a folder")
        if path.stat().st_size > MAX_BYTES:
            return Report("unreadable", detail="over 5 MB, too big for a task file")
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return Report("unreadable", detail="not UTF-8 text")
    except OSError as e:
        return Report("unreadable", detail=e.strerror or str(e))
    head = []
    for line in text.splitlines():
        if line.startswith("## "):
            break
        head.append(line.strip())
    version = next((int(m.group(1)) for ln in head if (m := VERSION.match(ln))), None)
    if version is not None and version > 1:
        return Report("newer", version=version)
    doc = parse(text)
    known = [s.value for s in Section]
    present = [k for k in known if re.search(rf"^## {re.escape(k)}\s*$", text, re.M)]
    items = sum(1 for s in Section for i in doc.items(s) if i.raw is None)
    raw = sum(1 for s in Section for i in doc.items(s) if i.raw is not None)
    unknown = [k for k in doc.sections if k not in known]
    if version is None and not present:
        return Report("foreign", other_lines=sum(1 for ln in text.splitlines() if ln.strip()))
    if version is None or raw or unknown:
        return Report("partial", version=version, items=items, raw_lines=raw, unknown_sections=unknown)
    return Report("ok", version=version, items=items)


def prepare(path: Path, report: Report) -> None:
    """After consent: create a missing file with the six lists, or adopt a foreign one by
    writing it back through the parser (its lines are kept, the lists appended)."""
    path = Path(path)
    if report.status == "missing":
        save(path, Doc.empty())
    elif report.status == "foreign":
        save(path, parse(path.read_text()))
