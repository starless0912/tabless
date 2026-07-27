"""The project axis: which project a file belongs to, and what colour it gets.

A project table is entirely optional. Without one everything lands in `_inbox`
and the tool still works -- you just lose the top-level tabs. With one, tabless
can look at a path and work out where a document belongs, which is what makes
`tabless add report.html` a complete command rather than one you have to
decorate with flags every time.

`<TABLESS_HOME>/projects.toml`:

    [projects.acme]
    path = "~/code/acme"
    tint = "#0d2624"          # optional; a colour is derived if omitted

    [sources]
    yaml = "~/some/other/registry.yaml"   # optional; needs pyyaml

The `[sources] yaml` hook exists because plenty of people already keep a list of
their projects somewhere -- a workspace manifest, a terminal theme config, a
dotfiles registry. Pointing at it beats maintaining the same list twice. Any YAML
document with a top-level `projects:` mapping of `{name: {path, tint}}` works.
"""

from __future__ import annotations

import colorsys
import hashlib
import re
import time
import tomllib
from pathlib import Path

from . import config

# Palette used when a project has no tint of its own. Assigned by hashing the
# name, so a given project keeps the same colour across machines and restarts.
FALLBACK_PALETTE = [
    "#f87171", "#60a5fa", "#4ade80", "#fbbf24", "#a78bfa",
    "#f472b6", "#2dd4bf", "#fb923c", "#818cf8", "#34d399",
]

# Where coding agents put their scratch files. Reversing these tells us the
# project without the caller having to say so.
#   Windows: …\Temp\claude\<SLUG>\<session>\scratchpad\…
#   POSIX:   /tmp/claude/<SLUG>/<session>/scratchpad/…
#   either:  ~/.claude/projects/<SLUG>/…
_SCRATCH_RES = [
    re.compile(r"/te?mp/claude/([^/]+)/"),
    re.compile(r"/\.claude/projects/([^/]+)/"),
]


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return {}


def _entries(raw: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, cfg in (raw.get("projects") or {}).items():
        if not isinstance(cfg, dict):
            continue
        path = str(cfg.get("path", "")).strip()
        # Relative paths cannot anchor a prefix match, so they are useless for
        # inference and would only produce confident wrong answers.
        if not path or path.startswith("."):
            continue
        out[str(name)] = {
            "path": str(Path(path).expanduser()),
            "tint": str(cfg.get("tint") or ""),
        }
    return out


def _load_yaml_table(path: Path) -> dict[str, dict]:
    """Read an external YAML project table, if one is configured and readable.

    Deliberately best-effort: a missing file, a missing pyyaml, or a malformed
    document all mean "no extra projects", never a crash. tabless has no
    required third-party dependencies and this must not become one.
    """
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return _entries(data if isinstance(data, dict) else {})


def _load() -> dict[str, dict]:
    raw = _read_toml(config.PROJECTS_FILE) if config.PROJECTS_FILE.exists() else {}
    table = _entries(raw)
    external = str((raw.get("sources") or {}).get("yaml") or "").strip()
    if external:
        # Local table wins on conflict: the file you edited by hand beats the
        # one you merely pointed at.
        merged = _load_yaml_table(Path(external).expanduser())
        merged.update(table)
        return merged
    return table


_cache: tuple[float, dict[str, dict]] | None = None


def all_projects() -> dict[str, dict]:
    """The project table, cached for ten seconds.

    Short cache rather than none: `scan` calls this once per candidate file, and
    the table is a file that something else may be rewriting underneath us.
    """
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < 10:
        return _cache[1]
    table = _load()
    _cache = (now, table)
    return table


def invalidate_cache() -> None:
    global _cache
    _cache = None


def accent_color(project: str) -> str:
    """The project's colour in the reader.

    Tints are often terminal background colours -- very dark, very desaturated
    (`#0d1525`) -- and unreadable as UI accents. So keep the hue and push
    lightness and saturation into a usable range: the reader's colour and the
    terminal's stay recognisably the same family without one dictating the other.
    """
    tint = (all_projects().get(project) or {}).get("tint") or ""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", tint.strip())
    if m:
        raw = m.group(1)
        r, g, b = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
        h, _l, s = colorsys.rgb_to_hls(r, g, b)
        if s > 0.05:  # a grey tint has no hue worth keeping; fall through to the hash
            r, g, b = colorsys.hls_to_rgb(h, 0.66, 0.72)
            rgb = (round(r * 255), round(g * 255), round(b * 255))
            return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    idx = int(hashlib.md5(project.encode("utf-8")).hexdigest(), 16)
    return FALLBACK_PALETTE[idx % len(FALLBACK_PALETTE)]


def _cc_slug(path: str) -> str:
    """The slug a coding agent derives from a project path.

    `D:/code/acme_lab` -> `D--code-acme-lab`, `/home/me/acme` -> `-home-me-acme`
    """
    return re.sub(r"[:/\\_.]", "-", path)


def _norm(p: str) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def _real(p: str) -> str:
    """The symlink-resolved form of a path, for prefix matching.

    Comparing the literal strings is not enough, and fails quietly rather than
    loudly. `add_document` resolves the file it is given, while the project
    table holds whatever was typed into it -- and on macOS `/tmp` and `/var` are
    symlinks into `/private`, so those two forms never match. Windows can hand
    back 8.3 short paths for the same reason. The document then lands in
    `_inbox` with nothing to indicate why.

    Both forms are tried, so a table entry written either way still matches.
    """
    try:
        return _norm(Path(p).resolve())
    except (OSError, ValueError):
        return _norm(p)


def normalize(name: str) -> str:
    """Map an explicitly supplied project name onto a registered one.

    Callers reach for the working directory's name (`AcmeLab`) far more readily
    than the registered one (`acme`), and without this the same project splits
    into two tabs -- which is the exact failure this tool exists to prevent.
    This has genuinely happened.

    Names that match nothing are kept verbatim: that may well be a real project
    nobody has registered yet, and silently renaming it would be worse.
    """
    name = (name or "").strip()
    if not name:
        return ""
    table = all_projects()
    if name in table:
        return name
    low = name.lower()
    for p in table:
        if p.lower() == low:
            return p
    # Directory name -> registered name: AcmeLab -> …/AcmeLab -> acme
    for p, cfg in table.items():
        if Path(cfg["path"]).name.lower() == low:
            return p
    return name


def infer(src: Path, cwd: Path | None = None) -> str | None:
    """Work out the project from a path. None means "file it under _inbox"."""
    table = all_projects()
    candidates = [str(src)] + ([str(cwd)] if cwd else [])

    # 1. An agent scratchpad path carries the project inside it.
    for cand in candidates:
        normalized = _norm(cand)
        for pattern in _SCRATCH_RES:
            m = pattern.search(normalized)
            if not m:
                continue
            slug = m.group(1)
            for name, cfg in table.items():
                if _cc_slug(_norm(cfg["path"])) == slug.lower():
                    return name

    # 2. Longest path-prefix wins -- otherwise a parent directory registered as
    #    its own project swallows every project nested beneath it.
    #
    #    Both the literal and the symlink-resolved form of each side are tried,
    #    because the caller's path has usually been resolved and the table's has
    #    not. See `_real` for why that mismatch is silent and therefore nasty.
    for cand in candidates:
        forms = {_norm(cand), _real(cand)}
        best: tuple[int, str] | None = None
        for name, cfg in table.items():
            for base in {_norm(cfg["path"]), _real(cfg["path"])}:
                if not base:
                    continue
                matches = any(f == base or f.startswith(base + "/") for f in forms)
                if matches and (best is None or len(base) > best[0]):
                    best = (len(base), name)
        if best:
            return best[1]

    return None
