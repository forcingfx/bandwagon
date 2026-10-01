"""The date picker: Due and Start fields over a live list of suggestions (see dates.py).

DatePicker is a widget, so the clarify card can show it inline; DateScreen wraps it in a
pop-up for the `d` key. Enter takes the highlighted suggestion: on Due, "no date" finishes
(one key to skip), a date moves on to Start; on Start it finishes. Tab switches field,
↑/↓ move through suggestions.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import dates

VALUE_COLOUR = "#73d0ff"


class DatePicker(Vertical):
    BINDINGS = [Binding("tab", "switch", "Switch", priority=True),
                Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False)]
    WIDTH = 60  # characters in a suggestion row: phrase on the left, date on the right

    class Done(Message):
        def __init__(self, due: date | None, start: date | None) -> None:
            super().__init__()
            self.due, self.start = due, start

    def __init__(self, today: date, due: date | None = None, start: date | None = None,
                 state: Path | None = None) -> None:
        super().__init__(classes="datepicker")
        self.today, self.state = today, state or dates.STATE
        self.values = {"due": due, "start": start}
        self.field = "due"
        self.rows: list[dates.Suggestion] = []

    def compose(self) -> ComposeResult:
        for field in ("due", "start"):
            with Horizontal(classes="datefield"):
                yield Static(id=f"{field}-label", markup=False)
                yield Input(value=str(self.values[field] or ""), placeholder="Try: 3 days, fri, dec 5", id=field)
        yield OptionList(id="suggestions")

    def on_mount(self) -> None:
        self.query_one("#due", Input).focus()
        self.refresh_rows()

    def refresh_rows(self) -> None:
        text = self.query_one(f"#{self.field}", Input).value
        self.rows = dates.suggest(text, self.today, field=self.field, due=self.values["due"],
                                  last=dates.last_used(self.field, self.state))
        options = self.query_one("#suggestions", OptionList)
        options.clear_options()
        if self.rows:
            options.add_options([Option(self.row(s)) for s in self.rows])
            options.highlighted = 0
        else:
            options.add_option(Option(Content.styled("no date matches", "dim italic"), disabled=True))
        if text.strip():
            self.values[self.field] = self.rows[0].day if self.rows else self.values[self.field]
        else:
            self.values[self.field] = None
        self.draw_labels()

    def row(self, s: dates.Suggestion) -> Content:
        right = dates.fmt(s.day) if s.day else ""
        return Content(f"{s.label:<{self.WIDTH - len(right)}}{right}")

    def draw_labels(self) -> None:
        for field in ("due", "start"):
            value = self.values[field]
            here = "▸ " if field == self.field else "  "
            self.query_one(f"#{field}-label", Static).update(Content.assemble(
                (f"{here}{field.title():<6}", "bold" if field == self.field else "dim"),
                ((dates.fmt(value) if value else "no date").ljust(13), VALUE_COLOUR if value else "dim")))

    @on(Input.Changed)
    def typed(self, event: Input.Changed) -> None:
        if event.input.id == self.field:
            self.refresh_rows()

    def action_move(self, step: int) -> None:
        options = self.query_one("#suggestions", OptionList)
        options.action_cursor_down() if step > 0 else options.action_cursor_up()
        if options.highlighted is not None and options.highlighted < len(self.rows):
            self.values[self.field] = self.rows[options.highlighted].day
            self.draw_labels()

    def action_switch(self) -> None:
        self.field = "start" if self.field == "due" else "due"
        self.query_one(f"#{self.field}", Input).focus()
        self.refresh_rows() if self.query_one(f"#{self.field}", Input).value.strip() else self.show_rows()

    def show_rows(self) -> None:
        """Suggestions for an untouched field, keeping its current value."""
        keep = self.values[self.field]
        self.refresh_rows()
        self.values[self.field] = keep
        self.draw_labels()

    def take(self) -> None:
        options = self.query_one("#suggestions", OptionList)
        if options.highlighted is not None and options.highlighted < len(self.rows):
            self.values[self.field] = self.rows[options.highlighted].day
        elif self.rows == [] and self.query_one(f"#{self.field}", Input).value.strip():
            return self.notify("No date matches that; try 3 days, fri or dec 5.", severity="warning")
        if self.field == "due" and self.values["due"] is not None:
            self.field = "start"
            self.query_one("#start", Input).focus()
            return self.show_rows()
        for field, value in self.values.items():
            if value:
                dates.remember(field, value, self.state)
        self.post_message(self.Done(self.values["due"], self.values["start"]))

    @on(Input.Submitted)
    def entered(self, event: Input.Submitted) -> None:
        event.stop()
        self.take()

    @on(OptionList.OptionSelected)
    def clicked(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.take()


class DateScreen(ModalScreen):
    """The date picker as a pop-up (the `d` key). Dismisses with (due, start) or "back"."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, today: date, due: date | None = None, start: date | None = None,
                 header: Content | None = None, title: str = "When?", state: Path | None = None) -> None:
        super().__init__()
        self.today, self.due, self.start, self.header, self.title, self.state = today, due, start, header, title, state

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            if self.header:
                yield Static(self.header, classes="stepbar")
            yield Static(self.title, classes="question", markup=False)
            yield DatePicker(self.today, self.due, self.start, self.state)
            yield Static("↑↓ pick · Enter take · Tab due/start · Esc back", classes="hint", markup=False)

    @on(DatePicker.Done)
    def done(self, event: DatePicker.Done) -> None:
        self.dismiss((event.due, event.start))

    def action_back(self) -> None:
        self.dismiss("back")
