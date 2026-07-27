"""Talking to the local reader service, and starting it if nobody has.

The CLI never renders anything itself. It archives, then tells the service, and
the service decides whether that means pushing into a window that is already
open or starting one. Concentrating that decision in one place is what keeps the
promise that N reports produce one window.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .i18n import t


def _url(path: str) -> str:
    return f"http://{config.HOST}:{config.PORT}{path}"


def alive(timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(_url("/api/ping"), timeout=timeout):
            return True
    except Exception:
        return False


def _spawn_kwargs() -> dict:
    """Detach the service from this terminal, however the platform spells that."""
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NO_WINDOW -- no console flashes into view.
        return {"creationflags": 0x00000008 | 0x08000000}
    return {"start_new_session": True}


def _interpreter() -> str:
    """Prefer a windowless interpreter on Windows; elsewhere there is only one."""
    if os.name == "nt":
        pyw = Path(sys.executable).with_name("pythonw.exe")
        if pyw.exists():
            return str(pyw)
    return sys.executable


def ensure() -> bool:
    """Start the service if it isn't running. Waits up to six seconds for it."""
    if alive():
        return True
    try:
        subprocess.Popen(
            [_interpreter(), "-m", "tabless.server"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_spawn_kwargs(),
        )
    except Exception as exc:
        print(t("cli.server_spawn_failed", error=exc), file=sys.stderr)
        return False
    for _ in range(30):
        time.sleep(0.2)
        if alive():
            return True
    return False


def _post(path: str, payload: dict, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(
        _url(path),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read()
    return json.loads(body) if body else {}


def notify(entry: dict) -> bool:
    """Tell the service a document arrived. It decides push-vs-open."""
    try:
        _post("/api/notify", entry, timeout=8.0)
        return True
    except Exception:
        return False


def open_reader(project: str, doc_id: str = "") -> dict:
    """Show a project in the reader. Raises on failure so the CLI can report it."""
    return _post("/api/open", {"project": project, "doc": doc_id})
