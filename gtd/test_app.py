"""Drive the TUI headlessly with Textual's Pilot and check the file on disk."""
import asyncio
from datetime import date

from gtd import store
from gtd.app import GTDApp
from gtd import model
from gtd.model import Doc, Item, Section

TODAY = date(2026, 9, 29)


def run(path, *keys, text=None):
    """Press keys in the app; `text` entries are typed into the current prompt then Enter."""
    async def go():
        app = GTDApp(path, today=TODAY)
        async with app.run_test() as pilot:
            await pilot.pause()
            for key in keys:
                if isinstance(key, tuple):  # ("type", "some words")
                    await pilot.press(*key[1], "enter")
                else:
                    await pilot.press(key)
                await pilot.pause()
            return app
    return asyncio.run(go())


def T(words):
    return ("type", words)


def test_capture_writes_to_in(tmp_path):
    path = tmp_path / "gtd.md"
    run(path, "a", T("Plan offsite"))
    assert [i.text for i in store.load(path).items(Section.IN)] == ["Plan offsite"]


def test_clarify_three_branches(tmp_path):
    path = tmp_path / "gtd.md"
    for text in ("Old flyer", "Reply to Jen", "Budget offsite"):
        run(path, "a", T(text))
    run(path, "c",
        # Old flyer: trash
        "4",
        # Reply to Jen: one action (default text), do it now
        "1", T(""), "2",
        # Budget offsite: project, next action deferred to @computer
        "2", T(""), T("Draft agenda"), "1", "2", "enter")   # enter: no dates
    doc = store.load(path)
    assert doc.items(Section.IN) == []
    assert [i.text for i in doc.items(Section.DONE)] == ["Reply to Jen"]
    [project] = doc.items(Section.PROJECTS)
    [action] = doc.items(Section.NEXT)
    assert (action.text, action.context, action.project) == ("Draft agenda", "@computer", project.project)


def test_delegate_goes_to_waiting_for(tmp_path):
    path = tmp_path / "gtd.md"
    run(path, "a", T("Board brief"))
    run(path, "c", "1", T(""), "3", T("Mike"), "enter")
    [waiting] = store.load(path).items(Section.WAITING)
    assert (waiting.text, waiting.waiting) == ("Board brief", "mike")


def test_engage_filters_by_context(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## Next Actions\n"
                    "- [ ] Call Fred @calls\n- [ ] Draft memo @computer\n")
    app = run(path, "f", "2")  # 1 All, 2 @calls, 3 @computer
    assert app.context_filter == "@calls"
    assert [i.text for i in app.shown] == ["Call Fred"]


def test_complete_highlighted_item(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Buy stamps\n")
    run(path, "down", "space")
    doc = store.load(path)
    assert doc.items(Section.IN) == []
    assert doc.items(Section.DONE)[0].done_on == TODAY


def test_edit_rewrites_line_and_tags(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Buy stamps\n")
    run(path, "down", "e", "ctrl+u", T("Buy stamps @errands"))
    [item] = store.load(path).items(Section.IN)
    assert (item.text, item.context) == ("Buy stamps", "@errands")


def test_move_to_someday(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Learn Italian\n")
    run(path, "down", "m", "4")  # targets exclude In: Next, Projects, Waiting, Someday
    doc = store.load(path)
    assert [i.text for i in doc.items(Section.SOMEDAY)] == ["Learn Italian"]


def test_shortcuts_are_ignored_while_a_question_is_open(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Buy stamps\n")
    run(path, "down", "m", "space", "d", "escape")  # move dialog open; space and d are not its answers
    assert [i.text for i in store.load(path).items(Section.IN)] == ["Buy stamps"]


def test_clarify_back_and_forth_changes_the_answer(tmp_path):
    path = tmp_path / "gtd.md"
    run(path, "a", T("Budget offsite"))
    run(path, "c",
        "2", T(""),          # a project, outcome accepted
        "escape", "escape",  # back from the action prompt to outcome, then to "what is it?"
        "1", T(""),          # change mind: one action, keep the default text
        "1", "0",            # defer, then back from the context screen
        "2")                 # do it now instead
    doc = store.load(path)
    assert doc.items(Section.PROJECTS) == [] and doc.items(Section.NEXT) == []
    assert [i.text for i in doc.items(Section.DONE)] == ["Budget offsite"]


def test_back_from_first_clarify_screen_leaves_in_untouched(tmp_path):
    path = tmp_path / "gtd.md"
    run(path, "a", T("Buy stamps"))
    run(path, "c", "0")
    assert [i.text for i in store.load(path).items(Section.IN)] == ["Buy stamps"]


def test_tab_steps_between_lists(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## Waiting For\n- [ ] Brief waiting:mike\n")
    app = run(path, "tab", "tab", "tab", "space")  # In -> Next -> Projects -> Waiting, first item highlighted
    assert app.current is Section.WAITING
    assert store.load(path).items(Section.WAITING) == []
    assert run(path, *["tab"] * 6).current is Section.IN  # wraps from Done back to In


def test_number_keys_do_nothing_on_the_main_screen(tmp_path):
    assert run(tmp_path / "gtd.md", "4").current is Section.IN

def test_capture_highlights_the_new_item(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Old item\n")
    run(path, "tab", "a", T("New item"), "space")  # capture from another list, then complete the highlight
    doc = store.load(path)
    assert [i.text for i in doc.items(Section.IN)] == ["Old item"]
    assert [i.text for i in doc.items(Section.DONE)] == ["New item"]


def test_c_after_capture_clarifies_the_new_item_first(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Old item\n")
    run(path, "a", T("New item"), "c", "4", "0")  # trash the new item, then stop on the old one
    assert [i.text for i in store.load(path).items(Section.IN)] == ["Old item"]


def test_c_continues_through_rest_of_in_from_the_top(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] First\n- [ ] Second\n- [ ] Third\n")
    app = run(path, "down", "down", "down", "c",
              "4",   # Third (highlighted): trash
              "4")   # then First from the top: trash
    assert [i.text for i in store.load(path).items(Section.IN)] == ["Second"]


def test_tab_keeps_capturing_enter_closes(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Old item\n")
    run(path, "a", *"One", "tab", *"Two", "tab", T("Three"),  # Enter on the last one closes
        "c", "4", "0")                                               # clarify starts on "One": trash it, stop
    assert [i.text for i in store.load(path).items(Section.IN)] == ["Old item", "Two", "Three"]


def test_escape_closes_capture_and_keeps_what_was_saved(tmp_path):
    path = tmp_path / "gtd.md"
    run(path, "a", *"One", "tab", *"half typed", "escape")
    assert [i.text for i in store.load(path).items(Section.IN)] == ["One"]


def test_up_down_move_within_a_list(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] First\n- [ ] Second\n- [ ] Third\n")
    run(path, "down", "down", "down", "up", "space")  # down to Third, back up to Second, complete it
    assert [i.text for i in store.load(path).items(Section.IN)] == ["First", "Third"]


def test_up_down_pick_options_in_pop_ups(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Learn Italian\n")
    run(path, "down", "m", "down", "down", "down", "up", "enter")  # move: highlight option 3 (Waiting For)
    assert [i.text for i in store.load(path).items(Section.WAITING)] == ["Learn Italian"]


def test_rows_show_heavy_checkboxes(tmp_path):
    app = GTDApp(tmp_path / "gtd.md", today=TODAY)
    open_row = app.describe(store.parse_item("- [ ] Call Fred @calls +offsite"), set())
    assert open_row.plain == " [ ]  Call Fred  Offsite  @calls"  # mark bar, action, project chip (slug in words), context
    app.current = Section.DONE
    done_row = app.describe(store.parse_item("- [x] Book room done:2026-09-28"), set())
    assert done_row.plain.startswith(" [✔]  Book room")


def test_space_ticks_the_highlighted_item(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Buy stamps\n- [ ] Post letter\n")
    run(path, "down", "down", "space")
    doc = store.load(path)
    assert [i.text for i in doc.items(Section.IN)] == ["Buy stamps"]
    assert [i.text for i in doc.items(Section.DONE)] == ["Post letter"]


TWO_IN = "<!-- gtd v1 -->\n\n## In\n- [ ] Alpha\n- [ ] Beta\n"


def test_tick_moves_item_below_the_list_and_into_done(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    app = run(path, "down", "space")
    assert [i.text for i in store.load(path).items(Section.DONE)] == ["Alpha"]
    assert [i.text for i in app.shown] == ["Beta"]
    assert [i.text for i, _ in app.ticked[Section.IN]] == ["Alpha"]


def test_switching_lists_clears_the_ticked_section(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    assert run(path, "down", "space", *["tab"] * 6).ticked.get(Section.IN, []) == []
    assert [i.text for i in store.load(path).items(Section.DONE)] == ["Alpha"]  # still done


def test_z_undoes_step_by_step(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    app = run(path, "down", "backspace", "space", "z")  # delete Alpha, tick Beta, undo the tick
    doc = store.load(path)
    assert [i.text for i in doc.items(Section.IN)] == ["Beta"] and doc.items(Section.DONE) == []
    assert app.ticked.get(Section.IN, []) == []
    run(path, "z")  # a new session has nothing to undo; the file is untouched
    assert [i.text for i in store.load(path).items(Section.IN)] == ["Beta"]


def test_undo_refuses_when_file_changed_outside(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)

    async def go():
        app = GTDApp(path, today=TODAY)
        async with app.run_test() as pilot:
            await pilot.press("down", "backspace")
            await pilot.pause()
            path.write_text(path.read_text().replace("## Someday/Maybe\n", "## Someday/Maybe\n- [ ] Synced in\n"))
            await pilot.press("z")
            await pilot.pause()
    asyncio.run(go())
    text = path.read_text()
    assert "Synced in" in text and "Alpha" not in text


def test_outside_edits_appear_without_a_reload_key(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Alpha\n")

    async def go():
        app = GTDApp(path, today=TODAY)
        async with app.run_test() as pilot:
            await pilot.press("down")
            path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Alpha\n- [ ] From Obsidian\n")
            await app.watch_file()
            await pilot.pause()
            return [i.text for i in app.shown], app.selected().text
    assert asyncio.run(go()) == (["Alpha", "From Obsidian"], "Alpha")  # highlight kept


def test_lists_show_as_tabs_across_the_top(tmp_path):
    app = GTDApp(tmp_path / "gtd.md", today=TODAY)
    assert app.tabs_text().plain == " In 0 │ Next 0 │ Projects 0 │ Waiting 0 │ Someday 0 │ Done 0 "


def test_left_right_switch_lists(tmp_path):
    assert run(tmp_path / "gtd.md", "right").current is Section.NEXT
    assert run(tmp_path / "gtd.md", "left").current is Section.DONE


def test_ctrl_enter_is_not_a_capture_key(tmp_path):
    path = tmp_path / "gtd.md"
    run(path, "a", *"One", "ctrl+enter", *"Two", "enter")
    assert [i.text for i in store.load(path).items(Section.IN)] == ["OneTwo"]


def test_weekly_review_is_a_tour_of_the_lists(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## Projects\n- [ ] Ship search v2 +ship-search-v2\n")

    async def go():
        app = GTDApp(path, today=TODAY)
        async with app.run_test() as pilot:
            seen = []
            await pilot.press("w"); await pilot.pause()
            for _ in range(3):  # In -> Next Actions -> Waiting For -> Projects
                seen.append((app.current, app.status_text.split(" · ")[0]))
                await pilot.press("n"); await pilot.pause()
            seen.append((app.current, app.status_text.split(" · ")[0]))
            # c on the stalled project asks for its next action; defer it to @computer
            await pilot.press("c", *"Write spec", "enter", "1", "2", "enter"); await pilot.pause()
            assert app.current is Section.PROJECTS
            await pilot.press("n", "n"); await pilot.pause()  # Someday, then finish
            return seen, app.review_step, app.status_text
    seen, step, status = asyncio.run(go())
    assert seen == [(Section.IN, "Review 1/5"), (Section.NEXT, "Review 2/5"),
                    (Section.WAITING, "Review 3/5"), (Section.PROJECTS, "Review 4/5")]
    assert step is None and "last review today" in status
    doc = store.load(path)
    assert doc.last_review == TODAY
    assert doc.items(Section.NEXT)[0].project == "ship-search-v2"


def test_escape_stops_review_without_recording(tmp_path):
    path = tmp_path / "gtd.md"
    app = run(path, "w", "n", "escape")
    assert app.review_step is None
    assert store.load(path).last_review is None


def test_enter_clarifies_an_in_item_and_edits_elsewhere(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    run(path, "down", "down", "enter", "4", "0")        # Enter on Beta clarifies it: trash; then stop
    assert [i.text for i in store.load(path).items(Section.IN)] == ["Alpha"]
    path.write_text("<!-- gtd v1 -->\n\n## Next Actions\n- [ ] Call Fred @calls\n")
    run(path, "tab", "enter", "ctrl+u", T("Call Fred re brakes @calls"))
    assert store.load(path).items(Section.NEXT)[0].text == "Call Fred re brakes"


def test_c_on_someday_item_clarifies_it_into_a_project(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## Someday/Maybe\n- [ ] Learn Italian\n")
    app = run(path, *["tab"] * 4, "c", "2", T(""), T("Book a trial lesson"), "1", "1", "enter")
    doc = store.load(path)
    assert doc.items(Section.SOMEDAY) == []
    assert [p.text for p in doc.items(Section.PROJECTS)] == ["Learn Italian"]
    assert app.current is Section.SOMEDAY


def test_x_no_longer_ticks(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    run(path, "down", "x")
    assert store.load(path).items(Section.DONE) == []



def test_u_i_no_longer_switch_lists_and_shift_tab_goes_back(tmp_path):
    assert run(tmp_path / "gtd.md", "i", "u").current is Section.IN
    assert run(tmp_path / "gtd.md", "shift+tab").current is Section.DONE


def test_j_k_and_left_no_longer_do_anything(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    run(path, "j", "j", "space")               # j is not "down": nothing highlighted, nothing ticked
    assert store.load(path).items(Section.DONE) == []
    run(path, "down", "m", "left", "4")        # left is not "back" in a pop-up: 4 still picks Someday
    assert [i.text for i in store.load(path).items(Section.SOMEDAY)] == ["Alpha"]


def test_footer_speaks_allen(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 · last-review: 2026-09-20 -->\n\n## In\n- [ ] Idea\n\n"
                    "## Projects\n- [ ] Ship v2 +ship-v2\n")
    app = run(path)
    assert app.status_text == "In 1 to clarify · 1 project without a next action · last review 9 days ago"
    path.write_text("<!-- gtd v1 · last-review: 2026-09-29 -->\n")
    assert run(path).status_text == "In clear · every project has a next action · last review today"


def test_tab_counts_are_coloured(tmp_path):
    app = GTDApp(tmp_path / "gtd.md", today=TODAY)
    styles = {span.style for span in app.tabs_text().spans}
    assert any("#73d0ff" in str(style) for style in styles)


def test_slash_palette_runs_an_action_by_name(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    run(path, "down", "slash", *"delete", "enter")
    assert [i.text for i in store.load(path).items(Section.IN)] == ["Beta"]


def test_palette_lists_only_our_actions_with_keys(tmp_path):
    app = GTDApp(tmp_path / "gtd.md", today=TODAY)
    entries = {name: key for name, key, _ in app.palette_entries()}
    assert entries["Move highlighted item"] == "m" and entries["Clear ticked items"] == ""
    assert list(app.get_system_commands(None)) == []


def test_slash_types_normally_inside_capture(tmp_path):
    path = tmp_path / "gtd.md"
    run(path, "a", *"a", "slash", *"b", "enter")
    assert [i.text for i in store.load(path).items(Section.IN)] == ["a/b"]


def test_prune_on_demand_setting(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## Done\n- [x] Old thing done:2026-09-01\n")
    run(path, "slash", *"on demand", "enter")               # switch to prune on demand
    assert "prune: manual" in path.read_text()
    run(path, "a", T("New"))                                # a write no longer prunes
    assert "Old thing" in path.read_text()
    run(path, "slash", *"prune done now", "enter")          # pruning on demand
    assert "Old thing" not in path.read_text()


def test_clear_ticked_from_palette(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    assert run(path, "down", "space", "slash", *"clear ticked", "enter").ticked.get(Section.IN, []) == []


def test_space_on_a_ticked_row_unticks_it_back_into_place(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    app = run(path, "down", "space", "down", "space")  # tick Alpha; down skips the divider onto it; untick
    doc = store.load(path)
    assert [i.text for i in doc.items(Section.IN)] == ["Alpha", "Beta"] and doc.items(Section.DONE) == []
    assert app.last_toast == "Unticked “Alpha” · z to undo"


def test_space_on_done_reopens_where_the_line_says(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## Done\n- [x] Call Fred @calls done:2026-09-29\n")
    app = run(path, "shift+tab", "space")
    doc = store.load(path)
    assert [i.text for i in doc.items(Section.NEXT)] == ["Call Fred"] and doc.items(Section.DONE) == []
    assert app.last_toast == "Reopened “Call Fred” in Next Actions · z to undo"


def test_actions_toast_what_happened(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    assert run(path, "down", "space").last_toast == "Ticked “Alpha” · z to undo"
    path.write_text(TWO_IN)
    assert run(path, "down", "backspace").last_toast == "Deleted “Alpha” · z to undo"
    path.write_text(TWO_IN)
    assert run(path, "down", "slash", *"move", "enter", "4").last_toast == "Moved “Alpha” to Someday/Maybe · z to undo"
    path.write_text(TWO_IN)
    assert run(path, "c", "1", T(""), "1", "1", "enter").last_toast == "Deferred “Alpha” to @calls · z to undo"


THREE_IN = "<!-- gtd v1 -->\n\n## In\n- [ ] Alpha\n- [ ] Beta\n- [ ] Gamma\n"


def in_texts(path):
    return [i.text for i in store.load(path).items(Section.IN)]


def test_backspace_and_delete_delete_and_d_does_not(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(THREE_IN)
    run(path, "down", "backspace", "delete", "d")
    assert in_texts(path) == ["Gamma"]


def test_x_marks_rows_and_delete_removes_them_as_one_undoable_change(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(THREE_IN)
    app = run(path, "down", "x", "down", "down", "x", "backspace")
    assert in_texts(path) == ["Beta"]
    assert app.last_toast == "Deleted 2 items · z to undo"
    run(path, "down", "x", "escape", "backspace")  # Esc clears marks: only the highlighted row goes
    assert in_texts(path) == []
    path.write_text(THREE_IN)
    run(path, "down", "x", "down", "x", "backspace", "z")
    assert in_texts(path) == ["Alpha", "Beta", "Gamma"]


def test_home_end_and_paging_move_the_highlight(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(THREE_IN)
    run(path, "down", "end", "space")
    assert [i.text for i in store.load(path).items(Section.DONE)] == ["Gamma"]
    path.write_text(THREE_IN)
    run(path, "down", "end", "home", "space")
    assert [i.text for i in store.load(path).items(Section.DONE)] == ["Alpha"]
    path.write_text(THREE_IN)
    run(path, "down", "pagedown", "space")
    assert [i.text for i in store.load(path).items(Section.DONE)] == ["Gamma"]


def test_key_line_follows_the_list(tmp_path):
    path = tmp_path / "gtd.md"
    assert "⏎ clarify" in run(path).keys_text and "⌫ delete" in run(path).keys_text
    assert "space reopen" in run(path, "shift+tab").keys_text
    path.write_text(THREE_IN)
    assert run(path, "down", "x").keys_text.startswith("1 marked")


def test_slash_searches_items_and_jumps_to_them(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Alpha\n\n## Waiting For\n- [ ] Brief from Mike waiting:mike\n")
    app = run(path, "slash", *"brief", "enter", "space")
    assert app.current is Section.WAITING
    assert [i.text for i in store.load(path).items(Section.DONE)] == ["Brief from Mike"]


def test_question_mark_shows_every_key(tmp_path):
    async def go():
        app = GTDApp(tmp_path / "gtd.md", today=TODAY)
        async with app.run_test() as pilot:
            await pilot.press("question_mark"); await pilot.pause()
            shown = type(app.screen).__name__
            await pilot.press("escape"); await pilot.pause()
            return shown, type(app.screen).__name__
    from gtd.app import HelpScreen
    assert asyncio.run(go()) == ("HelpScreen", "Screen")


def test_long_rows_do_not_wrap():
    from gtd.app import GTDApp as A
    assert "text-wrap: nowrap" in A.CSS and "text-overflow: ellipsis" in A.CSS


from gtd import config


def two_areas(tmp_path, log=True):
    work, home = tmp_path / "work.md", tmp_path / "home.md"
    work.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Work thing\n")
    home.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Home thing\n\n## Next Actions\n"
                    "- [ ] Pay rent every:monthly due:2026-10-01\n")
    completions = tmp_path / "completions.md"
    completions.write_text("| Completed | Task | Was due | Recurrence |\n|---|---|---|---|\n")
    return {"work": config.Area("work", work),
            "home": config.Area("home", home, completions_log=completions if log else None)}


def run_areas(areas, *keys, capture="work"):
    async def go():
        app = GTDApp(areas, today=TODAY, capture=capture)
        async with app.run_test() as pilot:
            await pilot.pause()
            for key in keys:
                if isinstance(key, tuple):
                    await pilot.press(*key[1], "enter")
                else:
                    await pilot.press(key)
                await pilot.pause()
            return app
    return asyncio.run(go())


def texts(path, section=Section.IN):
    return [i.text for i in store.load(path).items(section)]


def test_both_areas_show_together_and_captures_go_to_work(tmp_path):
    areas = two_areas(tmp_path)
    app = run_areas(areas, "a", T("New idea"))
    assert [i.text for i in app.shown] == ["Work thing", "New idea", "Home thing"]
    assert texts(areas["work"].file) == ["Work thing", "New idea"]
    assert app.describe(app.shown[2], set()).plain.endswith("home")


def test_each_change_goes_to_its_own_file(tmp_path):
    areas = two_areas(tmp_path)
    run_areas(areas, "down", "down", "backspace")  # delete the home item
    assert texts(areas["home"].file) == [] and texts(areas["work"].file) == ["Work thing"]


def test_bulk_delete_across_areas_is_one_undo(tmp_path):
    areas = two_areas(tmp_path)
    run_areas(areas, "down", "x", "down", "x", "backspace")
    assert texts(areas["work"].file) == [] and texts(areas["home"].file) == []
    areas = two_areas(tmp_path)
    run_areas(areas, "down", "x", "down", "x", "backspace", "z")
    assert texts(areas["work"].file) == ["Work thing"] and texts(areas["home"].file) == ["Home thing"]


def test_recurring_tick_rolls_due_and_logs_completion_undoably(tmp_path):
    areas = two_areas(tmp_path)
    app = run_areas(areas, "tab", "down", "space")
    item = store.load(areas["home"].file).items(Section.NEXT)[0]
    assert item.due == date(2026, 11, 1) and not item.done
    assert "| 2026-09-29 | Pay rent | 2026-10-01 | monthly |" in areas["home"].completions_log.read_text()
    assert app.last_toast.startswith("Ticked “Pay rent” · next due Nov 1")
    areas = two_areas(tmp_path)
    run_areas(areas, "tab", "down", "space", "z")
    assert store.load(areas["home"].file).items(Section.NEXT)[0].due == date(2026, 10, 1)
    assert "Pay rent" not in areas["home"].completions_log.read_text()


def test_palette_narrows_to_one_area_and_flips_capture(tmp_path):
    areas = two_areas(tmp_path)
    app = run_areas(areas, "slash", *"home only", "enter")
    assert app.view_areas == ["home"] and [i.text for i in app.shown] == ["Home thing"]
    areas = two_areas(tmp_path)
    run_areas(areas, "slash", *"captures go to home", "enter", "a", T("Buy milk"))
    assert texts(areas["home"].file) == ["Home thing", "Buy milk"]


def test_proposals_are_dimmed_and_clearable(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Mine\n- [ ] Follow up with Ben src:granola://meeting/1 added:2026-09-29\n")
    app = GTDApp(path, today=TODAY)
    row = app.describe(store.load(path).items(Section.IN)[1], set())
    assert row.plain.startswith(" [ ]  Follow up with Ben  granola")
    run(path, "slash", *"clear proposals", "enter")
    assert texts(path) == ["Mine"]


MIXED_IN = ("<!-- gtd v1 -->\n\n## In\n- [ ] Proposal one src:process added:2026-09-29\n- [ ] My capture\n"
            "- [ ] Proposal two src:process added:2026-09-29\n- [ ] Another of mine\n")


def test_own_captures_sit_above_a_proposals_divider(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(MIXED_IN)
    app = run(path)
    assert [i.text for i in app.shown] == ["My capture", "Another of mine", "Proposal one", "Proposal two"]
    assert [kind for kind, _ in app.rows] == ["item", "item", "divider", "item", "item"]


def test_arrows_cross_the_proposals_divider(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(MIXED_IN)
    run(path, "down", "down", "down", "space")  # My capture, Another of mine, (divider skipped) Proposal one
    assert [i.text for i in store.load(path).items(Section.DONE)] == ["Proposal one"]


def test_clarify_takes_own_captures_first(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(MIXED_IN)
    run(path, "c", "4", "4", "0")  # trash the first two clarified, then stop
    assert texts(path) == ["Proposal one", "Proposal two"]


LINKED = ("<!-- gtd v1 -->\n\n## In\n- [ ] [[reference/home/dishwasher#Seal|Dishwasher]]: wipe the seal\n"
          "- [ ] Follow up with Ben src:granola://meeting/84c0 added:2026-09-29\n"
          "- [ ] Read [the memo](https://docs.example/memo) before [[projects/maps|Maps]] review\n")


def test_rows_show_link_labels_not_syntax(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(LINKED)
    app = GTDApp(path, today=TODAY)
    row = app.describe(store.load(path).items(Section.IN)[0], set())
    assert "Dishwasher: wipe the seal" in row.plain and "[[" not in row.plain


def opened_with(path, *keys):
    opened = []

    async def go():
        app = GTDApp(path, today=TODAY)
        app.opener = opened.append
        async with app.run_test() as pilot:
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
            return app
    app = asyncio.run(go())
    return opened, app


def test_o_opens_a_note_link_in_its_vault(tmp_path):
    vault = tmp_path / "homepalace"
    vault.mkdir()
    path = vault / "gtd.md"
    path.write_text(LINKED)
    opened, app = opened_with(path, "down", "o")
    assert opened == ["obsidian://open?vault=homepalace&file=reference%2Fhome%2Fdishwasher"]
    assert app.last_toast.startswith("Opened “Dishwasher”")


def test_o_opens_a_proposal_source(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(LINKED)
    opened, _ = opened_with(path, "down", "down", "down", "o")  # below the proposals divider
    assert opened == ["granola://meeting/84c0"]


def test_o_with_several_links_asks_which(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(LINKED)
    opened, _ = opened_with(path, "down", "down", "o", "2")
    assert opened == ["obsidian://open?vault=" + tmp_path.name + "&file=projects%2Fmaps"]


def test_clicking_a_link_label_opens_it_and_elsewhere_just_highlights(tmp_path):
    vault = tmp_path / "homepalace"
    vault.mkdir()
    path = vault / "gtd.md"
    path.write_text(LINKED)
    opened = []

    async def go():
        app = GTDApp(path, today=TODAY)
        app.opener = opened.append
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            await pilot.click("#items", offset=(8, 0))    # on "Dishwasher" in row 1
            await pilot.pause()
            await pilot.click("#items", offset=(40, 0))   # plain text in row 1
            await pilot.pause()
            return app.selected().text
    selected = asyncio.run(go())
    assert opened == ["obsidian://open?vault=homepalace&file=reference%2Fhome%2Fdishwasher"]
    assert selected.startswith("[[reference/home/dishwasher")


def test_obsidian_links_pull_the_window_to_the_current_workspace():
    from gtd import app as app_mod
    script = app_mod.follow_script("2: work")
    assert "app_id" in script and "obsidian" in script
    assert "move container to workspace '2: work', focus" in script.replace('"', "'")
    assert app_mod.follow_script(None) is None


def vault_with_notes(tmp_path):
    vault = tmp_path / "mindpalace"
    (vault / "projects" / "archived").mkdir(parents=True)
    (vault / "people" / "directs").mkdir(parents=True)
    (vault / "projects" / "maps.md").write_text("# Maps\n")
    (vault / "projects" / "archived" / "old-thing.md").write_text("# Old\n")
    (vault / "people" / "directs" / "maciej-zak.md").write_text("# Maciej\n")
    path = vault / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## Next Actions\n- [ ] Draft the brief @agenda-maciej +maps\n"
                    "- [ ] Close it out +old-thing\n- [ ] Unknown +nowhere\n\n## Waiting For\n"
                    "- [ ] Budget numbers waiting:maciej\n")
    return path


def test_project_and_person_tags_resolve_to_notes(tmp_path):
    path = vault_with_notes(tmp_path)
    app = GTDApp(path, today=TODAY)
    doc = store.load(path)
    for i in doc.items(Section.NEXT) + doc.items(Section.WAITING):
        i.area = "main"
    brief, old, unknown = doc.items(Section.NEXT)
    uri = "obsidian://open?vault=mindpalace&file="
    assert app.item_links(brief) == [("Maps", uri + "projects%2Fmaps"),          # project (its # heading), then person
                                     ("Maciej", uri + "people%2Fdirects%2Fmaciej-zak")]
    assert app.item_links(old) == [("Old", uri + "projects%2Farchived%2Fold-thing")]
    assert app.item_links(unknown) == []
    assert app.item_links(doc.items(Section.WAITING)[0]) == [("Maciej", uri + "people%2Fdirects%2Fmaciej-zak")]


def test_o_on_a_single_project_tag_opens_the_project(tmp_path):
    path = vault_with_notes(tmp_path)
    opened, _ = opened_with(path, "tab", "down", "o")   # Tab lands on row 1; down to row 2: +old-thing
    assert opened == ["obsidian://open?vault=mindpalace&file=projects%2Farchived%2Fold-thing"]


def click_run(path, *actions):
    async def go():
        app = GTDApp(path, today=TODAY)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            for act in actions:
                if isinstance(act, tuple):
                    await pilot.click(act[0], offset=act[1])
                else:
                    await pilot.press(act)
                await pilot.pause()
            return app
    return asyncio.run(go())


def test_clicking_a_tab_opens_that_list(tmp_path):
    app = click_run(tmp_path / "gtd.md", ("#tabs", (9, 0)))       # " In 0 │ Next …": column 9 is in "Next"
    assert app.current is Section.NEXT


def test_clicking_a_checkbox_ticks_and_clicking_again_unticks(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    click_run(path, ("#items", (2, 0)))                            # the [ ] of row 1
    assert [i.text for i in store.load(path).items(Section.DONE)] == ["Alpha"]
    path.write_text(TWO_IN)
    click_run(path, ("#items", (2, 0)), ("#items", (2, 2)))        # tick Alpha; its ticked row is row 3
    assert texts(path) == ["Alpha", "Beta"]


def test_shift_arrows_extend_the_selection(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(THREE_IN)
    assert len(run(path, "down", "shift+down", "shift+down").marked) == 3
    assert len(run(path, "down", "shift+down", "shift+up").marked) == 1   # back up: the range shrinks
    run(path, "down", "shift+down", "backspace")
    assert texts(path) == ["Gamma"]


def test_several_contexts_round_trip_and_split():
    item = store.parse_item("- [ ] Pick up the parcel @errands @calls +move added:2026-09-29")
    assert item.raw is None and item.contexts == ["@errands", "@calls"] and item.context == "@errands @calls"
    assert item.line() == "- [ ] Pick up the parcel @errands @calls +move added:2026-09-29"


def test_context_picker_number_picks_one_space_toggles_several(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN)
    run(path, "c", "1", T(""), "1", "2", "enter")                # number: @computer alone, instantly; no dates
    assert store.load(path).items(Section.NEXT)[0].context == "@computer"
    path.write_text(TWO_IN)
    run(path, "c", "1", T(""), "1", "space", "down", "space", "enter", "enter")   # toggle @calls and @computer
    assert store.load(path).items(Section.NEXT)[0].contexts == ["@calls", "@computer"]


def test_configured_contexts_come_first_then_ones_in_use(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(TWO_IN + "\n## Next Actions\n- [ ] Old @garden\n")
    app = GTDApp(path, today=TODAY, contexts=["@deep", "@calls"])
    assert app.context_choices() == ["@deep", "@calls", "@garden"]


def test_filter_matches_any_of_several_contexts(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## Next Actions\n- [ ] Both @calls @errands\n- [ ] Just @errands\n")
    app = run(path, "f", "2")   # 1 All, 2 @calls, 3 @errands
    assert [i.text for i in app.shown] == ["Both"]


PROJECTS_IN = ("<!-- gtd v1 -->\n\n## In\n- [ ] Draft agenda\n\n## Projects\n- [ ] Maps relaunch +maps\n"
               "- [ ] Q4 offsite plan +q4-offsite\n")


def test_clarify_asks_for_a_project_and_enter_skips(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(PROJECTS_IN)
    run(path, "c", "1", T(""), "enter", "1", "1", "enter")     # Enter: no project; defer to @calls; no dates
    assert store.load(path).items(Section.NEXT)[0].project is None
    path.write_text(PROJECTS_IN)
    run(path, "c", "1", T(""), "3", "1", "1", "enter")         # 3: Q4 offsite plan
    assert store.load(path).items(Section.NEXT)[0].project == "q4-offsite"


def test_config_reads_contexts(tmp_path):
    from gtd import config
    cfg = tmp_path / "config.toml"
    cfg.write_text('contexts = ["@deep", "@calls"]\n[areas.work]\nfile = "x.md"\n')
    assert config.contexts(cfg) == ["@deep", "@calls"]
    assert config.contexts(tmp_path / "missing.toml") == config.DEFAULT_CONTEXTS


START_FILE = ("<!-- gtd v1 -->\n\n## Next Actions\n- [ ] Clean the drum every:monthly start:2026-10-02 due:2026-10-05\n"
              "- [ ] Water plants start:2026-09-28\n- [ ] No start at all\n")


def test_start_tag_round_trips():
    line = "- [ ] Clean the drum @home every:monthly start:2026-10-02 due:2026-10-05"
    item = store.parse_item(line)
    assert item.raw is None and str(item.start) == "2026-10-02" and item.line() == line


def test_recurring_roll_moves_start_with_due():
    doc = Doc.empty()
    item = Item(text="Clean the drum", every="monthly", start=date(2026, 10, 2), due=date(2026, 10, 5))
    doc.items(Section.NEXT).append(item)
    model.complete(doc, item, TODAY)
    assert (item.start, item.due) == (date(2026, 11, 2), date(2026, 11, 5))


def test_tasks_starting_later_are_hidden_until_toggled(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(START_FILE)
    app = run(path, "tab")
    assert [i.text for i in app.shown] == ["Water plants", "No start at all"]
    assert "1 starting later" in app.status_text
    assert " Next 2 " in app.tabs_text().plain
    app = run(path, "tab", "s")
    assert [i.text for i in app.shown] == ["Clean the drum", "Water plants", "No start at all"]
    assert "starts Oct 2" in app.describe(app.shown[0], set()).plain


def test_links_move_to_the_end_of_the_text():
    assert model.links_last("[[ref/dishwasher#Seal|Dishwasher]]: wipe the seal") == \
        "Wipe the seal [[ref/dishwasher#Seal|Dishwasher]]"
    assert model.links_last("[[projects/london-relocation|London move]] W4: chase the movers",
                            project="london-relocation") == "W4: chase the movers"
    assert model.links_last("Read [the memo](https://m.example) before Maps review") == \
        "Read [the memo](https://m.example) before Maps review"          # mid-sentence links stay
    assert model.links_last("Plain action") == "Plain action"


def chip_vault(tmp_path):
    vault = tmp_path / "mindpalace"
    (vault / "projects").mkdir(parents=True)
    (vault / "people").mkdir()
    (vault / "projects" / "payments-strategy.md").write_text("# Payments Performance & GTM\n")
    (vault / "people" / "mike.md").write_text("# Mike\n")
    path = vault / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## Next Actions\n"
                    "- [ ] Wipe the seal [[ref/dishwasher|Dishwasher]] @home every:weekly\n"
                    "- [ ] Draft the deck @agenda-mike +payments-strategy src:granola://m/1 added:2026-09-29\n"
                    "\n## Projects\n- [ ] Payments Performance & GTM +payments-strategy\n")
    return path


def test_row_is_action_then_named_chips_in_a_fixed_order(tmp_path):
    path = chip_vault(tmp_path)
    app = GTDApp(path, today=TODAY)
    app.current = Section.NEXT
    wipe, deck = app.docs["main"].items(Section.NEXT)
    assert app.describe(wipe, set()).plain == " [ ]  Wipe the seal  Dishwasher  @home every:weekly"
    assert app.describe(deck, set()).plain == \
        " [ ]  Draft the deck  Payments Performance & GTM · Mike · granola  @agenda-mike  added:2026-09-29"
    assert [label for label, _ in app.item_links(deck)] == ["Payments Performance & GTM", "Mike", "granola"]


def test_t_sets_due_and_start_dates(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Renew passport\n")
    app = run(path, "down", "t", T("2026-10-20"), T("-3d"))
    item = store.load(path).items(Section.IN)[0]
    assert (str(item.due), str(item.start)) == ("2026-10-20", "2026-10-17")
    assert app.last_toast.startswith("Renew passport: due Oct 20 · starts Oct 17 (hidden until then)")
    run(path, "s", "down", "t", "ctrl+u", "tab", "ctrl+u", "enter")   # s: it starts later, so show it; clear both
    item = store.load(path).items(Section.IN)[0]
    assert (item.due, item.start) == (None, None)


import tempfile as _tempfile
from pathlib import Path as _Path

GTDApp.DATE_STATE = _Path(_tempfile.mkdtemp()) / "last-used.json"   # never touch the real one in tests
ONE_IN = "<!-- gtd v1 -->\n\n## In\n- [ ] Renew passport\n"


def next_item(path):
    return store.load(path).items(Section.NEXT)[0]


def test_date_screen_number_enter_sets_due_then_enter_skips_start(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(ONE_IN)
    run(path, "down", "t", "2", "enter", "enter")          # due: in 2 days (Oct 1 from Tue Sep 29); start: no date
    item = store.load(path).items(Section.IN)[0]
    assert (str(item.due), item.start) == ("2026-10-01", None)


def test_date_screen_arrows_pick_a_suggestion_and_tab_moves_to_start(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(ONE_IN)
    run(path, "down", "t", "2", "down", "enter", "-", "3", "enter")   # in 2 weekdays (Oct 1)… 2nd row = in 2 weeks
    item = store.load(path).items(Section.IN)[0]
    assert (str(item.due), str(item.start)) == ("2026-10-13", "2026-10-10")


def test_date_screen_enter_on_empty_due_skips_everything(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(ONE_IN)
    run(path, "down", "t", "enter")
    item = store.load(path).items(Section.IN)[0]
    assert (item.due, item.start) == (None, None)


def test_date_screen_tab_goes_straight_to_start(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(ONE_IN)
    run(path, "down", "t", "tab", "f", "r", "i", "enter")     # start only: Friday Oct 2
    item = store.load(path).items(Section.IN)[0]
    assert (item.due, str(item.start)) == (None, "2026-10-02")


def test_clarify_has_an_optional_dates_step(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(ONE_IN)
    run(path, "c", "1", T(""), "1", "1", "enter")               # one action, defer, @calls, Enter skips dates
    assert (next_item(path).context, next_item(path).due) == ("@calls", None)
    path.write_text(ONE_IN)
    run(path, "c", "1", T(""), "1", "1", *"dec 5", "enter", "enter")
    assert str(next_item(path).due) == "2026-12-05"


def test_delegate_can_set_dates(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(ONE_IN)
    run(path, "c", "1", T(""), "3", T("Mike"), *"fri", "enter", "enter")
    item = store.load(path).items(Section.WAITING)[0]
    assert (item.waiting, str(item.due)) == ("mike", "2026-10-02")


def test_step_path_follows_the_answers():
    from gtd.app import predict_path
    assert predict_path({}, "what", has_projects=True) == ["what", "action", "project", "how", "context", "dates"]
    assert predict_path({}, "what", has_projects=False) == ["what", "action", "how", "context", "dates"]
    assert predict_path({"choice": 2, "kind": "project"}, "what", True) == \
        ["what", "outcome", "action", "how", "context", "dates"]
    assert predict_path({"choice": 4, "actionable": False}, "what", True) == ["what"]
    assert predict_path({"choice": 1, "kind": "single", "how": "now"}, "what", False) == ["what", "action", "how"]
    assert predict_path({"choice": 1, "kind": "single", "how": "delegate"}, "what", False) == \
        ["what", "action", "how", "who", "dates"]
    assert predict_path({}, "action", True) == ["action", "how", "context", "dates"]   # adding to an existing project


def test_clarify_screens_show_the_step_bar(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Renew passport\n- [ ] Buy stamps\n")

    async def go():
        app = GTDApp(path, today=TODAY)
        async with app.run_test() as pilot:
            await pilot.press("c", "1"); await pilot.pause()
            first = app.header.plain
            await pilot.press("enter"); await pilot.pause()
            second = app.header.plain
            return first, second
    first, second = asyncio.run(go())
    assert first.startswith("Item 1 of 2 · What is it ✓ › Action › How › Context › Dates")
    assert "One action" in first
    assert second.startswith("Item 1 of 2 · What is it ✓ › Action ✓ › How › Context › Dates")
    assert "“Renew passport”" in second


def test_review_status_shows_the_step_bar(tmp_path):
    app = run(tmp_path / "gtd.md", "w", "n")
    assert app.status_text.startswith("Review 2/5 · Get Clear ✓ › Next Actions › Waiting For › Projects › Someday")
