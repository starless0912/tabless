"""Where things live, and the few hard limits.

Everything another module needs to know about the filesystem layout is resolved
here, so `core` and `projects` can both depend on it without importing each
other.

Paths are re-readable: `reconfigure()` picks up a changed `TABLESS_HOME`, which
is what lets the test suite point at a throwaway library. Read them as
`config.HOME` rather than importing the name, so a reconfigure actually reaches
you.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

INBOX = "_inbox"
DEFAULT_TYPE = "report"

# Size ceiling for a site snapshot. Blowing past this means the dependency
# closure went somewhere it shouldn't have -- a reference that escaped to the
# drive root, say -- not that the report is genuinely enormous.
MAX_SITE_BYTES = 300 * 1024 * 1024

# How much history to keep per document. Without a ceiling the library grows
# silently, and this really happened: two comparison bundles from different days
# had identical titles, so they were folded into one document over and over, and
# every round parked another 230MB in `_index/versions/`. Four rounds, 711MB,
# and the document list showed nothing at all.
MAX_VERSION_KEEP = 5
MAX_VERSION_BYTES = 200 * 1024 * 1024

# Directories that must never be mistaken for content: debugging leftovers,
# dependencies, build output. A stray 194MB browser profile in a scratchpad is
# how this list started.
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", ".next", ".cache",
    ".venv", "venv", "site-packages", ".pytest_cache", "Extensions", ".idea",
}
SKIP_SUBSTR = ("chrome-profile", "chrome_profile", "playwright-", "-profile-")

HOST = "127.0.0.1"


def default_home() -> Path:
    """Where the library lives when TABLESS_HOME is not set.

    A library is content, not configuration -- it grows to hundreds of megabytes
    once site snapshots start landing -- so it belongs in the platform's data
    directory, not in a dotfile beside your shell config.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "tabless"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "tabless"
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "tabless"


def reconfigure() -> None:
    """Re-derive every path from the environment."""
    global HOME, INDEX_DIR, INDEX_FILE, VERSIONS_DIR
    global CACHE_DIR, CHROME_PROFILE, PROJECTS_FILE, PORT

    HOME = Path(os.environ.get("TABLESS_HOME") or default_home()).expanduser()

    INDEX_DIR = HOME / "_index"
    INDEX_FILE = INDEX_DIR / "index.json"
    VERSIONS_DIR = INDEX_DIR / "versions"

    # Scratch space that belongs to the library, not to the installed package.
    # The reader's browser profile alone grows past 200MB, and a pip-installed
    # package directory is the wrong place for that to accumulate.
    CACHE_DIR = HOME / ".cache"
    CHROME_PROFILE = CACHE_DIR / "chrome-profile"

    # Optional project table. Absent is fine -- everything lands in _inbox.
    PROJECTS_FILE = HOME / "projects.toml"

    PORT = int(os.environ.get("TABLESS_PORT", "6180"))


reconfigure()
