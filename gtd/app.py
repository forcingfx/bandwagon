"""Textual TUI for the five GTD steps: capture, clarify, organize, reflect, engage."""
from __future__ import annotations

import json
import subprocess
from datetime import date
import hashlib
from pathlib import Path
from urllib.parse import quote

from textual import on, work
from textual.app import App, ComposeResult
from textual.fuzzy import Matcher
from textual.binding import Binding
from textual.content import Content
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.style import Style
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import config, dates, model, store
from .clarify import ClarifyScreen, prefill_from_item
from .datescreen import DatePicker, DateScreen
from .model import Section

LIST_ORDER = [Section.IN, Section.NEXT, Section.PROJECTS, Section.WAITING, Section.SOMEDAY, Section.DONE]
FOCUS = "focus"  # the Focus tab: every starred open item, from any list; shown after In while any exist
STAR_COLOUR = "#ffd580"   # the kitty palette's yellow

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
             Section.WAITING: "Waiting", Section.SOMEDAY: "Someday", Section.DONE: "Done", FOCUS: "Focus"}
# The key line follows the list you're on (lazygit-style); the tail is the same everywhere.
LIST_KEYS = {
    Section.IN: "a capture · ⏎ clarify · s star · space tick · h dates · o open · ⌫ delete",
    FOCUS: "space tick · s unstar · ⏎ edit · c re-clarify · h dates · a capture starred · o open",
    Section.NEXT: "space tick · s star · m project · l context · h dates · c re-clarify · f filter · ⌫ delete",
    Section.PROJECTS: "⏎ open · c add tasks · space tick · x mark · ⌫ delete",
    Section.WAITING: "space received · s star · p who · h dates · m project · c re-clarify · ⌫ delete",
    Section.SOMEDAY: "⏎ clarify · c re-clarify · s star · h dates · ⌫ delete · a capture",
    Section.DONE: "space reopen · x mark · ⌫ delete",
}
KEYS_TAIL = "z undo · ← → lists · v vault · d later · w review · / search · ? keys"
# Every key, grouped, for the ? screen.
HELP = [
    ("Move", [("↑ ↓ or j k", "move through the list"), ("Home End", "first / last item"),
              ("PgUp PgDn", "a page up / down"), ("← → u i Tab ⇧Tab", "previous / next list (or click a tab)"),
              ("⇧↑ ⇧↓", "select rows from here, like a file manager")]),
    ("Act on the highlighted item", [("⏎", "clarify In/Someday · next action for a project · edit"),
                                     ("Space", "tick (or click the checkbox); on a ticked row untick; on Done reopen"),
                                     ("o", "open a link in the item (a picker if it has several)"),
                                     ("x", "mark for a bulk delete"), ("⌫ Del", "delete the marked rows, or this one"),
                                     ("e", "edit the text"), ("c", "re-clarify: change anything, move lists"),
                                     ("m", "project"), ("l", "context"), ("p", "waiting for (moves to Waiting For)"),
                                     ("s", "star for Focus (again to unstar); the Focus tab lists every star"),
                                     ("h", "dates: type 2, fri, dec 5 or -3 (before due) and pick a suggestion"),
                                     ("z", "undo the last change")]),
    ("The five steps", [("a", "capture (Tab: save and capture another)"), ("c", "clarify In"),
                        ("w", "weekly review (n next step, Esc stop)"), ("f", "show one context in Next Actions"),
                        ("L", "contexts: add, rename, delete, reorder"),
                        ("d", "show or hide deferred tasks, whose start date is still ahead")]),
    ("Everything else", [("/", "search tasks and commands"), ("?", "this screen"), ("Esc", "clear marks / stop review"),
                         ("q", "quit")]),
]


HINT_KEYS = {"space", "tab", "⇧tab", "esc", "Esc", "Space"}


def hint_content(hints: str) -> Content:
    """The key line with each key in colour and its label plain: "a capture · ← → lists"."""
    parts: list = []
    for n, segment in enumerate(hints.split(" · ")):
        words = segment.split(" ")
        keys = 0
        while keys < len(words) - 1 and (len(words[keys]) <= 3 or words[keys] in HINT_KEYS):
            keys += 1
        if n:
            parts.append(("  ", ""))
        if keys:
            parts.append((" ".join(words[:keys]), f"bold {COUNT_COLOUR}"))
            parts.append((" " + " ".join(words[keys:]), "dim"))
        else:
            parts.append((segment, "dim"))
    return Content.assemble(*parts)


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


AREA_COLOURS = {"work": "#73d0ff", "home": "#bae67e"}   # the kitty palette's blue and green


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
        elif (area := event.style.meta.get("gtd_area")) is not None:
            await self.app.action_show_area(area)


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


PALETTE_GROUPS = [  # (heading, the actions it holds, by name before any "(")
    ("Do", {"capture", "clarify", "star", "complete", "undo", "review", "review_next", "engage", "open_link"}),
    ("Edit item", {"edit", "edit_field", "delete", "toggle_mark"}),
    ("Lists & views", {"go", "cycle_view", "show_area", "toggle_later", "clear_ticked", "clear_proposals", "help"}),
    ("Settings", {"manage_contexts", "capture_to", "prune_now", "toggle_prune", "quit"}),
]
PALETTE_WIDTH = 76  # characters in a row: the name on the left, its key or list on the right


def task_matches(query: str, item: model.Item) -> bool:
    """Every word of the query appears in the task's text or tags. Plain substrings, not
    fuzzy: fuzzy matching is exponential on long task lines and stalls typing."""
    hay = " ".join(filter(None, [item.text, item.context, item.project and f"+{item.project}",
                                 item.waiting])).lower()
    return all(word in hay for word in query.lower().split())


def highlight_words(text: str, query: str) -> Content:
    content, low = Content(text), text.lower()
    for word in query.lower().split():
        at = low.find(word)
        while at >= 0:
            content = content.stylize("bold underline", at, at + len(word))
            at = low.find(word, at + len(word))
    return content


class PaletteScreen(ModalScreen):
    """/ : one box for commands and tasks. Commands come first, each with its key on the
    right; matching tasks follow, each with its list and vault. An empty box lists every
    command by group. ↑ ↓ move (j and k type), Enter runs, Esc closes.
    Dismisses with ("action", name), ("item", (section, line)) or None."""

    BINDINGS = [Binding("escape", "close", "Close"),
                Binding("up", "move(-1)", show=False, priority=True),
                Binding("down", "move(1)", show=False, priority=True)]
    MAX_TASKS = 30

    def __init__(self) -> None:
        super().__init__()
        self.items: list = []  # (section, line) per task row, by the row's id

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Input(placeholder="Search tasks and commands…", id="palette-input")
            yield OptionList(id="palette-results")
            yield Static("↑ ↓ move · Enter run · Esc close", classes="hint", markup=False)

    def on_mount(self) -> None:
        self.fill("")
        self.query_one("#palette-input", Input).focus()

    @staticmethod
    def row(left: Content, right: Content) -> Content:
        room = PALETTE_WIDTH - right.cell_length - 2
        if left.cell_length > room:
            left = left.truncate(room - 1) + Content("…")
        return left + Content(" " * (PALETTE_WIDTH - left.cell_length - right.cell_length)) + right

    def command(self, name: Content, key: str, action: str) -> Option:
        return Option(self.row(name, Content.styled(key, COUNT_COLOUR)), id=f"action:{action}")

    @staticmethod
    def heading(text: str, first: bool = False) -> Option:
        return Option(Content.styled(("" if first else "\n") + text.upper(), "bold dim"), disabled=True)

    def fill(self, query: str) -> None:
        app, options = self.app, []
        entries = app.palette_entries()
        if not query.strip():
            for heading, names in PALETTE_GROUPS:
                group = [e for e in entries if e[2].split("(")[0] in names]
                if group:
                    options += [self.heading(heading, not options)] + [self.command(Content(n), k, a) for n, k, a in group]
        else:
            matcher = Matcher(query)
            hits = sorted(((matcher.match(n), n, k, a) for n, k, a in entries), key=lambda h: -h[0])
            commands = [self.command(matcher.highlight(n), k, a) for score, n, k, a in hits if score > 0]
            found = [(sec, i) for sec in LIST_ORDER for i in app.doc.items(sec)
                     if i.raw is None and task_matches(query, i)][:self.MAX_TASKS]
            tasks = [Option(self.row(highlight_words(i.text, query), Content.assemble(
                        (sec.value.rjust(13), "dim"), " ", ("▌", AREA_COLOURS.get(i.area, "dim")))),
                        id=f"item:{n}") for n, (sec, i) in enumerate(found)]
            self.items = [(sec, i.line()) for sec, i in found]
            if commands:
                options += [self.heading("Commands", True)] + commands
            if tasks:
                options += [self.heading("Tasks", not options)] + tasks
            if not options:
                options = [Option(Content.styled("Nothing matches", "dim italic"), disabled=True)]
        widget = self.query_one("#palette-results", OptionList)
        widget.clear_options()
        widget.add_options(options)
        widget.highlighted = next((n for n, o in enumerate(options) if not o.disabled), None)

    @on(Input.Changed, "#palette-input")
    def typed(self, event: Input.Changed) -> None:
        self.fill(event.value)

    def action_move(self, step: int) -> None:
        widget = self.query_one("#palette-results", OptionList)
        widget.action_cursor_down() if step > 0 else widget.action_cursor_up()

    def choose(self, option_id: str | None) -> None:
        if not option_id:
            return
        kind, value = option_id.split(":", 1)
        self.dismiss(("action", value) if kind == "action" else ("item", self.items[int(value)]))

    @on(Input.Submitted, "#palette-input")
    def submitted(self, event: Input.Submitted) -> None:
        event.stop()
        widget = self.query_one("#palette-results", OptionList)
        if widget.highlighted is not None:
            self.choose(widget.get_option_at_index(widget.highlighted).id)

    @on(OptionList.OptionSelected, "#palette-results")
    def clicked(self, event: OptionList.OptionSelected) -> None:
        self.choose(event.option.id)

    def action_close(self) -> None:
        self.dismiss(None)


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

    BINDINGS = [Binding("escape", "back", "Back"), Binding("0", "back", "Back"),
                Binding("j", "cursor(1)", show=False), Binding("k", "cursor(-1)", show=False)]

    def action_cursor(self, step: int) -> None:
        options = self.query_one(OptionList)
        options.action_cursor_down() if step > 0 else options.action_cursor_up()

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
                Binding("space", "toggle", "Toggle", priority=True),
                Binding("j", "cursor(1)", show=False), Binding("k", "cursor(-1)", show=False)]

    def action_cursor(self, step: int) -> None:
        options = self.query_one(OptionList)
        options.action_cursor_down() if step > 0 else options.action_cursor_up()

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


class ContextsScreen(ModalScreen):
    """L: the contexts, in the order clarify offers them, with how many open items use each
    across every vault. a adds, e renames (in every file and the config), ⌫ deletes (asking
    first when items use it), J K move. Changes apply at once; Esc closes."""

    BINDINGS = [Binding("escape", "back", "Close"),
                Binding("j", "cursor(1)", show=False), Binding("k", "cursor(-1)", show=False),
                Binding("J", "shift(1)", show=False), Binding("K", "shift(-1)", show=False),
                Binding("a", "add", show=False), Binding("e", "rename", show=False),
                Binding("backspace", "delete", show=False), Binding("delete", "delete", show=False)]
    HINT = "a add · e rename · ⌫ delete · J K move · Esc close"

    def __init__(self) -> None:
        super().__init__()
        self.mode: str | None = None   # "add", "rename" or "confirm" while one is under way

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Contexts", classes="question", markup=False)
            yield OptionList(id="contexts")
            yield Vertical(id="contexts-entry")
            yield Static(self.HINT, classes="hint", markup=False, id="contexts-hint")

    def on_mount(self) -> None:
        self.draw(0)

    def draw(self, at: int | None = None) -> None:
        names, counts = self.app.context_choices(), self.app.context_counts()
        options = self.query_one("#contexts", OptionList)
        keep = options.highlighted if at is None else at
        options.clear_options()
        width = max((len(n) for n in names), default=0) + 4
        options.add_options([Option(Content.assemble(
            f"{n:<{width}}", (f"{counts.get(n, 0)} open" if counts.get(n) else "unused", "dim")))
            for n in names])
        if names:
            options.highlighted = min(keep or 0, len(names) - 1)
        options.focus()

    def current(self) -> str | None:
        n = self.query_one("#contexts", OptionList).highlighted
        names = self.app.context_choices()
        return names[n] if n is not None and n < len(names) else None

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        return action == "back" or self.mode is None

    def action_cursor(self, step: int) -> None:
        options = self.query_one("#contexts", OptionList)
        options.action_cursor_down() if step > 0 else options.action_cursor_up()

    def action_shift(self, step: int) -> None:
        name, names = self.current(), self.app.context_choices()
        if name is None or not 0 <= (to := names.index(name) + step) < len(names):
            return
        names.remove(name)
        names.insert(to, name)
        self.app.save_context_order(names)
        self.draw(to)

    async def ask_text(self, mode: str, value: str = "") -> None:
        self.mode = mode
        entry = self.query_one("#contexts-entry", Vertical)
        await entry.remove_children()
        placeholder = "New context, e.g. deep work" if mode == "add" else "New name"
        await entry.mount(Input(value=value, placeholder=placeholder, id="contexts-input"))
        self.query_one("#contexts-hint", Static).update("Enter save · Esc cancel")
        self.query_one("#contexts-input", Input).focus()

    async def action_add(self) -> None:
        await self.ask_text("add")

    async def action_rename(self) -> None:
        if name := self.current():
            await self.ask_text("rename", name.lstrip("@"))

    async def action_delete(self) -> None:
        if not (name := self.current()):
            return
        if not (used := self.app.context_counts().get(name, 0)):
            await self.app.delete_context(name)
            return self.draw()
        self.mode = "confirm"
        self.query_one("#contexts-hint", Static).update(Content.assemble(
            (f"{name} is on {used} open item{'s' if used != 1 else ''}. ", "bold"),
            "1 delete it and remove it from them · Esc keep it"))

    async def on_key(self, event) -> None:
        if self.mode == "confirm" and event.character == "1":
            event.stop()
            name = self.current()
            await self.finish()
            await self.app.delete_context(name)
            self.draw()

    @on(Input.Submitted, "#contexts-input")
    async def submitted(self, event: Input.Submitted) -> None:
        event.stop()
        new, mode, old = context_name(event.value), self.mode, self.current()
        await self.finish()
        names = self.app.context_choices()
        if not new or (mode == "rename" and new == old):
            return
        if new in names:
            self.notify(f"{new} already exists.", severity="warning")
        elif mode == "add":
            self.app.save_context_order(names + [new])
            self.draw(len(names))
        elif old:
            await self.app.rename_context(old, new)
            self.draw()

    async def finish(self) -> None:
        self.mode = None
        await self.query_one("#contexts-entry", Vertical).remove_children()
        self.query_one("#contexts-hint", Static).update(self.HINT)
        self.query_one("#contexts", OptionList).focus()

    async def action_back(self) -> None:
        if self.mode:
            return await self.finish()
        self.dismiss(None)


def context_name(text: str) -> str | None:
    """"deep work" → "@deep-work"."""
    words = text.strip().lstrip("@").lower().split()
    return "@" + "-".join(words) if words else None


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
    AskScreen, TextScreen, CaptureScreen, HelpScreen, ContextScreen, DateScreen, ContextsScreen { align: center middle; }
    .stepbar { margin-bottom: 1; }
    DatePicker { height: auto; }
    DatePicker #suggestions { height: auto; max-height: 7; margin-top: 1; }
    DatePicker .datefield { height: 1; }
    DatePicker .datefield Static { width: 23; }
    DatePicker .datefield Input { border: none; height: 1; padding: 0 1; background: $boost; width: 1fr; }
    HelpScreen .dialog { max-height: 95%; }
    HelpScreen VerticalScroll { height: auto; max-height: 100%; }
    ContextsScreen .dialog { width: 56; }
    ContextsScreen #contexts { height: auto; max-height: 20; border: none; }
    ContextsScreen #contexts-entry { height: auto; }
    PaletteScreen { align: center top; }
    PaletteScreen .dialog { width: 84; margin-top: 3; max-height: 85%; }
    PaletteScreen #palette-results { height: auto; max-height: 30; border: none; margin-top: 1; }
    .dialog { width: 72; height: auto; max-height: 80%; border: thick $primary; background: $surface; padding: 1 2; }
    .question { margin-bottom: 1; }
    .hint { margin-top: 1; color: $text-muted; }
    #card-title { margin-bottom: 1; }
    #card-item { margin-bottom: 1; }
    #card-picker { margin-top: 1; height: auto; }
    .dialog OptionList { height: auto; max-height: 14; }
    """
    COMMANDS: set = set()  # the / palette is our own PaletteScreen; no Textual built-ins
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
        Binding("s", "star", "Star", show=False),
        Binding("d", "toggle_later", "Show/hide deferred", show=False),
        Binding("v", "cycle_view", "Vault view", show=False),
        Binding("backspace", "delete", "Delete", show=False),
        Binding("delete", "delete", "Delete", show=False),
        Binding("question_mark", "help", "Keys", show=False),
        Binding("z", "undo", "Undo"),
        Binding("e", "edit", "Edit"),
        Binding("m", "edit_field('project')", "Project", show=False),
        Binding("l", "edit_field('context')", "Context", show=False),
        Binding("h", "edit_field('dates')", "Dates", show=False),
        Binding("p", "edit_field('who')", "Waiting for", show=False),
        Binding("L", "manage_contexts", "Contexts", show=False),
        Binding("q", "quit", "Quit"),
    ]
    WATCH_SECONDS = 1.0  # how often to check gtd.md for edits made outside the app
    FORCE_EVERY = 5      # every 5th check re-reads the file even if size and mtime look unchanged
    DATE_STATE = dates.STATE  # last-used dates; tests point this elsewhere

    def __init__(self, areas: dict[str, config.Area] | Path | str, today: date | None = None,
                 capture: str | None = None, contexts: list[str] | None = None,
                 config_path: Path | None = None) -> None:
        super().__init__()
        self.contexts = list(contexts or config.DEFAULT_CONTEXTS)
        self.config_path = config_path  # where the contexts editor saves; None keeps changes in memory
        self.show_later = False  # tasks whose start: is after today stay hidden until toggled
        self.focus_origin: dict[str, Section] = {}  # Done line → the list an item ticked on Focus came from
        self.project_view: tuple[str, str] | None = None  # (area, slug) while a project page is open
        self.header: Content | None = None  # the clarify step bar while clarify runs
        self.clarify_progress: tuple[int, int] | None = None
        self.areas = areas if isinstance(areas, dict) else config.single(Path(areas))
        self.view_areas: list[str] = list(self.areas)  # areas on screen; the palette narrows it
        self.capture_area = capture if capture in self.areas else next(iter(self.areas))
        self.today = today or date.today()
        self.load()
        self.seen: dict[str, tuple] = {}
        self.polls = 0
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
        if self.project_view:
            return await self.render_project_page(stalled)
        if self.current == FOCUS:
            return self.render_focus(stalled)
        items = self.visible_items(self.current)
        if self.current is Section.NEXT and self.context_filter:
            items = [i for i in items if self.context_filter in i.contexts]
        if self.current is Section.NEXT:
            items = sorted(items, key=lambda i: i.contexts[0] if i.contexts else "~")
        outside = ([] if self.current is Section.DONE  # ticked in Obsidian, not yet filed
                   else [i for i in items if i.done and i.raw is None])
        own, proposals = in_order([i for i in items if not any(i is o for o in outside)])
        self.shown = own + proposals
        self.marked &= {(i.area, i.line()) for i in self.shown}
        rows: list[tuple[str, object]] = [("item", i) for i in own]
        if self.current is Section.PROJECTS and len(self.view_areas) > 1:  # one coloured block per vault
            rows = [row for area in self.view_areas for row in
                    ([("header", area)] + [("item", i) for i in own if i.area == area]
                     if any(i.area == area for i in own) else [])]
        if proposals:  # automated proposals sit below Arjun's own captures
            rows += [("divider", f"proposals · {len(proposals)}")] + [("item", i) for i in proposals]
        # Resolve ticked items against the file; drop any that left Done (undo, outside edit).
        done = {x.line(): x for x in self.doc.items(Section.DONE)}
        ticked = [(done[t.line()], pos) for t, pos in self.ticked.get(self.current, []) if t.line() in done]
        ticked += [(i, n) for n, i in enumerate(self.doc.items(self.current)) if any(i is o for o in outside)]
        if ticked:
            self.ticked[self.current] = ticked
            rows += [("divider", f"ticked · {len(ticked)}")] + [("ticked", entry) for entry in ticked]
        else:
            self.ticked.pop(self.current, None)
        self.draw_rows(rows, stalled)

    def draw_rows(self, rows: list, stalled: set) -> None:
        self.rows = rows
        view = self.query_one("#items", ItemList)
        index = view.highlighted
        view.clear_options()
        view.add_options([self.option(kind, value, stalled) for kind, value in rows])
        view.highlighted = self.nearest_row(index)
        self.update_status()

    FOCUS_SECTIONS = [Section.NEXT, Section.WAITING, Section.IN, Section.PROJECTS, Section.SOMEDAY]

    def focus_items(self) -> list[model.Item]:
        """Every starred open item in the visible vaults, in list order."""
        return [i for sec in self.FOCUS_SECTIONS for i in self.visible_items(sec)
                if i.star and not i.done and i.raw is None]

    def tab_order(self) -> list:
        """The tabs: Focus sits after In while anything is starred (or while it is open)."""
        if self.current == FOCUS or self.focus_items():
            return LIST_ORDER[:1] + [FOCUS] + LIST_ORDER[1:]
        return LIST_ORDER

    def render_focus(self, stalled: set) -> None:
        own = self.focus_items()
        self.shown = own
        self.marked &= {(i.area, i.line()) for i in own}
        rows: list[tuple[str, object]] = [("item", i) for i in own]
        done = {x.line(): x for x in self.doc.items(Section.DONE)}
        ticked = [(done[t.line()], pos) for t, pos in self.ticked.get(FOCUS, []) if t.line() in done]
        if ticked:
            self.ticked[FOCUS] = ticked
            rows += [("divider", f"ticked · {len(ticked)}")] + [("ticked", entry) for entry in ticked]
        else:
            self.ticked.pop(FOCUS, None)
        self.draw_rows(rows, stalled)

    PAGE_SECTIONS = [Section.NEXT, Section.WAITING, Section.SOMEDAY, Section.IN, Section.DONE]

    async def render_project_page(self, stalled: set) -> None:
        """Every item tagged +slug in the project's vault, under one divider per list."""
        area, slug = self.project_view
        doc = self.docs[area]
        rows: list[tuple[str, object]] = []
        self.shown = []
        for section in self.PAGE_SECTIONS:
            items = [i for i in doc.items(section) if i.project == slug and i.raw is None
                     and (self.show_later or not model.starts_later(i, self.today))]
            if items:
                rows += [("divider", section.value)] + [("item", i) for i in items]
                self.shown += items
        self.marked &= {(i.area, i.line()) for i in self.shown}
        self.draw_rows(rows, stalled)

    def section_of(self, item: model.Item) -> Section:
        """The list an item lives in (a project page shows items from several)."""
        fallback = self.current if self.current != FOCUS else Section.NEXT
        return next((s for s in Section if any(i is item for i in self.doc.items(s))), fallback)

    async def open_project(self, item: model.Item) -> None:
        self.project_view = (item.area, item.project)
        await self.render_lists()
        view = self.query_one("#items", ItemList)
        view.focus()
        view.highlighted = self.first_row()

    async def close_project(self) -> None:
        slug = self.project_view[1] if self.project_view else None
        self.project_view = None
        await self.render_lists()
        view = self.query_one("#items", ItemList)
        view.highlighted = next((n for n, (k, v) in enumerate(self.rows) if k == "item" and v.project == slug),
                                self.first_row())

    def option(self, kind: str, value, stalled: set) -> Option:
        if kind == "divider":
            return Option(Content.styled(f"── {value} ──", "dim"), disabled=True)
        if kind == "header":
            colour = AREA_COLOURS.get(value, COUNT_COLOUR)
            return Option(Content.styled(f" {value.upper()} ", f"bold #212733 on {colour}"), disabled=True)
        item = value[0] if kind == "ticked" else value
        return Option(self.describe(item, stalled, marked=(item.area, item.line()) in self.marked))

    def nearest_row(self, index: int | None) -> int | None:
        """The selectable row closest to `index`, looking up first (after a row left the list)."""
        if index is None:
            return None
        selectable = [n for n, (kind, _) in enumerate(self.rows) if kind not in ("divider", "header")]
        before = [n for n in selectable if n <= index]
        return before[-1] if before else (selectable[0] if selectable else None)

    def row_of(self, line: str) -> int | None:
        return next((n for n, (kind, v) in enumerate(self.rows) if kind == "item" and v.line() == line), None)

    def first_row(self) -> int | None:
        return next((n for n, (kind, _) in enumerate(self.rows) if kind == "item"), None)

    def tabs_text(self) -> Content:
        """The vault switch (with two vaults), then the lists as one row of tabs."""
        parts: list = []
        if len(self.areas) > 1:
            choices = [(name, name.upper(), [name]) for name in self.areas] + [("", "BOTH", list(self.areas))]
            for name, label, view in choices:
                active = self.view_areas == view
                colour = AREA_COLOURS.get(name, "#d9d7ce")
                style = f"bold #212733 on {colour}" if active else ("dim " + colour if name else "dim")
                parts.append(Content.styled(f" {label} ", style).stylize(Style.from_meta({"gtd_area": name})))
            parts.append(("  ", ""))
        for n, sec in enumerate(self.tab_order()):
            if n:
                parts.append(("│", "dim"))
            here = "reverse bold " if sec == self.current else ""
            count = len(self.focus_items()) if sec == FOCUS else len(self.visible_items(sec))
            name = f" ★ {TAB_NAMES[sec]} " if sec == FOCUS else f" {TAB_NAMES[sec]} "
            tab = Content.assemble((name, here + (STAR_COLOUR if sec == FOCUS else "")),
                                   (f"{count} ", here + (STAR_COLOUR if sec == FOCUS else COUNT_COLOUR)))
            parts.append(tab.stylize(Style.from_meta({"gtd_tab": n})))
            if sec is Section.PROJECTS and self.project_view:  # breadcrumb to the open project
                area, slug = self.project_view
                parts.append((f"› {self.title_of(area, slug)} ", "bold"))
        return Content.assemble(*parts)

    def describe(self, item: model.Item, stalled: set, marked: bool = False) -> Content:
        """One row: a mark bar, a heavy checkbox, the text, then the tags dimmed."""
        colour = AREA_COLOURS.get(item.area, COUNT_COLOUR) if len(self.areas) > 1 else None
        bar = ("▌", colour) if colour else " "          # the vault, at a glance
        gap = ("●", COUNT_COLOUR) if marked else " "      # marked for a bulk action
        if item.raw is not None:
            return Content.assemble(bar, gap, item.line())
        box = Content.styled("[✔]", "bold #4ade80") if item.done else Content.styled("[ ]", "bold")
        parts: list = [bar, box.stylize(Style.from_meta({"gtd_tick": True})), gap, " "]
        if item.star and not item.done:
            parts.append(("★ ", STAR_COLOUR))
        if self.current is Section.PROJECTS and not self.project_view and item.project in stalled:
            parts.append(("⚠ no next action · ", "yellow"))
        # Proposals (items with a source) are dimmed so Arjun's own captures stand out; link
        # syntax is shown as its label, underlined, and o opens it.
        base = "strike dim" if item.done else "dim" if item.src else ""
        action, _ = model.split_trailing_links(item.text)
        chips = self.chips(item)
        same = next(((label, url) for label, url in chips if label.casefold() == action.strip().casefold()), None)
        if same:  # a project row titled like its note: the title is the link, with no repeat chip
            chips.remove(same)
            parts.append(self.link_part(action, same[1], base) if same[1] else (action, base))
        for text, target in [] if same else model.link_segments(action):  # a link inside the sentence stays in it
            parts.append(self.link_part(text, self.resolve(item, target), base) if target else (text, base))
        # Every other link comes after the action, as named chips in one fixed order:
        # the note it is about, its project, its people, its source.
        for n, (label, url) in enumerate(chips):
            parts.append(("  " if n == 0 else " · ", "dim"))
            label = label if len(label) <= CHIP_MAX else label[:CHIP_MAX - 1] + "…"
            parts.append(self.link_part(label, url, base) if url else (label, "dim"))
        if self.current is Section.PROJECTS and not self.project_view and item.project and not item.done:
            parts.append((f"  {self.project_counts(item)}", "dim"))
        tags = [t for t in (*item.contexts, item.every and f"every:{item.every}") if t]
        if tags:
            parts.append(("  " + " ".join(tags), "dim"))
        if model.starts_later(item, self.today):
            parts.append((f"  starts {item.start:%b %-d}", "dim italic"))
        if item.due and not item.done:
            late = item.due < self.today
            soon = (item.due - self.today).days <= 2
            parts.append((f"  due {item.due:%b %-d}", "bold #f28779" if late else "#ffd580" if soon else "dim"))
        if self.current == FOCUS and not item.done:  # where it lives
            parts.append((f"  {TAB_NAMES[self.section_of(item)]}", "dim italic"))
        listing_projects = self.current is Section.PROJECTS and not self.project_view
        dates = [t for t in (item.since and f"since:{item.since}",
                             not listing_projects and item.added and f"added:{item.added}",
                             item.done_on and f"done:{item.done_on}") if t]
        if dates:
            parts.append(("  " + " ".join(dates), "dim"))
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
            self.query_one("#keys", Static).update(hint_content(hints))
            return
        elif self.marked:
            self.status_text = self.allen_status()
            hints = f"{len(self.marked)} marked · ⌫ delete marked · x unmark · Esc clear marks"
        else:
            self.status_text, hints = self.allen_status(), f"{LIST_KEYS[self.current]} · {KEYS_TAIL}"
        self.keys_text = hints
        overdue = self.doc.last_review is None or (self.today - self.doc.last_review).days > 7
        chip: list = []
        if len(self.view_areas) == 1 and len(self.areas) > 1:  # a narrowed view says which vault, in colour
            name = self.view_areas[0]
            chip = [(f" {name.upper()} ", f"bold #212733 on {AREA_COLOURS.get(name, COUNT_COLOUR)}"), " "]
        self.query_one("#status", Static).update(Content.assemble(
            *chip, (self.status_text, "bold yellow" if overdue and self.review_step is None else "bold")))
        self.query_one("#keys", Static).update(hint_content(hints))

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

    def file_state(self, path: Path, previous: tuple | None, force: bool) -> tuple:
        """(size, mtime, inode) plus a hash of the content. The hash is recomputed when the
        stat changes or when forced, because some sync clients write new content with the
        remote device's modification time, which a stat-only check would miss."""
        try:
            st = path.stat()
        except FileNotFoundError:
            return (None, None)
        key = (st.st_size, st.st_mtime_ns, st.st_ino)
        if previous and previous[0] == key and not force:
            return previous
        return (key, hashlib.sha1(path.read_bytes()).hexdigest())

    def stamps(self, force: bool = True) -> dict[str, tuple]:
        return {name: self.file_state(area.file, self.seen.get(name), force) for name, area in self.areas.items()}

    async def watch_file(self) -> None:
        """Pick up edits from Obsidian, Sync or the CLI without a reload key, and say what
        arrived from elsewhere."""
        self.polls += 1
        now = self.stamps(force=self.polls % self.FORCE_EVERY == 0)
        if {n: v[1] for n, v in now.items()} == {n: v[1] for n, v in self.seen.items()}:
            self.seen = now
            return
        self.seen = now
        before = self.item_states()
        self.load()
        self.announce_sync(before, self.item_states())
        await self.render_lists()

    def item_states(self) -> dict[tuple[str, str], bool]:
        """(area, text) → done, for every item, to describe what an outside change did."""
        return {(i.area, i.text): i.done or s is Section.DONE
                for s in Section for i in self.doc.items(s) if i.raw is None}

    def announce_sync(self, before: dict, after: dict) -> None:
        ticked = sum(1 for k, done in after.items() if done and before.get(k) is False)
        added = sum(1 for k, done in after.items() if not done and k not in before)
        parts = [f"{ticked} ticked"] * bool(ticked) + [f"{added} added"] * bool(added)
        self.last_toast = " · ".join(["Synced from another device", *parts])
        self.notify(self.last_toast, timeout=4)

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
        if self.project_view:  # ← and → leave a project page first
            return await self.close_project()
        tabs = self.tab_order()
        await self.action_go((tabs.index(self.current) + step) % len(tabs))

    async def action_go(self, index: int) -> None:
        self.project_view = None
        tabs = self.tab_order()
        await self.show(tabs[min(index, len(tabs) - 1)])
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
            ("Tick highlighted item", "space", "complete"),
            ("Undo", "z", "undo"),
            ("Weekly review", "w", "review"),
            ("Next weekly review step", "n", "review_next"),
            ("Edit highlighted item", "e", "edit"),
            ("Set project of highlighted item", "m", "edit_field('project')"),
            ("Set context of highlighted item", "l", "edit_field('context')"),
            ("Set dates of highlighted item", "h", "edit_field('dates')"),
            ("Star or unstar highlighted item for Focus", "s", "star"),
            ("Waiting for someone on highlighted item", "p", "edit_field('who')"),
            ("Clarify In, or re-clarify the highlighted item", "c", "clarify"),
            ("Delete marked or highlighted item", "⌫", "delete"),
            ("Mark or unmark highlighted item", "x", "toggle_mark"),
            ("Open a link in highlighted item", "o", "open_link"),
            ("Show or hide deferred tasks (start date ahead)", "d", "toggle_later"),
            ("Show every key", "?", "help"),
            ("Show one context in Next Actions", "f", "engage"),
            ("Manage contexts: add, rename, delete, reorder", "L", "manage_contexts"),
            *((f"Go to {TAB_NAMES[sec] if sec == FOCUS else sec.value}", "tab / ⇧tab", f"go({n})")
              for n, sec in enumerate(self.tab_order())),
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
        entries = [("Switch vault: " + ", ".join(self.areas) + ", both", "v", "cycle_view")]
        entries += [(f"Show {name} only", "", f"show_area('{name}')") for name in self.areas]
        entries.append(("Show all areas", "", "show_area('')"))
        entries += [(f"Captures go to {name}", "", f"capture_to('{name}')") for name in self.areas
                    if name != self.capture_area]
        return entries

    async def action_cycle_view(self) -> None:
        """v: both vaults → the first → the second → both again."""
        names = list(self.areas)
        if len(names) < 2:
            return
        order = [names] + [[n] for n in names]
        now = next((i for i, view in enumerate(order) if view == self.view_areas), 0)
        await self.action_show_area("" if (nxt := order[(now + 1) % len(order)]) == names else nxt[0])

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
            if self.project_view:  # captured into the open project: stay on its page
                await self.render_lists()
            else:
                await self.show(Section.IN)
            view = self.query_one("#items", ItemList)
            view.focus()
            view.highlighted = self.row_of(captured[0].line())

    async def save_capture(self, text: str) -> model.Item:
        """A capture lands in In, or, on a project page, straight into the project."""
        saved: list[model.Item] = []
        if self.project_view:
            area, slug = self.project_view
            await self.change(lambda d: saved.append(model.defer(d, None, text, None, slug, self.today)), area)
            return saved[0]
        area = self.view_areas[0] if len(self.view_areas) == 1 else self.capture_area
        star = self.today if self.current == FOCUS else None  # captured on Focus: starred

        def add(d):
            saved.append(item := model.capture(d, text, self.today))
            item.star = star
        await self.change(add, area)
        return saved[0]

    # 2. Clarify: the processing workflow diagram

    @work(exclusive=True)
    async def action_clarify(self) -> None:
        """c. On In: clarify from the highlighted item, then the rest of In. On a project:
        capture tasks into it. Anywhere else: re-clarify the highlighted item, every row
        prefilled, so any answer can change (this is also how an item moves between lists)."""
        item = self.selected()
        here = self.section_of(item) if item is not None else self.current
        if here is Section.PROJECTS and item is not None:
            await self.clarify(item, None, "tasks", existing=item.project, locked_project=True, kind="addto")
            return await self.show(here)
        if (here is not Section.IN or self.project_view) and item is not None:
            await self.clarify(item, here, "what", **prefill_from_item(item, here))
            return await self.render_lists() if self.project_view else await self.show(here)
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

    def card(self, item: model.Item, **kw) -> ClarifyScreen:
        """The clarify card for an item, set up for its vault."""
        projects = [(self.title_of(item.area, p.project), p.project) for p in self.area_projects(item.area)]
        return ClarifyScreen(item, projects=projects, contexts=self.context_choices(), today=self.today,
                             progress=self.clarify_progress, area=item.area if len(self.areas) > 1 else None,
                             area_colour=AREA_COLOURS.get(item.area, ""),
                             source=source_name(item.src) if item.src else None, date_state=self.DATE_STATE, **kw)

    def title_of(self, area: str | None, slug: str) -> str:
        return self.project_title(model.Item(text="", project=slug, area=area))

    async def clarify(self, item: model.Item, source: Section | None = Section.IN,
                      start: str = "what", **preset) -> bool:
        """Run the clarify card for one item and apply the answers. False if Arjun backed out."""
        a = await self.push_screen_wait(self.card(item, start=start, preset=preset))
        if a is None:
            return False
        locate = self.on_disk(source, item) if source else (lambda d: None)
        await self.change(lambda d: self.apply_clarify(d, a, locate(d)), item.area)
        self.toast(self.clarify_summary(item, a))
        return True

    @staticmethod
    def clarify_summary(item: model.Item, a: dict) -> str:
        if a.get("actionable") is False:
            return {3: f"Moved “{item.text}” to Someday/Maybe", 4: f"Trashed “{item.text}”"}.get(
                a.get("choice"), f"Cleared “{item.text}” from In (file it in Obsidian)")
        action = a["action"]
        extra = f" (+{len(a['extra_tasks'])} more in the project)" if a.get("extra_tasks") else ""
        return {"defer": f"Deferred “{action}” to {a.get('context') or 'no context'}{extra}",
                "now": f"Done now: “{action}”{extra}",
                "delegate": f"Delegated “{action}” to {a.get('who')}{extra}"}[a["how"]]

    def apply_clarify(self, d: model.Doc, a: dict, src: model.Item | None) -> None:
        keep_every = src.every if src is not None else None
        keep_star = src.star if src is not None else None
        if a.get("actionable") is False:
            if a["dest"] is None:
                model.trash(d, src)
            elif src is not None:
                model.move(d, src, a["dest"])
            return
        slug = a.get("existing")
        if a.get("new_project"):
            slug = model.make_project(d, None, a["new_project"], self.today)
        if a.get("kind") == "project" and not slug:
            slug, src = model.make_project(d, src, a["outcome"], self.today), None
        made = None
        if a["how"] == "defer":
            made = model.defer(d, src, a["action"], a.get("context"), slug, self.today, a.get("due"), a.get("start"))
        elif a["how"] == "now":
            model.do_now(d, src, a["action"], slug, self.today)
        else:
            made = model.delegate(d, src, a["action"], a["who"], slug, self.today, a.get("due"), a.get("start"))
        if made is not None and keep_every:
            made.every = keep_every  # re-clarifying a recurring item keeps it recurring
        if made is not None and keep_star:
            made.star = keep_star    # and a starred one stays on Focus
        for task in a.get("extra_tasks") or []:  # further tasks captured into the project
            model.defer(d, None, task, None, slug, self.today)

    # One row of the card on an already-clarified item: m project, l context, d dates, p waiting for

    @work(exclusive=True)
    async def action_edit_field(self, field: str) -> None:
        if not (item := self.selected()) or item.raw is not None:
            return
        section = self.section_of(item)
        a = await self.push_screen_wait(self.card(item, fields=[field], preset=prefill_from_item(item, section)))
        if a is None:
            return
        locate = self.on_disk(section, item)

        def apply(d):
            if (it := locate(d)) is None:
                return
            if field == "project":
                it.project = (model.make_project(d, None, a["new_project"], self.today) if a.get("new_project")
                              else a.get("existing"))
            elif field == "context":
                it.context = a.get("context")
            elif field == "dates":
                it.due, it.start = a.get("due"), a.get("start")
            elif field == "who":
                it.waiting, it.since = a["who"].strip().lower(), it.since or self.today
                if section is not Section.WAITING:
                    model.move(d, it, Section.WAITING)
        await self.change(apply, item.area)
        said = {"project": lambda: f"project {a.get('new_project') or self.title_of(item.area, a['existing'])}"
                if a.get("existing") or a.get("new_project") else "no project",
                "context": lambda: a.get("context") or "no context",
                "dates": lambda: " · ".join(x for x in (
                    a.get("due") and f"due {a['due']:%b %-d}",
                    a.get("start") and f"starts {a['start']:%b %-d}" + (
                        " (hidden until then)" if a["start"] > self.today and not self.show_later else ""))
                    if x) or "no dates",
                "who": lambda: f"waiting for {a['who']}"}[field]()
        self.toast(f"{model.split_trailing_links(item.text)[0][:40]}: {said}")

    # 3. Organize: act on the highlighted item

    async def action_complete(self) -> None:
        """Space. On an open item: tick it into Done and the ticked section below the list.
        On a ticked row: untick it back into place. On the Done tab: reopen it where its
        tags say it belongs."""
        section = self.section_of(self.selected()) if self.selected() else self.current
        if entry := self.selected_ticked():
            done_item, pos = entry
            if section == FOCUS:  # back to the list it came from
                section = self.focus_origin.get(done_item.line(), model.reopen_target(done_item))
            if section is not Section.DONE and any(done_item is i for i in self.doc.items(section)):
                # ticked in Obsidian and not yet filed: untick it where it is
                locate = self.on_disk(section, done_item)
                await self.change(lambda d: (it := locate(d)) and setattr(it, "done", False), done_item.area)
                return self.toast(f"Unticked “{done_item.text}”")
            if self.current == FOCUS:
                self.ticked[FOCUS].remove(entry)
            else:
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
                elif self.current == FOCUS:
                    self.ticked.setdefault(FOCUS, []).append((it, pos))
                    self.focus_origin[it.line()] = section
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
            marked = set(self.marked)
            self.marked.clear()

            def drop(area):  # from whichever list holds each row (Focus and project pages mix lists)
                lines = {line for a, line in marked if a == area}
                return lambda d: [d.sections.__setitem__(sec.value, [i for i in d.items(sec) if i.line() not in lines])
                                  for sec in Section if sec is not Section.DONE or self.current is Section.DONE]
            await self.change_each({area: drop(area) for area in {a for a, _ in marked}})
            return self.toast(f"Deleted {len(marked)} item{'s' if len(marked) != 1 else ''}")
        if item := self.selected():
            locate = self.on_disk(self.section_of(item), item)
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

    async def action_star(self) -> None:
        """s: star the marked rows or the highlighted one for Focus; on starred ones, unstar."""
        items = [i for i in self.shown if (i.area, i.line()) in self.marked] or \
            ([self.selected()] if self.selected() else [])
        if not items:
            return
        star = None if all(i.star for i in items) else self.today
        fns: dict = {}
        for item in items:
            locate = self.on_disk(self.section_of(item), item)
            fns.setdefault(item.area, []).append(locate)

        def apply(locates):
            return lambda d: [setattr(it, "star", star if not it.star or not star else it.star)
                              for it in (loc(d) for loc in locates) if it is not None]
        self.marked.clear()
        await self.change_each({area: apply(locates) for area, locates in fns.items()})
        what = f"“{items[0].text}”" if len(items) == 1 else f"{len(items)} items"
        self.toast(f"Starred {what} for Focus" if star else f"Unstarred {what}")

    async def action_toggle_later(self) -> None:
        self.show_later = not self.show_later
        await self.render_lists()
        self.notify("Showing deferred tasks." if self.show_later else "Hiding deferred tasks.")

    def context_choices(self) -> list[str]:
        """The configured contexts in their order, then any others already in use."""
        used = sorted({c for d in self.docs.values() for s in Section for i in d.items(s) for c in i.contexts})
        return self.contexts + [c for c in used if c not in self.contexts]

    def context_counts(self) -> dict[str, int]:
        """Open items per context, across every vault."""
        counts: dict[str, int] = {}
        for doc in self.docs.values():
            for section in Section:
                if section is not Section.DONE:
                    for item in doc.items(section):
                        for c in item.contexts:
                            counts[c] = counts.get(c, 0) + 1
        return counts

    def save_context_order(self, names: list[str]) -> None:
        self.contexts = list(names)
        if self.config_path:
            config.save_contexts(self.contexts, self.config_path)

    async def rename_context(self, old: str, new: str | None) -> None:
        """Rename (or with None, remove) a context in every vault's file, as one undo step,
        and in the configured list."""
        names = self.context_choices()
        users = [a for a, doc in self.docs.items()
                 if any(old in i.contexts for s in Section for i in doc.items(s))]
        if users:
            await self.change_each({a: (lambda d: model.rename_context(d, old, new)) for a in users})
        self.save_context_order([new if n == old else n for n in names if new or n != old])
        if self.context_filter == old:
            self.context_filter = new
            await self.render_lists()
        self.notify(f"Renamed {old} to {new}." if new else f"Deleted {old}.")

    async def delete_context(self, name: str) -> None:
        await self.rename_context(name, None)

    def action_manage_contexts(self) -> None:
        self.push_screen(ContextsScreen())

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

    def project_counts(self, item: model.Item) -> str:
        doc = self.docs.get(item.area)
        if not doc:
            return ""
        counts = [(len([i for i in doc.items(s) if i.project == item.project and not i.done]), label)
                  for s, label in ((Section.NEXT, "next"), (Section.WAITING, "waiting"), (Section.SOMEDAY, "someday"))]
        return " · ".join(f"{n} {label}" for n, label in counts if n) or "no tasks"

    def project_title(self, item: model.Item) -> str:
        """A project's short name: its note's first "# " heading, else its own line in Projects
        when that is short, else its slug in words."""
        key = (item.area, "title", item.project)
        if key not in self._tag_notes:
            title = None
            if note := self.tag_note(item, "project", item.project):
                path = self.areas[item.area].file.parent / f"{note}.md"
                title = next((ln[2:].strip() for ln in path.read_text().splitlines() if ln.startswith("# ")), None)
            if not title:  # no note heading: the project's own line, unless it is a long description
                doc = self.docs.get(item.area)
                line = next((p for p in (doc.items(Section.PROJECTS) if doc else [])
                             if p.project == item.project and p.raw is None), None)
                text = model.split_trailing_links(line.text)[0] if line else ""
                title = text if 0 < len(text) <= 40 else None
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
        if self.project_view and not self.marked:
            return await self.close_project()
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
        item = self.selected()
        if self.current is Section.PROJECTS and not self.project_view and item.project:
            return await self.open_project(item)
        if self.section_of(item) in (Section.IN, Section.SOMEDAY):
            self.action_clarify()
        else:
            self.action_edit()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_command_palette(self) -> None:
        if not isinstance(self.screen, PaletteScreen):
            self.push_screen(PaletteScreen(), callback=self.palette_chosen)

    async def palette_chosen(self, choice) -> None:
        if not choice:
            return
        kind, value = choice
        if kind == "action":
            await self.run_action(value)
        else:
            await self.jump_to(*value)

    async def jump_to(self, section: Section, line: str) -> None:
        """Open a list with the given item highlighted (used by / search)."""
        await self.action_go(self.tab_order().index(section))
        if (row := self.row_of(line)) is not None:
            self.query_one("#items", ItemList).highlighted = row

    @work(exclusive=True)
    async def action_edit(self) -> None:
        if not (item := self.selected()):
            return
        section = self.section_of(item)
        locate, prefix = self.on_disk(section, item), item.line()[:6]
        body = await self.text("Edit (tags at the end: @context +project waiting:who)",
                               item.line()[6:] if item.raw is None else item.line())
        if not body:
            return
        new = store.parse_item(prefix + body if item.raw is None else body)

        def replace(d):
            items = d.items(section)
            if (old := locate(d)) is not None:
                items[next(i for i, x in enumerate(items) if x is old)] = new
        await self.change(replace, item.area)
        self.toast(f"Edited “{new.text}”")

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
