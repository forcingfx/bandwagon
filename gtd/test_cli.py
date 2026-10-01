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
