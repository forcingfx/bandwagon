"""The picker `gtd find` opens in a terminal, and `gtd done` when its words match several
open items: a search box over the open items in every area, then the matching set to work
through. Words match the text or the tags in any order, so "+payments", "@computer" or a
person's name each pull a set. In the box you type; Enter or Tab moves to the list, where
Space ticks and s stars, as in the app, and / or Tab goes back to the box. Esc quits.
Runs inline under the prompt and stays open, so a whole set can be worked in one go."""
from __future__ import annotations

from datetime import date

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import cli
from .app import AREA_COLOURS, COUNT_COLOUR, LIST_ORDER, PALETTE_WIDTH, STAR_COLOUR, highlight_words, hint_content
from .model import task_matches

MAX_ROWS = 12
BOX_HINT = "Tab list · Esc quit"
LIST_HINT = "Space tick · s star · / search · Esc quit"


class SetList(OptionList):
    BINDINGS = [Binding("space", "app.act('tick')", show=False), Binding("s", "app.act('star')", show=False),
                Binding("slash", "app.to_box", show=False)]


class PickApp(App):
    """Returns the list of changes made, one line each, for the command to print."""

    CSS = """
    Screen { height: auto; }
    #pick-input { border: none; height: 1; padding: 0 1; background: $boost; }
    #pick-results { height: auto; max-height: 14; border: none; margin-top: 1; }
    #pick-status { color: $text-muted; }
    """
    BINDINGS = [Binding("escape", "quit_pick", "Quit", priority=True),
                Binding("tab", "toggle", show=False, priority=True)]

    def __init__(self, areas, query: str, today: date) -> None:
        super().__init__()
        self.areas, self.query_text, self.today = areas, query, today
        self.shown: list = []
        self.changes: list[str] = []

    def compose(self) -> ComposeResult:
        yield Input(value=self.query_text, placeholder="Words, +project, @context or a person…", id="pick-input")
        yield SetList(id="pick-results")
        yield Static(id="pick-status")

    def on_mount(self) -> None:
        self.fill()
        if self.query_text.strip() and self.shown:
            self.to_list()
        else:
            self.action_to_box()

    def rows(self) -> list:
        order = {s: n for n, s in enumerate(LIST_ORDER)}
        found = [r for r in cli.open_rows(self.areas) if task_matches(self.query_text, r[2])]
        return sorted(found, key=lambda r: order[r[1]])

    def fill(self, keep: int | None = None) -> None:
        found = self.rows()
        self.shown = found[:MAX_ROWS]
        options = []
        for n, (area, section, item) in enumerate(self.shown):
            right = Content.assemble((section.value.rjust(13), "dim"), " ", ("▌", AREA_COLOURS.get(area, "dim")))
            left = Content.styled("★ ", STAR_COLOUR) if item.star else Content("  ")
            left += highlight_words(item.text, self.query_text)
            room = PALETTE_WIDTH - right.cell_length - 2
            if left.cell_length > room:
                left = left.truncate(room - 1) + Content("…")
            options.append(Option(left + Content(" " * (PALETTE_WIDTH - left.cell_length - right.cell_length)) + right,
                                  id=str(n)))
        widget = self.query_one(SetList)
        widget.clear_options()
        widget.add_options(options or [Option(Content.styled("Nothing matches", "dim italic"), disabled=True)])
        widget.highlighted = min(keep or 0, len(options) - 1) if options else None
        more = f" · {len(found) - MAX_ROWS} more, narrow the words" if len(found) > MAX_ROWS else ""
        self.count = f"{len(found)} open{more}"
        self.status()

    def status(self, said: str = "") -> None:
        hints = LIST_HINT if self.focused is self.query_one(SetList) else BOX_HINT
        lead = Content.styled(said, COUNT_COLOUR) if said else Content.styled(self.count, "dim")
        room = PALETTE_WIDTH - len(hints) - 3
        if lead.cell_length > room:
            lead = lead.truncate(room - 1) + Content("…")
        line = lead + Content(" " * (PALETTE_WIDTH - lead.cell_length - len(hints))) + hint_content(hints)
        self.query_one("#pick-status", Static).update(line)

    @on(Input.Changed, "#pick-input")
    def typed(self, event: Input.Changed) -> None:
        self.query_text = event.value
        self.fill()

    @on(Input.Submitted, "#pick-input")
    def submitted(self, event: Input.Submitted) -> None:
        if self.shown:
            self.to_list()

    def to_list(self) -> None:
        self.query_one(SetList).focus()
        self.status()

    def action_to_box(self) -> None:
        self.query_one("#pick-input", Input).focus()
        self.status()

    def action_toggle(self) -> None:
        if self.focused is self.query_one(SetList):
            self.action_to_box()
        elif self.shown:
            self.to_list()

    def action_act(self, what: str) -> None:
        widget = self.query_one(SetList)
        if widget.highlighted is None or not self.shown:
            return
        at = widget.highlighted
        do = cli.tick_item if what == "tick" else cli.star_item
        said = do(self.areas, *self.shown[at], self.today)
        self.changes.append(said)
        self.fill(keep=at)
        self.status(said)
        if not self.shown:
            self.action_to_box()

    def action_quit_pick(self) -> None:
        self.exit(self.changes)


def pick(areas, query: str, today: date) -> list[str]:
    return PickApp(areas, query, today).run(inline=True) or []
