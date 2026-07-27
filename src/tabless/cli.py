"""The command line. Everything an agent needs is `tabless add`.

Commands print one line and get out of the way, because most of the time the
caller is a coding agent wrapping up a task, not a person reading output. The
one exception is `add` announcing a type it has never seen before -- see
`_cmd_add` for why that is worth interrupting for.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import __version__, client, config, core, demo, i18n
from .i18n import t

PREFIX = "[tabless]"
KIND_MARK = {"site": "▤", "live": "◈", "page": " "}


def say(message: str, err: bool = False) -> None:
    print(f"{PREFIX} {message}", file=sys.stderr if err else sys.stdout)


def _where(doc: dict) -> str:
    return f"{doc['project']}/{doc.get('type') or config.DEFAULT_TYPE}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_add(args) -> int:
    before = set(core.known_types())
    entry = core.add_document(
        args.path, project=args.project, title=args.title,
        source=args.source, cwd=args.cwd, doctype=args.type,
    )
    tag = (t("cli.updated", version=entry["version"]) if entry["version"] > 1
           else t("cli.added"))
    say(f"{tag}: [{_where(entry)}] {entry['title']}")

    # A new type means a new folder in the reader. Usually that is intentional,
    # but a typo looks exactly the same and would otherwise pass in silence --
    # and a set that has quietly shattered into `report`/`reports`/`Reports` is
    # the failure this tool exists to prevent. So: name it, and list what's there.
    dtype = entry.get("type", config.DEFAULT_TYPE)
    if dtype not in before:
        say(t("cli.new_type", type=dtype,
              others=", ".join(sorted(before)) or t("cli.none")))

    if not args.no_open:
        if client.ensure():
            client.notify(entry)
        else:
            say(t("cli.server_not_ready"), err=True)
    return 0


def cmd_scan(args) -> int:
    """Adopt HTML that is already lying around.

    Defaults to the agent scratchpad rather than your project directories: the
    overwhelming majority of HTML inside a repository is build output, and
    pouring that into the library would be tipping noise into the place you
    just tidied.
    """
    roots = ([Path(p) for p in args.roots] if args.roots
             else [Path(tempfile.gettempdir()) / "claude"])
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            candidates += [f for f in root.rglob("*.html") if not core.is_skipped(f)]

    # HTML files that link to each other are one site, and should become one
    # entry (a six-page experiment board is one thing, not six).
    #
    # "Exclude anything that was referenced" does not work: index.html and
    # rules.html reference each other, so both look like inner pages and the
    # site ends up with no entry point at all. Connected components, then pick a
    # representative.
    candidates = sorted({f.resolve() for f in candidates})
    graph: dict[Path, set[Path]] = {f: set() for f in candidates}
    for f in candidates:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ref in core.local_refs(text, f):
            if ref in graph and ref != f:
                graph[f].add(ref)
                graph[ref].add(f)

    entries: list[Path] = []
    seen_nodes: set[Path] = set()
    inner_count = 0
    for f in candidates:
        if f in seen_nodes:
            continue
        comp, stack = [], [f]
        while stack:
            n = stack.pop()
            if n in seen_nodes:
                continue
            seen_nodes.add(n)
            comp.append(n)
            stack.extend(m for m in graph[n] if m not in seen_nodes)
        # Representative: an index.* if there is one, otherwise the shallowest path.
        rep = min(comp, key=lambda p: (p.stem.lower() != "index", len(p.parts), str(p)))
        entries.append(rep)
        inner_count += len(comp) - 1

    known_origins = {d.get("origin") for d in core.load_index()}
    added = skipped = 0
    # Oldest first: when same-titled documents fold into versions, the newest
    # has to be applied last or the "current" version ends up being an old one.
    for f in sorted(entries, key=lambda p: p.stat().st_mtime):
        if str(f.resolve()) in known_origins:
            skipped += 1
            continue
        try:
            entry = core.add_document(f, source=args.source, doctype=args.type)
        except Exception as exc:
            say(t("cli.scan_skipped", name=f.name, error=exc), err=True)
            skipped += 1
            continue
        added += 1
        mark = f"↑v{entry['version']}" if entry["version"] > 1 else " + "
        extra = (f"  {t('cli.scan_assets', count=entry['assets'])}"
                 if entry["kind"] == "site" else "")
        print(f" {mark} [{_where(entry)}] {entry['title']}{extra}")

    say(t("cli.scan_summary", candidates=len(candidates), entries=len(entries),
          inner=inner_count, added=added, skipped=skipped))
    return 0


def cmd_live(args) -> int:
    entry = core.add_live(
        target=args.target, project=args.project,
        title=args.title, hint=args.hint or "", doctype=args.type,
    )
    say(f"{t('cli.registered_live')}: [{_where(entry)}] "
        f"{entry['title']} → {entry['target']}")
    return 0


def cmd_list(args) -> int:
    docs = core.load_index()
    if args.project:
        docs = [d for d in docs if d["project"] == args.project]
    if args.type:
        want = core.normalize_type(args.type)
        docs = [d for d in docs if (d.get("type") or config.DEFAULT_TYPE) == want]
    for d in sorted(docs, key=lambda d: d["updated_at"], reverse=True):
        mark = "●" if d.get("unread") else " "
        kind = KIND_MARK.get(d.get("kind", "page"), " ")
        ver = f" v{d['version']}" if d["version"] > 1 else ""
        extra = (f"  ({d['assets']} · {d['bytes'] / 1048576:.1f}M)"
                 if d.get("assets") else "")
        print(f"{mark}{kind} {d['id']}  [{_where(d):<22}] "
              f"{d['updated_at'][:16]}  {d['title']}{ver}{extra}")
    print(t("cli.list_footer", count=len(docs)))
    return 0


def cmd_projects(_args) -> int:
    for p in core.list_projects():
        unread = f" {t('cli.projects_unread', count=p['unread'])}" if p["unread"] else ""
        print(f"{p['color']}  {p['name']:<16} "
              f"{t('cli.projects_docs', count=p['total'])}{unread}")
    return 0


def cmd_types(_args) -> int:
    """List the types in use -- a glance before `add` saves a folder full of synonyms."""
    docs = core.load_index()
    stats: dict[str, dict[str, int]] = {}
    for d in docs:
        s = stats.setdefault(d.get("type") or config.DEFAULT_TYPE, {})
        s[d["project"]] = s.get(d["project"], 0) + 1
    if not stats:
        say(t("cli.empty_library"))
        return 0
    for ty, per in sorted(stats.items(), key=lambda kv: -sum(kv[1].values())):
        where = "  ".join(f"{p}×{n}" for p, n
                          in sorted(per.items(), key=lambda kv: -kv[1]))
        print(f"{ty:<14} {t('cli.types_row', count=sum(per.values())):>10}   {where}")
    print(t("cli.types_footer", count=len(stats)))
    return 0


def cmd_retype(args) -> int:
    doc = core.retype_document(args.id, args.type)
    if not doc:
        say(t("cli.not_found", id=args.id), err=True)
        return 1
    say(f"{t('cli.refiled')}: [{_where(doc)}] {doc['title']}")
    return 0


def _latest_project() -> str:
    """The most recently updated project -- where a bare `open` lands."""
    docs = core.load_index()
    return max(docs, key=lambda d: d["updated_at"])["project"] if docs else ""


def cmd_open(args) -> int:
    if not client.ensure():
        say(t("cli.server_start_failed"), err=True)
        return 1
    project = args.project
    if project is None:
        project = "" if args.all else _latest_project()
    try:
        res = client.open_reader(project)
    except Exception as exc:
        say(t("cli.open_failed", error=exc), err=True)
        return 1
    verb = t("cli.switched") if res.get("action") == "switched-tab" else t("cli.opened")
    say(f"{verb} {project or t('cli.everything')} {t('cli.open_note')}")
    return 0


def cmd_where(_args) -> int:
    rows = [
        (t("cli.where_home"), config.HOME),
        (t("cli.where_config"), config.config_file()),
        (t("cli.where_index"), config.INDEX_FILE),
        (t("cli.where_projects"), config.PROJECTS_FILE),
        (t("cli.where_cache"), config.CACHE_DIR),
    ]
    width = max(len(label) for label, _ in rows)
    for label, path in rows:
        missing = "" if Path(path).exists() else f"  {t('cli.where_missing')}"
        print(f"{label:<{width}}  {path}{missing}")
    print(f"{t('cli.where_port'):<{width}}  {config.PORT}")
    print(f"{t('cli.where_language'):<{width}}  {i18n.lang()}")
    return 0


def cmd_demo(args) -> int:
    entries = demo.install()
    say(t("cli.demo_done", count=len(entries)))
    if not args.no_open:
        return cmd_open(argparse.Namespace(project=None, all=True))
    return 0


def cmd_server(_args) -> int:
    from . import server
    return server.main()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tabless", description=t("help.prog"))
    parser.add_argument("--version", action="version", version=f"tabless {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help=t("help.add"))
    p.add_argument("path", help=t("help.add.path"))
    p.add_argument("--project", help=t("help.add.project"))
    p.add_argument("--type", help=t("help.add.type"))
    p.add_argument("--title", help=t("help.add.title"))
    p.add_argument("--source", default="agent", help=t("help.add.source"))
    p.add_argument("--cwd", help=t("help.add.cwd"))
    p.add_argument("--no-open", action="store_true", help=t("help.add.no_open"))
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("scan", help=t("help.scan"))
    p.add_argument("roots", nargs="*", help=t("help.scan.roots"))
    p.add_argument("--source", default="agent")
    p.add_argument("--type", help=t("help.scan.type"))
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("live", help=t("help.live"))
    p.add_argument("target", help=t("help.live.target"))
    p.add_argument("--project", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--hint", help=t("help.live.hint"))
    p.add_argument("--type", help=t("help.live.type"))
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("list", help=t("help.list"))
    p.add_argument("--project")
    p.add_argument("--type")
    p.set_defaults(func=cmd_list)

    sub.add_parser("projects", help=t("help.projects")).set_defaults(func=cmd_projects)
    sub.add_parser("types", help=t("help.types")).set_defaults(func=cmd_types)

    p = sub.add_parser("retype", help=t("help.retype"))
    p.add_argument("id", help=t("help.retype.id"))
    p.add_argument("type")
    p.set_defaults(func=cmd_retype)

    p = sub.add_parser("open", help=t("help.open"))
    p.add_argument("project", nargs="?", help=t("help.open.project"))
    p.add_argument("--all", action="store_true", help=t("help.open.all"))
    p.set_defaults(func=cmd_open)

    sub.add_parser("where", help=t("help.where")).set_defaults(func=cmd_where)

    p = sub.add_parser("demo", help=t("help.demo"))
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(func=cmd_demo)

    sub.add_parser("server", help=t("help.server")).set_defaults(func=cmd_server)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Under a windowless interpreter there is no console at all and sys.stdout
    # is None, which makes the first print() an AttributeError.
    # These streams live for the whole process, so a context manager is exactly
    # the wrong shape here.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    # A console may default to a legacy codepage, where printing a non-ASCII
    # title raises UnicodeEncodeError before anything useful happens.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        say(str(exc), err=True)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
