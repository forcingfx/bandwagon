"""Which task files the app and CLI work on: ~/.config/gtd/config.toml (or $GTD_CONFIG).

Each [areas.NAME] names one markdown file; the app shows the areas side by side. The
format is EXAMPLE below, which is also what the Files screen and `gtd init` write.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG = Path(os.environ.get("GTD_CONFIG", "~/.config/gtd/config.toml")).expanduser()

EXAMPLE = '''\
# gtd config. One [areas.NAME] per task file; the app shows them side by side.
capture = "work"                 # where captures land when every area is showing
contexts = ["@calls", "@computer", "@office", "@home", "@errands", "@agendas"]   # offered in clarify, in order

[areas.work]
file = "~/notes/work/gtd.md"
state_dir = "~/notes/work/.gtd"            # proposal counts, overflow, expired log (gtd propose, gtd expire)
proposal_cap = 5                           # automated proposals per day; omit for no cap
proposal_reserve = { process = 3 }         # slots of the cap kept for one caller (propose --caller)

[areas.home]
file = "~/notes/home/gtd.md"
completions_log = "~/notes/home/completions.md"   # a table of recurring and one-off ticks, appended to
'''

NO_AREAS = ("No task file is configured in {path}.\n"
            "Run gtd in a terminal and the Files screen will create or pick one, or run\n"
            "gtd init PATH/gtd.md from a script. See README.md for the config format.")


@dataclass
class Area:
    name: str
    file: Path
    state_dir: Path | None = None
    completions_log: Path | None = None
    proposal_cap: int | None = None
    proposal_reserve: dict[str, int] = field(default_factory=dict)


def _path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


def _read(path: Path) -> dict:
    """The config as a dict; {} when there is none. A broken file is one clear line, not a trace."""
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"Can't read {path}: {e}") from None


def areas_from(data: dict, path: Path) -> dict[str, Area]:
    areas = {}
    for name, spec in data.get("areas", {}).items():
        if not isinstance(spec, dict) or not spec.get("file"):
            raise SystemExit(f"[areas.{name}] in {path} needs file = \"path/to/gtd.md\"")
        areas[name] = Area(name, _path(spec["file"]), _path(spec.get("state_dir")),
                           _path(spec.get("completions_log")), spec.get("proposal_cap"),
                           dict(spec.get("proposal_reserve", {})))
    return areas


def load(path: Path | None = None) -> tuple[dict[str, Area], str]:
    """Areas in file order, and the area captures go to when every area is showing."""
    path = path or CONFIG
    data = _read(path)
    areas = areas_from(data, path)
    if not areas:
        raise SystemExit(NO_AREAS.format(path=path))
    capture = data.get("capture", next(iter(areas)))
    return areas, capture if capture in areas else next(iter(areas))


def load_or_empty(path: Path | None = None) -> tuple[dict[str, Area], str | None]:
    """As load(), but no areas is an empty dict, for the app to open on the Files screen."""
    path = path or CONFIG
    data = _read(path)
    areas = areas_from(data, path)
    capture = data.get("capture")
    return areas, (capture if capture in areas else next(iter(areas), None))


DEFAULT_CONTEXTS = ["@calls", "@computer", "@office", "@home", "@errands", "@agendas"]


def contexts(path: Path | None = None) -> list[str]:
    """The contexts clarify offers, in order; contexts already in use are added after them."""
    data = _read(path or CONFIG)
    return [c if c.startswith("@") else f"@{c}" for c in data.get("contexts", DEFAULT_CONTEXTS)]


def single(file: Path, name: str = "main") -> dict[str, Area]:
    """One ad-hoc area for --file and tests."""
    return {name: Area(name, Path(file))}


def _write(path: Path, lines: list[str]) -> None:
    tmp = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)


def save_contexts(names: list[str], path: Path = CONFIG) -> None:
    """Write the contexts list back, rewriting only the `contexts = [...]` line (added at the
    top when missing), so comments and the areas stay as you wrote them."""
    line = "contexts = [" + ", ".join(f'"{n}"' for n in names) + "]"
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()
    at = next((n for n, ln in enumerate(lines) if ln.split("=")[0].strip() == "contexts" and "[" in ln), None)
    if at is None:
        lines.insert(0, line)
    else:
        end = at
        while "]" not in lines[end].split("#")[0] and end + 1 < len(lines):  # a list spread over lines
            end += 1
        lines[at:end + 1] = [line]
    _write(path, lines)


# Areas in and out of the config: the Files screen and `gtd init`. Same line surgery as
# save_contexts, so your comments and the other areas stay as you wrote them.

NEW_LINES = [
    "# gtd config. One [areas.NAME] per task file; the app shows them side by side.",
    '# capture = "work"       # where captures land when every area is showing (default: the first)',
    '# contexts = ["@calls", "@computer", "@home"]     # offered in clarify, in order',
]
AREA_LINES = [
    '# state_dir = "~/notes/.gtd"           # proposal counts and logs, for gtd propose and gtd expire',
    '# completions_log = "~/notes/done.md"  # a table of ticks, appended to',
    '# proposal_cap = 5                     # automated proposals per day',
]


def area_name(text: str) -> str | None:
    """"Work notes" → "work-notes": lower-case, a TOML bare key."""
    words = re.sub(r"[^A-Za-z0-9_-]+", " ", text).lower().split()
    return "-".join(words) or None


def portable(file: Path) -> str:
    """The path as written to the config: under the home folder as ~/…, else absolute."""
    file = Path(file).expanduser().absolute()
    try:
        return "~/" + file.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(file)


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else list(NEW_LINES)


def _block(lines: list[str], name: str) -> tuple[int, int] | None:
    """[start, end) of the [areas.name] table, up to the next table header."""
    start = next((n for n, ln in enumerate(lines) if ln.split("#")[0].strip() == f"[areas.{name}]"), None)
    if start is None:
        return None
    end = next((n for n in range(start + 1, len(lines)) if lines[n].split("#")[0].strip().startswith("[")), len(lines))
    return start, end


def _commit(path: Path, lines: list[str]) -> None:
    try:
        tomllib.loads("\n".join(lines) + "\n")
    except tomllib.TOMLDecodeError as e:   # never leave a config the app can't read
        raise ValueError(f"That would break {path}: {e}") from None
    _write(path, lines)


def _capture_line(lines: list[str]) -> int | None:
    return next((n for n, ln in enumerate(lines) if ln.split("#")[0].split("=")[0].strip() == "capture"), None)


def add_area(name: str, file: Path, path: Path = CONFIG) -> None:
    """Append [areas.name] pointing at `file`. ValueError if the name is taken."""
    lines = _lines(path)
    if _block(lines, name):
        raise ValueError(f"{name} is already an area in {path}")
    _commit(path, lines + ["", f"[areas.{name}]", f'file = "{portable(file)}"'] + AREA_LINES)


def remove_area(name: str, path: Path = CONFIG) -> None:
    """Take [areas.name] out of the config; the file itself stays."""
    lines = _lines(path)
    if not (block := _block(lines, name)):
        raise ValueError(f"{name} is not an area in {path}")
    start, end = block
    while start > 0 and not lines[start - 1].strip():
        start -= 1
    del lines[start:end]
    if (at := _capture_line(lines)) is not None and lines[at].split("#")[0].split("=", 1)[1].strip().strip('"') == name:
        del lines[at]
    _commit(path, lines)


def rename_area(old: str, new: str, path: Path = CONFIG) -> None:
    lines = _lines(path)
    if not (block := _block(lines, old)):
        raise ValueError(f"{old} is not an area in {path}")
    if _block(lines, new):
        raise ValueError(f"{new} is already an area in {path}")
    lines[block[0]] = f"[areas.{new}]"
    if (at := _capture_line(lines)) is not None and lines[at].split("#")[0].split("=", 1)[1].strip().strip('"') == old:
        lines[at] = f'capture = "{new}"'
    _commit(path, lines)


def set_area_file(name: str, file: Path, path: Path = CONFIG) -> None:
    lines = _lines(path)
    if not (block := _block(lines, name)):
        raise ValueError(f"{name} is not an area in {path}")
    start, end = block
    line = f'file = "{portable(file)}"'
    at = next((n for n in range(start + 1, end) if lines[n].split("#")[0].split("=")[0].strip() == "file"), None)
    if at is None:
        lines.insert(start + 1, line)
    else:
        lines[at] = line
    _commit(path, lines)
