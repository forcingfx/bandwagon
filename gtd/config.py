"""Which gtd.md files the app and CLI work on: ~/.config/gtd/config.toml.

    capture = "work"                 # where captures land when every area is showing
    contexts = ["@calls", "@computer", "@office", "@home", "@errands", "@agendas"]   # offered in clarify, in order

    [areas.work]
    file = "~/mindpalace/gtd.md"
    state_dir = "~/mindpalace/system/state"    # proposal counts, overflow, expired log
    proposal_cap = 5                           # automated proposals per day; omit for no cap
    proposal_reserve = { process = 3 }         # slots of the cap kept for one caller (propose --caller)

    [areas.home]
    file = "~/homepalace/gtd.md"
    completions_log = "~/homepalace/system/logs/completions.md"   # recurring and one-off ticks
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG = Path(os.environ.get("GTD_CONFIG", "~/.config/gtd/config.toml")).expanduser()


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


def load(path: Path = CONFIG) -> tuple[dict[str, Area], str]:
    """Areas in file order, and the area captures go to when every area is showing."""
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    areas = {
        name: Area(name, _path(spec["file"]), _path(spec.get("state_dir")),
                   _path(spec.get("completions_log")), spec.get("proposal_cap"),
                   dict(spec.get("proposal_reserve", {})))
        for name, spec in data.get("areas", {}).items()
    }
    if not areas:
        raise SystemExit(f"No areas configured in {path}; see gtd/config.py for the format.")
    capture = data.get("capture", next(iter(areas)))
    return areas, capture if capture in areas else next(iter(areas))


DEFAULT_CONTEXTS = ["@calls", "@computer", "@office", "@home", "@errands", "@agendas"]


def contexts(path: Path = CONFIG) -> list[str]:
    """The contexts clarify offers, in order; contexts already in use are added after them."""
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    return [c if c.startswith("@") else f"@{c}" for c in data.get("contexts", DEFAULT_CONTEXTS)]


def single(file: Path, name: str = "main") -> dict[str, Area]:
    """One ad-hoc area for --file and tests."""
    return {name: Area(name, Path(file))}


def save_contexts(names: list[str], path: Path = CONFIG) -> None:
    """Write the contexts list back, rewriting only the `contexts = [...]` line (added at the
    top when missing), so comments and the areas stay as Arjun wrote them."""
    line = "contexts = [" + ", ".join(f'"{n}"' for n in names) + "]"
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()
    at = next((n for n, l in enumerate(lines) if l.split("=")[0].strip() == "contexts" and "[" in l), None)
    if at is None:
        lines.insert(0, line)
    else:
        end = at
        while "]" not in lines[end].split("#")[0] and end + 1 < len(lines):  # a list spread over lines
            end += 1
        lines[at:end + 1] = [line]
    tmp = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)
