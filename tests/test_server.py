"""Tests for the reader service's request handling.

Same principle as `test_core`: most of these guard a specific decision that was
arrived at the hard way -- why the scrollbar is layered, why site entries
redirect instead of getting a `<base>` tag, why HTML never serves Range.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tabless import config, core, projects, server


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tabless-srv-"))
        self._saved = os.environ.get("TABLESS_HOME")
        os.environ["TABLESS_HOME"] = str(self.tmp / "home")
        config.reconfigure()
        projects.invalidate_cache()
        core.ensure_dirs()

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("TABLESS_HOME", None)
        else:
            os.environ["TABLESS_HOME"] = self._saved
        config.reconfigure()
        projects.invalidate_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestInjectChrome(unittest.TestCase):
    def test_style_lands_in_the_head(self):
        out = server.inject_chrome(b"<html><head><title>x</title></head><body>hi</body></html>")
        text = out.decode("utf-8")
        self.assertIn('data-tabless="scrollbar"', text)
        self.assertLess(text.index("data-tabless"), text.index("</head>"))

    def test_the_rules_are_layered(self):
        """Layered rules always lose to unlayered ones, so a document that
        styled its own scrollbar keeps its design. Dropping the layer would mean
        the library silently rewriting somebody's visuals."""
        self.assertIn("@layer tabless-scrollbar", server.SCROLLBAR_CSS)

    def test_falls_back_to_after_body_when_there_is_no_head(self):
        out = server.inject_chrome(b"<body>bare</body>").decode("utf-8")
        self.assertIn("data-tabless", out)
        self.assertLess(out.index("<body>"), out.index("data-tabless"))

    def test_fragment_without_head_or_body_still_works(self):
        self.assertIn(b"data-tabless", server.inject_chrome(b"<p>just a fragment</p>"))

    def test_non_utf8_bytes_are_returned_untouched(self):
        """Better ugly than broken: force-decoding a legacy document with
        replacement characters corrupts it permanently on screen."""
        raw = "<html><head></head><body>caf\xe9</body></html>".encode("latin-1")
        self.assertEqual(server.inject_chrome(raw), raw)

    def test_huge_documents_are_passed_through(self):
        raw = b"<html><head></head><body>" + b"x" * (9 * 1024 * 1024) + b"</body></html>"
        self.assertEqual(server.inject_chrome(raw), raw)


class TestReaderPage(unittest.TestCase):
    def test_language_pack_is_injected(self):
        text = server.reader_page().decode("utf-8")
        self.assertIn("window.__TABLESS_I18N__", text)
        self.assertIn("ui.starred_group", text)
        self.assertLess(text.index("__TABLESS_I18N__"), text.index("</head>"))

    def test_html_lang_follows_the_locale(self):
        saved = os.environ.get("TABLESS_LANG")
        try:
            os.environ["TABLESS_LANG"] = "zh_CN"
            self.assertIn('<html lang="zh">', server.reader_page().decode("utf-8"))
            os.environ["TABLESS_LANG"] = "en_US"
            self.assertIn('<html lang="en">', server.reader_page().decode("utf-8"))
        finally:
            os.environ.pop("TABLESS_LANG", None)
            if saved is not None:
                os.environ["TABLESS_LANG"] = saved

    def test_the_reader_ships_with_the_package(self):
        self.assertTrue(server.READER_HTML.is_file())


class TestPathGuard(ServerCase):
    def test_paths_inside_the_library_pass(self):
        target = config.HOME / "proj" / "report" / "a.html"
        target.parent.mkdir(parents=True)
        target.write_text("x", encoding="utf-8")
        self.assertTrue(server.inside_library(target))

    def test_traversal_out_of_the_library_is_refused(self):
        """`..` survives URL decoding, and the site route joins caller-supplied
        text onto a real directory."""
        escaped = config.HOME / "proj" / ".." / ".." / ".." / "etc" / "passwd"
        self.assertFalse(server.inside_library(escaped))
        self.assertFalse(server.inside_library(Path(tempfile.gettempdir()) / "elsewhere"))


class TestDecorate(ServerCase):
    def test_disk_path_is_added_at_response_time(self):
        src = self.tmp / "r.html"
        src.write_text("<title>T</title>", encoding="utf-8")
        entry = core.add_document(src, project="p")
        self.assertTrue(server.decorate(entry)["path"].endswith(".html"))

    def test_missing_type_is_backfilled(self):
        """Entries written before types existed must not force null checks
        through the whole front end."""
        self.assertEqual(server.decorate({"id": "x", "kind": "page"})["type"],
                         config.DEFAULT_TYPE)


class TestServerConfiguration(unittest.TestCase):
    def test_address_reuse_matches_the_platform(self):
        """The two platforms want opposite things here.

        Windows lets two processes bind the same port when SO_REUSEADDR is on,
        so it must be off and a failed bind becomes a reliable "already
        running". POSIX never permits two live binds; there the flag only skips
        TIME_WAIT, and leaving it off makes a quick restart look like a running
        server that isn't there.
        """
        self.assertEqual(server.Server.allow_reuse_address, os.name != "nt")

    def test_it_binds_loopback_only(self):
        self.assertEqual(config.HOST, "127.0.0.1")

    def test_browser_lookup_does_not_raise(self):
        result = server.find_chrome()
        self.assertTrue(result is None or Path(result).exists())


if __name__ == "__main__":
    unittest.main()
