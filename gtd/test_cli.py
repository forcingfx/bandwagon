import json
from datetime import date

import pytest

from gtd import cli, config, store
from gtd.model import Section

TODAY = date(2026, 9, 29)


@pytest.fixture
def areas(tmp_path):
    (tmp_path / "state").mkdir()
    work, home = tmp_path / "work.md", tmp_path / "home.md"
    home.write_text("<!-- gtd v1 -->\n\n## Next Actions\n- [ ] Pay rent every:monthly due:2026-10-01\n"
                    "- [ ] Call plumber @calls\n")
    log = tmp_path / "completions.md"
    log.write_text("| Completed | Task | Was due | Recurrence |\n|---|---|---|---|\n")
    return {"work": config.Area("work", work, state_dir=tmp_path / "state", proposal_cap=2),
            "home": config.Area("home", home, completions_log=log)}


def texts(area, section=Section.IN):
    return [i.text for i in store.load(area.file).items(section)]


def test_add_and_propose(areas):
    cli.cmd_add(areas, "work", "Mine", TODAY)
    cli.cmd_propose(areas, "work", "From Slack", "https://x.slack.com/archives/1", TODAY)
    items = store.load(areas["work"].file).items(Section.IN)
    assert [(i.text, i.src) for i in items] == [("Mine", None), ("From Slack", "https://x.slack.com/archives/1")]


def test_propose_cap_parks_overflow(areas):
    for n in range(3):
        msg = cli.cmd_propose(areas, "work", f"P{n}", "process", TODAY)
    assert msg.startswith("cap: 2 proposals")
    assert texts(areas["work"]) == ["P0", "P1"]
    overflow = (areas["work"].state_dir / "task-overflow.md").read_text()
    assert "- 2026-09-29 P2 [source](process)" in overflow
    assert cli.cmd_propose(areas, "work", "Tomorrow", "process", date(2026, 9, 30)).startswith("Proposed")


def test_done_ticks_one_match_and_rolls_recurring_with_log(areas):
    assert cli.cmd_done(areas, "rent", TODAY) == "Ticked in home: Pay rent (next due 2026-11-01)"
    assert "| 2026-09-29 | Pay rent | 2026-10-01 | monthly |" in areas["home"].completions_log.read_text()
    cli.cmd_done(areas, "plumber", TODAY)
    assert texts(areas["home"], Section.DONE) == ["Call plumber"]


def test_done_refuses_ambiguous_or_missing(areas):
    cli.cmd_add(areas, "work", "Call plumber again", TODAY)
    with pytest.raises(SystemExit, match="2 open items match"):
        cli.cmd_done(areas, "plumber", TODAY)
    with pytest.raises(SystemExit, match="0 open items match"):
        cli.cmd_done(areas, "nothing like this", TODAY)


def test_done_matches_words_in_any_order_and_tags(areas):
    assert cli.cmd_done(areas, "plumber call", TODAY) == "Ticked in home: Call plumber"
    cli.cmd_add(areas, "work", "Draft the memo +offsite", TODAY)
    assert cli.cmd_done(areas, "memo +offsite", TODAY) == "Ticked in work: Draft the memo"


def test_done_hands_several_to_the_picker_and_refuses_none(areas):
    cli.cmd_add(areas, "work", "Call plumber again", TODAY)
    seen = []
    assert cli.cmd_done(areas, "plumber", TODAY, lambda words: seen.append(words) or ["Ticked x"]) == "Ticked x"
    assert seen == ["plumber"]
    assert cli.cmd_done(areas, "plumber", TODAY, lambda words: []) == "Nothing changed."
    with pytest.raises(SystemExit, match="0 open items match"):
        cli.cmd_done(areas, "nothing like this", TODAY, lambda words: ["never"])


def test_find_matches_projects_contexts_and_people(areas):
    cli.cmd_add(areas, "work", "Send the note +payments-strategy waiting:nils", TODAY)
    cli.cmd_add(areas, "work", "Agenda item @agenda-mike", TODAY)
    hits = lambda words: sorted(i.text for _, _, i in cli.matches(areas, words))  # noqa: E731
    assert hits("+payments") == ["Send the note"]
    assert hits("waiting:nils") == hits("nils") == ["Send the note"]
    assert hits("mike") == ["Agenda item"]
    assert hits("@calls") == ["Call plumber"]


def test_pick_app_works_through_a_set(areas):
    import asyncio
    from gtd.pick import PickApp
    cli.cmd_add(areas, "work", "Call plumber again", TODAY)

    async def go(query, *keys):
        app = PickApp(areas, query, TODAY)
        async with app.run_test() as pilot:
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
        return app.return_value
    # a query lands in the list: s stars the first (In sorts first), ↓ Space ticks the second
    assert asyncio.run(go("plumber", "s", "down", "space", "escape")) == [
        "Starred in work: Call plumber again", "Ticked in home: Call plumber"]
    assert [i.star for i in store.load(areas["work"].file).items(Section.IN)] == [TODAY]
    assert texts(areas["home"], Section.DONE) == ["Call plumber"]
    # no query starts in the box, where letters type; Tab moves to the list
    assert asyncio.run(go("", *"rent", "tab", "space", "escape")) == ["Ticked in home: Pay rent (next due 2026-11-01)"]
    assert asyncio.run(go("plumber", "escape")) == []


def test_list_is_json_for_skills(areas, capsys, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: (areas, "work"))
    cli.main(["list", "--area", "home", "--section", "next"], today=TODAY)
    rows = json.loads(capsys.readouterr().out)
    assert [(r["text"], r["every"], r["due"]) for r in rows] == [("Pay rent", "monthly", "2026-10-01"),
                                                                   ("Call plumber", None, None)]


def test_expire_moves_old_proposals_to_a_log(areas):
    cli.cmd_propose(areas, "work", "Old", "process", date(2026, 9, 20))
    cli.cmd_add(areas, "work", "Mine", date(2026, 9, 1))
    assert cli.cmd_expire(areas, TODAY, 7) == ["Expired from work In: Old"]
    assert texts(areas["work"]) == ["Mine"]
    assert "- 2026-09-29 Old [source](process)" in (areas["work"].state_dir / "gtd-expired.md").read_text()


def test_sweep_files_obsidian_ticks(areas):
    areas["home"].file.write_text("<!-- gtd v1 -->\n\n## Next Actions\n- [x] Pay rent every:monthly due:2026-10-01\n"
                                  "- [x] Call plumber @calls\n- [ ] Still open\n")
    assert cli.cmd_sweep(areas, TODAY) == ["Rolled in home: Pay rent (next due 2026-11-01)",
                                           "Done in home: Call plumber"]
    doc = store.load(areas["home"].file)
    assert [(i.text, i.done) for i in doc.items(Section.NEXT)] == [("Pay rent", False), ("Still open", False)]
    assert [(i.text, i.done_on) for i in doc.items(Section.DONE)] == [("Call plumber", TODAY)]
    assert areas["home"].completions_log.read_text().count("| 2026-09-29 |") == 2


def test_add_and_propose_read_tags_in_the_text_and_flags(areas):
    cli.cmd_propose(areas, "home", "Renew passport due:2026-10-20", "process", TODAY)
    cli.cmd_add(areas, "home", "Pay council tax", TODAY, section="next", every="yearly", due="2026-11-01",
                project="bills")
    doc = store.load(areas["home"].file)
    proposal = doc.items(Section.IN)[0]
    assert (proposal.text, proposal.src, str(proposal.due), proposal.raw) == (
        "Renew passport", "process", "2026-10-20", None)
    tax = doc.items(Section.NEXT)[-1]
    assert (tax.text, tax.every, str(tax.due), tax.project, tax.raw) == (
        "Pay council tax", "yearly", "2026-11-01", "bills", None)


def test_completion_rows_escape_pipes(areas):
    cli.cmd_add(areas, "home", "Call [[people/kita|Kita]]", TODAY, section="next")
    cli.cmd_done(areas, "Kita", TODAY)
    assert "| Call [[people/kita\\|Kita]] |" in areas["home"].completions_log.read_text()


def test_sweep_can_be_limited_to_one_area(areas):
    areas["work"].file.write_text("<!-- gtd v1 -->\n\n## Next Actions\n- [x] Work tick\n")
    areas["home"].file.write_text("<!-- gtd v1 -->\n\n## Next Actions\n- [x] Home tick\n")
    assert cli.cmd_sweep(areas, TODAY, area="home") == ["Done in home: Home tick"]
    assert store.load(areas["work"].file).items(Section.NEXT)[0].done


def test_expire_can_be_limited_to_one_area(areas):
    cli.cmd_propose(areas, "home", "Old home", "process", date(2026, 9, 1))
    cli.cmd_propose(areas, "work", "Old work", "process", date(2026, 9, 1))
    assert cli.cmd_expire(areas, TODAY, 7, area="work") == ["Expired from work In: Old work"]
    assert texts(areas["home"]) == ["Old home"]


def test_add_takes_quick_date_forms(areas):
    cli.cmd_add(areas, "home", "Renew passport", TODAY, section="next", due="fri", start="-2")
    item = store.load(areas["home"].file).items(Section.NEXT)[-1]
    assert (str(item.due), str(item.start)) == ("2026-10-02", "2026-09-30")   # TODAY is Tue Sep 29


def test_save_contexts_rewrites_only_that_line(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('capture = "work"\ncontexts = ["@calls", "@home"]\n\n[areas.work]\nfile = "w.md"\n')
    config.save_contexts(["@home", "@deep"], cfg)
    text = cfg.read_text()
    assert 'contexts = ["@home", "@deep"]' in text and '[areas.work]' in text and text.count("contexts =") == 1
    assert config.contexts(cfg) == ["@home", "@deep"]
    bare = tmp_path / "bare.toml"
    bare.write_text('[areas.work]\nfile = "w.md"\n')
    config.save_contexts(["@calls"], bare)
    assert config.contexts(bare) == ["@calls"] and config.load(bare)[0]["work"].file.name == "w.md"


# Help: never acts, fits 80 columns, everyday first, examples, where the docs live

HELP_FORMS = [["--help"], ["-h"], ["help"]]
SUBCOMMANDS = ["add", "propose", "done", "find", "list", "expire", "sweep"]


@pytest.fixture
def no_side_effects(monkeypatch, tmp_path):
    """Help must not read the config, touch a file or open the app."""
    def refuse(*a, **k):
        raise AssertionError("help loaded the config")
    monkeypatch.setattr(config, "load", refuse)
    monkeypatch.setattr(config, "single", refuse)
    monkeypatch.setattr(store, "mutate", refuse)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    assert list(tmp_path.iterdir()) == []


def run_help(argv, capsys) -> str:
    with pytest.raises(SystemExit) as exit_:
        cli.main(argv, today=TODAY)
    assert exit_.value.code in (0, None)
    out = capsys.readouterr()
    assert out.err == ""
    return out.out


def assert_readable(text: str, max_lines: int | None = None) -> None:
    assert text.isascii(), [ln for ln in text.splitlines() if not ln.isascii()]
    wide = [ln for ln in text.splitlines() if len(ln) > 80]
    assert wide == []
    if max_lines:
        assert len(text.splitlines()) <= max_lines


@pytest.mark.parametrize("argv", HELP_FORMS)
def test_top_help_is_grouped_with_examples_and_docs(argv, capsys, no_side_effects, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    out = run_help(argv, capsys)
    assert_readable(out, max_lines=40)
    lines = out.splitlines()
    assert "task" in lines[0].lower()
    assert lines.index("Everyday") < lines.index("Automation (for scripts, skills and timers)")
    assert "Examples" in lines
    assert "README.md" in lines[-1]
    for name in SUBCOMMANDS:
        assert f"gtd {name}" in out
    assert "?" in out  # how to find the app's own keys


def test_help_forms_print_the_same_text(capsys, no_side_effects):
    assert run_help(["--help"], capsys) == run_help(["-h"], capsys) == run_help(["help"], capsys)


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_command_help_fits_and_has_an_example(name, capsys, no_side_effects, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    out = run_help([name, "--help"], capsys)
    assert_readable(out)
    assert out.startswith(f"usage: gtd {name}")
    assert "Example" in out
    assert run_help(["help", name], capsys) == out


def test_help_with_an_unknown_command_says_so(capsys, no_side_effects):
    with pytest.raises(SystemExit) as exit_:
        cli.main(["help", "frobnicate"], today=TODAY)
    assert exit_.value.code == 2
    assert "frobnicate" in capsys.readouterr().err
