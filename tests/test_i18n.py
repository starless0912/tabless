"""Tests for language resolution and the shared string table.

The bundles are shared between the CLI and the reader, so a key present in one
language and missing from the other is a UI defect in two places at once. The
parity test below is the cheap way to keep that from happening.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tabless import i18n


class LangEnvCase(unittest.TestCase):
    """Locale detection reads the environment, so save and restore all of it."""

    VARS = ("TABLESS_LANG", "LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")

    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in self.VARS}
        for k in self.VARS:
            os.environ.pop(k, None)
        i18n.set_lang(None)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        i18n.set_lang(None)


class TestResolution(LangEnvCase):
    def test_explicit_override_wins(self):
        os.environ["TABLESS_LANG"] = "zh"
        self.assertEqual(i18n.lang(), "zh")

    def test_regional_tags_reduce_to_a_bundle(self):
        for tag in ("zh_CN.UTF-8", "zh-Hans", "zh_TW"):
            os.environ["TABLESS_LANG"] = tag
            self.assertEqual(i18n.lang(), "zh", tag)
        for tag in ("en_GB", "en-US", "fr_FR"):
            os.environ["TABLESS_LANG"] = tag
            self.assertEqual(i18n.lang(), "en", tag)

    def test_windows_style_locale_names_are_understood(self):
        os.environ["TABLESS_LANG"] = "Chinese (Simplified)_China"
        self.assertEqual(i18n.lang(), "zh")

    def test_c_locale_means_no_preference(self):
        """`C` and `POSIX` are the absence of a language, not a language."""
        for tag in ("C", "POSIX"):
            os.environ["TABLESS_LANG"] = tag
            self.assertEqual(i18n.lang(), "en", tag)

    def test_pinning_overrides_the_environment(self):
        os.environ["TABLESS_LANG"] = "zh"
        i18n.set_lang("en")
        self.assertEqual(i18n.lang(), "en")


class TestLookup(LangEnvCase):
    def test_translation_and_formatting(self):
        i18n.set_lang("en")
        self.assertEqual(i18n.t("ui.starred_group"), "Starred")
        self.assertEqual(i18n.t("ui.count_docs", count=3), "3 docs")
        i18n.set_lang("zh")
        self.assertEqual(i18n.t("ui.starred_group"), "星标")

    def test_unknown_keys_render_as_themselves(self):
        """A visible `ui.nope` is a bug report; a blank label is a mystery."""
        self.assertEqual(i18n.t("ui.nope"), "ui.nope")

    def test_a_missing_placeholder_does_not_crash(self):
        self.assertIn("{", i18n.t("ui.count_docs"))

    def test_bundle_is_prefix_filtered_and_backfilled(self):
        i18n.set_lang("zh")
        ui = i18n.bundle("ui.", "type.")
        self.assertTrue(all(k.startswith(("ui.", "type.")) for k in ui))
        self.assertEqual(ui["ui.starred_group"], "星标")
        self.assertNotIn("cli.added", ui)

    def test_bundle_survives_json_round_trip(self):
        """It is injected into the reader as a JSON literal."""
        json.loads(json.dumps(i18n.bundle("ui."), ensure_ascii=False))


class TestReload(LangEnvCase):
    def test_an_edited_locale_file_takes_effect_after_reload(self):
        """The tables are cached for the life of the process. The reader is
        re-read from disk on every request, so `/api/reload` shipped a freshly
        edited UI whose new labels rendered as raw keys -- the HTML was current
        and the strings were whatever had been on disk at startup."""
        import tempfile

        original = i18n.LOCALES_DIR
        with tempfile.TemporaryDirectory() as tmp:
            i18n.LOCALES_DIR = Path(tmp)
            try:
                (i18n.LOCALES_DIR / "en.json").write_text(
                    '{"ui.probe": "before"}', encoding="utf-8")
                i18n.set_lang("en")
                self.assertEqual(i18n.t("ui.probe"), "before")

                (i18n.LOCALES_DIR / "en.json").write_text(
                    '{"ui.probe": "after"}', encoding="utf-8")
                self.assertEqual(i18n.t("ui.probe"), "before")  # still cached

                i18n.reload()
                self.assertEqual(i18n.t("ui.probe"), "after")
            finally:
                i18n.LOCALES_DIR = original
                i18n.set_lang(None)


class TestBundleParity(unittest.TestCase):
    def test_every_language_defines_the_same_keys(self):
        """A key in one bundle and not the other is a UI defect twice over."""
        tables = {code: json.loads((i18n.LOCALES_DIR / f"{code}.json")
                                   .read_text(encoding="utf-8"))
                  for code in i18n.available()}
        self.assertIn("en", tables)
        reference = set(tables["en"])
        for code, table in tables.items():
            self.assertEqual(set(table), reference,
                             f"{code}.json has different keys from en.json")

    def test_placeholders_match_across_languages(self):
        """A translation that drops `{count}` renders a sentence with a hole."""
        import re
        holes = lambda s: set(re.findall(r"\{(\w+)\}", s))  # noqa: E731
        tables = {code: json.loads((i18n.LOCALES_DIR / f"{code}.json")
                                   .read_text(encoding="utf-8"))
                  for code in i18n.available()}
        for key, text in tables["en"].items():
            for code, table in tables.items():
                self.assertEqual(holes(table[key]), holes(text),
                                 f"{code}.json:{key} has different placeholders")

    def test_no_key_is_empty(self):
        for code in i18n.available():
            table = json.loads((i18n.LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
            for key, value in table.items():
                self.assertTrue(str(value).strip(), f"{code}.json:{key} is empty")


if __name__ == "__main__":
    unittest.main()
