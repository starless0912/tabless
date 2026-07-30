"""Tests for the archiving core.

These are deliberately not generic coverage. Almost every case below is a bug
that actually happened -- a report that silently vanished, a library that grew
by 700MB without showing a single new row, a delete that pointed at the library
root. Each test is here to stop one of them coming back, and the docstrings say
which one.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tabless import config, core, projects

PAGE = "<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>"


class LibraryCase(unittest.TestCase):
    """A throwaway library per test, so nothing leaks between them."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tabless-test-"))
        self._saved_home = os.environ.get("TABLESS_HOME")
        os.environ["TABLESS_HOME"] = str(self.tmp / "home")
        config.reconfigure()
        projects.invalidate_cache()
        core.ensure_dirs()
        self.src = self.tmp / "src"
        self.src.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        if self._saved_home is None:
            os.environ.pop("TABLESS_HOME", None)
        else:
            os.environ["TABLESS_HOME"] = self._saved_home
        config.reconfigure()
        projects.invalidate_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers ---------------------------------------------------------------

    def write(self, name: str, title: str = "Sample", body: str = "hello") -> Path:
        path = self.src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PAGE.format(title=title, body=body), encoding="utf-8")
        return path

    def set_projects(self, text: str) -> None:
        config.PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.PROJECTS_FILE.write_text(text, encoding="utf-8")
        projects.invalidate_cache()


# ---------------------------------------------------------------------------
# Type normalisation -- the open set that must not shatter
# ---------------------------------------------------------------------------


class TestNormalizeType(unittest.TestCase):
    def test_aliases_fold(self):
        for raw, want in [("reports", "report"), ("报告", "report"),
                          ("PROTO", "prototype"), ("Docs", "doc"),
                          ("wiki", "doc"), ("benchmark", "eval")]:
            self.assertEqual(core.normalize_type(raw), want, raw)

    def test_unknown_names_survive(self):
        """An open set means an unrecognised type is kept, not rejected."""
        self.assertEqual(core.normalize_type("postmortem"), "postmortem")
        self.assertEqual(core.normalize_type("Runbook  Notes"), "runbook-notes")

    def test_empty_falls_back(self):
        self.assertEqual(core.normalize_type(None), config.DEFAULT_TYPE)
        self.assertEqual(core.normalize_type("   "), config.DEFAULT_TYPE)

    def test_illegal_characters_stripped(self):
        """A type becomes a directory name, so it cannot contain path syntax."""
        self.assertEqual(core.normalize_type("a/b:c"), "abc")
        self.assertNotIn("/", core.normalize_type("../escape"))

    def test_star_group_key_cannot_collide(self):
        """The reader's starred pseudo-group is `__starred__`; underscores fold
        to hyphens, so no real type can ever produce that key."""
        self.assertNotEqual(core.normalize_type("__starred__"), "__starred__")

    def test_date_bucket_keys_cannot_collide(self):
        """Grouping by date shares one collapsed-state set with grouping by
        type, so a bucket key that a real type could produce would make
        collapsing "Today" also collapse somebody's type. The buckets borrow
        the `__x__` shape from the starred group for exactly that reason."""
        for key in ("__d_today__", "__d_3__", "__d_7__", "__d_old__"):
            self.assertNotEqual(core.normalize_type(key), key)


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


class TestTitles(unittest.TestCase):
    def test_entities_are_decoded(self):
        """Some exporters write the whole title as numeric entities. Listing
        `&#36816;&#21160;…` as a title makes the document unfindable."""
        html = "<title>&#36816;&#21160;&#25968;&#25454;</title>"
        self.assertEqual(core.extract_title(html, "fallback"), "运动数据")

    def test_tags_stripped_before_entities(self):
        """Order matters: strip tags, then unescape. Reversed, a `&lt;b&gt;` in
        the source would turn into a tag and then be stripped as one."""
        html = "<title>Week <b>31</b> &amp; 32</title>"
        self.assertEqual(core.extract_title(html, "x"), "Week 31 & 32")

    def test_h1_is_the_second_choice(self):
        self.assertEqual(core.extract_title("<h1>From H1</h1>", "x"), "From H1")

    def test_fallback_when_nothing_usable(self):
        self.assertEqual(core.extract_title("<p>no title</p>", "the-file"), "the-file")

    def test_truncation_never_splits_an_entity(self):
        """Decoding happens before the 120-char cut, so the cut lands on real
        characters. A trailing `&#210` fragment is both unreadable and unsearchable."""
        title = core.extract_title(f"<title>{'&#36816;' * 200}</title>", "x")
        self.assertEqual(len(title), 120)
        self.assertNotIn("&#", title)

    def test_slugify_keeps_non_ascii(self):
        """You have to be able to recognise the file in a file browser."""
        self.assertEqual(core.slugify("运动 数据"), "运动-数据")
        self.assertNotIn("/", core.slugify("a/b"))
        self.assertEqual(core.slugify("///"), "untitled")


# ---------------------------------------------------------------------------
# Page vs site
# ---------------------------------------------------------------------------


class TestClosure(LibraryCase):
    def test_self_contained_html_is_a_page(self):
        entry = core.add_document(self.write("solo.html"), project="p")
        self.assertEqual(entry["kind"], "page")
        self.assertEqual(entry["assets"], 0)
        self.assertTrue((config.HOME / entry["file"]).is_file())

    def test_referencing_a_file_makes_it_a_site(self):
        (self.src / "assets").mkdir()
        (self.src / "assets" / "logo.svg").write_text("<svg/>", encoding="utf-8")
        page = self.write("index.html", body='<img src="assets/logo.svg">')

        entry = core.add_document(page, project="p")
        self.assertEqual(entry["kind"], "site")
        self.assertEqual(entry["assets"], 1)
        root = config.HOME / entry["file"]
        self.assertTrue(root.is_dir())
        # Relative structure has to survive, or the copy's own links break.
        self.assertTrue((root / "assets" / "logo.svg").is_file())
        self.assertEqual(entry["entry"], "index.html")

    def test_only_referenced_files_are_copied(self):
        """The closure, not the directory. A neighbouring 25MB of goldens must
        not ride along just for sitting in the same folder."""
        (self.src / "used.css").write_text("body{}", encoding="utf-8")
        (self.src / "unused.css").write_text("body{}", encoding="utf-8")
        page = self.write("index.html", body='<link href="used.css">')

        entry = core.add_document(page, project="p")
        root = config.HOME / entry["file"]
        self.assertTrue((root / "used.css").is_file())
        self.assertFalse((root / "unused.css").exists())

    def test_remote_and_data_urls_are_not_dependencies(self):
        page = self.write("index.html", body=(
            '<img src="https://example.com/a.png">'
            '<img src="data:image/gif;base64,R0lGOD">'
            '<a href="#section">jump</a>'))
        self.assertEqual(core.add_document(page, project="p")["kind"], "page")

    def test_css_url_references_are_followed(self):
        (self.src / "bg.png").write_bytes(b"\x89PNG")
        (self.src / "style.css").write_text("body{background:url(bg.png)}", encoding="utf-8")
        page = self.write("index.html", body='<link href="style.css">')
        entry = core.add_document(page, project="p")
        self.assertEqual(entry["assets"], 2)      # the stylesheet and the image

    def test_media_named_only_in_a_script_is_still_a_dependency(self):
        """The blind-eval regression: a page that builds its <video> grid from a
        script-side data array carries no src= attributes, so the closure saw
        zero attachments and archived a 40KB shell -- while 267MB of clips
        stayed behind in a scratch directory, waiting to be cleaned."""
        (self.src / "media").mkdir()
        (self.src / "media" / "c01.mp4").write_bytes(b"vid")
        page = self.write("review.html", body=(
            '<script>const CLIPS = ["media/c01.mp4"];</script>'))
        entry = core.add_document(page, project="p")
        self.assertEqual(entry["kind"], "site")
        self.assertTrue(
            (config.HOME / entry["file"] / "media" / "c01.mp4").is_file())

    def test_a_mentioned_but_absent_media_name_is_not_a_dependency(self):
        """The other half of the bargain above: scanning every quoted string
        must not let prose that merely mentions a filename drag a page into
        being a site. Only strings that resolve to a real file count."""
        page = self.write("post.html", body=(
            "<script>let note = 'missing/demo.mp4';</script>"))
        self.assertEqual(core.add_document(page, project="p")["kind"], "page")

    def test_media_named_in_an_external_script_is_followed(self):
        """Same failure one level deeper: the data array naming the clips often
        lives in its own script file, so .js has to be parseable too."""
        (self.src / "app.js").write_text('const V = ["clip.mp4"];', encoding="utf-8")
        (self.src / "clip.mp4").write_bytes(b"vid")
        page = self.write("index.html", body='<script src="app.js"></script>')
        entry = core.add_document(page, project="p")
        self.assertEqual(entry["assets"], 2)      # the script and the clip

    def test_skipped_directories_are_never_pulled_in(self):
        """A scratchpad once held a 194MB browser profile. Following a reference
        into node_modules or a profile directory would archive the world."""
        (self.src / "node_modules").mkdir()
        (self.src / "node_modules" / "x.css").write_text("body{}", encoding="utf-8")
        page = self.write("index.html", body='<link href="node_modules/x.css">')
        self.assertEqual(core.add_document(page, project="p")["kind"], "page")

    def test_oversized_closure_is_refused(self):
        original = config.MAX_SITE_BYTES
        config.MAX_SITE_BYTES = 10
        try:
            (self.src / "big.css").write_text("x" * 500, encoding="utf-8")
            page = self.write("index.html", body='<link href="big.css">')
            with self.assertRaises(ValueError):
                core.add_document(page, project="p")
        finally:
            config.MAX_SITE_BYTES = original

    def test_the_entry_can_always_express_itself_against_the_root(self):
        """The invariant `_materialize` depends on: whatever root comes back, the
        entry must have a relative path against it.

        POSIX always has a common root (`/`), so this only really bites on
        Windows, where a report on one drive referencing an asset on another has
        none at all. Picking an arbitrary member of the closure can land on the
        dependency, and then the entry has no relative path and archiving fails
        outright rather than losing one asset.
        """
        entry = self.src / "index.html"
        far = Path("Z:/elsewhere/logo.svg") if os.name == "nt" else Path("/elsewhere/logo.svg")
        root = core._common_root({entry, far}, entry)
        entry.relative_to(root)                       # must not raise
        if os.name == "nt":
            self.assertEqual(root, entry.parent)      # no common path exists

    def test_missing_source_raises(self):
        with self.assertRaises(FileNotFoundError):
            core.add_document(self.src / "nope.html", project="p")


# ---------------------------------------------------------------------------
# Version folding
# ---------------------------------------------------------------------------


class TestVersions(LibraryCase):
    def test_same_title_folds_into_one_entry(self):
        page = self.write("r.html", title="Weekly", body="v1")
        first = core.add_document(page, project="p")
        page.write_text(PAGE.format(title="Weekly", body="v2"), encoding="utf-8")
        second = core.add_document(page, project="p")

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["version"], 2)
        self.assertEqual(len(core.load_index()), 1)
        self.assertIn("v2", (config.HOME / second["file"]).read_text(encoding="utf-8"))
        self.assertTrue((config.VERSIONS_DIR / second["id"]).is_dir())

    def test_identical_content_is_not_a_new_version(self):
        """Re-opening a report must not inflate its version number."""
        page = self.write("r.html", title="Weekly")
        core.add_document(page, project="p")
        again = core.add_document(page, project="p")
        self.assertEqual(again["version"], 1)

    def test_a_changed_closure_reopens_an_unchanged_entry(self):
        """Repairing the JS-media bug meant re-adding documents whose HTML had
        not changed by a byte -- only what the scanner could see had. The
        identical-content short-circuit judged by entry bytes alone, so the
        repair was silently refused: `add` reported success, the version
        stayed at 1, and the media stayed missing."""
        (self.src / "media").mkdir()
        clip = self.src / "media" / "c01.mp4"
        page = self.write("review.html",
                          body='<script>const C = ["media/c01.mp4"];</script>')
        # Archived while the clip is absent: a page with zero assets.
        v1 = core.add_document(page, project="p")
        self.assertEqual((v1["kind"], v1["assets"]), ("page", 0))
        # The clip appearing stands in for the scanner learning to see it.
        clip.write_bytes(b"vid")
        v2 = core.add_document(page, project="p")
        self.assertEqual((v2["kind"], v2["assets"], v2["version"]),
                         ("site", 1, 2))
        self.assertTrue(
            (config.HOME / v2["file"] / "media" / "c01.mp4").is_file())

    def test_explicit_type_applies_even_when_content_is_unchanged(self):
        """`add --type eval` on an already-archived document used to report
        success and do nothing at all."""
        page = self.write("r.html", title="Weekly")
        core.add_document(page, project="p", doctype="report")
        moved = core.add_document(page, project="p", doctype="eval")

        self.assertEqual(moved["type"], "eval")
        self.assertTrue((config.HOME / moved["file"]).exists())
        self.assertIn("eval", Path(moved["file"]).parts)

    def test_reindexing_without_a_type_keeps_the_existing_one(self):
        """The default must not quietly drag a refiled document back to report."""
        page = self.write("r.html", title="Weekly")
        core.add_document(page, project="p", doctype="eval")
        page.write_text(PAGE.format(title="Weekly", body="v2"), encoding="utf-8")
        self.assertEqual(core.add_document(page, project="p")["type"], "eval")

    def test_type_is_not_part_of_the_identity(self):
        """Refiling is one document changing folder, not two documents."""
        self.assertEqual(core._doc_id("p", "T"), core._doc_id("p", "T"))
        page = self.write("r.html", title="Weekly")
        first = core.add_document(page, project="p", doctype="report")
        core.retype_document(first["id"], "doc")
        self.assertEqual(len(core.load_index()), 1)

    def test_history_is_capped_by_count(self):
        """Unbounded history once grew a library by 711MB with nothing visible
        in the document list to explain it."""
        original = config.MAX_VERSION_KEEP
        config.MAX_VERSION_KEEP = 2
        try:
            page = self.write("r.html", title="Weekly")
            for i in range(6):
                page.write_text(PAGE.format(title="Weekly", body=f"v{i}"), encoding="utf-8")
                entry = core.add_document(page, project="p")
            kept = list((config.VERSIONS_DIR / entry["id"]).iterdir())
            self.assertLessEqual(len(kept), 2)
        finally:
            config.MAX_VERSION_KEEP = original

    def test_history_is_capped_by_size(self):
        original = config.MAX_VERSION_BYTES
        config.MAX_VERSION_BYTES = 200
        try:
            page = self.write("r.html", title="Weekly")
            for i in range(4):
                page.write_text(PAGE.format(title="Weekly", body="x" * 400 + str(i)),
                                encoding="utf-8")
                entry = core.add_document(page, project="p")
            kept = list((config.VERSIONS_DIR / entry["id"]).iterdir())
            self.assertLessEqual(len(kept), 1)   # newest survives at any size
        finally:
            config.MAX_VERSION_BYTES = original


# ---------------------------------------------------------------------------
# Refiling and deletion
# ---------------------------------------------------------------------------


class TestRetypeAndDelete(LibraryCase):
    def test_retype_moves_the_copy_too(self):
        """Index-only refiling leaves entries whose `file` disagrees with disk,
        which surfaces as "clicking it shows a blank page" much later."""
        entry = core.add_document(self.write("r.html"), project="p", doctype="report")
        old = config.HOME / entry["file"]
        moved = core.retype_document(entry["id"], "doc")

        self.assertFalse(old.exists())
        self.assertTrue((config.HOME / moved["file"]).is_file())
        self.assertIn("doc", Path(moved["file"]).parts)

    def test_retype_of_unknown_id_returns_none(self):
        self.assertIsNone(core.retype_document("deadbeef", "doc"))

    def test_delete_removes_files_and_history(self):
        page = self.write("r.html", title="Weekly")
        core.add_document(page, project="p")
        page.write_text(PAGE.format(title="Weekly", body="v2"), encoding="utf-8")
        entry = core.add_document(page, project="p")

        self.assertTrue(core.delete_document(entry["id"]))
        self.assertFalse((config.HOME / entry["file"]).exists())
        self.assertFalse((config.VERSIONS_DIR / entry["id"]).exists())
        self.assertEqual(core.load_index(), [])

    def test_deleting_a_live_entry_does_not_delete_the_library(self):
        """A live entry has `file == ""`, and `HOME / ""` is the library root.
        Without the guard, removing a pointer removes everything."""
        keeper = core.add_document(self.write("keep.html"), project="p")
        live = core.add_live("http://localhost:5173/", project="p", title="Proto")

        self.assertTrue(core.delete_document(live["id"]))
        self.assertTrue(config.HOME.is_dir())
        self.assertTrue((config.HOME / keeper["file"]).is_file())
        self.assertEqual([d["id"] for d in core.load_index()], [keeper["id"]])

    def test_deleting_a_site_removes_the_whole_snapshot(self):
        (self.src / "a.css").write_text("body{}", encoding="utf-8")
        page = self.write("index.html", body='<link href="a.css">')
        entry = core.add_document(page, project="p")
        root = config.HOME / entry["file"]
        self.assertTrue(root.is_dir())
        core.delete_document(entry["id"])
        self.assertFalse(root.exists())


# ---------------------------------------------------------------------------
# Read-only sources
# ---------------------------------------------------------------------------


class TestReadOnlySources(LibraryCase):
    def test_a_read_only_source_still_folds(self):
        """Files from chat clients, cloud drives and mail attachments arrive
        read-only. copy2 preserves that, and the library then cannot delete or
        fold its own copies. Since those inboxes are a main entry point, this
        would fail constantly."""
        page = self.write("r.html", title="Weekly", body="v1")
        page.chmod(page.stat().st_mode & ~stat.S_IWRITE)
        try:
            first = core.add_document(page, project="p")
            copy = config.HOME / first["file"]
            self.assertTrue(os.access(copy, os.W_OK))

            page.chmod(page.stat().st_mode | stat.S_IWRITE)
            page.write_text(PAGE.format(title="Weekly", body="v2"), encoding="utf-8")
            page.chmod(page.stat().st_mode & ~stat.S_IWRITE)
            second = core.add_document(page, project="p")   # must not raise
            self.assertEqual(second["version"], 2)
            self.assertTrue(core.delete_document(second["id"]))
        finally:
            page.chmod(page.stat().st_mode | stat.S_IWRITE)


# ---------------------------------------------------------------------------
# Paths handed to agents
# ---------------------------------------------------------------------------


class TestDiskPath(LibraryCase):
    def test_page_points_at_the_file(self):
        entry = core.add_document(self.write("r.html"), project="p")
        self.assertTrue(core.disk_path(entry).endswith(".html"))

    def test_site_points_at_the_entry_page_not_the_directory(self):
        """The path gets pasted to an agent, and an agent cannot read a directory."""
        (self.src / "a.css").write_text("body{}", encoding="utf-8")
        entry = core.add_document(self.write("index.html", body='<link href="a.css">'),
                                  project="p")
        self.assertTrue(core.disk_path(entry).endswith("index.html"))

    def test_live_file_url_becomes_a_local_path(self):
        """An agent cannot open a file:// URL, so hand it the path instead."""
        live = core.add_live("file:///C:/tmp/proto/index.html", project="p", title="Proto")
        self.assertNotIn("file://", core.disk_path(live))
        self.assertIn("index.html", core.disk_path(live))

    def test_live_http_url_is_returned_as_is(self):
        live = core.add_live("http://localhost:5173/", project="p", title="Server proto")
        self.assertEqual(core.disk_path(live), "http://localhost:5173/")

    def test_disk_path_is_not_stored_in_the_index(self):
        """TABLESS_HOME can move; a stored absolute path would go stale."""
        entry = core.add_document(self.write("r.html"), project="p")
        self.assertNotIn("path", core.load_index()[0])
        self.assertTrue(core.disk_path(entry))


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class TestProjects(LibraryCase):
    def test_no_project_table_means_inbox(self):
        entry = core.add_document(self.write("r.html"))
        self.assertEqual(entry["project"], config.INBOX)

    def test_longest_prefix_wins(self):
        """A parent directory registered as its own project must not swallow the
        projects nested inside it."""
        outer = self.tmp / "work"
        inner = outer / "acme"
        inner.mkdir(parents=True)
        self.set_projects(
            f'[projects.work]\npath = "{outer.as_posix()}"\n'
            f'[projects.acme]\npath = "{inner.as_posix()}"\n')

        page = inner / "r.html"
        page.write_text(PAGE.format(title="T", body="x"), encoding="utf-8")
        self.assertEqual(core.add_document(page)["project"], "acme")

    def test_scratchpad_paths_are_reversed(self):
        """Agent scratchpads encode the project in the path; both the temp-dir
        layout and ~/.claude/projects have to resolve."""
        self.set_projects('[projects.acme]\npath = "D:/code/acme_lab"\n')
        for candidate in ("/x/Temp/claude/D--code-acme-lab/s1/scratchpad/r.html",
                          "/home/me/.claude/projects/D--code-acme-lab/memory/r.html"):
            self.assertEqual(projects.infer(Path(candidate)), "acme", candidate)

    def test_a_symlinked_project_path_still_matches(self):
        """`add_document` resolves the file it is handed; the project table holds
        whatever was typed into it. On macOS `/tmp` and `/var` are symlinks into
        `/private`, so a literal comparison never matches and everything lands in
        `_inbox` -- silently, which is the worst part. Both directions have to
        work, because either side may be the symlinked one."""
        real = self.tmp / "real-code" / "acme"
        real.mkdir(parents=True)
        link = self.tmp / "linked-code"
        try:
            link.symlink_to(self.tmp / "real-code", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this platform does not allow creating symlinks here")

        page = real / "r.html"
        page.write_text(PAGE.format(title="T", body="x"), encoding="utf-8")

        # Table written with the symlinked path, file arrives resolved.
        self.set_projects(f'[projects.acme]\npath = "{(link / "acme").as_posix()}"\n')
        self.assertEqual(projects.infer(page.resolve()), "acme")

        # Table written with the real path, file arrives via the symlink.
        self.set_projects(f'[projects.acme]\npath = "{real.as_posix()}"\n')
        self.assertEqual(projects.infer(link / "acme" / "r.html"), "acme")

    def test_directory_name_maps_to_registered_name(self):
        """Callers reach for the directory name; without folding, one project
        splits into two tabs -- the exact failure this tool exists to prevent."""
        self.set_projects('[projects.acme]\npath = "D:/code/AcmeLab"\n')
        self.assertEqual(projects.normalize("AcmeLab"), "acme")
        self.assertEqual(projects.normalize("ACME"), "acme")

    def test_unknown_project_names_are_kept(self):
        """It may be a real project nobody has registered yet."""
        self.assertEqual(projects.normalize("brand-new"), "brand-new")

    def test_relative_paths_are_ignored_in_the_table(self):
        """A relative path cannot anchor a prefix match; keeping it would only
        produce confident wrong answers."""
        self.set_projects('[projects.rel]\npath = "./somewhere"\n')
        self.assertEqual(projects.all_projects(), {})

    def test_accent_colour_is_stable_and_hue_preserving(self):
        self.set_projects('[projects.acme]\npath = "D:/x"\ntint = "#0d2624"\n')
        first = projects.accent_color("acme")
        self.assertRegex(first, r"^#[0-9a-f]{6}$")
        self.assertEqual(first, projects.accent_color("acme"))
        # A tint that dark is unusable as a UI accent; it must be lifted.
        self.assertGreater(int(first[1:3] + "00", 16) + int(first[3:5], 16), 0x30)

    def test_projects_list_puts_inbox_last(self):
        core.add_document(self.write("a.html", title="A"))          # -> _inbox
        core.add_document(self.write("b.html", title="B"), project="zeta")
        self.assertEqual([p["name"] for p in core.list_projects()][-1], config.INBOX)


# ---------------------------------------------------------------------------
# Index durability
# ---------------------------------------------------------------------------


class TestIndex(LibraryCase):
    def test_a_corrupt_index_reads_as_empty_rather_than_crashing(self):
        config.INDEX_FILE.write_text("{ not json", encoding="utf-8")
        self.assertEqual(core.load_index(), [])

    def test_index_survives_non_ascii_titles(self):
        core.add_document(self.write("r.html", title="运动数据 · 第 31 周"), project="p")
        self.assertEqual(core.load_index()[0]["title"], "运动数据 · 第 31 周")

    def test_known_types_reports_what_is_in_use(self):
        core.add_document(self.write("a.html", title="A"), project="p", doctype="eval")
        core.add_document(self.write("b.html", title="B"), project="p", doctype="doc")
        self.assertEqual(core.known_types(), ["doc", "eval"])

    def test_copies_outlive_their_sources(self):
        """The whole premise: copies, not references."""
        page = self.write("r.html")
        entry = core.add_document(page, project="p")
        page.unlink()
        self.assertTrue((config.HOME / entry["file"]).is_file())


if __name__ == "__main__":
    unittest.main()
