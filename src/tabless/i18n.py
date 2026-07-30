"""Bilingual strings for the CLI, the reader UI and the server's error pages.

One flat key/value file per language under `locales/`, shared by Python and by
the reader's JavaScript -- the server hands the reader its bundle at request
time. Keeping a single file per language (rather than one for Python and one
for the browser) is what stops the two halves of the UI from drifting apart.

Resolution order: TABLESS_LANG -> `lang` in the settings file -> the usual POSIX
locale variables -> whatever the OS reports -> English. Anything that looks like
`zh*` gets Chinese; everything else gets English, because those are the two
bundles that exist.
"""

from __future__ import annotations

import json
import os
import warnings
from functools import cache
from pathlib import Path

from . import config

LOCALES_DIR = Path(__file__).parent / "locales"
FALLBACK = "en"

_forced: str | None = None


def available() -> list[str]:
    return sorted(p.stem for p in LOCALES_DIR.glob("*.json"))


def _system_tag() -> str:
    """The rawest locale tag we can get, e.g. `zh_CN.UTF-8` or `en-GB`."""
    if os.environ.get("TABLESS_LANG"):
        return os.environ["TABLESS_LANG"]
    if config.LANG:
        return config.LANG
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var)
        if value:
            return value
    try:
        import locale
        with warnings.catch_warnings():
            # getdefaultlocale() is soft-deprecated but it is still the only
            # call that reports the OS preference without mutating the process's
            # global locale as a side effect. If a future runtime drops it, the
            # except below quietly falls through to English.
            warnings.simplefilter("ignore", DeprecationWarning)
            return locale.getdefaultlocale()[0] or ""
    except Exception:
        return ""


def lang() -> str:
    """The active language code -- one of `available()`."""
    if _forced:
        return _forced
    tag = _system_tag().strip().lower().replace("-", "_")
    # "C" and "POSIX" mean "no preference", not a language.
    if tag in ("c", "posix", ""):
        return FALLBACK
    head = tag.split("_")[0].split(".")[0]
    if head in available():
        return head
    # Windows reports things like "chinese (simplified)_china"; match on the
    # endonym-free prefix rather than trying to enumerate every spelling.
    if tag.startswith("zh") or "chinese" in tag:
        return "zh"
    return FALLBACK


def set_lang(code: str | None) -> None:
    """Pin the language, or pass None to go back to autodetection (tests use this)."""
    global _forced
    _forced = code
    _load.cache_clear()


@cache
def _load(code: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{code}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reload() -> None:
    """Drop the cached tables so an edited locale file takes effect.

    `_load` is cached for the life of the process, which is right for a CLI
    invocation and wrong for the long-lived service. The reader is re-read from
    disk on every request, so `/api/reload` used to ship a freshly edited UI
    whose new labels rendered as raw keys: the HTML was current and the strings
    were whatever had been on disk when the service started. Editing the reader
    and editing the locales is nearly always the same edit -- a new control
    needs a new label -- so they have to refresh together.
    """
    _load.cache_clear()


def t(key: str, **kwargs) -> str:
    """Look up `key`, falling back to English and then to the key itself.

    A missing key renders as the key, which is ugly on purpose: a visible
    `ui.starred_group` in the UI is a bug report, whereas a silently blank
    label is just a mystery.
    """
    table = _load(lang())
    text = table.get(key) or _load(FALLBACK).get(key) or key
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text


def bundle(*prefixes: str) -> dict[str, str]:
    """Every key under the given prefixes, English-backfilled.

    Used to hand the reader its strings in one go. Backfilling means a
    half-translated locale degrades to mixed languages rather than to raw keys.
    """
    out = dict(_load(FALLBACK))
    out.update(_load(lang()))
    if not prefixes:
        return out
    return {k: v for k, v in out.items() if k.startswith(prefixes)}
