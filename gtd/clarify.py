"""The clarify card: one screen for Allen's processing questions.

The item is shown once at the top. Every question is a row (Type, Action or Outcome and
Tasks, Project, How, Context or Who, Dates); answered rows show ✓ and their value, the
active row ▸ opens its picker below, and later rows show —. A one-action item's action is
filled in from its own text, so the card never asks for it twice (e rephrases it).

The same card re-clarifies an existing item (every row prefilled from it), and in
single-row mode it edits one field (m project, l context, d dates, p waiting for).

Pure functions (next_step, card_rows, prefill_from_item, row_value) hold the flow, so the
tests can check it without a terminal; ClarifyScreen only draws it and takes keys.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from . import dates, model
from .datescreen import DatePicker
from .model import Section

ROW_NAMES = {"what": "Type", "outcome": "Outcome", "tasks": "Tasks", "action": "Action", "project": "Project",
             "how": "How", "context": "Context", "who": "Who", "dates": "Dates"}
TYPES = ["One action", "A project (more than one step)", "Someday/Maybe (incubate)", "Trash",
         "Reference: file it in Obsidian"]
TYPE_SHORT = {1: "One action", 2: "A project", 3: "Someday/Maybe", 4: "Trash", 5: "Reference"}
HOWS = ["Defer it (Next Actions)", "Do it now (under two minutes)", "Delegate it (Waiting For)"]
HOW_CODES = ["defer", "now", "delegate"]
HOW_SHORT = {"defer": "Defer", "now": "Do it now", "delegate": "Delegate"}
OPTION_STEPS = {"what", "project", "how", "context"}


# The flow, as pure functions

def next_step(step: str, a: dict, has_projects: bool) -> str:
    """The row after `step`, given the answers so far; "commit" when the card is done."""
    if step == "what":
        if not a.get("actionable", True):
            return "commit"
        if a.get("kind") == "project":
            return "outcome"
        return "project" if has_projects and not a.get("locked_project") else "how"
    if step == "outcome":
        return "tasks"
    if step == "tasks":
        return "how"
    if step == "action":
        return "project" if a.get("kind") == "single" and has_projects and not a.get("locked_project") else "how"
    if step == "project":
        return "how"
    if step == "how":
        return {"defer": "context", "now": "commit", "delegate": "who"}[a["how"]]
    if step in ("context", "who"):
        return "dates"
    return "commit"


def card_rows(a: dict, start: str, has_projects: bool) -> list[str]:
    """Every row the card shows for the answers so far; unanswered choices are assumed to
    take the default branch (one action, deferred)."""
    if start == "what":
        rows = ["what"]
        if not a.get("actionable", True):
            return rows
        if a.get("kind") == "project":
            rows += ["outcome", "tasks"]
        else:
            rows += ["action"] + (["project"] if has_projects and not a.get("locked_project") else [])
    elif start == "outcome":
        rows = ["outcome", "tasks"]
    elif start == "tasks":
        rows = ["tasks"]
    else:
        rows = ["action"]
    rows.append("how")
    if a.get("how") == "now":
        return rows
    return rows + (["who", "dates"] if a.get("how") == "delegate" else ["context", "dates"])


def prefill_from_item(item: model.Item, section: Section) -> dict:
    """Answers that reproduce an already-clarified item, so re-clarifying changes only
    what Arjun changes."""
    a = {"choice": 3 if section is Section.SOMEDAY else 1, "actionable": section is not Section.SOMEDAY,
         "kind": "single", "action": item.text,
         "existing": item.project, "context": item.context, "who": item.waiting,
         "due": item.due, "start": item.start}
    if section is Section.SOMEDAY:
        a["dest"] = Section.SOMEDAY
    if section is Section.WAITING:
        a["how"] = "delegate"
    elif section in (Section.NEXT, Section.IN):
        a["how"] = "defer" if section is Section.NEXT else None
    return {k: v for k, v in a.items() if v is not None}


def row_value(step: str, a: dict, project_titles: dict[str, str]) -> str | None:
    """What an answered row shows, or None while it is unanswered."""
    if step == "what":
        return TYPE_SHORT.get(a.get("choice"))
    if step in ("outcome", "action", "who"):
        return a.get(step)
    if step == "tasks":
        tasks = a.get("tasks") or []
        return None if not tasks else tasks[0] + (f"  (+{len(tasks) - 1} more)" if len(tasks) > 1 else "")
    if step == "project":
        if "existing" not in a and "new_project" not in a:
            return None
        if a.get("new_project"):
            return f"{a['new_project']} (new)"
        return project_titles.get(a.get("existing"), a.get("existing")) if a.get("existing") else "No project"
    if step == "how":
        return HOW_SHORT.get(a.get("how"))
    if step == "context":
        return a.get("context")
    if step == "dates":
        if "dates_set" not in a:
            return None
        due, start = a.get("due"), a.get("start")
        if not due and not start:
            return "No dates"
        return " · ".join(p for p in (due and f"due {dates.fmt(due)}", start and f"start {dates.fmt(start)}") if p)
    return None


# The card

class TaskInput(Input):
    """The Tasks row's input: Tab saves the task and clears for another, inside the
    input's own key queue so fast typing never overtakes the save."""

    async def _on_key(self, event) -> None:
        if event.key == "tab":
            event.stop()
            event.prevent_default()
            await self.screen.add_task(keep_going=True)
        else:
            await super()._on_key(event)


class ClarifyScreen(ModalScreen):
    """Dismisses with the answers dict, or None when Arjun backs out of the first row."""

    BINDINGS = [Binding("escape", "back", "Back"),
                Binding("j", "cursor(1)", show=False), Binding("k", "cursor(-1)", show=False),
                Binding("e", "rephrase", "Rephrase", show=False),
                Binding("space", "toggle", "Toggle", show=False, priority=True)]

    def __init__(self, item: model.Item, *, start: str = "what", preset: dict | None = None,
                 projects: list[tuple[str, str]] | None = None, contexts: list[str] | None = None,
                 today: date | None = None, progress: tuple[int, int] | None = None,
                 area: str | None = None, area_colour: str = "", source: str | None = None,
                 fields: list[str] | None = None, date_state: Path | None = None) -> None:
        super().__init__()
        self.item, self.start = item, start
        self.a: dict = dict(preset or {})
        self.projects = projects or []                       # (title, slug), in the item's vault
        self.titles = {slug: title for title, slug in self.projects}
        self.contexts = list(contexts or [])
        self.today = today or date.today()
        self.progress, self.area, self.area_colour, self.source = progress, area, area_colour, source
        self.fields = fields                                  # single-row mode when set
        self.date_state = date_state
        self.step = fields[0] if fields else start
        self.history: list[str] = []
        self.sub: str | None = None                           # "new_project" or "other_context" text entry

    # Layout

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog card"):
            yield Static(self.title_text(), id="card-title")
            yield Static(self.item_text(), id="card-item")
            yield Static(id="card-rows")
            yield Vertical(id="card-picker")
            yield Static(id="card-hint", classes="hint")

    async def on_mount(self) -> None:
        await self.show_step()

    def title_text(self) -> Content:
        parts: list = [("Edit" if self.fields else "Clarify", "bold")]
        if self.progress:
            parts.append((f" · item {self.progress[0]} of {self.progress[1]}", "dim"))
        if self.area:
            parts += [(" · ", "dim"), (f" {self.area.upper()} ", f"bold #212733 on {self.area_colour}"
                                       if self.area_colour else "bold")]
        return Content.assemble(*parts)

    def item_text(self) -> Content:
        parts: list = []
        for text, target in model.link_segments(self.item.text):
            parts.append((text, "bold underline" if target else "bold"))
        if self.source:
            parts.append((f"   via {self.source}", "dim italic"))
        return Content.assemble(*parts)

    def rows(self) -> list[str]:
        if self.fields:
            return list(self.fields)
        return card_rows(self.a, self.start, bool(self.projects))

    ROW_ROOM = 54  # characters a row's value may take before it is cut with …

    def draw_rows(self) -> None:
        lines: list = []
        rows = self.rows()
        here = rows.index(self.step) if self.step in rows else len(rows)
        for n, step in enumerate(rows):
            value = row_value(step, self.a, self.titles)
            if value and step in ("action", "outcome") and value == self.item.text:
                value = "as written above"                    # never repeat the item on the card
            room = self.ROW_ROOM - (13 if step == "action" and self.a.get("kind") == "single" else 0)
            if value and len(value) > room:                   # one line per row
                value = value[:room - 1].rstrip() + "…"
            if n == here:
                lines += [("▸ ", "bold"), (f"{ROW_NAMES[step]:<9}", "bold")]
                lines.append((value or "", "") if value else ("", ""))
            elif n < here and value is not None:
                lines += [("✓ ", "#4ade80"), (f"{ROW_NAMES[step]:<9}", "dim"), (value, "")]
                if step == "action" and self.a.get("kind") == "single":
                    lines.append(("   e rephrase", "dim italic"))
            else:
                lines += [("  ", ""), (f"{ROW_NAMES[step]:<9}", "dim"), (value or "—", "dim")]
            lines.append("\n" if n < len(rows) - 1 else "")
        self.query_one("#card-rows", Static).update(Content.assemble(*lines))

    def hint(self) -> str:
        if self.sub:
            return "Enter confirm · Esc back"
        return {"tasks": "Tab add another · Enter done · Esc back",
                "dates": "↑↓ pick · Enter take · Tab due/start · Esc back",
                "context": "1-9 pick · Space toggle · Enter confirm · e edit list · Esc back"}.get(
            self.step, "1-9 pick · j k move · Enter take · Esc back" if self.step in OPTION_STEPS
            else "Enter confirm · Esc back")

    # Pickers

    async def show_step(self) -> None:
        """Draw the rows and mount the active row's picker, then focus it (after it has
        mounted, so the first keystroke never goes astray)."""
        self.draw_rows()
        picker = self.query_one("#card-picker", Vertical)
        await picker.remove_children()
        await picker.mount(self.make_picker())
        self.query_one("#card-hint", Static).update(self.hint())
        self.focus_picker()

    def focus_picker(self) -> None:
        for kind in (Input, OptionList):
            found = self.query_one("#card-picker").query(kind)
            if found:
                found.first().focus()
                return

    def options(self, labels: list[str], current: int | None = None) -> OptionList:
        widget = OptionList(*(Option(Content(f"{n}  {label}"), id=str(n)) for n, label in enumerate(labels, 1)))
        widget.highlighted = (current - 1) if current else 0
        return widget

    def make_picker(self):
        step, a = self.step, self.a
        if self.sub == "new_project":
            return Input(placeholder="Project: what does done look like?", id="sub")
        if self.sub == "other_context":
            return Input(value=(a.get("context") or "").lstrip("@"), placeholder="Context name", id="sub")
        if step == "what":
            return self.options(TYPES, a.get("choice"))
        if step == "project":
            labels = ["No project"] + [title for title, _ in self.projects] + ["New project…"]
            slugs = [slug for _, slug in self.projects]
            current = 1 if not a.get("existing") else slugs.index(a["existing"]) + 2 if a["existing"] in slugs else 1
            return self.options(labels, current)
        if step == "how":
            return self.options(HOWS, HOW_CODES.index(a["how"]) + 1 if a.get("how") else 1)
        if step == "context":
            chosen = (a.get("context") or "").split()
            choices = self.contexts + [c for c in chosen if c not in self.contexts]
            self.contexts = choices
            widget = OptionList(*(Option(self.context_label(n, chosen), id=str(n)) for n in range(len(choices) + 1)))
            widget.highlighted = 0
            self.toggled = list(chosen)
            return widget
        if step == "dates":
            return DatePicker(self.today, a.get("due"), a.get("start"), self.date_state)
        if step == "tasks":
            return Vertical(Static(self.tasks_list(), id="tasks-list"),
                            TaskInput(placeholder="A task for this project", id="task-input"), id="tasks-box")
        value = {"outcome": a.get("outcome", self.item.text), "action": a.get("action", self.item.text),
                 "who": a.get("who", "")}[step]
        placeholder = {"outcome": "What does done look like?", "action": "The next physical, visible action",
                       "who": "Waiting for whom?"}[step]
        return Input(value=value, placeholder=placeholder, id="text")

    def context_label(self, n: int, chosen: list[str]) -> Content:
        if n >= len(self.contexts):
            return Content(f"{n + 1}  Other…")
        c = self.contexts[n]
        return Content(f"{n + 1}  {'[x]' if c in chosen else '[ ]'} {c}")

    def tasks_list(self) -> Content:
        tasks = self.a.get("tasks") or []
        if not tasks:
            return Content.styled("The first task is the project's next action.", "dim italic")
        return Content("\n".join(f"{'▸' if n == 0 else '·'} {t}" for n, t in enumerate(tasks)))

    # Answers

    async def answer(self, **values) -> None:
        self.a.update(values)
        if self.fields:
            return self.dismiss(self.a)
        nxt = next_step(self.step, self.a, bool(self.projects))
        if nxt == "commit":
            return self.dismiss(self.a)
        self.history.append(self.step)
        self.step = nxt
        await self.show_step()

    async def choose(self, n: int) -> None:
        step = self.step
        if step == "what":
            single = n == 1
            values = {"choice": n, "actionable": n <= 2, "kind": "project" if n == 2 else "single",
                      "dest": Section.SOMEDAY if n == 3 else None}
            if single:
                values["action"] = self.a.get("action") or self.item.text  # filled from the item, never asked twice
            return await self.answer(**values)
        if step == "project":
            if n == len(self.projects) + 2:                   # New project…
                self.sub = "new_project"
                return await self.show_step()
            return await self.answer(existing=None if n == 1 else self.projects[n - 2][1], new_project=None)
        if step == "how":
            return await self.answer(how=HOW_CODES[n - 1])
        if step == "context":
            if n == len(self.contexts) + 1:
                self.sub = "other_context"
                return await self.show_step()
            return await self.answer(context=self.contexts[n - 1])

    async def on_key(self, event) -> None:
        if self.step in OPTION_STEPS and not self.sub and event.character and event.character.isdigit():
            n = int(event.character)
            limit = {"what": len(TYPES), "how": len(HOWS), "project": len(self.projects) + 2,
                     "context": len(self.contexts) + 1}[self.step]
            if 1 <= n <= limit:
                event.stop()
                await self.choose(n)

    @on(OptionList.OptionSelected)
    async def picked(self, event: OptionList.OptionSelected) -> None:
        if self.step not in OPTION_STEPS or self.sub:
            return
        event.stop()
        n = int(event.option.id) + (1 if self.step == "context" else 0)
        if self.step == "context" and self.toggled:
            return await self.answer(context=" ".join(c for c in self.contexts if c in self.toggled))
        await self.choose(n)

    @on(Input.Submitted)
    async def submitted(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if self.sub == "new_project":
            if text:
                self.sub = None
                await self.answer(existing=None, new_project=text)
            return
        if self.sub == "other_context":
            if text:
                self.sub = None
                await self.answer(context=f"@{text.lstrip('@').replace(' ', '-')}")
            return
        if self.step == "tasks":
            return await self.add_task(keep_going=False)
        if not text and self.step != "who":
            return
        if self.step == "who" and not text:
            return
        await self.answer(**{self.step: text})

    async def add_task(self, keep_going: bool) -> None:
        box = self.query_one("#task-input", Input)
        text = box.value.strip()
        tasks = list(self.a.get("tasks") or [])
        if text:
            tasks.append(text)
            self.a["tasks"] = tasks
            box.value = ""
            self.query_one("#tasks-list", Static).update(self.tasks_list())
            self.draw_rows()
        if not keep_going and tasks:
            await self.answer(tasks=tasks, action=tasks[0], extra_tasks=tasks[1:])

    @on(DatePicker.Done)
    async def dated(self, event: DatePicker.Done) -> None:
        event.stop()
        await self.answer(due=event.due, start=event.start, dates_set=True)

    def action_toggle(self) -> None:
        if self.step != "context" or self.sub:
            return
        options = self.query_one("#card-picker").query_one(OptionList)
        n = options.highlighted
        if n is None or n >= len(self.contexts):
            return
        c = self.contexts[n]
        self.toggled = [x for x in self.toggled if x != c] if c in self.toggled else self.toggled + [c]
        options.replace_option_prompt_at_index(n, self.context_label(n, self.toggled))

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "toggle":   # Space only toggles contexts; elsewhere it types a space
            return self.step == "context" and not self.sub
        if action in ("cursor", "rephrase"):
            return not isinstance(self.focused, Input)
        return True

    def action_cursor(self, step: int) -> None:
        found = self.query_one("#card-picker").query(OptionList)
        if found:
            found.first().action_cursor_down() if step > 0 else found.first().action_cursor_up()

    async def action_rephrase(self) -> None:
        """e: back to the Action row to reword a one-action item's action; on the Context
        row, the contexts editor instead."""
        if self.step == "context" and not self.sub and hasattr(self.app, "action_manage_contexts"):
            from .app import ContextsScreen
            return self.app.push_screen(ContextsScreen(), callback=self.contexts_edited)
        if self.fields or self.a.get("kind") != "single" or "action" not in self.rows():
            return
        if self.step != "action":
            self.history.append(self.step)
        self.step = "action"
        await self.show_step()

    async def contexts_edited(self, _) -> None:
        self.contexts = self.app.context_choices()
        await self.show_step()

    async def action_back(self) -> None:
        if self.sub:
            self.sub = None
            return await self.show_step()
        if not self.history:
            return self.dismiss(None)
        self.step = self.history.pop()
        await self.show_step()
