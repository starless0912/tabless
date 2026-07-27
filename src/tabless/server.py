"""The local reader service.

Binds 127.0.0.1 only, and uses nothing but the standard library.

Its real job is **window reuse**. When a document arrives, it first asks whether
a reader window is still open (an SSE connection is the evidence):

  * open   -> push the document into that window's list; open nothing
  * closed -> only then start a browser window

That is the whole mechanism behind "fifty reports, one window" instead of fifty
tabs. Everything else here is plumbing around it.
"""

from __future__ import annotations

import html as html_mod
import itertools
import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, config, core, i18n, projects
from .i18n import t

READER_HTML = Path(__file__).parent / "reader.html"

# project -> the message queues of every live reader window
_readers: dict[str, set[queue.Queue]] = {}
_readers_lock = threading.Lock()


def _subscribe(project: str) -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _readers_lock:
        _readers.setdefault(project, set()).add(q)
    return q


def _unsubscribe(project: str, q: queue.Queue) -> None:
    with _readers_lock:
        if project in _readers:
            _readers[project].discard(q)
            if not _readers[project]:
                del _readers[project]


def _broadcast(event: dict) -> int:
    """Send to every live reader window.

    There is only ever one: projects are tabs along the top, not separate
    windows. A terminal needs several windows because you work in several
    projects at once; a reader is something you read, one thing at a time.
    Giving it parallel windows would just trade a pile of tabs for a pile of
    windows -- that is sorting, not reducing, and "too many places" was the
    original complaint.
    """
    with _readers_lock:
        targets = [q for qs in _readers.values() for q in qs]
    for q in targets:
        q.put(event)
    return len(targets)


# Delivery receipts. An open SSE connection is not proof that a window still
# exists: after it closes, the server only finds out on the next failed write.
# Anything pushed in that gap vanishes silently -- and a report the user never
# learns existed is the worst failure this system can have. So the pushes that
# matter wait to be acknowledged.
_ack_events: dict[str, threading.Event] = {}
_ack_lock = threading.Lock()
_seq_counter = itertools.count(1)


def _broadcast_confirmed(event: dict, timeout: float = 1.5) -> bool:
    """Broadcast, and wait for any window to acknowledge. No receipt means no window."""
    seq = str(next(_seq_counter))
    done = threading.Event()
    with _ack_lock:
        _ack_events[seq] = done
    try:
        if not _broadcast({**event, "seq": seq}):
            return False
        return done.wait(timeout)
    finally:
        with _ack_lock:
            _ack_events.pop(seq, None)


# ---------------------------------------------------------------------------
# Page dressing
# ---------------------------------------------------------------------------

# One scrollbar for the whole library. The documents in here come from many
# different authors and almost none of them thought about scrollbars, so
# Chromium's default grey trough with arrows at both ends sits awkwardly on a
# dark layout (the horizontal one especially). This applies to every HTML the
# service serves; the documents never need to know it exists.
#
# @layer is the crucial part: layered rules always lose to unlayered ones, so a
# document that *did* style its own `::-webkit-scrollbar` keeps its design
# untouched. This only covers the ones that didn't. Rewriting somebody's visual
# design without being asked would be overreach.
SCROLLBAR_CSS = r"""
<style data-tabless="scrollbar">
@layer tabless-scrollbar {
  :root{
    --tbl-track:transparent;
    --tbl-thumb:rgba(140,150,168,.42);
    --tbl-thumb-hover:rgba(140,150,168,.7);
    --tbl-thumb-active:rgba(140,150,168,.88);
  }
  *::-webkit-scrollbar{width:12px;height:12px;background:var(--tbl-track)}
  *::-webkit-scrollbar-track{background:var(--tbl-track)}
  *::-webkit-scrollbar-thumb{
    background:var(--tbl-thumb);border:3px solid transparent;
    border-radius:10px;background-clip:padding-box;
  }
  *::-webkit-scrollbar-thumb:hover{
    background:var(--tbl-thumb-hover);border-width:2px;background-clip:padding-box;
  }
  *::-webkit-scrollbar-thumb:active{
    background:var(--tbl-thumb-active);border-width:2px;background-clip:padding-box;
  }
  *::-webkit-scrollbar-corner{background:var(--tbl-track)}
  *::-webkit-scrollbar-button{display:none;width:0;height:0}
}
</style>
<script data-tabless="scrollbar">
/* Measure whether this document is dark or light, and paint the trough to match.
   A transparent trough does not solve it: the body background propagates to the
   canvas but not into the scrollbar gutter, so what shows through is the
   browser's own default -- white under a light theme, which is glaring beside a
   dark report. The server cannot know the colour, so the page has to measure
   itself once it is up. */
(function(){
  function probe(){
    var els = [document.documentElement, document.body], i, c, m;
    for (i = 0; i < els.length; i++){
      if (!els[i]) continue;
      c = getComputedStyle(els[i]).backgroundColor || '';
      m = c.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/);
      if (m && (m[4] === undefined || parseFloat(m[4]) > .5)) return m;
    }
    return null;                  /* gradient / image / unset -- leave the default */
  }
  function apply(){
    var m = probe(); if (!m) return;
    var s = document.documentElement.style;
    s.setProperty('--tbl-track', 'rgb(' + m[1] + ',' + m[2] + ',' + m[3] + ')');
    if ((0.299 * +m[1] + 0.587 * +m[2] + 0.114 * +m[3]) / 255 < .5){
      /* dark layout: the thumb has to be brighter than default to surface at all */
      s.setProperty('--tbl-thumb', 'rgba(196,206,224,.26)');
      s.setProperty('--tbl-thumb-hover', 'rgba(196,206,224,.46)');
      s.setProperty('--tbl-thumb-active', 'rgba(196,206,224,.62)');
    }
  }
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', apply);
  else apply();
})();
</script>
"""

# Past this size, reading the whole document into memory for a scrollbar
# stops being a good trade.
_INJECT_LIMIT = 8 * 1024 * 1024
_BODY_RE = re.compile(r"<body[^>]*>", re.I)


def inject_chrome(raw: bytes) -> bytes:
    """Add the shared scrollbar to a document. If it doesn't fit, return it untouched.

    Better ugly than broken -- which is also why non-UTF-8 documents are passed
    through byte for byte instead of being force-decoded with replacements.
    """
    if len(raw) > _INJECT_LIMIT:
        return raw
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw          # an older document in some other encoding; don't touch its bytes
    at = text.lower().find("</head>")
    if at < 0:
        m = _BODY_RE.search(text)
        at = m.end() if m else 0
    return (text[:at] + SCROLLBAR_CSS + text[at:]).encode("utf-8")


def reader_page() -> bytes:
    """The reader itself, with its language pack injected.

    The strings live in `locales/*.json` alongside the Python ones rather than
    inside the HTML, so the CLI and the UI cannot drift into different wordings
    for the same concept.
    """
    text = READER_HTML.read_text(encoding="utf-8")
    payload = json.dumps(i18n.bundle("ui.", "type."), ensure_ascii=False)
    text = text.replace('<html lang="en">', f'<html lang="{i18n.lang()}">', 1)
    text = text.replace(
        "</head>",
        f"<script>window.__TABLESS_I18N__ = {payload};</script>\n</head>",
        1,
    )
    return text.encode("utf-8")


def inside_library(target: Path) -> bool:
    """Path traversal guard: every read off disk has to stay inside the library.

    Resolved on both sides, because `..` segments survive URL decoding and the
    site route joins caller-supplied text onto a real directory.
    """
    try:
        return target.resolve().is_relative_to(config.HOME.resolve())
    except (OSError, ValueError):
        return False


def decorate(doc: dict) -> dict:
    """Add the disk path for the front end.

    Not stored in the index: `TABLESS_HOME` can move, and a stored path would
    quietly go stale. Also backfills `type` for entries written before types
    existed, so the front end doesn't need null checks scattered through it.
    """
    return {**doc, "path": core.disk_path(doc),
            "type": doc.get("type") or config.DEFAULT_TYPE}


# ---------------------------------------------------------------------------
# Its own window
# ---------------------------------------------------------------------------

# Chromium-family browsers, because `--app` is what produces a window with no
# address bar and no tab strip. Deliberately not your default browser: a reader
# window that lands in the same window as everyday browsing is a tab again, and
# the whole point was to stop making tabs.
_CHROME_CANDIDATES = {
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe",
        r"%LOCALAPPDATA%\Chromium\Application\chrome.exe",
    ],
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
        "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
}
_CHROME_ON_PATH = ("google-chrome", "google-chrome-stable", "chromium",
                   "chromium-browser", "brave-browser", "vivaldi-stable",
                   "chrome")


def find_chrome() -> str | None:
    for raw in _CHROME_CANDIDATES.get(sys.platform, []):
        path = Path(os.path.expandvars(raw)).expanduser()
        if path.exists():
            return str(path)
    for name in _CHROME_ON_PATH:
        found = shutil.which(name)
        if found:
            return found
    return None


def open_url_window(url: str) -> bool:
    """Open a URL as its own application window. Falls back to the default browser."""
    chrome = find_chrome()
    if not chrome:
        # No Chromium anywhere: nothing is lost functionally, it just costs a tab.
        import webbrowser
        webbrowser.open(url)
        return True
    config.CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen([
            chrome, f"--app={url}",
            f"--user-data-dir={config.CHROME_PROFILE}",
            "--no-first-run", "--no-default-browser-check",
            "--window-size=1500,950",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def open_window(project: str, doc_id: str = "") -> bool:
    url = f"http://{config.HOST}:{config.PORT}/reader?p={urllib.parse.quote(project)}"
    if doc_id:
        url += f"&doc={doc_id}"
    return open_url_window(url)


def _live_placeholder(doc: dict) -> str:
    """What a live entry shows in the reader: it has no copy, only a way in."""
    target = html_mod.escape(doc.get("target", ""))
    hint = html_mod.escape(doc.get("hint", "") or "")
    title = html_mod.escape(doc.get("title", ""))
    hint_block = (f'<p class="hint"><b>{t("live.how")}</b><br>{hint}</p>' if hint else "")
    return f"""<!doctype html><html lang="{i18n.lang()}"><head><meta charset="utf-8">
<style>
 body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
   background:#0b0d12;color:#dde3ec;
   font:15px/1.7 system-ui,"Segoe UI","PingFang SC",sans-serif}}
 .card{{max-width:520px;padding:34px 38px;background:#151922;border:1px solid #242b38;
   border-radius:14px}}
 .k{{display:inline-block;padding:2px 9px;border-radius:5px;background:#2dd4bf22;
   color:#2dd4bf;border:1px solid #2dd4bf44;font-size:12px;margin-bottom:14px}}
 h1{{margin:0 0 6px;font-size:19px;font-weight:600}}
 code{{display:block;margin:14px 0;padding:10px 12px;background:#0b0d12;
   border:1px solid #242b38;border-radius:7px;font-size:12.5px;
   color:#8b95a6;word-break:break-all}}
 .hint{{color:#8b95a6;font-size:13px;border-left:2px solid #2dd4bf55;
   padding-left:12px;margin:16px 0}}
 button{{margin-top:8px;padding:9px 18px;border-radius:8px;border:0;cursor:pointer;
   background:#2dd4bf;color:#0b0d12;font:600 13.5px system-ui}}
 button:hover{{filter:brightness(1.1)}}
 .note{{margin-top:16px;font-size:12px;color:#5d6675}}
</style></head><body><div class="card">
<span class="k">{t("live.badge")}</span>
<h1>{title}</h1>
<p style="color:#8b95a6;font-size:13px;margin:0">{t("live.desc")}</p>
<code>{target}</code>
{hint_block}
<button onclick="go()">{t("live.button")}</button>
<p class="note">{t("live.note")}</p>
</div><script>
function go(){{
  fetch('/api/open-live', {{method:'POST',headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{id:'{doc["id"]}'}})}});
}}
</script></body></html>"""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"tabless/{__version__}"

    def log_message(self, *_args) -> None:  # silent: this is a background daemon
        pass

    # -- responses ---------------------------------------------------------

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error_page(self, message: str, status: int) -> None:
        body = (f"<!doctype html><meta charset='utf-8'>"
                f"<body style='margin:0;height:100vh;display:flex;align-items:center;"
                f"justify-content:center;background:#0b0d12;color:#8b95a6;"
                f"font:14px system-ui,sans-serif'>{html_mod.escape(message)}</body>")
        self._bytes(body.encode("utf-8"), "text/html; charset=utf-8", status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        project = (query.get("p") or [""])[0]

        if path == "/api/ping":
            return self._json({"ok": True, "pid": os.getpid(), "version": __version__})

        if path == "/api/status":
            # Live SSE connections are the reader windows ("" is the all-projects view)
            with _readers_lock:
                windows = {k or "(all)": len(v) for k, v in _readers.items()}
            return self._json({
                "pid": os.getpid(),
                "version": __version__,
                "home": str(config.HOME),
                "windows": windows,
                "docs": len(core.load_index()),
            })

        if path == "/api/projects":
            return self._json(core.list_projects())

        if path == "/api/docs":
            docs = core.load_index()
            if project:
                docs = [d for d in docs if d["project"] == project]
            docs.sort(key=lambda d: (bool(d.get("pinned")), d["updated_at"]), reverse=True)
            return self._json({
                "project": project,
                "color": projects.accent_color(project) if project else "#8ea3c4",
                "docs": [decorate(d) for d in docs],
            })

        if path.startswith("/doc/"):
            return self._serve_doc(path[len("/doc/"):])

        if path.startswith("/site/"):
            return self._serve_site(path[len("/site/"):])

        if path == "/events":
            return self._serve_sse(project)

        if path in ("/", "/reader"):
            return self._bytes(reader_page(), "text/html; charset=utf-8")

        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        payload = self._read_json()

        if path == "/api/notify":
            return self._handle_notify(payload)

        if path == "/api/add":
            src = payload.get("path")
            if not src:
                return self._json({"error": "missing path"}, 400)
            try:
                entry = core.add_document(
                    src,
                    project=payload.get("project"),
                    title=payload.get("title"),
                    source=payload.get("source", "manual"),
                    cwd=payload.get("cwd"),
                    doctype=payload.get("type"),
                )
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            self._handle_notify(entry, respond=False)
            return self._json(decorate(entry))

        if path.startswith("/api/doc/"):
            doc = core.update_document(path[len("/api/doc/"):], **payload)
            return self._json(decorate(doc) if doc else {"error": "not found"},
                              200 if doc else 404)

        if path.startswith("/api/delete/"):
            ok = core.delete_document(path[len("/api/delete/"):])
            return self._json({"ok": ok}, 200 if ok else 404)

        if path == "/api/open-live":
            doc = next((d for d in core.load_index()
                        if d["id"] == payload.get("id")), None)
            if not doc or not doc.get("target"):
                return self._json({"error": "not a live entry"}, 404)
            open_url_window(doc["target"])
            return self._json({"ok": True})

        if path == "/api/ack":
            with _ack_lock:
                ev = _ack_events.get(str(payload.get("seq", "")))
            if ev:
                ev.set()
            return self._json({"ok": True})

        if path == "/api/open":
            proj = payload.get("project") or ""
            # A window that already exists switches tabs. It never opens a second one.
            if _broadcast_confirmed({"type": "goto", "project": proj,
                                     "doc": payload.get("doc", "")}):
                return self._json({"ok": True, "action": "switched-tab"})
            open_window(proj, payload.get("doc", ""))
            return self._json({"ok": True, "action": "opened"})

        if path == "/api/reload":
            return self._json({"ok": True, "reached": _broadcast({"type": "reload"})})

        self._json({"error": "not found"}, 404)

    # -- handlers ----------------------------------------------------------

    def _handle_notify(self, entry: dict, respond: bool = True) -> None:
        """A document arrived. Push if a window is open; only start one if not.

        The push goes to the single window. If the document doesn't belong to the
        project that window is currently showing, the front end lights up the
        corresponding tab instead of yanking the view away from whatever is
        being read.
        """
        if _broadcast_confirmed({"type": "doc", "doc": decorate(entry)}):
            action = "pushed"
        else:
            open_window(entry.get("project") or "", entry.get("id", ""))
            action = "opened"
        if respond:
            self._json({"ok": True, "action": action})

    def _serve_doc(self, doc_id: str) -> None:
        doc = next((d for d in core.load_index() if d["id"] == doc_id), None)
        if not doc:
            return self._error_page(t("err.doc_missing"), 404)

        if doc.get("kind") == "live":
            return self._bytes(inject_chrome(_live_placeholder(doc).encode("utf-8")),
                               "text/html; charset=utf-8")

        if doc.get("kind") == "site":
            # Redirect into the snapshot rather than injecting a <base> tag:
            # <base> turns in-page anchors (#LY9) into cross-page navigation and
            # scrambles every internal link in the site.
            entry = urllib.parse.quote(doc.get("entry") or "index.html")
            self.send_response(302)
            self.send_header("Location", f"/site/{doc_id}/{entry}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        target = (config.HOME / doc["file"]).resolve()
        if not inside_library(target):
            return self._error_page(t("err.bad_path"), 403)
        if not target.exists():
            return self._error_page(t("err.file_lost"), 404)
        self._serve_file(target)

    def _serve_site(self, rest: str) -> None:
        """Any file inside a site snapshot. Relative paths, links and anchors just work."""
        doc_id, _, rel = rest.partition("/")
        doc = next((d for d in core.load_index() if d["id"] == doc_id), None)
        if not doc or doc.get("kind") != "site":
            return self._bytes(b"not found", "text/plain", 404)
        rel = urllib.parse.unquote(rel) or (doc.get("entry") or "index.html")
        target = (config.HOME / doc["file"] / rel).resolve()
        if not inside_library(target):
            return self._bytes(b"forbidden", "text/plain", 403)
        if not target.is_file():
            return self._error_page(t("err.asset_missing", path=rel), 404)
        self._serve_file(target)

    def _serve_file(self, path: Path) -> None:
        """Static file, with Range support so video scrubbing works."""
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype == "text/html":
            # HTML gets dressed (the shared scrollbar), which changes its length,
            # so it cannot serve Range -- Content-Length would disagree with the
            # range headers. Browsers don't Range the main document, so nothing
            # is lost.
            return self._bytes(inject_chrome(path.read_bytes()),
                               "text/html; charset=utf-8")
        if ctype.startswith("text/") or ctype in ("application/javascript",
                                                  "application/json"):
            ctype += "; charset=utf-8"
        size = path.stat().st_size
        rng = (self.headers.get("Range") or "").strip()

        start, end = 0, size - 1
        partial = False
        if rng.startswith("bytes="):
            raw_start, _, raw_end = rng[6:].partition("-")
            try:
                start = int(raw_start) if raw_start else 0
                end = int(raw_end) if raw_end else size - 1
            except ValueError:
                start, end = 0, size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            partial = True

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_sse(self, project: str) -> None:
        """Long-lived connection. Its existence *is* the signal that a window is open."""
        q = _subscribe(project)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=6)
                except queue.Empty:
                    # Heartbeat and disconnect probe in one. After a window
                    # closes, this failing write is how the connection
                    # deregisters itself; a short interval keeps /api/status
                    # from over-reporting windows for long.
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                data = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()
        except Exception:
            pass  # the window closed; this is the normal path
        finally:
            _unsubscribe(project, q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Platform-dependent, and the two platforms want opposite things.
    #
    # Windows: SO_REUSEADDR lets two processes bind the same port, so a second
    # server would start happily alongside the first. It must be off, and a
    # failed bind is then a reliable "one is already running".
    #
    # POSIX: SO_REUSEADDR does *not* permit two live binds; all it does is skip
    # the TIME_WAIT delay. Turning it off there means a restart within a minute
    # fails to bind and gets misread as "another server exists".
    allow_reuse_address = os.name != "nt"


def main() -> int:
    core.ensure_dirs()
    try:
        httpd = Server((config.HOST, config.PORT), Handler)
    except OSError:
        return 0  # port taken = a server is already running; exit quietly
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
