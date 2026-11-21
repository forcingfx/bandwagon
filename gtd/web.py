"""`gtd web`: a small page for the phone, served from the machine that holds the task files.

Every write goes through store.mutate, so the page shares one lock and one parser with the
app, the CLI and the timers. An item is named by its area, its list and its exact line, the
way the CLI finds one; a line that changed since the page last looked is refused (409), never
guessed at. Each change hands back an undo: the line as it was, and where it sat.

There is no login. The page is for a private network such as WireGuard, and it binds to
127.0.0.1 unless told otherwise. Two checks keep other web pages out: the Host header must
be an IP address, localhost or a name passed with --allow-host (which stops DNS rebinding),
and every write must be a JSON POST from the page's own origin (which stops a form on another
site from posting here).
"""
from __future__ import annotations

import ipaddress
import json
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from . import cli, config, model, store
from .model import Section

MAX_BODY = 64_000
MOVES = {"in": Section.IN, "next": Section.NEXT, "someday": Section.SOMEDAY}   # lists a tap can send an item to
MANIFEST = {
    "name": "gtd", "short_name": "gtd", "start_url": "/", "display": "standalone",
    "background_color": "#16181d", "theme_color": "#16181d",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
}
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
        'fill="#3d7bfd"/><path d="M18 33l9 9 19-21" fill="none" stroke="#fff" stroke-width="7" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>')


class Refused(Exception):
    """A request the page should show as a message, with its HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def page() -> str:
    return resources.files("gtd").joinpath("web.html").read_text(encoding="utf-8")


# What the page reads

def item_json(area: str, section: Section, item: model.Item, today: date) -> dict:
    return cli.item_json(area, section, item) | {
        "star": item.star and str(item.star),
        "later": model.starts_later(item, today),
        "segments": model.link_segments(item.text),
    }


def context_choices(doc: model.Doc, fallback: list[str]) -> list[str]:
    """The vault's own list in order (the config's while it has none), then contexts in use."""
    listed = list(fallback if doc.contexts is None else doc.contexts)
    used = sorted({c for s in Section for i in doc.items(s) for c in i.contexts})
    return listed + [c for c in used if c not in listed]


def state(areas: dict[str, config.Area], capture: str, contexts: list[str], today: date) -> dict:
    out = []
    for name, spec in areas.items():
        doc = store.load(spec.file)
        stalled = {p.project for p in model.stalled_projects(doc)}
        out.append({
            "name": name,
            "contexts": context_choices(doc, contexts),
            "stalled": sorted(stalled),
            "items": [item_json(name, s, i, today) for s in Section for i in doc.items(s) if i.raw is None],
        })
    return {"today": str(today), "capture": capture, "areas": out}


# What the page changes. Each change runs under the file's lock on a fresh read of the file.

def _section(value) -> Section:
    for s in Section:
        if value == s.value:
            return s
    raise Refused(HTTPStatus.BAD_REQUEST, f"No list called {value!r}")


def _locate(doc: model.Doc, section: Section, line: str) -> tuple[model.Item, int]:
    items = doc.items(section)
    for index, item in enumerate(items):
        if item.line() == line:
            return item, index
    raise Refused(HTTPStatus.CONFLICT, "That task changed since this page loaded. Refreshed.")


def _where(section: Section, item: model.Item) -> dict:
    return {"section": section.value, "line": item.line()}


def _section_of(doc: model.Doc, item: model.Item) -> Section:
    return next(s for s in Section if any(x is item for x in doc.items(s)))


def apply(areas: dict[str, config.Area], action: str, body: dict, today: date) -> dict:
    """One change from the page. Returns a message and the undo that reverses it."""
    area = body.get("area")
    if area not in areas:
        raise Refused(HTTPStatus.BAD_REQUEST, f"Unknown area {area!r}")
    spec = areas[area]
    out: dict = {}
    ticked: list = []   # for the completions log, written once the file is

    if action == "add":
        text = str(body.get("text", "")).strip()
        if not text:
            raise Refused(HTTPStatus.BAD_REQUEST, "Nothing to capture")

        def change(d):
            item = model.capture(d, text, today)
            out.update(message=f"Captured to {area} In: {item.text}",
                       undo={"area": area, "after": _where(Section.IN, item), "before": None})
    elif action == "undo":
        after, before = body.get("after"), body.get("before")

        def change(d):
            if after:
                item, _ = _locate(d, _section(after.get("section")), str(after.get("line")))
                d.remove(item)
            if before:
                items = d.items(_section(before.get("section")))
                items.insert(min(int(before.get("index", len(items))), len(items)),
                             store.parse_item(str(before.get("line"))))
            out.update(message="Undone.")
    elif action in ("done", "star", "drop", "move", "edit", "reopen"):
        section, line = _section(body.get("section")), str(body.get("line"))

        def change(d):
            item, index = _locate(d, section, line)
            before = {"section": section.value, "line": line, "index": index}
            if action == "done":
                if item.done or section is Section.DONE:
                    raise Refused(HTTPStatus.CONFLICT, "Already done.")
                was = model.complete(d, item, today)
                ticked.append((item.text, was or item.due, item.every or "once"))
                message = (f"Done: {item.text} (next due {item.due})" if was is not None else f"Done: {item.text}")
            elif action == "star":
                item.star = None if item.star else today
                message = f"{'Starred' if item.star else 'Unstarred'}: {item.text}"
            elif action == "drop":
                d.remove(item)
                out.update(message=f"Deleted: {item.text}", undo={"area": area, "after": None, "before": before})
                return
            elif action == "move":
                where = MOVES.get(body.get("to"))
                if where is None:
                    raise Refused(HTTPStatus.BAD_REQUEST, f"Can't move to {body.get('to')!r}")
                model.move(d, item, where)
                message = f"Moved to {where.value}: {item.text}"
            elif action == "edit":
                typed = str(body.get("text", "")).strip()
                if not typed:
                    raise Refused(HTTPStatus.BAD_REQUEST, "A task needs words")
                new = model.retitle(item, typed)
                for key in ("text", "context", "project", "waiting", "due", "start"):
                    setattr(item, key, getattr(new, key))
                message = f"Edited: {item.text}"
            else:   # reopen a Done item onto the list its own tags point to
                if section is not Section.DONE:
                    raise Refused(HTTPStatus.CONFLICT, "Only a done task can be reopened.")
                where = model.reopen_target(item)
                model.uncomplete(d, item, where, len(d.items(where)))
                message = f"Reopened to {where.value}: {item.text}"
            out.update(message=message, undo={"area": area, "after": _where(_section_of(d, item), item),
                                              "before": before})
    else:
        raise Refused(HTTPStatus.NOT_FOUND, f"No action {action!r}")

    store.mutate(spec.file, change, today)
    cli.log_completions(spec, today, ticked)
    return out


# The server

def host_allowed(header: str | None, extra: set[str]) -> bool:
    """An IP address, localhost or a name passed with --allow-host; any other name could be
    another site's domain pointed at this machine."""
    if not header:
        return False
    host = header.strip().lower()
    if host.startswith("["):           # [::1]:8765
        host = host[1:host.find("]")]
    elif host.count(":") == 1:         # 10.0.0.1:8765, box:8765
        host = host.split(":")[0]
    if host == "localhost" or host in extra:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def handler(areas: dict[str, config.Area], capture: str, contexts: list[str],
            allow_hosts: set[str], today: date | None = None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "gtd-web"

        def log_message(self, fmt, *args):  # quiet: one line per error, none per request
            pass

        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, data: dict) -> None:
            self.send(status, json.dumps(data).encode(), "application/json")

        def today(self) -> date:
            return today or date.today()

        def guard(self) -> bool:
            if not host_allowed(self.headers.get("Host"), allow_hosts):
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "Host not allowed; start gtd web with --allow-host NAME"})
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            path = self.path.split("?", 1)[0]
            if path == "/":
                self.send(HTTPStatus.OK, page().encode(), "text/html; charset=utf-8")
            elif path == "/manifest.json":
                self.send(HTTPStatus.OK, json.dumps(MANIFEST).encode(), "application/manifest+json")
            elif path == "/icon.svg":
                self.send(HTTPStatus.OK, ICON.encode(), "image/svg+xml")
            elif path == "/api/state":
                try:
                    self.send_json(HTTPStatus.OK, state(areas, capture, contexts, self.today()))
                except (OSError, SystemExit) as e:
                    self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self):
            if not self.guard():
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{self.headers.get('Host')}" \
                    and origin != f"https://{self.headers.get('Host')}":
                return self.send_json(HTTPStatus.FORBIDDEN, {"error": "Cross-origin request refused"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self.send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send JSON"})
            path = self.path.split("?", 1)[0]
            if not path.startswith("/api/"):
                return self.send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise Refused(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Too big")
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise Refused(HTTPStatus.BAD_REQUEST, "Send a JSON object")
                result = apply(areas, path[len("/api/"):], body, self.today())
                result["state"] = state(areas, capture, contexts, self.today())
                self.send_json(HTTPStatus.OK, result)
            except Refused as e:
                self.send_json(e.status, {"error": str(e), "state": state(areas, capture, contexts, self.today())})
            except (ValueError, TypeError) as e:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": f"Bad request: {e}"})
            except OSError as e:
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})

    return Handler


def serve(areas: dict[str, config.Area], capture: str, contexts: list[str], host: str, port: int,
          allow_hosts: list[str] | None = None, today: date | None = None) -> ThreadingHTTPServer:
    """A server ready for serve_forever(); port 0 picks a free one (tests)."""
    allowed = {h.lower() for h in allow_hosts or []}
    return ThreadingHTTPServer((host, port), handler(areas, capture, contexts, allowed, today))


def run(areas: dict[str, config.Area], capture: str, host: str, port: int, allow_hosts: list[str]) -> None:
    server = serve(areas, capture, config.contexts(), host, port, allow_hosts)
    shown = f"[{host}]" if ":" in host else host
    files = ", ".join(f"{a} ({config.portable(Path(s.file))})" for a, s in areas.items())
    print(f"gtd web on http://{shown}:{server.server_port}/ serving {files}. Ctrl-C stops it.", flush=True)
    if host in ("0.0.0.0", "::"):
        print("Listening on every interface, with no login: keep this port off the open internet.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
