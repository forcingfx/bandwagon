"""Textual TUI for the five GTD steps: capture, clarify, organize, reflect, engage."""
from __future__ import annotations

import json
import subprocess
from datetime import date
from functools import partial
from pathlib import Path
from urllib.parse import quote

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import CommandPalette, DiscoveryHit, Hit, Hits, Provider
from textual.content import Content
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.style import Style
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import config, dates, model, store
from .model import Section

LIST_ORDER = [Section.IN, Section.NEXT, Section.PROJECTS, Section.WAITING, Section.SOMEDAY, Section.DONE]

# The weekly review (ch. 8) as a tour of the real lists: Get Clear, Get Current, Get Creative.
REVIEW_STEPS = [
    (Section.IN, "Get Clear · capture what's on your mind (a), then clarify In to empty (c)"),
    (Section.NEXT, "Get Current · Next Actions: tick what's done, delete what's dead"),
    (Section.WAITING, "Get Current · Waiting For: chase or tick what arrived; scan your calendar, capture with a"),
    (Section.PROJECTS, "Get Current · Projects: every one needs a next action; ⏎ adds one to the highlighted"),
    (Section.SOMEDAY, "Get Creative · Someday/Maybe: ⏎ turns one into a project, ⌫ drops it, a adds new ideas"),
]
REVIEW_NAMES = ["Get Clear", "Next Actions", "Waiting For", "Projects", "Someday"]
COUNT_COLOUR = "#73d0ff"  # the kitty palette's bright blue
LINK_COLOUR = "#95e6cb"   # the kitty palette's bright cyan
CHIP_MAX = 32             # chips are names; anything longer is cut with …
TAB_NAMES = {Section.IN: "In", Section.NEXT: "Next", Section.PROJECTS: "Projects",
             Section.WAITING: "Waiting", Section.SOMEDAY: "Someday", Section.DONE: "Done"}
# The key line follows the list you're on (lazygit-style); the tail is the same everywhere.
LIST_KEYS = {
    Section.IN: "a capture · ⏎ clarify · space tick · t dates · o open · x mark · ⌫ delete",
    Section.NEXT: "space tick · ⏎ edit · t dates · o open · f context · x mark · ⌫ delete",
    Section.PROJECTS: "⏎ add next action · space tick · x mark · ⌫ delete",
    Section.WAITING: "space received · ⏎ edit · o open · x mark · ⌫ delete",
    Section.SOMEDAY: "⏎ clarify · x mark · ⌫ delete · a capture",
    Section.DONE: "space reopen · x mark · ⌫ delete",
}
KEYS_TAIL = "z undo · ← → lists · s later · w review · / search · ? keys"
# Every key, grouped, for the ? screen.
HELP = [
    ("Move", [("↑ ↓ or j k", "move through the list"), ("Home End", "first / last item"),
              ("PgUp PgDn", "a page up / down"), ("← → u i Tab ⇧Tab", "previous / next list (or click a tab)"),
              ("⇧↑ ⇧↓", "select rows from here, like a file manager")]),
    ("Act on the highlighted item", [("⏎", "clarify In/Someday · next action for a project · edit"),
                                     ("Space", "tick (or click the checkbox); on a ticked row untick; on Done reopen"),
                                     ("o", "open a link in the item (a picker if it has several)"),
                                     ("x", "mark for a bulk delete"), ("⌫ Del", "delete the marked rows, or this one"),
                                     ("e", "edit"), ("m", "move to another list"),
                                     ("t", "dates: type 2, fri, dec 5 or -3 (before due) and pick a suggestion"),
                                     ("z", "undo the last change")]),
    ("The five steps", [("a", "capture (Tab: save and capture another)"), ("c", "clarify In"),
                        ("w", "weekly review (n next step, Esc stop)"), ("f", "show one context in Next Actions"),
                        ("s", "show or hide tasks whose start date is still ahead")]),
    ("Everything else", [("/", "search tasks and commands"), ("?", "this screen"), ("Esc", "clear marks / stop review"),
                         ("q", "quit")]),
]


def focused_workspace() -> str | None:
    """The Sway workspace Arjun is on, or None outside Sway."""
    try:
        out = subprocess.run(["swaymsg", "-t", "get_workspaces"], capture_output=True, text=True, timeout=2)
        return next((w["name"] for w in json.loads(out.stdout) if w.get("focused")), None)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def follow_script(workspace: str | None) -> str | None:
    """A shell loop that waits (up to ~5 s, in case Obsidian is still starting) for the
    Obsidian window, then moves it to `workspace` and focuses it. Sway assigns Obsidian
    to workspace 3 on launch; opening a note from the app should bring it here instead."""
    if not workspace:
        return None
    ws = workspace.replace("'", "")
    return ("for i in $(seq 20); do "
            "if swaymsg -t get_tree | grep -q '\"app_id\": \"obsidian\"'; then "
            f"swaymsg '[app_id=\"obsidian\"] move container to workspace \"{ws}\", focus' >/dev/null; break; fi; "
            "sleep 0.25; done")


STEP_NAMES = {"what": "What is it", "outcome": "Outcome", "action": "Action", "project": "Project",
              "how": "How", "context": "Context", "context_other": "Context", "who": "Who", "dates": "Dates"}
CHOICES = {1: "One action", 2: "A project", 3: "Someday/Maybe", 4: "Trash", 5: "Reference"}


def predict_path(a: dict, start: str, has_projects: bool) -> list[str]:
    """The clarify steps the answers so far lead to; unanswered choices are assumed to take
    the default branch (one action, deferred)."""
    if start == "what":
        path = ["what"]
        if a.get("actionable") is False:
            return path
        if a.get("kind") == "project":
            path += ["outcome", "action"]
        else:
            path += ["action"] + (["project"] if has_projects and not a.get("existing") else [])
    elif start == "outcome":
        path = ["outcome", "action"]
    else:
        path = ["action"]
    path.append("how")
    if a.get("how") == "now":
        return path
    return path + (["who", "dates"] if a.get("how") == "delegate" else ["context", "dates"])


def step_bar(path: list[str], current: str, progress: tuple[int, int] | None) -> Content:
    """Item 3 of 12 · What is it ✓ › Action › …: done steps ticked, the current one bold."""
    current = "context" if current == "context_other" else current
    here = path.index(current) if current in path else 0
    parts: list = [(f"Item {progress[0]} of {progress[1]} · ", "dim")] if progress else []
    for n, step in enumerate(path):
        if n:
            parts.append((" › ", "dim"))
        name = STEP_NAMES[step]
        parts.append((f"{name} ✓", "dim") if n < here else (name, "bold") if n == here else (name, "dim"))
    return Content.assemble(*parts)


def open_externally(url: str) -> None:
    """Hand the link to the desktop (Obsidian, browser, Granola). An Obsidian note is pulled
    to the current Sway workspace. If opening fails, the link goes to the clipboard."""
    workspace = focused_workspace() if url.startswith("obsidian://") else None
    try:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        subprocess.run(["wl-copy", url], check=False)
        return
    if script := follow_script(workspace):
        subprocess.Popen(["sh", "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


def in_order(items: list) -> tuple[list, list]:
    """Arjun's own items, then automated proposals (items with a source), each in file order."""
    return [i for i in items if not i.src], [i for i in items if i.src]


def source_name(src: str) -> str:
    """A short name for where a proposal came from: granola, gmail, slack, confluence, process."""
    if "://" not in src:
        return src
    scheme, rest = src.split("://", 1)
    host = rest.split("/", 1)[0]
    for needle, name in (("mail.google", "gmail"), ("slack", "slack"), ("atlassian", "confluence"),
                         ("docs.google", "gdoc")):
        if needle in host:
            return name
    return scheme if scheme not in ("http", "https") else host


class Tabs(Static):
    """The list tabs; a click on one opens that list."""

    async def _on_click(self, event) -> None:
        if (index := event.style.meta.get("gtd_tab")) is not None:
            await self.app.action_go(index)


class ItemList(OptionList, inherit_bindings=False):
    async def _on_click(self, event) -> None:
        """A click on a link label opens the link; anywhere else it highlights the row."""
        event.prevent_default()  # OptionList's own click handler would act on a row that a tick just redrew
        if url := event.style.meta.get("gtd_link"):
            event.stop()
            self.app.open_link_url(url)
            return
        clicked = event.style.meta.get("option")
        if clicked is not None and not self._options[clicked].disabled:
            self.highlighted = clicked  # highlight only: Enter, not a click, acts on the item
            if event.style.meta.get("gtd_tick"):  # a click on the checkbox ticks (or unticks)
                await self.app.action_complete()

    def action_cursor_down(self) -> None:
        self.app.anchor = None  # a plain move ends a shift-selection
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        self.app.anchor = None
        super().action_cursor_up()

    """The item pane: one widget that draws every row, so a redraw stays fast on long
    lists. Enter is left to the app (it acts on the highlighted item), and Tab /
    Shift+Tab, which Textual uses to move focus, switch lists instead."""

    BINDINGS = [Binding("up", "cursor_up", show=False), Binding("down", "cursor_down", show=False),
                Binding("left", "app.step_list(-1)", show=False), Binding("right", "app.step_list(1)", show=False),
                Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False),
                Binding("u", "app.step_list(-1)", show=False), Binding("i", "app.step_list(1)", show=False),
                Binding("shift+up", "app.extend(-1)", show=False), Binding("shift+down", "app.extend(1)", show=False),
                Binding("home", "first", show=False), Binding("end", "last", show=False),
                Binding("pageup", "page_up", show=False), Binding("pagedown", "page_down", show=False),
                Binding("tab", "app.step_list(1)", show=False),
                Binding("shift+tab", "app.step_list(-1)", show=False)]


class GTDCommands(Provider):
    """The / box: search tasks in every list and every command by name, each command
    showing its direct key. An empty box lists the commands."""

    @staticmethod
    def row(name, key: str) -> Content:
        """The command, then its direct key dimmed on the same line."""
        name = name if isinstance(name, Content) else Content(name)
        return name + Content.styled(f"   {key}", "dim") if key else name

    async def discover(self) -> Hits:
        for name, key, action in self.app.palette_entries():
            yield DiscoveryHit(self.row(name, key), partial(self.app.run_action, action), text=name)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, key, action in self.app.palette_entries():
            if (score := matcher.match(name)) > 0:
                yield Hit(score, self.row(matcher.highlight(name), key), partial(self.app.run_action, action),
                          text=name)
        for sec in LIST_ORDER:
            for item in self.app.doc.items(sec):
                if item.raw is None and (score := matcher.match(item.text)) > 0:
                    yield Hit(score, matcher.highlight(item.text) + Content.styled(f"   {sec.value}", "dim"),
                              partial(self.app.jump_to, sec, item.line()), text=item.text)


class HelpScreen(ModalScreen[None]):
    """? : every key on one screen. Esc, ? or q closes it."""

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("question_mark", "dismiss", "Close"),
                Binding("q", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        parts: list = []
        for group, keys in HELP:
            parts.append((f"{group}\n", "bold"))
            parts += [part for key, what in keys for part in ((f"  {key:<11}", COUNT_COLOUR), f"{what}\n")]
            parts.append("\n")
        with Vertical(classes="dialog"):
            with VerticalScroll():
                yield Static(Content.assemble(*parts[:-1]), markup=False)
            yield Static("↑ ↓ scroll · Esc  close", classes="hint", markup=False)


class AskScreen(ModalScreen[int | None]):
    """A question with numbered answers. 1-9 picks, 0 or Esc goes back one screen."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("0", "back", "Back")]

    def __init__(self, question: str, labels: list[str], header: Content | None = None) -> None:
        super().__init__()
        self.question, self.labels, self.header = question, labels, header

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            if self.header:
                yield Static(self.header, classes="stepbar")
            yield Static(self.question, classes="question", markup=False)
            yield OptionList(*(Option(Content(f"{n}  {label}"), id=str(n))
                               for n, label in enumerate(self.labels, 1)))
            yield Static("0 / Esc  back", classes="hint", markup=False)

    def on_key(self, event) -> None:
        if event.character and event.character.isdigit() and 1 <= int(event.character) <= len(self.labels):
            event.stop()
            self.dismiss(int(event.character))

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(int(event.option.id))

    def action_back(self) -> None:
        self.dismiss(None)


class ContextScreen(ModalScreen[list | None]):
    """Pick contexts. A number picks that one and moves on (the fast path); Space toggles
    the highlighted one for picking several, and Enter confirms. Returns the chosen
    contexts, ["other"] for "Other…", or None to go back."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("0", "back", "Back"),
                Binding("space", "toggle", "Toggle", priority=True)]

    def __init__(self, question: str, contexts: list[str], chosen: list[str] | None = None,
                 header: Content | None = None) -> None:
        super().__init__()
        self.question, self.contexts, self.header = question, contexts, header
        self.chosen: list[str] = [c for c in (chosen or []) if c in contexts]

    def label(self, n: int) -> Content:
        if n >= len(self.contexts):
            return Content(f"{n + 1}  Other…")
        c = self.contexts[n]
        return Content(f"{n + 1}  {'[x]' if c in self.chosen else '[ ]'} {c}")

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            if self.header:
                yield Static(self.header, classes="stepbar")
            yield Static(self.question, classes="question", markup=False)
            yield OptionList(*(Option(self.label(n), id=str(n)) for n in range(len(self.contexts) + 1)))
            yield Static("number  pick one · Space  toggle · Enter  confirm · Esc  back", classes="hint", markup=False)

    def on_key(self, event) -> None:
        if event.character and event.character.isdigit() and 1 <= int(event.character) <= len(self.contexts) + 1:
            event.stop()
            n = int(event.character) - 1
            self.dismiss(["other"] if n == len(self.contexts) else [self.contexts[n]])

    def action_toggle(self) -> None:
        options = self.query_one(OptionList)
        n = options.highlighted
        if n is None or n >= len(self.contexts):
            return
        c = self.contexts[n]
        self.chosen = [x for x in self.chosen if x != c] if c in self.chosen else self.chosen + [c]
        options.replace_option_prompt_at_index(n, self.label(n))

    @on(OptionList.OptionSelected)
    def chosen_by_enter(self, event: OptionList.OptionSelected) -> None:
        n = int(event.option.id)
        if self.chosen:
            self.dismiss([c for c in self.contexts if c in self.chosen])
        else:
            self.dismiss(["other"] if n == len(self.contexts) else [self.contexts[n]])

    def action_back(self) -> None:
        self.dismiss(None)


class DateScreen(ModalScreen):
    """Due and start dates, Superhuman-style: type a few characters and pick from live
    suggestions (see dates.py). Enter takes the highlighted suggestion: on Due, "no date"
    finishes the screen (one key to skip), a date moves on to Start; on Start it finishes.
    Tab switches field, ↑/↓ move through suggestions, Esc goes back. Dismisses with
    (due, start) or "back"."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("tab", "switch", "Switch", priority=True),
                Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False)]
    WIDTH = 60  # characters in a suggestion row: phrase on the left, date on the right

    def __init__(self, today: date, due: date | None = None, start: date | None = None,
                 header: Content | None = None, title: str = "When?", state: Path | None = None) -> None:
        super().__init__()
        self.today, self.header, self.title, self.state = today, header, title, state or dates.STATE
        self.values = {"due": due, "start": start}
        self.field = "due"
        self.rows: list[dates.Suggestion] = []

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            if self.header:
                yield Static(self.header, classes="stepbar")
            yield Static(self.title, classes="question", markup=False)
            for field in ("due", "start"):
                with Horizontal(classes="datefield"):
                    yield Static(id=f"{field}-label", markup=False)
                    yield Input(value=str(self.values[field] or ""), placeholder="Try: 3 days, fri, dec 5", id=field)
            yield OptionList(id="suggestions")
            yield Static("↑↓ pick · Enter take · Tab due/start · Esc back", classes="hint", markup=False)

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
                ((dates.fmt(value) if value else "no date").ljust(13), COUNT_COLOUR if value else "dim")))

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
        self.finish()

    def finish(self) -> None:
        for field, value in self.values.items():
            if value:
                dates.remember(field, value, self.state)
        self.dismiss((self.values["due"], self.values["start"]))

    @on(Input.Submitted)
    def entered(self, event: Input.Submitted) -> None:
        event.stop()
        self.take()

    @on(OptionList.OptionSelected)
    def clicked(self, event: OptionList.OptionSelected) -> None:
        self.take()

    def action_back(self) -> None:
        self.dismiss("back")


class TextScreen(ModalScreen[str | None]):
    """A one-line text prompt. Enter submits, Esc goes back one screen."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, prompt: str, value: str = "", header: Content | None = None) -> None:
        super().__init__()
        self.prompt, self.value, self.header = prompt, value, header

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            if self.header:
                yield Static(self.header, classes="stepbar")
            yield Static(self.prompt, classes="question", markup=False)
            yield Input(value=self.value)
            yield Static("Enter  confirm · Esc  back", classes="hint", markup=False)

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_back(self) -> None:
        self.dismiss(None)


class CaptureInput(Input):
    """Handles Tab inside the input's own key queue, so a fast
    typist's next keystrokes can never overtake the save."""

    async def _on_key(self, event) -> None:
        if event.key == "tab":
            event.stop()
            event.prevent_default()
            await self.screen.save_and_next()
        else:
            await super()._on_key(event)


class CaptureScreen(ModalScreen[list]):
    """One capture window for a whole batch.

    Enter saves and closes. Tab saves and clears for the next item. Esc closes.
    Dismisses with the items captured, in order.
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    PROMPT = "Capture: what has your attention?"

    def __init__(self, save) -> None:
        super().__init__()
        self.save, self.captured = save, []

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.PROMPT, id="capture-prompt", classes="question", markup=False)
            yield CaptureInput()
            yield Static("Enter  save · Tab  save + next · Esc  close",
                         classes="hint", markup=False)

    async def save_and_next(self) -> None:
        box = self.query_one(CaptureInput)
        if text := box.value.strip():
            self.captured.append(await self.save(text))
            box.value = ""
            self.query_one("#capture-prompt", Static).update(f"{self.PROMPT} · {len(self.captured)} captured")

    @on(Input.Submitted)
    async def submitted(self) -> None:
        await self.save_and_next()
        self.dismiss(self.captured)

    def action_close(self) -> None:
        self.dismiss(self.captured)


class GTDApp(App):
    TITLE = "GTD"
    CSS = """
    #tabs { height: 1; background: $boost; }
    #items { height: 1fr; max-height: 100%; border: none; padding: 0; background: transparent;
             text-wrap: nowrap; text-overflow: ellipsis; }
    #items:focus { border: none; }
    #status { height: 1; background: $boost; padding: 0 1; }
    #keys { height: 1; padding: 0 1; color: $text-muted; }
    AskScreen, TextScreen, CaptureScreen, HelpScreen, ContextScreen, DateScreen { align: center middle; }
    .stepbar { margin-bottom: 1; }
    DateScreen #suggestions { height: auto; max-height: 7; margin-top: 1; }
    DateScreen .datefield { height: 1; }
    DateScreen .datefield Static { width: 23; }
    DateScreen .datefield Input { border: none; height: 1; padding: 0 1; background: $boost; width: 1fr; }
    HelpScreen .dialog { max-height: 95%; }
    HelpScreen VerticalScroll { height: auto; max-height: 100%; }
    .dialog { width: 72; height: auto; max-height: 80%; border: thick $primary; background: $surface; padding: 1 2; }
    .question { margin-bottom: 1; }
    .hint { margin-top: 1; color: $text-muted; }
    .dialog OptionList { height: auto; max-height: 14; }
    """
    COMMANDS = {GTDCommands}  # only our actions: no themes, screenshots or other built-ins
    BINDINGS = [
        Binding("slash", "command_palette", "Palette", show=False),  # replaces Textual's Ctrl+P
        Binding("a", "capture", "Capture"),
        Binding("c", "clarify", "Clarify"),
        Binding("w", "review", "Weekly review"),
        Binding("f", "engage", "Context"),
        Binding("space", "complete", "Tick"),
        Binding("enter", "primary", "Act on item", show=False),
        Binding("n", "review_next", "Next review step", show=False),
        Binding("escape", "escape", "Clear marks / stop review", show=False),
        Binding("x", "toggle_mark", "Mark", show=False),
        Binding("o", "open_link", "Open link", show=False),
        Binding("s", "toggle_later", "Show/hide later", show=False),
        Binding("t", "dates", "Dates", show=False),
        Binding("backspace", "delete", "Delete", show=False),
        Binding("delete", "delete", "Delete", show=False),
        Binding("question_mark", "help", "Keys", show=False),
        Binding("z", "undo", "Undo"),
        Binding("e", "edit", "Edit"),
        Binding("m", "move", "Move"),
        Binding("q", "quit", "Quit"),
    ]
    WATCH_SECONDS = 2.0  # how often to check gtd.md for edits made outside the app
    DATE_STATE = dates.STATE  # last-used dates; tests point this elsewhere

    def __init__(self, areas: dict[str, config.Area] | Path | str, today: date | None = None,
                 capture: str | None = None, contexts: list[str] | None = None) -> None:
        super().__init__()
        self.contexts = contexts or config.DEFAULT_CONTEXTS
        self.show_later = False  # tasks whose start: is after today stay hidden until toggled
        self.header: Content | None = None  # the clarify step bar while clarify runs
        self.clarify_progress: tuple[int, int] | None = None
        self.areas = areas if isinstance(areas, dict) else config.single(Path(areas))
        self.view_areas: list[str] = list(self.areas)  # areas on screen; the palette narrows it
        self.capture_area = capture if capture in self.areas else next(iter(self.areas))
        self.today = today or date.today()
        self.load()
        self.seen = self.stamps()
        self.current = Section.IN
        self.context_filter: str | None = None
        self.shown: list[model.Item] = []  # open items in display order
        self.rows: list[tuple[str, object]] = []  # per list row: ("item", Item), ("ticked", entry), ("divider", None)
        self.review_step: int | None = None  # index into REVIEW_STEPS while a review is running
        # Ticked this visit, shown below a divider: (the item now in Done, its old position).
        self.ticked: dict[Section, list[tuple[model.Item, int]]] = {}
        self.last_toast: str | None = None
        self.opener = open_externally  # replaced in tests
        self._tag_notes: dict[tuple, str | None] = {}  # (area, kind, name) -> note path, cached
        self.marked: set[tuple[str, str]] = set()  # (area, line) marked with x in the current list
        self.anchor: int | None = None  # row where a Shift+arrow selection started
        self.anchor_base: set = set()   # marks that existed before it
        # Per change: [(file, text before, text after)] for every file that change touched.
        self.undo_stack: list[list[tuple[Path, str | None, str]]] = []

    # Layout and rendering

    def compose(self) -> ComposeResult:
        yield Tabs(id="tabs", markup=False)
        yield ItemList(id="items")
        yield Static(id="status", markup=False)
        yield Static(id="keys", markup=False)

    async def on_mount(self) -> None:
        await self.render_lists()
        self.query_one("#items").focus()
        self.set_interval(self.WATCH_SECONDS, self.watch_file)

    async def render_lists(self) -> None:
        """Redraw everything, keeping the highlight where it was."""
        self.query_one("#tabs", Static).update(self.tabs_text())
        stalled = {p.project for p in model.stalled_projects(self.doc)}
        items = self.visible_items(self.current)
        if self.current is Section.NEXT and self.context_filter:
            items = [i for i in items if self.context_filter in i.contexts]
        if self.current is Section.NEXT:
            items = sorted(items, key=lambda i: i.contexts[0] if i.contexts else "~")
        own, proposals = in_order(items)
        self.shown = own + proposals
        self.marked &= {(i.area, i.line()) for i in self.shown}
        rows: list[tuple[str, object]] = [("item", i) for i in own]
        if proposals:  # automated proposals sit below Arjun's own captures
            rows += [("divider", f"proposals · {len(proposals)}")] + [("item", i) for i in proposals]
        # Resolve ticked items against the file; drop any that left Done (undo, outside edit).
        done = {x.line(): x for x in self.doc.items(Section.DONE)}
        if ticked := [(done[t.line()], pos) for t, pos in self.ticked.get(self.current, []) if t.line() in done]:
            self.ticked[self.current] = ticked
            rows += [("divider", f"ticked · {len(ticked)}")] + [("ticked", entry) for entry in ticked]
        else:
            self.ticked.pop(self.current, None)
        self.rows = rows
        view = self.query_one("#items", ItemList)
        index = view.highlighted
        view.clear_options()
        view.add_options([self.option(kind, value, stalled) for kind, value in rows])
        view.highlighted = self.nearest_row(index)
        self.update_status()

    def option(self, kind: str, value, stalled: set) -> Option:
        if kind == "divider":
            return Option(Content.styled(f"── {value} ──", "dim"), disabled=True)
        item = value[0] if kind == "ticked" else value
        return Option(self.describe(item, stalled, marked=(item.area, item.line()) in self.marked))

    def nearest_row(self, index: int | None) -> int | None:
        """The selectable row closest to `index`, looking up first (after a row left the list)."""
        if index is None:
            return None
        selectable = [n for n, (kind, _) in enumerate(self.rows) if kind != "divider"]
        before = [n for n in selectable if n <= index]
        return before[-1] if before else (selectable[0] if selectable else None)

    def row_of(self, line: str) -> int | None:
        return next((n for n, (kind, v) in enumerate(self.rows) if kind == "item" and v.line() == line), None)

    def first_row(self) -> int | None:
        return next((n for n, (kind, _) in enumerate(self.rows) if kind == "item"), None)

    def tabs_text(self) -> Content:
        """The lists as one row of tabs, the current one highlighted."""
        parts: list = []
        for n, sec in enumerate(LIST_ORDER):
            if n:
                parts.append(("│", "dim"))
            here = "reverse bold " if sec is self.current else ""
            tab = Content.assemble((f" {TAB_NAMES[sec]} ", here.strip()),
                                   (f"{len(self.visible_items(sec))} ", here + COUNT_COLOUR))
            parts.append(tab.stylize(Style.from_meta({"gtd_tab": n})))
        return Content.assemble(*parts)

    def describe(self, item: model.Item, stalled: set, marked: bool = False) -> Content:
        """One row: a mark bar, a heavy checkbox, the text, then the tags dimmed."""
        bar = ("▌", COUNT_COLOUR) if marked else " "
        area = ("  " + item.area, "dim italic") if len(self.view_areas) > 1 and item.area else ""
        if item.raw is not None:
            return Content.assemble(bar, item.line(), area)
        box = Content.styled("[✔]", "bold #4ade80") if item.done else Content.styled("[ ]", "bold")
        parts: list = [bar, box.stylize(Style.from_meta({"gtd_tick": True})), "  "]
        if self.current is Section.PROJECTS and item.project in stalled:
            parts.append(("⚠ no next action · ", "yellow"))
        # Proposals (items with a source) are dimmed so Arjun's own captures stand out; link
        # syntax is shown as its label, underlined, and o opens it.
        base = "strike dim" if item.done else "dim" if item.src else ""
        action, _ = model.split_trailing_links(item.text)
        for text, target in model.link_segments(action):  # a link inside the sentence stays in it
            parts.append(self.link_part(text, self.resolve(item, target), base) if target else (text, base))
        # Every other link comes after the action, as named chips in one fixed order:
        # the note it is about, its project, its people, its source.
        chips = self.chips(item)
        for n, (label, url) in enumerate(chips):
            parts.append(("  " if n == 0 else " · ", "dim"))
            label = label if len(label) <= CHIP_MAX else label[:CHIP_MAX - 1] + "…"
            parts.append(self.link_part(label, url, base) if url else (label, "dim"))
        tags = [t for t in (*item.contexts, item.every and f"every:{item.every}") if t]
        if tags:
            parts.append(("  " + " ".join(tags), "dim"))
        if model.starts_later(item, self.today):
            parts.append((f"  starts {item.start:%b %-d}", "dim italic"))
        if item.due and not item.done:
            late = item.due < self.today
            soon = (item.due - self.today).days <= 2
            parts.append((f"  due {item.due:%b %-d}", "bold #f28779" if late else "#ffd580" if soon else "dim"))
        dates = [t for t in (item.since and f"since:{item.since}", item.added and f"added:{item.added}",
                             item.done_on and f"done:{item.done_on}") if t]
        if dates:
            parts.append(("  " + " ".join(dates), "dim"))
        parts.append(area)
        return Content.assemble(*parts)

    def update_status(self) -> None:
        """Line 1: Allen's status, or the review step. Line 2: the keys that matter right now."""
        if self.review_step is not None:
            n = self.review_step
            bar = Content.assemble(*[part for i, name in enumerate(REVIEW_NAMES) for part in (
                ((" › ", "dim"),) if i else ()) + (((f"{name} ✓", "dim"),) if i < n else ((name, "bold"),)
                                                   if i == n else ((name, "dim"),))])
            instruction = REVIEW_STEPS[n][1].split(" · ", 1)[-1]
            self.status_text = f"Review {n + 1}/{len(REVIEW_STEPS)} · {bar.plain} · {instruction}"
            hints = f"n next step · Esc stop · {LIST_KEYS[self.current]}"
            self.keys_text = hints
            self.query_one("#status", Static).update(Content.assemble(
                (f"Review {n + 1}/{len(REVIEW_STEPS)} · ", "bold"), bar, (f" · {instruction}", "")))
            self.query_one("#keys", Static).update(hints)
            return
        elif self.marked:
            self.status_text = self.allen_status()
            hints = f"{len(self.marked)} marked · ⌫ delete marked · x unmark · Esc clear marks"
        else:
            self.status_text, hints = self.allen_status(), f"{LIST_KEYS[self.current]} · {KEYS_TAIL}"
        self.keys_text = hints
        overdue = self.doc.last_review is None or (self.today - self.doc.last_review).days > 7
        self.query_one("#status", Static).update(Content.assemble(
            (self.status_text, "bold yellow" if overdue and self.review_step is None else "bold")))
        self.query_one("#keys", Static).update(hints)

    def allen_status(self) -> str:
        """The three things Allen watches: In at zero, every project moving, the weekly review current."""
        inbox = len(self.doc.items(Section.IN))
        stuck = len(model.stalled_projects(self.doc))
        days = None if self.doc.last_review is None else (self.today - self.doc.last_review).days
        parts = [
            f"In {inbox} to clarify" if inbox else "In clear",
            f"{stuck} project{'s' if stuck != 1 else ''} without a next action" if stuck
            else "every project has a next action",
            "no weekly review yet" if days is None else "last review today" if days == 0
            else f"last review {days} day{'s' if days != 1 else ''} ago",
        ]
        if self.context_filter:
            parts.append(f"showing {self.context_filter}")
        if len(self.view_areas) < len(self.areas):
            parts.append(f"{self.view_areas[0]} only")
        if (later := self.later_count()) and not self.show_later:
            parts.append(f"{later} starting later")
        return " · ".join(parts)

    # The files: every change re-reads them, is undoable, and outside edits appear on their own

    def load(self) -> None:
        """Read every area's file and build the merged view of the visible ones."""
        self.docs = {name: store.load(area.file) for name, area in self.areas.items()}
        for name, doc in self.docs.items():
            for items in doc.sections.values():
                for item in items:
                    item.area = name
        shown = [self.docs[n] for n in self.view_areas]
        merged = model.Doc.empty()
        for doc in shown:
            for section in Section:
                merged.items(section).extend(doc.items(section))
        reviews = [d.last_review for d in shown]
        merged.last_review = None if None in reviews else min(reviews)
        merged.prune_manual = all(d.prune_manual for d in shown)
        self.doc = merged

    def stamps(self) -> dict[str, int | None]:
        def stamp(path: Path) -> int | None:
            try:
                return path.stat().st_mtime_ns
            except FileNotFoundError:
                return None
        return {name: stamp(area.file) for name, area in self.areas.items()}

    async def watch_file(self) -> None:
        """Pick up edits from Obsidian, Sync or the CLI without a reload key."""
        if (now := self.stamps()) != self.seen:
            self.seen = now
            self.load()
            await self.render_lists()

    async def change(self, fn, area: str | None = None) -> None:
        """Apply one change to one area's file (or, with no area, to every visible one),
        record it as a single undo step, then redraw."""
        await self.change_each({a: fn for a in ([area] if area else self.view_areas)})

    async def change_each(self, fns: dict) -> None:
        step = []
        for name, fn in fns.items():
            path = self.areas[name].file
            before, _ = store.mutate_with_before(path, fn, self.today)  # before is read under the lock
            step.append((path, before, path.read_text()))
        self.undo_stack = [*self.undo_stack[-99:], step]
        self.seen = self.stamps()
        self.load()
        await self.render_lists()

    async def action_undo(self) -> None:
        """Step back one change. Refuses if a file changed outside the app since,
        because restoring would silently discard that edit."""
        if not self.undo_stack:
            self.notify("Nothing to undo.")
            return
        step = self.undo_stack[-1]
        if any((path.read_text() if path.exists() else None) != after for path, _, after in step):
            self.undo_stack.clear()
            self.notify("gtd.md changed outside the app, so earlier changes can't be undone.",
                        severity="warning")
            return
        self.undo_stack.pop()
        for path, before, _ in step:
            with store.locked(path):
                if before is None:
                    path.unlink()
                else:
                    store.write_text(path, before)
        self.seen = self.stamps()
        self.load()
        await self.render_lists()
        self.notify("Undone.")

    # Navigation

    def highlighted_row(self) -> tuple[str, object]:
        index = self.query_one("#items", ItemList).highlighted
        return self.rows[index] if index is not None and index < len(self.rows) else ("none", None)

    def selected_ticked(self) -> tuple[model.Item, int] | None:
        kind, value = self.highlighted_row()
        return value if kind == "ticked" else None

    def toast(self, message: str) -> None:
        """Say what just happened and how to take it back."""
        self.last_toast = f"{message} · z to undo"
        self.notify(self.last_toast, timeout=5)

    def selected(self) -> model.Item | None:
        kind, value = self.highlighted_row()
        return value if kind == "item" else None

    async def show(self, section: Section) -> None:
        if section is not self.current:
            self.ticked.pop(self.current, None)  # leaving a list clears its ticked section and marks
            self.marked.clear()
            self.anchor = None
        self.current = section
        await self.render_lists()

    async def action_step_list(self, step: int) -> None:
        await self.action_go((LIST_ORDER.index(self.current) + step) % len(LIST_ORDER))

    async def action_go(self, index: int) -> None:
        await self.show(LIST_ORDER[index])
        view = self.query_one("#items", ItemList)
        view.focus()
        view.highlighted = self.first_row()

    # The / palette: every action by name; the rare ones live only here

    def palette_entries(self) -> list[tuple[str, str, str]]:
        """(name, direct key, action) for every command the palette offers."""
        prune = ("Prune Done: switch to automatic, after 14 days" if self.doc.prune_manual
                 else "Prune Done: switch to only on demand")
        return [
            ("Capture", "a", "capture"),
            ("Clarify", "c", "clarify"),
            ("Tick highlighted item", "space", "complete"),
            ("Undo", "z", "undo"),
            ("Weekly review", "w", "review"),
            ("Next weekly review step", "n", "review_next"),
            ("Edit highlighted item", "e", "edit"),
            ("Move highlighted item", "m", "move"),
            ("Delete marked or highlighted item", "⌫", "delete"),
            ("Mark or unmark highlighted item", "x", "toggle_mark"),
            ("Open a link in highlighted item", "o", "open_link"),
            ("Show or hide tasks that start later", "s", "toggle_later"),
            ("Set due and start dates of highlighted item", "t", "dates"),
            ("Show every key", "?", "help"),
            ("Show one context in Next Actions", "f", "engage"),
            *((f"Go to {sec.value}", "tab / ⇧tab", f"go({n})") for n, sec in enumerate(LIST_ORDER)),
            ("Clear ticked items", "", "clear_ticked"),
            ("Clear proposals from In", "", "clear_proposals"),
            *self.area_entries(),
            ("Prune Done now", "", "prune_now"),
            (prune, "", "toggle_prune"),
            ("Quit", "q", "quit"),
        ]

    def area_entries(self) -> list[tuple[str, str, str]]:
        if len(self.areas) < 2:
            return []
        entries = [(f"Show {name} only", "", f"show_area('{name}')") for name in self.areas]
        entries.append(("Show all areas", "", "show_area('')"))
        entries += [(f"Captures go to {name}", "", f"capture_to('{name}')") for name in self.areas
                    if name != self.capture_area]
        return entries

    async def action_show_area(self, name: str) -> None:
        self.view_areas = [name] if name else list(self.areas)
        self.marked.clear()
        self.load()
        await self.render_lists()
        self.notify(f"Showing {name or 'all areas'}.")

    def action_capture_to(self, name: str) -> None:
        self.capture_area = name
        self.notify(f"Captures now go to {name} when all areas are showing.")

    async def action_clear_proposals(self) -> None:
        count = sum(1 for i in self.doc.items(Section.IN) if i.src)
        drop = lambda d: d.sections.__setitem__(  # noqa: E731
            Section.IN.value, [i for i in d.items(Section.IN) if not i.src])
        await self.change(drop)
        self.toast(f"Cleared {count} proposal{'s' if count != 1 else ''} from In")

    def get_system_commands(self, screen):
        return iter(())

    async def action_clear_ticked(self) -> None:
        self.ticked.pop(self.current, None)
        await self.render_lists()

    async def action_prune_now(self) -> None:
        before = len(self.doc.items(Section.DONE))
        await self.change(lambda d: model.prune_done(d, self.today))
        self.notify(f"Pruned {before - len(self.doc.items(Section.DONE))} Done item(s) older than "
                    f"{model.DONE_RETENTION_DAYS} days.")

    async def action_toggle_prune(self) -> None:
        await self.change(lambda d: setattr(d, "prune_manual", not d.prune_manual))
        self.notify("Done is now pruned only on demand." if self.doc.prune_manual
                    else f"Done is now pruned automatically after {model.DONE_RETENTION_DAYS} days.")

    # Helpers for flows

    async def ask(self, question: str, labels: list[str]) -> int | None:
        """Returns the 1-based answer number, or None when Arjun goes back."""
        return await self.push_screen_wait(AskScreen(question, labels, self.header))

    async def text(self, prompt: str, value: str = "") -> str | None:
        return await self.push_screen_wait(TextScreen(prompt, value, self.header))

    def on_disk(self, section: Section, item: model.Item):
        """Wrap a change so it targets the same item in the freshly re-read file."""
        line = item.line()

        def locate(doc):
            return model.find(doc, section, line)
        return locate

    # 1. Capture

    @work(exclusive=True)
    async def action_capture(self) -> None:
        """Capture one (Enter) or many (Tab between items). On close,
        the first item of the batch is highlighted so `c` clarifies it next."""
        if captured := await self.push_screen_wait(CaptureScreen(self.save_capture)):
            await self.show(Section.IN)
            view = self.query_one("#items", ItemList)
            view.focus()
            view.highlighted = self.row_of(captured[0].line())

    async def save_capture(self, text: str) -> model.Item:
        saved: list[model.Item] = []
        area = self.view_areas[0] if len(self.view_areas) == 1 else self.capture_area
        await self.change(lambda d: saved.append(model.capture(d, text, self.today)), area)
        return saved[0]

    # 2. Clarify: the processing workflow diagram

    @work(exclusive=True)
    async def action_clarify(self) -> None:
        """Clarify the highlighted thing. On a project: ask for its next action. On a
        Someday item: run it through clarify. Otherwise: clarify In, starting from
        the highlighted In item if there is one, then the rest from the top."""
        here, item = self.current, self.selected()
        if here is Section.PROJECTS and item is not None:
            await self.clarify(item, None, "action", existing=item.project)
            return await self.show(here)
        if here is Section.SOMEDAY and item is not None:
            await self.clarify(item, Section.SOMEDAY)
            return await self.show(here)
        first = item if here is Section.IN else None
        await self.show(Section.IN)
        self.clarify_progress = (1, len(self.doc.items(Section.IN)))
        if first is None or await self.clarify(first):
            self.load()
            await self.clarify_inbox(done=1 if first else 0)
        self.clarify_progress = None
        await self.show(Section.IN)

    async def clarify_inbox(self, done: int = 0) -> bool:
        """Walk In to empty. Returns False if Arjun stopped early."""
        while inbox := sum(in_order(self.doc.items(Section.IN)), []):  # own captures first
            self.clarify_progress = (done + 1, done + len(inbox))
            if not await self.clarify(inbox[0]):
                self.clarify_progress = None
                return False
            done += 1
            self.load()
        self.clarify_progress = None
        return True

    async def clarify(self, item: model.Item, source: Section | None = Section.IN,
                      start: str = "what", **preset) -> bool:
        """Walk one item through the processing diagram, one screen per decision.

        0 / ← / Esc goes back a screen with the earlier answer kept; nothing is
        written until the last screen. Returns False when Arjun backs out of
        the first screen. `preset` pre-answers steps, e.g. existing=<slug>.
        """
        a = dict(preset)
        title = f"“{item.text}”\n\n"

        async def what():
            # Allen's two questions in one screen: actionable? and if so, one step or a project?
            r = await self.ask(title + "What is it?",
                               ["One action", "A project (more than one step)", "Someday/Maybe (incubate)",
                                "Trash", "Reference: file it in Obsidian"])
            if r is not None:
                a["choice"] = r
                a["actionable"] = r <= 2
                a["kind"] = "project" if r == 2 else "single"
                a["dest"] = Section.SOMEDAY if r == 3 else None  # trash and reference both clear it from In
                return ["action", "outcome", "commit", "commit", "commit"][r - 1]

        async def outcome():
            r = await self.text("Project: what does done look like?", a.get("outcome", item.text))
            if r is not None:
                a["outcome"] = r
                return "action" if r else "outcome"

        async def action():
            fresh = a.get("kind") == "project" or a.get("existing")
            r = await self.text("What is the next physical, visible action?",
                                a.get("action", "" if fresh else item.text))
            if r is not None:
                a["action"] = r
                if not r:
                    return "action"
                return "project" if a.get("kind") == "single" and self.area_projects(item.area) else "how"

        async def project():
            projects = self.area_projects(item.area)
            r = await self.ask(f"Next action: “{a['action']}”\n\nWhich project?",
                               ["No project"] + [p.text for p in projects])
            if r is not None:
                a["existing"] = None if r == 1 else projects[r - 2].project
                return "how"

        async def how():
            r = await self.ask(f"Next action: “{a['action']}”",
                               ["Defer it (Next Actions)", "Do it now (under two minutes)",
                                "Delegate it (Waiting For)"])
            if r is not None:
                a["how"] = ["defer", "now", "delegate"][r - 1]
                return ["context", "commit", "who"][r - 1]

        async def context():
            choices = self.context_choices()
            r = await self.push_screen_wait(ContextScreen(f"Defer “{a['action']}”: which context?", choices,
                                                          (a.get("context") or "").split(), self.header))
            if r is not None:
                if r == ["other"]:
                    return "context_other"
                a["context"] = " ".join(r)
                return "dates"

        async def context_other():
            r = await self.text("Context name", (a.get("context") or "").lstrip("@"))
            if r is not None:
                a["context"] = f"@{r.lstrip('@').replace(' ', '-')}" if r else None
                return "dates" if r else "context_other"

        async def who():
            r = await self.text("Waiting for whom?", a.get("who", ""))
            if r is not None:
                a["who"] = r
                return "dates" if r else "who"

        async def dates_step():
            what_for = "back from them" if a.get("how") == "delegate" else "done"
            r = await self.push_screen_wait(DateScreen(
                self.today, a.get("due"), a.get("start"), self.header,
                f"“{a['action']}”: when is it due ({what_for}), and when should it show up? Enter skips.",
                self.DATE_STATE))
            if r == "back":
                return None
            a["due"], a["start"] = r
            return "commit"

        steps = {f.__name__: f for f in (what, outcome, action, project, how, context, context_other, who)}
        steps["dates"] = dates_step
        has_projects = bool(self.area_projects(item.area))
        history, step = [], start
        while step != "commit":
            path = predict_path(a, start, has_projects)
            self.header = Content.assemble(step_bar(path, step, self.clarify_progress), "\n",
                                           (self.clarify_answers(a), "dim italic"))
            nxt = await steps[step]()
            if nxt is None:
                if not history:
                    self.header = None
                    return False
                step = history.pop()
            elif nxt != step:
                history.append(step)
                step = nxt
        self.header = None
        locate = self.on_disk(source, item) if source else (lambda d: None)
        await self.change(lambda d: self.apply_clarify(d, a, locate(d)), item.area)
        self.toast(self.clarify_summary(item, a))
        return True

    def clarify_answers(self, a: dict) -> str:
        """The answers so far, in one line under the step bar."""
        parts = [CHOICES[a["choice"]]] if a.get("choice") else []
        if a.get("outcome") and a.get("kind") == "project":
            parts.append(f"project “{a['outcome']}”")
        if a.get("action"):
            parts.append(f"“{a['action']}”")
        if a.get("existing"):
            parts.append(a["existing"].replace("-", " ").title())
        if a.get("how"):
            parts.append({"defer": "defer", "now": "do it now", "delegate": "delegate"}[a["how"]])
        if a.get("context"):
            parts.append(a["context"])
        if a.get("who"):
            parts.append(f"waiting for {a['who']}")
        return " · ".join(parts)

    @staticmethod
    def clarify_summary(item: model.Item, a: dict) -> str:
        if a.get("actionable") is False:
            return {3: f"Moved “{item.text}” to Someday/Maybe", 4: f"Trashed “{item.text}”"}.get(
                a.get("choice"), f"Cleared “{item.text}” from In (file it in Obsidian)")
        action = a["action"]
        return {"defer": f"Deferred “{action}” to {a.get('context')}",
                "now": f"Done now: “{action}”",
                "delegate": f"Delegated “{action}” to {a.get('who')}"}[a["how"]]

    def apply_clarify(self, d: model.Doc, a: dict, src: model.Item | None) -> None:
        if a.get("actionable") is False:
            if a["dest"] is None:
                model.trash(d, src)
            elif src is not None:
                model.move(d, src, a["dest"])
            return
        slug = a.get("existing")
        if a.get("kind") == "project" and not slug:
            slug, src = model.make_project(d, src, a["outcome"], self.today), None
        if a["how"] == "defer":
            model.defer(d, src, a["action"], a["context"], slug, self.today, a.get("due"), a.get("start"))
        elif a["how"] == "now":
            model.do_now(d, src, a["action"], slug, self.today)
        else:
            model.delegate(d, src, a["action"], a["who"], slug, self.today, a.get("due"), a.get("start"))

    # 3. Organize: act on the highlighted item

    async def action_complete(self) -> None:
        """Space. On an open item: tick it into Done and the ticked section below the list.
        On a ticked row: untick it back into place. On the Done tab: reopen it where its
        tags say it belongs."""
        section = self.current
        if entry := self.selected_ticked():
            done_item, pos = entry
            self.ticked[section].remove(entry)
            locate = self.on_disk(Section.DONE, done_item)
            await self.change(lambda d: (it := locate(d)) and model.uncomplete(d, it, section, pos), done_item.area)
            return self.toast(f"Unticked “{done_item.text}”")
        if not (item := self.selected()):
            return
        locate = self.on_disk(section, item)
        if section is Section.DONE:
            target = model.reopen_target(item)
            await self.change(lambda d: (it := locate(d)) and model.uncomplete(d, it, target, len(d.items(target))),
                              item.area)
            return self.toast(f"Reopened “{item.text}” in {target.value}")
        pos = next((n for n, i in enumerate(self.docs[item.area].items(section)) if i.line() == item.line()), 0)
        rolled: list = []

        def tick(d):
            if (it := locate(d)) is not None:
                if (was := model.complete(d, it, self.today)) is not None:
                    rolled.append((it, was))  # recurring: it stays, with its due date rolled forward
                else:
                    self.ticked.setdefault(section, []).append((it, pos))
        await self.change(tick, item.area)
        self.log_completion(item, rolled[0][1] if rolled else item.due)
        if rolled:
            return self.toast(f"Ticked “{item.text}” · next due {rolled[0][0].due:%b %-d}")
        self.toast(f"Ticked “{item.text}”")

    def log_completion(self, item: model.Item, was_due) -> None:
        """Append a row to the area's completions log, as part of the same undo step."""
        if not (log := self.areas[item.area].completions_log) or not log.exists():
            return
        before = log.read_text()
        text = item.text.replace("|", "\\|")  # a pipe inside [[a|b]] would split the table cell
        row = f"| {self.today} | {text} | {was_due or ''} | {item.every or 'once'} |\n"
        store.write_text(log, before if before.endswith("\n") else before + "\n")
        with log.open("a") as f:
            f.write(row)
        self.undo_stack[-1].append((log, before, log.read_text()))

    async def action_delete(self) -> None:
        """Delete the marked rows as one change (one z restores them all), else the highlighted one."""
        if self.marked:
            marked, section = set(self.marked), self.current
            self.marked.clear()

            def drop(area):
                lines = {line for a, line in marked if a == area}
                return lambda d: d.sections.__setitem__(
                    section.value, [i for i in d.items(section) if i.line() not in lines])
            await self.change_each({area: drop(area) for area in {a for a, _ in marked}})
            return self.toast(f"Deleted {len(marked)} item{'s' if len(marked) != 1 else ''}")
        if item := self.selected():
            locate = self.on_disk(self.current, item)
            await self.change(lambda d: model.trash(d, locate(d)), item.area)
            self.toast(f"Deleted “{item.text}”")

    def resolve(self, item: model.Item, target: str) -> str:
        """A link target made openable: vault notes become obsidian:// URIs for the item's vault."""
        if "://" in target:
            return target
        vault = self.areas[item.area].file.parent.name if item.area in self.areas else ""
        return f"obsidian://open?vault={quote(vault)}&file={quote(target.split('#')[0], safe='')}"

    def visible_items(self, section: Section) -> list[model.Item]:
        """A list's items, without tasks that start later unless they are being shown."""
        items = self.doc.items(section)
        return items if self.show_later else [i for i in items if not model.starts_later(i, self.today)]

    def later_count(self) -> int:
        return sum(model.starts_later(i, self.today) for s in Section for i in self.doc.items(s))

    async def action_toggle_later(self) -> None:
        self.show_later = not self.show_later
        await self.render_lists()
        self.notify("Showing tasks that start later." if self.show_later else "Hiding tasks that start later.")

    def context_choices(self) -> list[str]:
        """The configured contexts in their order, then any others already in use."""
        used = sorted({c for d in self.docs.values() for s in Section for i in d.items(s) for c in i.contexts})
        return self.contexts + [c for c in used if c not in self.contexts]

    def area_projects(self, area: str | None) -> list[model.Item]:
        doc = self.docs.get(area) if area else None
        return [p for p in (doc.items(Section.PROJECTS) if doc else []) if p.project and p.raw is None]

    def tag_note(self, item: model.Item, kind: str, name: str) -> str | None:
        """The vault note a tag points to, as a path without .md: +project finds
        projects/<slug> (also archived/ and folder-style projects), a person
        (waiting:<who>, @agenda-<who>) finds people/**/<who>.md or <who>-*.md."""
        if item.area not in self.areas or not name:
            return None
        key = (item.area, kind, name)
        if key not in self._tag_notes:
            vault = self.areas[item.area].file.parent
            if kind == "project":
                candidates = [vault / "projects" / f"{name}.md", vault / "projects" / "archived" / f"{name}.md",
                              vault / "projects" / name / f"{name}.md"]
            else:
                people = vault / "people"
                candidates = sorted(people.rglob(f"{name}.md")) + sorted(people.rglob(f"{name}-*.md")) \
                    if people.is_dir() else []
            found = next((c for c in candidates if c.is_file()), None)
            self._tag_notes[key] = str(found.relative_to(vault).with_suffix("")) if found else None
        return self._tag_notes[key]

    @staticmethod
    def link_part(label: str, url: str, base: str = "") -> Content:
        """A clickable label. The link rides in private metadata, not as a terminal
        hyperlink, so only the app handles the click (kitty would otherwise open it too)."""
        return Content.styled(label, f"{base} underline {LINK_COLOUR}".strip()).stylize(
            Style.from_meta({"gtd_link": url}))

    def project_title(self, item: model.Item) -> str:
        """A project's short name: its note's first "# " heading, else its slug in words."""
        key = (item.area, "title", item.project)
        if key not in self._tag_notes:
            title = None
            if note := self.tag_note(item, "project", item.project):
                path = self.areas[item.area].file.parent / f"{note}.md"
                title = next((ln[2:].strip() for ln in path.read_text().splitlines() if ln.startswith("# ")), None)
            self._tag_notes[key] = title or item.project.replace("-", " ").title()
        return self._tag_notes[key]

    def chips(self, item: model.Item) -> list[tuple[str, str | None]]:
        """(label, URL or None) after the action, in a fixed order: the notes linked at the
        end of the text, the project, the people (agenda, then waiting-on), the source."""
        _, trailing = model.split_trailing_links(item.text)
        out = [(label, self.resolve(item, target)) for link in trailing for label, _, target in model.links(link)]
        if item.project:
            note = self.tag_note(item, "project", item.project)
            out.append((self.project_title(item), note and self.resolve(item, note)))
        people = [c.removeprefix("@agenda-") for c in item.contexts if c.startswith("@agenda-")]
        for who in dict.fromkeys(people + ([item.waiting] if item.waiting else [])):
            note = self.tag_note(item, "person", who)
            out.append((who.replace("-", " ").title(), note and self.resolve(item, note)))
        if item.src:
            out.append((source_name(item.src), item.src if "://" in item.src else None))
        return out

    def item_links(self, item: model.Item) -> list[tuple[str, str]]:
        """(label, openable URL) for every link in the item, in the order the row shows them."""
        action, _ = model.split_trailing_links(item.text)
        out = [(label, self.resolve(item, target)) for label, _, target in model.links(action)]
        return out + [(label, url) for label, url in self.chips(item) if url]

    @work(exclusive=True)
    async def action_open_link(self) -> None:
        if not (item := self.selected()):
            return
        found = self.item_links(item)
        if not found:
            return self.notify("No link in this item.")
        if len(found) > 1:
            choice = await self.ask(f"Open which link in “{item.text[:60]}”?", [label for label, _ in found])
            if choice is None:
                return
            found = [found[choice - 1]]
        label, url = found[0]
        self.open_link_url(url, label)

    def open_link_url(self, url: str, label: str | None = None) -> None:
        self.opener(url)
        self.last_toast = f"Opened “{label or url}”"
        self.notify(self.last_toast, timeout=3)

    async def action_extend(self, step: int) -> None:
        """Shift+↑/↓: grow or shrink a selection from where it started, like a file manager."""
        view = self.query_one("#items", ItemList)
        if view.highlighted is None:
            return
        if self.anchor is None:
            self.anchor, self.anchor_base = view.highlighted, set(self.marked)
        super(ItemList, view).action_cursor_down() if step > 0 else super(ItemList, view).action_cursor_up()
        low, high = sorted((self.anchor, view.highlighted))
        span = {(v.area, v.line()) for kind, v in self.rows[low:high + 1] if kind == "item"}
        self.marked = self.anchor_base | span
        await self.render_lists()

    async def action_toggle_mark(self) -> None:
        if item := self.selected():
            self.marked ^= {(item.area, item.line())}
            await self.render_lists()

    async def action_escape(self) -> None:
        if self.marked:
            self.marked.clear()
            self.anchor = None
            return await self.render_lists()
        await self.action_review_stop()

    async def action_primary(self) -> None:
        """Enter: the obvious thing for this list. Clarify on In and Someday, add a next
        action on Projects, edit everywhere else."""
        if self.selected() is None:
            return
        if self.current in (Section.IN, Section.PROJECTS, Section.SOMEDAY):
            self.action_clarify()
        else:
            self.action_edit()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_command_palette(self) -> None:
        if not CommandPalette.is_open(self):
            self.push_screen(CommandPalette(id="--command-palette", placeholder="Search tasks and commands…"))

    async def jump_to(self, section: Section, line: str) -> None:
        """Open a list with the given item highlighted (used by / search)."""
        await self.action_go(LIST_ORDER.index(section))
        if (row := self.row_of(line)) is not None:
            self.query_one("#items", ItemList).highlighted = row

    @work(exclusive=True)
    async def action_edit(self) -> None:
        if not (item := self.selected()):
            return
        locate, prefix = self.on_disk(self.current, item), item.line()[:6]
        body = await self.text("Edit (tags at the end: @context +project waiting:who)",
                               item.line()[6:] if item.raw is None else item.line())
        if not body:
            return
        new = store.parse_item(prefix + body if item.raw is None else body)

        def replace(d):
            items = d.items(self.current)
            if (old := locate(d)) is not None:
                items[next(i for i, x in enumerate(items) if x is old)] = new
        await self.change(replace, item.area)
        self.toast(f"Edited “{new.text}”")

    @work(exclusive=True)
    async def action_dates(self) -> None:
        """t: due and start dates on the date screen (the same one clarify uses)."""
        if not (item := self.selected()):
            return
        r = await self.push_screen_wait(DateScreen(self.today, item.due, item.start,
                                                   title=f"“{item.text[:60]}”: due, then start",
                                                   state=self.DATE_STATE))
        if r == "back":
            return
        due, start = r
        locate = self.on_disk(self.current, item)

        def set_dates(d):
            if (it := locate(d)) is not None:
                it.due, it.start = due, start
        await self.change(set_dates, item.area)
        later = start and start > self.today and not self.show_later
        said = [f"due {due:%b %-d}" if due else "no due date",
                (f"starts {start:%b %-d}" + (" (hidden until then)" if later else "")) if start else "no start"]
        self.toast(f"{model.split_trailing_links(item.text)[0][:40]}: {' · '.join(said)}")

    @work(exclusive=True)
    async def action_move(self) -> None:
        if not (item := self.selected()):
            return
        locate = self.on_disk(self.current, item)
        targets = [s for s in LIST_ORDER if s is not self.current]
        choice = await self.ask(f"Move “{item.text}” to:", [t.value for t in targets])
        if choice:
            target = targets[choice - 1]
            await self.change(lambda d: model.move(d, locate(d), target), item.area)
            self.toast(f"Moved “{item.text}” to {target.value}")

    # 4. Reflect: the weekly review, as a guided tour of the lists

    async def action_review(self) -> None:
        self.review_step = 0
        await self.review_show()

    async def action_review_next(self) -> None:
        if self.review_step is None:
            return
        if self.review_step + 1 < len(REVIEW_STEPS):
            self.review_step += 1
            return await self.review_show()
        self.review_step = None
        await self.change(lambda d: setattr(d, "last_review", self.today))
        self.notify("Weekly review complete.")

    async def action_review_stop(self) -> None:
        if self.review_step is not None:
            self.review_step = None
            self.update_status()
            self.notify("Review stopped; nothing recorded.")

    async def review_show(self) -> None:
        await self.show(REVIEW_STEPS[self.review_step][0])
        view = self.query_one("#items", ItemList)
        view.focus()
        view.highlighted = self.first_row()

    # 5. Engage: filter Next Actions by context

    @work(exclusive=True)
    async def action_engage(self) -> None:
        present = sorted({c for i in self.doc.items(Section.NEXT) for c in i.contexts})
        choice = await self.ask("Where are you, and what can you do here?", ["All contexts", *present])
        if choice is not None:
            self.context_filter = None if choice == 1 else present[choice - 2]
            await self.show(Section.NEXT)
