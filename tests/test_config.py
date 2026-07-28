"""Tests for how tabless decides where the library is.

Getting this wrong has a uniquely bad failure mode: pointing at the default
location when the real library is elsewhere does not raise anything, it just
shows an empty shelf. "My documents are gone" is the worst answer this tool
could give, so the resolution order gets its own tests.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tabless import config


class ConfigCase(unittest.TestCase):
    VARS = ("TABLESS_HOME", "TABLESS_PORT", "TABLESS_MAX_SITE_MB",
            "APPDATA", "XDG_CONFIG_HOME")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tabless-cfg-"))
        self._saved = {k: os.environ.get(k) for k in self.VARS}
        for k in self.VARS:
            os.environ.pop(k, None)
        # Redirect the settings file into the sandbox on every platform.
        os.environ["APPDATA"] = str(self.tmp / "roaming")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        config.reconfigure()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.reconfigure()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_settings(self, text: str) -> None:
        path = config.config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        config.reconfigure()


class TestResolutionOrder(ConfigCase):
    def test_default_when_nothing_is_configured(self):
        self.assertEqual(config.HOME, config.default_home())

    def test_settings_file_moves_the_library(self):
        target = (self.tmp / "elsewhere").as_posix()
        self.write_settings(f'home = "{target}"\n')
        self.assertEqual(config.HOME, Path(target))

    def test_environment_beats_the_settings_file(self):
        self.write_settings(f'home = "{(self.tmp / "from-file").as_posix()}"\n')
        os.environ["TABLESS_HOME"] = str(self.tmp / "from-env")
        config.reconfigure()
        self.assertEqual(config.HOME, self.tmp / "from-env")

    def test_home_expands_a_tilde(self):
        self.write_settings('home = "~/my-library"\n')
        self.assertEqual(config.HOME, Path.home() / "my-library")

    def test_port_comes_from_the_file_and_yields_to_the_environment(self):
        self.write_settings("port = 6199\n")
        self.assertEqual(config.PORT, 6199)
        os.environ["TABLESS_PORT"] = "6200"
        config.reconfigure()
        self.assertEqual(config.PORT, 6200)

    def test_a_nonsense_port_falls_back_instead_of_crashing(self):
        self.write_settings('port = "not a number"\n')
        self.assertEqual(config.PORT, 6180)

    def test_site_ceiling_comes_from_the_file_and_yields_to_the_environment(self):
        """The 300MB default is a tripwire for a closure that escaped, but some
        documents genuinely are that big: three blind-eval bundles (609MB to
        954MB) could not be archived at all until the ceiling became settable."""
        self.write_settings("max_site_mb = 1500\n")
        self.assertEqual(config.MAX_SITE_BYTES, 1500 * 1024 * 1024)
        os.environ["TABLESS_MAX_SITE_MB"] = "64"
        config.reconfigure()
        self.assertEqual(config.MAX_SITE_BYTES, 64 * 1024 * 1024)

    def test_a_nonsense_site_ceiling_falls_back_instead_of_crashing(self):
        """Garbage, zero and negative all mean the default -- a ceiling of -5MB
        would quietly refuse every site snapshot from then on."""
        for bad in ('max_site_mb = "huge"\n', "max_site_mb = 0\n",
                    "max_site_mb = -5\n"):
            self.write_settings(bad)
            self.assertEqual(config.MAX_SITE_BYTES, 300 * 1024 * 1024, bad)

    def test_a_malformed_settings_file_is_treated_as_absent(self):
        """A broken config must not stop you reaching the library at all."""
        self.write_settings("this is not = valid toml [[[\n")
        self.assertEqual(config.HOME, config.default_home())

    def test_settings_live_outside_the_library(self):
        """Config inside the data directory could not tell you where the data
        directory is."""
        self.write_settings(f'home = "{(self.tmp / "lib").as_posix()}"\n')
        self.assertFalse(
            str(config.config_file()).startswith(str(config.HOME)))


class TestDerivedPaths(ConfigCase):
    def test_everything_hangs_off_home(self):
        os.environ["TABLESS_HOME"] = str(self.tmp / "lib")
        config.reconfigure()
        for path in (config.INDEX_FILE, config.VERSIONS_DIR,
                     config.CACHE_DIR, config.PROJECTS_FILE):
            self.assertTrue(str(path).startswith(str(config.HOME)), path)

    def test_the_browser_profile_is_not_in_the_package_directory(self):
        """It grows past 200MB; an installed package is the wrong place for it."""
        self.assertTrue(str(config.CHROME_PROFILE).startswith(str(config.HOME)))
        self.assertNotIn("site-packages", str(config.CHROME_PROFILE))

    def test_default_home_is_platform_appropriate(self):
        home = str(config.default_home()).lower()
        self.assertIn("tabless", home)
        if sys.platform == "darwin":
            self.assertIn("application support", home)
        elif sys.platform != "win32":
            self.assertIn(".local", home)


if __name__ == "__main__":
    unittest.main()
