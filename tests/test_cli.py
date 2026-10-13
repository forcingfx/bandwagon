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


def proposed(areas, n, caller=None, day=TODAY):
    """Propose n times; how many landed in In rather than overflow."""
    return sum(cli.cmd_propose(areas, "work", f"{caller or 'anon'} {day} {i}", "process", day, caller=caller)
               .startswith("Proposed") for i in range(n))


def test_reserve_keeps_slots_for_its_caller(areas):
    areas["work"].proposal_cap, areas["work"].proposal_reserve = 5, {"process": 3}
    assert proposed(areas, 4, "tick") == 2          # the 17:00 tick cannot spend the evening's slots
    msg = cli.cmd_propose(areas, "work", "One more", "inbox", TODAY, caller="tick")
    assert msg == "cap: 2 of 5 proposals made today and the other 3 reserved for process; parked to overflow"
    assert "- 2026-09-29 One more [source](inbox)" in (areas["work"].state_dir / "task-overflow.md").read_text()
    assert proposed(areas, 4, "process") == 3       # the reserve, and the cap still holds
    assert cli.cmd_propose(areas, "work", "Late", "process", TODAY, caller="process") == (
        "cap: 5 proposals already made today; parked to overflow")
    assert json.loads((areas["work"].state_dir / "gtd-proposals.json").read_text()) == {
        "2026-09-29": {"tick": 2, "process": 3}}


def test_reserved_caller_takes_what_remains_of_the_full_cap(areas):
    areas["work"].proposal_cap, areas["work"].proposal_reserve = 5, {"process": 3}
    assert proposed(areas, 6, "process") == 5       # nobody else proposed: all five
    day = date(2026, 9, 30)
    assert proposed(areas, 1, "tick", day) == 1
    assert proposed(areas, 6, "process", day) == 4  # what the tick left
    assert proposed(areas, 1, "tick", day) == 0


def test_no_caller_is_capped_as_before_minus_the_reserve(areas):
    areas["work"].proposal_cap, areas["work"].proposal_reserve = 5, {"process": 3}
    assert proposed(areas, 4) == 2                  # no caller named: the unreserved slots only
    assert proposed(areas, 1, "process") == 1
    assert proposed(areas, 1) == 0                  # process still holds two
    areas["work"].proposal_reserve = {}
    assert proposed(areas, 9, day=date(2026, 9, 30)) == 5   # no reserve: exactly the old cap


def test_reserve_counts_on_from_a_bare_number_in_the_counts_file(areas):
    areas["work"].proposal_cap, areas["work"].proposal_reserve = 5, {"process": 3}
    counts = areas["work"].state_dir / "gtd-proposals.json"
    counts.write_text('{"2026-09-29": 2}\n')        # the format before callers were counted
    assert proposed(areas, 1, "tick") == 0
    assert proposed(areas, 4, "process") == 3
    assert json.loads(counts.read_text()) == {"2026-09-29": {"": 2, "process": 3}}


def test_caller_comes_from_the_flag_or_the_environment(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[areas.work]\nfile = "{tmp_path / "w.md"}"\nstate_dir = "{tmp_path}"\n'
                   'proposal_cap = 2\nproposal_reserve = { process = 1 }\n')
    loaded = config.load(cfg)
    assert loaded[0]["work"].proposal_reserve == {"process": 1}
    monkeypatch.setattr(config, "load", lambda: loaded)
    monkeypatch.delenv("GTD_CALLER", raising=False)
    cli.main(["propose", "A", "--src", "inbox"], today=TODAY)
    cli.main(["propose", "B", "--src", "inbox"], today=TODAY)
    monkeypatch.setenv("GTD_CALLER", "process")
    cli.main(["propose", "C", "--src", "inbox"], today=TODAY)
    monkeypatch.setenv("GTD_CALLER", "tick")
    cli.main(["propose", "D", "--src", "inbox", "--caller", "process"], today=TODAY)
    out = capsys.readouterr().out.splitlines()
    assert [ln.split(":")[0] for ln in out] == ["Proposed to work In", "cap", "Proposed to work In", "cap"]
    assert json.loads((tmp_path / "gtd-proposals.json").read_text()) == {"2026-09-29": {"": 1, "process": 1}}


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
SUBCOMMANDS = ["add", "propose", "done", "find", "list", "expire", "sweep", "init"]


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


def test_version_prints_the_package_version(capsys, no_side_effects):
    from gtd import __version__
    assert run_help(["--version"], capsys).strip() == f"gtd {__version__}"


def test_help_with_an_unknown_command_says_so(capsys, no_side_effects):
    with pytest.raises(SystemExit) as exit_:
        cli.main(["help", "frobnicate"], today=TODAY)
    assert exit_.value.code == 2
    assert "frobnicate" in capsys.readouterr().err


# Config: a stranger's first mistakes get one clear line, never a traceback

def test_missing_config_points_at_init_and_the_files_screen(tmp_path):
    with pytest.raises(SystemExit) as exit_:
        config.load(tmp_path / "none.toml")
    assert "gtd init" in str(exit_.value) and "README.md" in str(exit_.value)


def test_broken_config_is_one_clear_line(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[areas.work\nfile = 'x.md'\n")
    with pytest.raises(SystemExit) as exit_:
        config.load(cfg)
    assert str(exit_.value).startswith(f"Can't read {cfg}")


def test_area_without_a_file_is_named(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[areas.work]\nproposal_cap = 3\n")
    with pytest.raises(SystemExit) as exit_:
        config.load(cfg)
    assert "[areas.work]" in str(exit_.value) and "file" in str(exit_.value)


def test_example_config_parses_into_two_areas(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(config.EXAMPLE)
    areas, capture = config.load(cfg)
    assert list(areas) == ["work", "home"] and capture == "work"
    assert areas["work"].proposal_reserve == {"process": 3} and areas["home"].completions_log


def test_load_or_empty_gives_no_areas_without_a_config(tmp_path):
    assert config.load_or_empty(tmp_path / "none.toml") == ({}, None)


# Areas in and out of the config, and gtd init

def test_area_edits_keep_comments_and_the_other_areas(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('# mine\ncapture = "work"\ncontexts = ["@calls"]\n\n[areas.work]\nfile = "w.md"   # keep\nproposal_cap = 2\n')
    config.add_area("home", tmp_path / "h.md", cfg)
    areas, capture = config.load(cfg)
    assert list(areas) == ["work", "home"] and areas["home"].file == tmp_path / "h.md" and capture == "work"
    with pytest.raises(ValueError):
        config.add_area("home", tmp_path / "x.md", cfg)
    config.rename_area("work", "office", cfg)
    assert list(config.load(cfg)[0]) == ["office", "home"] and config.load(cfg)[1] == "office"
    config.set_area_file("home", tmp_path / "h2.md", cfg)
    assert config.load(cfg)[0]["home"].file == tmp_path / "h2.md"
    config.remove_area("office", cfg)
    text = cfg.read_text()
    assert "# mine" in text and "[areas.office]" not in text and "capture" not in text and "# keep" not in text
    assert list(config.load(cfg)[0]) == ["home"] and config.contexts(cfg) == ["@calls"]
    assert config.area_name("Work Notes!") == "work-notes" and config.area_name("") is None


def test_init_creates_the_file_and_the_config(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG", cfg)
    file = tmp_path / "vault" / "gtd.md"
    cli.main(["init", str(file)], today=TODAY)
    assert capsys.readouterr().out.startswith("Added vault:") and file.read_text().startswith("<!-- gtd v1 -->")
    areas, capture = config.load(cfg)
    assert areas["vault"].file == file and capture == "vault"
    with pytest.raises(SystemExit) as exit_:
        cli.main(["init", str(file)], today=TODAY)
    assert "already an area" in str(exit_.value)
    cli.main(["init", str(tmp_path / "other.md"), "--area", "home"], today=TODAY)
    assert list(config.load(cfg)[0]) == ["vault", "home"]


def test_init_refuses_a_newer_file_and_adopts_a_plain_one(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG", cfg)
    newer = tmp_path / "v3.md"
    newer.write_text("<!-- gtd v3 -->\n## In\n")
    with pytest.raises(SystemExit) as exit_:
        cli.main(["init", str(newer)], today=TODAY)
    assert "newer gtd" in str(exit_.value) and not cfg.exists() and newer.read_text() == "<!-- gtd v3 -->\n## In\n"
    plain = tmp_path / "plain.md"
    plain.write_text("# Ideas\n- [ ] not in a list\n")
    cli.main(["init", str(plain), "--area", "ideas"], today=TODAY)
    assert "Not a gtd file" in capsys.readouterr().out
    assert plain.read_text().startswith("<!-- gtd v1 -->\n# Ideas\n- [ ] not in a list\n") and "## In" in plain.read_text()


def test_commands_without_a_config_point_at_init(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", tmp_path / "none.toml")
    with pytest.raises(SystemExit) as exit_:
        cli.main(["list"], today=TODAY)
    assert "gtd init" in str(exit_.value)
