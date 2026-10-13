"""Which task files the app and CLI work on: ~/.config/gtd/config.toml (or $GTD_CONFIG).

Each [areas.NAME] names one markdown file; the app shows the areas side by side. The
format is EXAMPLE below, which is also what the Files screen and `gtd init` write.
"""
from __future__ import annotations

import os
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


def load(path: Path = CONFIG) -> tuple[dict[str, Area], str]:
    """Areas in file order, and the area captures go to when every area is showing."""
    data = _read(path)
    areas = areas_from(data, path)
    if not areas:
        raise SystemExit(NO_AREAS.format(path=path))
    capture = data.get("capture", next(iter(areas)))
    return areas, capture if capture in areas else next(iter(areas))


def load_or_empty(path: Path = CONFIG) -> tuple[dict[str, Area], str | None]:
    """As load(), but no areas is an empty dict, for the app to open on the Files screen."""
    data = _read(path)
    areas = areas_from(data, path)
    capture = data.get("capture")
    return areas, (capture if capture in areas else next(iter(areas), None))


DEFAULT_CONTEXTS = ["@calls", "@computer", "@office", "@home", "@errands", "@agendas"]


def contexts(path: Path = CONFIG) -> list[str]:
    """The contexts clarify offers, in order; contexts already in use are added after them."""
    data = _read(path)
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
