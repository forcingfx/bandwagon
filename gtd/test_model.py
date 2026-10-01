from datetime import date

from gtd import model
from gtd.model import Doc, Item, Section

TODAY = date(2026, 9, 29)


def doc_with_inbox(text="Plan the offsite"):
    doc = Doc.empty()
    model.capture(doc, text, TODAY)
    return doc, doc.items(Section.IN)[0]


def test_capture_appends_to_in():
    doc, item = doc_with_inbox()
    assert item.text == "Plan the offsite"
    assert item.added == TODAY


def test_not_actionable_branches():
    doc, item = doc_with_inbox()
    model.trash(doc, item)
    assert doc.items(Section.IN) == []

    doc, item = doc_with_inbox()
    model.move(doc, item, Section.SOMEDAY)
    assert doc.items(Section.SOMEDAY) == [item]



def test_project_then_defer_links_action_to_project():
    doc, item = doc_with_inbox()
    slug = model.make_project(doc, item, "Budget offsite agreed with Rob", TODAY)
    assert slug == "budget-offsite-agreed-with-rob"
    model.defer(doc, None, "Draft the agenda", "@computer", slug, TODAY)
    action = doc.items(Section.NEXT)[0]
    assert (action.text, action.context, action.project) == ("Draft the agenda", "@computer", slug)
    assert doc.items(Section.IN) == []
    assert model.stalled_projects(doc) == []


def test_slug_is_unique():
    doc = Doc.empty()
    a = model.make_project(doc, None, "Hire GPM", TODAY)
    b = model.make_project(doc, None, "Hire GPM", TODAY)
    assert a != b


def test_two_minute_rule_goes_straight_to_done():
    doc, item = doc_with_inbox("Reply to Jen")
    model.do_now(doc, item, "Reply to Jen", None, TODAY)
    done = doc.items(Section.DONE)[0]
    assert done.done and done.done_on == TODAY
    assert doc.items(Section.IN) == []


def test_delegate_goes_to_waiting_for():
    doc, item = doc_with_inbox()
    model.delegate(doc, item, "Budget numbers", "maciej", None, TODAY)
    waiting = doc.items(Section.WAITING)[0]
    assert (waiting.waiting, waiting.since) == ("maciej", TODAY)


def test_stalled_project_has_no_open_action_or_waiting():
    doc = Doc.empty()
    slug = model.make_project(doc, None, "Ship search v2", TODAY)
    assert [p.project for p in model.stalled_projects(doc)] == [slug]
    model.delegate(doc, None, "Spec", "farid", slug, TODAY)
    assert model.stalled_projects(doc) == []


def test_complete_and_prune_done():
    doc = Doc.empty()
    old = Item(text="old", done=True, done_on=date(2026, 9, 1))
    doc.items(Section.DONE).append(old)
    model.defer(doc, None, "new", "@calls", None, TODAY)
    model.complete(doc, doc.items(Section.NEXT)[0], TODAY)
    model.prune_done(doc, TODAY)
    assert [i.text for i in doc.items(Section.DONE)] == ["new"]


def test_find_locates_item_by_line():
    doc, item = doc_with_inbox()
    assert model.find(doc, Section.IN, item.line()) is item


def test_uncomplete_returns_item_to_its_position():
    doc = Doc.empty()
    for text in ("A", "B", "C"):
        model.defer(doc, None, text, "@calls", None, TODAY)
    b = doc.items(Section.NEXT)[1]
    model.complete(doc, b, TODAY)
    model.uncomplete(doc, b, Section.NEXT, 1)
    assert [i.text for i in doc.items(Section.NEXT)] == ["A", "B", "C"]
    assert not b.done and b.done_on is None and doc.items(Section.DONE) == []


def test_reopen_target_reads_the_line():
    assert model.reopen_target(Item(text="x", waiting="mike")) is Section.WAITING
    assert model.reopen_target(Item(text="x", context="@calls", project="p")) is Section.NEXT
    assert model.reopen_target(Item(text="x", project="p")) is Section.PROJECTS
    assert model.reopen_target(Item(text="x")) is Section.IN


def test_next_due_rolls_from_the_later_of_due_and_today():
    assert model.next_due(date(2026, 9, 1), "monthly", TODAY) == date(2026, 10, 29)   # overdue: from today
    assert model.next_due(date(2026, 10, 15), "monthly", TODAY) == date(2026, 11, 15)
    assert model.next_due(date(2026, 1, 31), "monthly", date(2026, 1, 1)) == date(2026, 2, 28)
    assert model.next_due(date(2026, 10, 1), "2-weeks", TODAY) == date(2026, 10, 15)
    assert model.next_due(date(2026, 10, 1), "6-months", TODAY) == date(2027, 4, 1)
    assert model.next_due(date(2026, 10, 1), "yearly", TODAY) == date(2027, 10, 1)
    assert model.next_due(date(2026, 10, 1), "daily", TODAY) == date(2026, 10, 2)


def test_ticking_a_recurring_item_rolls_it_instead_of_finishing_it():
    doc = Doc.empty()
    item = Item(text="Pay rent", context="@home", every="monthly", due=date(2026, 10, 1))
    doc.items(Section.NEXT).append(item)
    rolled = model.complete(doc, item, TODAY)
    assert rolled == date(2026, 10, 1)                    # returns the due date it was ticked for
    assert doc.items(Section.NEXT) == [item] and not item.done
    assert item.due == date(2026, 11, 1) and doc.items(Section.DONE) == []


def test_proposals_are_items_with_a_source():
    doc = Doc.empty()
    model.capture(doc, "Mine", TODAY)
    model.propose(doc, "From Granola", "granola://meeting/1", TODAY)
    assert [(i.text, i.src) for i in doc.items(Section.IN)] == [("Mine", None), ("From Granola", "granola://meeting/1")]


def test_expire_proposals_keeps_own_captures():
    doc = Doc.empty()
    model.capture(doc, "Mine, old", date(2026, 9, 1))
    model.propose(doc, "Old proposal", "process", date(2026, 9, 20))
    model.propose(doc, "Fresh proposal", "process", date(2026, 9, 25))
    gone = model.expire_proposals(doc, TODAY, days=7)
    assert [i.text for i in gone] == ["Old proposal"]
    assert [i.text for i in doc.items(Section.IN)] == ["Mine, old", "Fresh proposal"]


def test_links_finds_wikilinks_markdown_links_and_urls():
    text = ("[[reference/home/dishwasher#Clean seal|Dishwasher]]: wipe it, see [manual](https://x.example/m) "
            "and [[projects/london]] or https://bare.example/y")
    assert model.links(text) == [
        ("Dishwasher", "note", "reference/home/dishwasher#Clean seal"),
        ("manual", "url", "https://x.example/m"),
        ("london", "note", "projects/london"),
        ("bare.example/y", "url", "https://bare.example/y"),
    ]


def test_link_segments_split_text_for_display():
    segs = model.link_segments("Wipe [[a/dishwasher|Dishwasher]] now")
    assert segs == [("Wipe ", None), ("Dishwasher", "a/dishwasher"), (" now", None)]



def test_rename_context_renames_and_removes():
    from gtd import store
    doc = store.parse("<!-- gtd v1 -->\n\n## Next Actions\n- [ ] A @calls @home\n- [ ] B @home\n")
    assert model.rename_context(doc, "@calls", "@phone") == 1
    assert [i.context for i in doc.items(Section.NEXT)] == ["@phone @home", "@home"]
    assert model.rename_context(doc, "@home", None) == 2
    assert [i.context for i in doc.items(Section.NEXT)] == ["@phone", None]
