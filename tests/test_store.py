from datetime import date

from gtd import store
from gtd.model import Section

CANONICAL = """<!-- gtd v1 · last-review: 2026-09-29 -->

## In
- [ ] Idea from the Maciej 1:1 added:2026-09-29

## Next Actions
- [ ] Call Fred re repair shop @calls +budget-offsite added:2026-09-29
- a hand-written note Claude cannot parse

## Projects
- [ ] Budget offsite agreed with Rob +budget-offsite added:2026-09-29

## Waiting For
- [ ] Mike: RNPL board brief +rnpl waiting:mike since:2026-09-29

## Someday/Maybe

## Done
- [x] Book room done:2026-09-28

## My Own Section
- [ ] kept as is
"""


def test_round_trip_is_identical():
    assert store.serialize(store.parse(CANONICAL)) == CANONICAL


def test_parse_reads_tags_and_header():
    doc = store.parse(CANONICAL)
    assert doc.last_review == date(2026, 9, 29)
    action = doc.items(Section.NEXT)[0]
    assert action.text == "Call Fred re repair shop"
    assert action.context == "@calls"
    assert action.project == "budget-offsite"
    assert action.added == date(2026, 9, 29)
    waiting = doc.items(Section.WAITING)[0]
    assert waiting.waiting == "mike"
    assert waiting.since == date(2026, 9, 29)
    assert doc.items(Section.DONE)[0].done is True


def test_unknown_lines_survive():
    doc = store.parse(CANONICAL)
    assert doc.items(Section.NEXT)[1].raw == "- a hand-written note Claude cannot parse"
    assert "My Own Section" in doc.sections


def test_text_with_at_sign_mid_sentence_is_not_a_context():
    item = store.parse_item("- [ ] Email bob@example.com about the brief @computer")
    assert item.text == "Email bob@example.com about the brief"
    assert item.context == "@computer"


def test_empty_or_missing_file_gives_all_sections(tmp_path):
    doc = store.load(tmp_path / "gtd.md")
    assert list(doc.sections) == [s.value for s in Section]


def test_mutate_rereads_disk_and_writes_atomically(tmp_path):
    path = tmp_path / "gtd.md"
    path.write_text(CANONICAL)
    # An edit arrives on disk (e.g. via Obsidian Sync) after the app loaded.
    path.write_text(CANONICAL.replace("## Someday/Maybe\n", "## Someday/Maybe\n- [ ] Learn Italian\n"))
    store.mutate(path, lambda doc: doc.items(Section.IN).clear(), today=date(2026, 9, 29))
    text = path.read_text()
    assert "Learn Italian" in text
    assert "Idea from the Maciej" not in text
    assert not list(tmp_path.glob("*.tmp"))


def test_legacy_reference_section_is_kept_after_done():
    text = "<!-- gtd v1 -->\n\n## Reference\n- [ ] Wifi password\n"
    doc = store.parse(text)
    assert list(doc.sections)[-1] == "Reference"
    assert "- [ ] Wifi password" in store.serialize(doc)


def test_prune_setting_round_trips_and_stops_auto_prune(tmp_path):
    text = "<!-- gtd v1 · last-review: 2026-09-29 · prune: manual -->\n\n## Done\n- [x] Old done:2026-09-01\n"
    doc = store.parse(text)
    assert doc.prune_manual and doc.last_review == date(2026, 9, 29)
    assert store.serialize(doc).startswith("<!-- gtd v1 · last-review: 2026-09-29 · prune: manual -->")
    path = tmp_path / "gtd.md"
    path.write_text(text)
    store.mutate(path, lambda d: None, today=date(2026, 9, 30))
    assert "Old done" in path.read_text()


def test_new_tags_round_trip_in_canonical_order():
    line = ("- [ ] Pay rent @home +bills waiting:alev src:https://mail.example/1 every:monthly "
            "due:2026-10-01 since:2026-09-29 added:2026-09-29")
    item = store.parse_item(line)
    assert item.raw is None
    assert (item.src, item.every, item.due) == ("https://mail.example/1", "monthly", date(2026, 10, 1))
    assert item.line() == line


def test_bad_every_value_keeps_the_line_verbatim():
    line = "- [ ] Water plants every:sometimes"
    item = store.parse_item(line)
    assert item.text == line[6:] or item.raw == line


def test_concurrent_mutates_never_lose_a_change(tmp_path):
    import threading
    path = tmp_path / "gtd.md"
    path.write_text("<!-- gtd v1 -->\n\n## In\n")

    def add(n):
        def slow_append(doc):
            import time
            items = doc.items(Section.IN)
            time.sleep(0.01)  # widen the read-change-write window
            items.append(store.parse_item(f"- [ ] Item {n}"))
        store.mutate(path, slow_append, today=date(2026, 9, 30))
    threads = [threading.Thread(target=add, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.load(path).items(Section.IN)) == 8


def test_star_round_trips_as_a_date_tag():
    line = "- [ ] Draft memo @computer +maps added:2026-10-01 star:2026-10-02"
    item = store.parse_item(line)
    assert str(item.star) == "2026-10-02" and item.added and item.line() == line
