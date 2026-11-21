import json
import threading
import urllib.error
import urllib.request
from datetime import date

import pytest

from gtd import config, store, web
from gtd.model import Section

TODAY = date(2026, 9, 29)
CONTEXTS = ["@calls", "@home"]


@pytest.fixture
def areas(tmp_path):
    work, home = tmp_path / "work.md", tmp_path / "home.md"
    work.write_text("<!-- gtd v1 -->\n\n## In\n- [ ] Sort receipts\n## Next Actions\n"
                    "- [ ] Call Anna @calls +budget\n- [ ] Read [the brief](https://example.com/b) start:2026-10-05\n"
                    "## Projects\n- [ ] Ship budget +budget\n- [ ] Plan offsite +offsite\n")
    home.write_text("<!-- gtd v1 -->\n\n## Next Actions\n- [ ] Pay rent every:monthly due:2026-10-01\n")
    log = tmp_path / "completions.md"
    log.write_text("| Completed | Task | Was due | Recurrence |\n|---|---|---|---|\n")
    return {"work": config.Area("work", work), "home": config.Area("home", home, completions_log=log)}


def lines(area, section):
    return [i.line() for i in store.load(area.file).items(section)]


def item(areas, area, text):
    st = web.state(areas, "work", CONTEXTS, TODAY)
    return next(i for a in st["areas"] if a["name"] == area for i in a["items"] if i["text"] == text)


def do(areas, action, it, **extra):
    return web.apply(areas, action, {"area": it["area"], "section": it["section"], "line": it["line"], **extra}, TODAY)


def test_state_lists_every_area_with_its_contexts_and_stalled_projects(areas):
    st = web.state(areas, "work", CONTEXTS, TODAY)
    assert st["today"] == "2026-09-29" and st["capture"] == "work"
    work = st["areas"][0]
    assert work["name"] == "work" and work["contexts"] == ["@calls", "@home"]
    assert work["stalled"] == ["offsite"]
    brief = item(areas, "work", "Read [the brief](https://example.com/b)")
    assert brief["later"] is True
    assert [list(s) for s in brief["segments"]] == [["Read ", None], ["the brief", "https://example.com/b"]]


def test_add_reads_typed_tags_and_undo_takes_it_back(areas):
    out = web.apply(areas, "add", {"area": "work", "text": "Book venue @calls due:2026-10-10"}, TODAY)
    assert out["message"] == "Captured to work In: Book venue"
    assert lines(areas["work"], Section.IN)[-1] == "- [ ] Book venue @calls due:2026-10-10 added:2026-09-29"
    web.apply(areas, "undo", out["undo"], TODAY)
    assert lines(areas["work"], Section.IN) == ["- [ ] Sort receipts"]


def test_done_moves_to_done_and_undo_puts_it_back_in_place(areas):
    out = do(areas, "done", item(areas, "work", "Call Anna"))
    assert lines(areas["work"], Section.DONE) == ["- [x] Call Anna @calls +budget done:2026-09-29"]
    web.apply(areas, "undo", out["undo"], TODAY)
    assert lines(areas["work"], Section.NEXT)[0] == "- [ ] Call Anna @calls +budget"
    assert lines(areas["work"], Section.DONE) == []


def test_done_on_a_recurring_task_rolls_it_and_logs_the_tick(areas):
    out = do(areas, "done", item(areas, "home", "Pay rent"))
    assert out["message"] == "Done: Pay rent (next due 2026-11-01)"
    assert lines(areas["home"], Section.NEXT) == ["- [ ] Pay rent every:monthly due:2026-11-01"]
    assert "| 2026-09-29 | Pay rent | 2026-10-01 | monthly |" in areas["home"].completions_log.read_text()
    web.apply(areas, "undo", out["undo"], TODAY)
    assert lines(areas["home"], Section.NEXT) == ["- [ ] Pay rent every:monthly due:2026-10-01"]


def test_star_move_edit_reopen_and_drop(areas):
    do(areas, "star", item(areas, "work", "Sort receipts"))
    assert lines(areas["work"], Section.IN) == ["- [ ] Sort receipts star:2026-09-29"]
    do(areas, "move", item(areas, "work", "Sort receipts"), to="someday")
    assert lines(areas["work"], Section.SOMEDAY) == ["- [ ] Sort receipts star:2026-09-29"]
    do(areas, "edit", item(areas, "work", "Sort receipts"), text="Sort all receipts @home")
    assert lines(areas["work"], Section.SOMEDAY) == ["- [ ] Sort all receipts @home star:2026-09-29"]
    do(areas, "done", item(areas, "work", "Call Anna"))
    do(areas, "reopen", item(areas, "work", "Call Anna"))
    assert lines(areas["work"], Section.NEXT)[-1] == "- [ ] Call Anna @calls +budget"
    out = do(areas, "drop", item(areas, "work", "Sort all receipts"))
    assert lines(areas["work"], Section.SOMEDAY) == []
    web.apply(areas, "undo", out["undo"], TODAY)
    assert lines(areas["work"], Section.SOMEDAY) == ["- [ ] Sort all receipts @home star:2026-09-29"]


def test_a_line_changed_since_the_page_looked_is_refused_and_nothing_is_written(areas):
    it = item(areas, "work", "Call Anna")
    store.mutate(areas["work"].file, lambda d: setattr(d.items(Section.NEXT)[0], "due", date(2026, 10, 2)), TODAY)
    before = areas["work"].file.read_text()
    with pytest.raises(web.Refused) as e:
        do(areas, "done", it)
    assert e.value.status == 409
    assert areas["work"].file.read_text() == before


def test_bad_requests_are_refused(areas):
    with pytest.raises(web.Refused):
        web.apply(areas, "add", {"area": "nowhere", "text": "x"}, TODAY)
    with pytest.raises(web.Refused):
        do(areas, "move", item(areas, "work", "Call Anna"), to="done")
    with pytest.raises(web.Refused):
        web.apply(areas, "rm-rf", {"area": "work"}, TODAY)


@pytest.mark.parametrize("header,ok", [
    ("127.0.0.1:8765", True), ("10.8.0.1:8765", True), ("[fd00::1]:8765", True), ("localhost:8765", True),
    ("box:8765", True), ("BOX", True), ("evil.example.com:8765", False), (None, False), ("", False)])
def test_host_header_must_be_an_address_or_an_allowed_name(header, ok):
    assert web.host_allowed(header, {"box"}) is ok


@pytest.fixture
def server(areas):
    srv = web.serve(areas, "work", CONTEXTS, "127.0.0.1", 0, today=TODAY)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def request(url, data=None, headers=None):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_over_http_the_page_loads_and_a_capture_lands(server, areas):
    status, page = request(server + "/")
    assert status == 200 and b"<title>gtd</title>" in page
    status, body = request(server + "/api/add", {"area": "work", "text": "From the phone"},
                           {"Content-Type": "application/json", "Origin": server})
    assert status == 200
    data = json.loads(body)
    assert data["message"] == "Captured to work In: From the phone"
    assert any(i["text"] == "From the phone" for i in data["state"]["areas"][0]["items"])
    assert lines(areas["work"], Section.IN)[-1] == "- [ ] From the phone added:2026-09-29"


def test_over_http_other_sites_cannot_write_or_read(server, areas):
    before = areas["work"].file.read_text()
    # A form on another site posts as text/plain or form data, never JSON without a preflight.
    status, _ = request(server + "/api/add", {"area": "work", "text": "x"}, {"Content-Type": "text/plain"})
    assert status == 415
    status, _ = request(server + "/api/add", {"area": "work", "text": "x"},
                        {"Content-Type": "application/json", "Origin": "http://evil.example.com"})
    assert status == 403
    # Another site's name pointed at this machine (DNS rebinding) cannot read the tasks.
    status, _ = request(server + "/api/state", headers={"Host": "evil.example.com"})
    assert status == 403
    assert areas["work"].file.read_text() == before


def test_over_http_a_stale_line_answers_409_with_fresh_state(server, areas):
    status, body = request(server + "/api/done", {"area": "work", "section": "Next Actions", "line": "- [ ] Gone"},
                           {"Content-Type": "application/json"})
    assert status == 409
    assert "changed" in json.loads(body)["error"] and "state" in json.loads(body)
