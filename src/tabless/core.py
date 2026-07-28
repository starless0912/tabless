"""Storage and index: everything that decides what a document *is* and where it goes.

Four ideas hold this file together.

**Copies, not references.** A reference-based index is all dead links within
three months. Agent scratchpads get cleaned by the OS; chat clients purge their
attachment caches. If the library only pointed at those files it would be an
elaborate way of losing things.

**A page or a site.** Parsing the entry HTML's dependency closure answers this
without anyone having to declare it. References nothing local -> one file gets
copied. References images, stylesheets, sibling pages -> those files come along
with their relative structure intact, so the copy still works away from home.

**Version folding.** The same (project, title) is the same document updated, not
a second document. The entry is updated in place and the old content is filed
under `_index/versions/`, so re-publishing a progress page fifty times leaves one
row in the list rather than fifty.

**Types are an open set.** `--type` takes whatever you give it. There is no
whitelist, because a tool that refuses to file something is a tool you stop
using. There *is* an alias table, because an open set left completely alone
shatters into `report` / `reports` / `Report` -- and "the same thing living in
several places" is the problem this tool exists to solve.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
import shutil
import stat
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config, projects
from .i18n import t

# --------------------------------------------------------------------------
# Types
# --------------------------------------------------------------------------

# Plurals, abbreviations, translations, and a handful of pairs that are simply
# the same thing under two names. Anything not listed here is kept verbatim --
# that may well be a genuinely new type, and refusing it would defeat the point.
TYPE_ALIASES = {
    "reports": "report", "报告": "report", "汇报": "report",
    "prototypes": "prototype", "proto": "prototype", "原型": "prototype",
    "docs": "doc", "document": "doc", "documentation": "doc", "文档": "doc",
    "evals": "eval", "evaluation": "eval", "评测": "eval",
    "overview": "report", "analysis": "report", "briefing": "report",
    "wiki": "doc", "spec": "doc", "reference": "doc",
    "demo": "prototype", "mockup": "prototype",
    "experiment": "eval", "benchmark": "eval", "review": "eval",
}

# Characters Windows will not accept in a path component. Applied on every
# platform: a library should stay copyable between machines.
_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def normalize_type(name: str | None) -> str:
    """Fold a type name. Empty -> the default type.

    Lowercase, strip characters that cannot be a directory name, consult the
    alias table. **No whitelist check** -- an unrecognised name survives intact.
    """
    n = (name or "").strip().lower()
    if not n:
        return config.DEFAULT_TYPE
    n = _ILLEGAL_RE.sub("", n)
    n = re.sub(r"[\s_]+", "-", n).strip("-.")
    if not n:
        return config.DEFAULT_TYPE
    return TYPE_ALIASES.get(n, n)


def known_types() -> list[str]:
    """Types actually in use, so the CLI can nudge towards reuse over invention."""
    return sorted({(d.get("type") or config.DEFAULT_TYPE) for d in load_index()})


# --------------------------------------------------------------------------
# Titles
# --------------------------------------------------------------------------


def extract_title(html: str, fallback: str) -> str:
    """`<title>`, then the first `<h1>`, then the filename.

    Entities must be decoded, and decoded *after* tags are stripped, not before.
    Some exporters write the whole title as `&#36816;&#21160;…`; skip the decode
    and the library lists a string of numeric entities. Worse, the 120-character
    cut then slices the last entity in half (`&#210`), and that fragment is
    neither recognisable nor searchable.
    """
    for pattern in (r"<title[^>]*>(.*?)</title>", r"<h1[^>]*>(.*?)</h1>"):
        m = re.search(pattern, html, re.S | re.I)
        if not m:
            continue
        text = re.sub(r"<[^>]+>", "", m.group(1))   # nested tags first
        text = html_lib.unescape(text)              # entities second
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text[:120]
    return fallback


def slugify(title: str) -> str:
    """Filename slug. Non-ASCII is kept: you should recognise it in a file browser."""
    s = _ILLEGAL_RE.sub("", title)
    s = re.sub(r"\s+", "-", s.strip()).strip("-.")
    return (s or "untitled")[:60]


# --------------------------------------------------------------------------
# Dependency closure -- is this one page, or a whole site?
# --------------------------------------------------------------------------

# src/href/poster in HTML, url() in CSS.
_REF_RE = re.compile(
    r"""(?:src|href|poster|data-src)\s*=\s*["']([^"']+)["']|url\(\s*["']?([^"')]+)["']?\s*\)""",
    re.I,
)

# Media that only JavaScript knows about. Blind-eval and comparison pages
# routinely keep their clip list in a script-side data array and build the
# <video> grid at runtime, so the attribute scan above sees a page with zero
# attachments -- and what lands in the library is a working-looking 40KB shell
# whose 267MB of clips stayed behind in a scratch directory, waiting to be
# cleaned. So any quoted string ending in a media extension is a candidate
# reference. The cost of over-matching is bounded by `collect_deps`, which
# keeps only candidates that exist on disk: prose that merely mentions
# "demo.mp4" resolves to nothing and drops out.
_MEDIA_EXT = (
    "mp4|webm|mov|m4v|ogv|mp3|wav|m4a|aac|ogg|flac|opus|"
    "png|jpg|jpeg|gif|webp|avif|svg|bmp|ico|vtt|srt|pdf"
)
_MEDIA_STR_RE = re.compile(
    rf"""["'`]([^"'`\n]+\.(?:{_MEDIA_EXT})(?:[?#][^"'`\n]*)?)["'`]""",
    re.I,
)

# .js is parseable for the same reason: the data array naming the media often
# lives in its own script file rather than inline.
_PARSEABLE = {".html", ".htm", ".css", ".js", ".mjs"}


def is_skipped(path: Path) -> bool:
    """True if any path component is a dependency or debugging leftover."""
    lowered = {d.lower() for d in config.SKIP_DIRS}
    parts = [p.lower() for p in path.parts]
    if any(p in lowered for p in parts):
        return True
    return any(sub in p for p in parts for sub in config.SKIP_SUBSTR)


def local_refs(text: str, base: Path) -> list[Path]:
    """Every reference that points at a local file. http/data/anchors are ignored."""
    refs = [(m.group(1) or m.group(2) or "").strip() for m in _REF_RE.finditer(text)]
    refs += [m.group(1).strip() for m in _MEDIA_STR_RE.finditer(text)]
    out = []
    for ref in refs:
        if not ref or ref.startswith(("data:", "http:", "https:", "//", "#",
                                      "javascript:", "mailto:", "blob:")):
            continue
        ref = ref.split("#")[0].split("?")[0]
        if not ref or ref.startswith("/"):
            continue          # a site-absolute path has no reliable base here
        try:
            out.append((base.parent / unquote(ref)).resolve())
        except (OSError, ValueError):
            continue
    return out


def collect_deps(entry: Path, limit: int = 4000) -> set[Path]:
    """Breadth-first walk of everything the entry file actually references.

    The closure, not the containing directory: an experiment board's `goldens/`
    may hold 25MB of images while a single detail page uses fourteen of them.
    Snapshotting the directory would multiply the library by the number of
    reports that happen to sit next to it.
    """
    seen: set[Path] = set()
    queue = [entry.resolve()]
    while queue and len(seen) < limit:
        f = queue.pop(0)
        if f in seen or not f.is_file() or is_skipped(f):
            continue
        seen.add(f)
        if f.suffix.lower() not in _PARSEABLE:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        queue.extend(r for r in local_refs(text, f) if r not in seen)
    return seen


def _common_root(paths: set[Path], entry: Path) -> Path:
    """The closure's shared parent directory, i.e. the site root.

    When there is no common root -- a Windows report referencing an asset on
    another drive -- anchor on the entry's own directory. It is the only root
    that can express the entry's position, which `_materialize` needs to record.
    Dependencies it cannot reach are skipped by the copy loop rather than
    bringing the whole archive down.

    Taking an arbitrary member of the set instead (which is what `next(iter(...))`
    does) can pick a dependency on the *other* drive, and then the entry itself
    has no relative path and archiving fails outright.
    """
    if len(paths) == 1:
        return next(iter(paths)).parent
    try:
        return Path(os.path.commonpath([str(p) for p in paths]))
    except ValueError:
        return entry.parent


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------


def ensure_dirs() -> None:
    for d in (config.HOME, config.INDEX_DIR, config.VERSIONS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_index() -> list[dict]:
    if not config.INDEX_FILE.exists():
        return []
    try:
        data = json.loads(config.INDEX_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_index(docs: list[dict]) -> None:
    """Atomic write: land a .tmp, then replace, so nobody reads a half-written index."""
    ensure_dirs()
    tmp = config.INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(config.INDEX_FILE)


class Lock:
    """Coarse file lock. The CLI and the server can archive at the same moment.

    **Not reentrant** -- it is an `O_CREAT | O_EXCL` file. A function holding it
    must not call another function that takes it, or it will wait out its own
    timeout and then carry on unlocked. This is why the relocate logic is split
    into an unlocked `_relocate()` with `retype_document()` as its locking shell.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        self.path = config.INDEX_DIR / ".lock"
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self) -> Lock:
        ensure_dirs()
        self.path = config.INDEX_DIR / ".lock"
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:    # a lock left behind by a crash is stale after 30s
                    if time.time() - self.path.stat().st_mtime > 30:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    # Proceeding unlocked risks a rare interleaved write; blocking
                    # forever guarantees a hung archive. The first is the better bet.
                    return self
                time.sleep(0.05)

    def __exit__(self, *_exc) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.path.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Archiving
# --------------------------------------------------------------------------


def _drop_readonly(p: Path) -> None:
    """Clear the read-only bit on our own copy.

    Files arriving from chat clients, cloud drives and mail attachments are
    routinely read-only, and `copy2` faithfully reproduces that -- whereupon the
    library cannot delete or fold its own copies (`WinError 5`). Since those
    inboxes are a main entry point, this must happen on the way in: copies inside
    the library belong to the library.
    """
    try:
        p.chmod(p.stat().st_mode | stat.S_IWRITE)
    except OSError:
        pass


def _materialize(src: Path, deps: set[Path], dest: Path, is_site: bool) -> str:
    """Put content in the library.

    Returns the entry page's path within the snapshot (empty string for a page).
    """
    if not is_site:
        shutil.copy2(src, dest)
        _drop_readonly(dest)
        return ""

    files = deps | {src}
    root = _common_root(files, src)
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue                      # outside the closure root; skip it
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        _drop_readonly(target)
    return src.relative_to(root).as_posix()


def _doc_id(project: str, title: str) -> str:
    """(project, title) identifies a document.

    Type is deliberately absent: refiling something is the same document moving
    to a different folder, and it should not split into two rows.
    """
    return hashlib.md5(f"{project}:{title}".encode()).hexdigest()[:12]


def add_live(
    target: str,
    project: str,
    title: str,
    hint: str = "",
    source: str = "manual",
    doctype: str | None = None,
) -> dict:
    """Register a live prototype -- a pointer, never a copy.

    Snapshotting something still under active change is worse than useless: what
    you keep is a copy that expires, and usually one that was broken on arrival
    because its stylesheet lived somewhere the snapshot never looked.

    The line this tool will not cross: **it does not manage process lifetimes.**
    No supervision, no starting your dev server, no port monitoring. If the
    service isn't running, the entry opens blank. That is the design.
    """
    project = projects.normalize(project) or config.INBOX
    # A live entry is by definition something still being built, so `prototype`
    # is a better default here than the library-wide one.
    dtype = normalize_type(doctype or "prototype")
    doc_id = _doc_id(project, title)
    now = datetime.now()
    with Lock():
        docs = load_index()
        existing = next((d for d in docs if d["id"] == doc_id), None)
        payload = {
            "kind": "live",
            "type": dtype,
            "target": target,
            "hint": hint,
            "title": title,
            "updated_at": now.isoformat(timespec="seconds"),
        }
        if existing:
            existing.update(payload)
            save_index(docs)
            return existing
        entry = {
            "id": doc_id,
            "project": project,
            "file": "",
            "entry": "",
            "assets": 0,
            "bytes": 0,
            "source": source,
            "origin": target,
            "version": 1,
            "created_at": now.isoformat(timespec="seconds"),
            "unread": True,
            "pinned": False,
            **payload,
        }
        docs.append(entry)
        save_index(docs)
        return entry


def add_document(
    src: str | Path,
    project: str | None = None,
    title: str | None = None,
    source: str = "agent",
    cwd: str | Path | None = None,
    doctype: str | None = None,
) -> dict:
    """Take a document into the library and return its index entry.

    Lands under `<project>/<type>/`. Page vs site is decided by the dependency
    closure, not by a flag.

    Same (project, title) means the same document updated: the entry is amended
    in place, `version` goes up, and the previous content moves to
    `_index/versions/`.

    The flip side is worth knowing: **two different things sharing a title get
    merged**, with the newer one demoting the older to history. Bundles from
    colleagues and repeated runs off one template both hit this. Pass `--title`
    to keep them apart.
    """
    src_path = Path(src).expanduser().resolve()
    if not src_path.exists():
        raise FileNotFoundError(t("cli.err_file_not_found", path=src_path))

    html = src_path.read_text(encoding="utf-8", errors="replace")
    title = (title or "").strip() or extract_title(html, src_path.stem)
    project = (projects.normalize(project or "")
               or projects.infer(src_path, Path(cwd) if cwd else None)
               or config.INBOX)
    dtype = normalize_type(doctype)

    doc_id = _doc_id(project, title)
    # The source file's mtime, not the moment of archiving -- otherwise adopting
    # a backlog of old documents flattens their real timeline into today.
    now = datetime.fromtimestamp(src_path.stat().st_mtime)

    deps = collect_deps(src_path)
    deps.discard(src_path)
    total = sum(p.stat().st_size for p in deps if p.is_file()) if deps else 0
    if total > config.MAX_SITE_BYTES:
        raise ValueError(t("cli.err_deps_too_big",
                           size=f"{total / 1048576:.0f}",
                           limit=config.MAX_SITE_BYTES // 1048576,
                           path=src_path))
    is_site = bool(deps)

    with Lock():
        docs = load_index()
        existing = next((d for d in docs if d["id"] == doc_id), None)

        # Re-adding without an explicit type keeps the filing it already has,
        # instead of letting the default quietly drag it back to `report`.
        if existing and doctype is None:
            dtype = normalize_type(existing.get("type"))

        dest_dir = config.HOME / project / dtype
        dest_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{now:%Y-%m-%d}-{slugify(title)}"
        dest = dest_dir / (stem if is_site else stem + ".html")

        if existing:
            old = config.HOME / existing["file"]
            if old.exists():
                # Identical entry content is not a new version -- otherwise
                # merely reopening a report inflates its version number. But
                # entry bytes alone only prove the snapshot is current if the
                # dependency closure is unchanged too: the scanner learns new
                # reference shapes (JS-side media lists, most recently), and
                # after such a fix re-adding an untouched file *is* the repair
                # path. Judging by entry bytes alone silently refused it.
                unchanged_closure = (
                    ("site" if is_site else "page") == existing.get("kind")
                    and len(deps) == existing.get("assets")
                    and total == existing.get("bytes"))
                entry_old = (old / existing["entry"]
                             if old.is_dir() and existing.get("entry") else old)
                try:
                    if (unchanged_closure and entry_old.is_file()
                            and entry_old.read_bytes() == src_path.read_bytes()):
                        existing["opened_at"] = datetime.now().isoformat(timespec="seconds")
                        # Unchanged content still has to honour an explicit
                        # `--type`, or `add --type eval` on an archived document
                        # would silently do nothing while reporting success.
                        if doctype is not None and dtype != existing.get("type"):
                            _relocate(existing, dtype)   # already locked
                        save_index(docs)
                        return existing
                except OSError:
                    pass
                vdir = config.VERSIONS_DIR / doc_id
                vdir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(vdir / f"v{existing['version']}{old.suffix}"))
                _prune_versions(doc_id)
                # If the type changed, don't leave the vacated folder behind.
                # The `!= dest_dir` guard is essential: without it, folding the
                # only document in its type folder deletes the very directory
                # the new copy is about to be written into, and the copy fails
                # with "path not found". It survived a long time because in a
                # busy library that rmdir almost always fails as non-empty.
                if old.parent != dest_dir:
                    try:
                        old.parent.rmdir()
                    except OSError:
                        pass

            entry_rel = _materialize(src_path, deps, dest, is_site)
            existing.update(
                file=str(dest.relative_to(config.HOME)),
                type=dtype,
                kind="site" if is_site else "page",
                entry=entry_rel,
                assets=len(deps),
                bytes=total,
                title=title,
                version=existing["version"] + 1,
                updated_at=now.isoformat(timespec="seconds"),
                origin=str(src_path),
                unread=True,
            )
            save_index(docs)
            return existing

        entry_rel = _materialize(src_path, deps, dest, is_site)
        entry = {
            "id": doc_id,
            "project": project,
            "type": dtype,
            "title": title,
            "kind": "site" if is_site else "page",
            "file": str(dest.relative_to(config.HOME)),
            "entry": entry_rel,
            "assets": len(deps),
            "bytes": total,
            "source": source,
            "origin": str(src_path),
            "version": 1,
            "created_at": now.isoformat(timespec="seconds"),
            "updated_at": now.isoformat(timespec="seconds"),
            "unread": True,
            "pinned": False,
        }
        docs.append(entry)
        save_index(docs)
        return entry


def update_document(doc_id: str, **fields) -> dict | None:
    """Update an entry's mutable fields (read state, star, title, project)."""
    allowed = {"unread", "pinned", "title", "project"}
    with Lock():
        docs = load_index()
        doc = next((d for d in docs if d["id"] == doc_id), None)
        if not doc:
            return None
        for k, v in fields.items():
            if k in allowed:
                doc[k] = v
        save_index(docs)
        return doc


def _prune_versions(doc_id: str) -> int:
    """Drop excess history, returning how many were removed. Newest always survives.

    Two gates, count and total size. Site snapshots run to hundreds of megabytes,
    so a count-only limit lets a single document eat a gigabyte.
    """
    vdir = config.VERSIONS_DIR / doc_id
    if not vdir.is_dir():
        return 0

    def _size(p: Path) -> int:
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return p.stat().st_size if p.is_file() else 0

    try:                                    # newest first
        items = sorted(vdir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return 0

    dropped, running = 0, 0
    for i, p in enumerate(items):
        sz = _size(p)
        running += sz
        too_many = i >= config.MAX_VERSION_KEEP
        too_big = i > 0 and running > config.MAX_VERSION_BYTES  # keep the newest at any size
        if not (too_many or too_big):
            continue
        if p.is_dir():
            for f in p.rglob("*"):
                _drop_readonly(f)
            shutil.rmtree(p, ignore_errors=True)
        else:
            _drop_readonly(p)
            p.unlink(missing_ok=True)
        dropped += 1
        running -= sz
    return dropped


def _relocate(doc: dict, dtype: str) -> bool:
    """Move an entry's copy into `<project>/<dtype>/` and update its fields.

    **The caller must already hold the Lock** -- see `Lock` on why taking it
    again here would deadlock against itself.
    """
    if doc.get("kind") == "live":                 # no copy exists; the field is the whole story
        doc["type"] = dtype
        return True
    rel = (doc.get("file") or "").strip()
    if not rel:
        return False
    old = config.HOME / rel
    new_dir = config.HOME / doc["project"] / dtype
    new = new_dir / old.name
    if old != new:
        if not old.exists():
            return False
        new_dir.mkdir(parents=True, exist_ok=True)
        if new.exists():                          # name collision: suffix it, never overwrite
            new = new_dir / f"{old.stem}-{doc['id'][:6]}{old.suffix}"
        shutil.move(str(old), str(new))
        try:
            old.parent.rmdir()
        except OSError:
            pass
    doc["type"] = dtype
    doc["file"] = str(new.relative_to(config.HOME))
    return True


def retype_document(doc_id: str, doctype: str) -> dict | None:
    """Refile an entry, moving its copy on disk to `<project>/<new type>/`.

    Touching only the index would leave entries whose `file` disagrees with
    reality -- a broken state that shows up as "clicking it gives a blank page",
    and only when somebody eventually clicks.
    """
    dtype = normalize_type(doctype)
    with Lock():
        docs = load_index()
        doc = next((d for d in docs if d["id"] == doc_id), None)
        if not doc:
            return None
        if not _relocate(doc, dtype):
            return None
        save_index(docs)
        return doc


def delete_document(doc_id: str) -> bool:
    """Remove an entry from the library, files and history included."""
    with Lock():
        docs = load_index()
        doc = next((d for d in docs if d["id"] == doc_id), None)
        if not doc:
            return False
        rel = (doc.get("file") or "").strip()
        if rel:
            # Guarded on purpose: a live entry has `file == ""`, and
            # `HOME / ""` is the library root. Deleting a pointer must not
            # delete the entire library.
            target = config.HOME / rel
            if target.is_dir():
                # Read-only files make rmtree leave debris behind, silently.
                for f in target.rglob("*"):
                    _drop_readonly(f)
                _drop_readonly(target)
                shutil.rmtree(target, ignore_errors=True)
            else:
                _drop_readonly(target)
                target.unlink(missing_ok=True)
            for parent in (target.parent, target.parent.parent):
                try:                           # don't leave empty type/project shells behind
                    if parent != config.HOME:
                        parent.rmdir()
                except OSError:
                    break
        shutil.rmtree(config.VERSIONS_DIR / doc_id, ignore_errors=True)
        save_index([d for d in docs if d["id"] != doc_id])
        return True


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


def disk_path(doc: dict) -> str:
    """The document's absolute path on disk -- what the reader's "copy path" button yields.

    It gives you the file you can hand straight to an agent, so a site resolves
    to its entry page rather than its directory. A live entry has no copy, so all
    there is to give is whatever it points at.
    """
    kind = doc.get("kind", "page")
    if kind == "live":
        target = doc.get("target", "")
        if target[:8].lower() == "file:///":
            # This gets pasted to an agent, and an agent cannot read a file:// URL.
            # A live entry aimed at a local server (http://…) can only be the URL.
            return str(Path(unquote(urlparse(target).path).lstrip("/")))
        return target
    rel = doc.get("file") or ""
    if not rel:
        return ""
    target = config.HOME / rel
    if kind == "site":
        target = target / (doc.get("entry") or "index.html")
    return str(target)


def list_projects() -> list[dict]:
    """Projects that actually hold documents, with unread counts and colours."""
    docs = load_index()
    stats: dict[str, dict] = {}
    for d in docs:
        s = stats.setdefault(d["project"],
                             {"name": d["project"], "total": 0, "unread": 0})
        s["total"] += 1
        s["unread"] += 1 if d.get("unread") else 0
    for name, s in stats.items():
        s["color"] = projects.accent_color(name)
    # _inbox last: it is a holding pen, not a project.
    return sorted(stats.values(), key=lambda s: (s["name"] == config.INBOX, s["name"]))
