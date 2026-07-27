# Design notes

Why tabless is shaped the way it is. Most of what follows was arrived at by
getting it wrong first, so each note says what the wrong version did.

The test suite is organised around this list: nearly every case in
`tests/` exists to stop one of these coming back.

---

## One window, always

Projects are tabs along the top of a single reader window. Every entry point —
`add`, `open`, a push from the service — either slots into that window or
switches its tab. Nothing ever opens a second one.

**The wrong version was one window per project.** It looked reasonable because
that is how terminals work. But a terminal needs several windows because you
*operate* in several projects at once and have to glance between them; a reader
is a static thing you read, one document at a time. Giving it parallel windows
just trades a pile of tabs for a pile of windows — that is sorting, not
reducing, and "the same kind of thing is scattered across too many places" was
the entire complaint.

Implementation: `server._broadcast_confirmed()` pushes and waits for a receipt.
Only if nobody answers does `open_window()` run.

## Copies, not references

An index of file paths is a list of dead links within three months. Agent
scratchpads get cleaned by the OS. Chat clients purge attachment caches. Repos
get rearranged.

This is not hypothetical: a prototype was once archived by reference and the
stylesheet and vendored library it needed had *already* been deleted from the
scratchpad by the time anyone opened the entry. What was preserved was a record
that something used to exist.

So `add` copies. `tests/test_core.py::test_copies_outlive_their_sources`
deletes the source and asserts the library is unaffected.

## Page, site, live

The question is not "is this HTML" but **does it still work away from home**,
and **is it still changing**.

`page` vs `site` is decided by parsing the entry file's dependency closure — no
flag, no guessing. Only the closure is copied, never the containing directory:
an experiment board's `goldens/` may hold 25MB of images while one detail page
uses fourteen of them, and snapshotting the folder would multiply the library
by however many reports happen to sit beside it.

`live` has to be registered deliberately, because **snapshotting a moving
target is worse than not archiving it**. You end up with a copy that expires,
and usually one that was broken on arrival.

### The line: no process management

tabless does not supervise processes, start your dev server, or monitor ports.
A `live` entry whose service isn't running opens blank. That is the design, not
a missing feature. Cross this line once and the tool grows into a launcher that
has to know about every project's toolchain.

## Types are an open set — with one fold

`--type` accepts anything. There is no whitelist, because a tool that refuses
to file something is a tool you route around.

But an open set left completely alone shatters. Three agents write `report`,
`reports` and `报告`, and now one shelf is three folders holding the same kind
of thing — which is precisely the problem this tool exists to solve. So there is
an alias table (`core.TYPE_ALIASES`) covering plurals, abbreviations,
translations and same-thing-different-name pairs. **Names it does not recognise
survive verbatim**; that may genuinely be a new type.

Two consequences that must not be undone:

- `tabless add` prints the existing types whenever it meets a new one, so a typo
  announces itself instead of silently splitting a shelf.
- **The reader must never hard-code styling per type name.** `TYPE_ICON` falls
  back to a generic glyph and the label falls back to the raw name. An
  unrecognised type must never render its group as broken.

The same reasoning applies to project names. `projects.normalize()` maps a
directory name back to its registered name, because callers reach for
`ProjectNexus` when the project is registered as `nexus` — and this has really
happened, splitting one project into two tabs.

## Starring pins across types

A starred entry is lifted out of its type group into a "Starred" group at the
top, and **does not also appear in its original group**.

Once types became groups, the old "pinned sorts first within its group" was
useless: being first inside a group is no help when finding the thing requires
remembering which type it is. And showing it in both places is more confusing
than any ordering benefit — the same document appearing twice in one list reads
as a bug.

It reuses the existing `pinned` field rather than adding a parallel concept. The
group key is `__starred__`, which cannot collide with a real type because
`normalize_type` folds underscores to hyphens.

## An open connection is not an open window

The service knows a reader window exists because of its SSE connection. But
after a window closes, the server only finds out on the next failed write —
and anything pushed in that gap **vanishes silently**.

A report the user never learns existed is the worst failure this system can
have. It is worse than an error, because there is nothing to notice.

So the pushes that matter go through `_broadcast_confirmed()`: broadcast, then
wait up to 1.5s for the front end to `POST /api/ack`. No receipt means no
window, and a real one gets opened. **Do not simplify this back to checking the
return value of `_broadcast()`.**

(Side effect: right after `/api/reload`, `/api/status` briefly reports two
windows while the old connection times out. That is expected.)

---

# Traps

Each of these is a bug that shipped.

### Site entries redirect; they do not get a `<base>` tag

Injecting `<base href="...">` looks like the easy way to make a snapshot's
relative paths resolve. It also turns every in-page anchor (`#section`) into
cross-page navigation, scrambling internal links throughout the site. So
`/doc/<id>` returns a **302** into `/site/<id>/<entry>` and relative paths,
links and anchors all work natively.

### Site entry points can't be found by elimination

"Any page that was referenced is an inner page" fails on the most common
layout: `index.html` and `rules.html` link to each other, so both look like
inner pages and the site ends up with no entry at all — zero documents
imported. `scan` builds connected components and picks a representative,
preferring `index.*`.

### The index lock is not reentrant

`core.Lock` is an `O_CREAT | O_EXCL` file. A function holding it must not call
another function that takes it. `add_document` calling `retype_document`
deadlocked against itself for the full five-second timeout and then continued
unlocked. Hence the split: an unlocked `_relocate()` with `retype_document()`
as its locking shell.

### Version history needs a ceiling

Two comparison bundles from different days happened to share a title, so they
folded into one document over and over, each round parking another 230MB in
`_index/versions/`. Four rounds, 711MB, and nothing visible in the document
list to explain it.

Two gates now: `MAX_VERSION_KEEP` and `MAX_VERSION_BYTES`. Size matters
independently, because a count-only limit lets five site snapshots eat a
gigabyte.

The broader lesson: **version folding assumes a title identifies a document.**
Bundles from other people and repeated runs off one template violate that. Use
`--title` to split them.

### Archiving must clear the read-only bit

Files from chat clients, cloud drives and mail attachments are routinely
read-only, and `copy2` reproduces that faithfully — after which the library
cannot delete or fold **its own copies**. Since those inboxes are a main entry
point, this failed constantly. `_drop_readonly()` runs on the way in, and again
before deletion, because `rmtree(ignore_errors=True)` meeting a read-only file
leaves debris behind in silence.

### Deleting a live entry nearly deleted the library

A live entry has `file == ""`, and `HOME / ""` is the library root. Without the
guard in `delete_document`, removing a pointer removes everything.
`tests/test_core.py::test_deleting_a_live_entry_does_not_delete_the_library`
exists for exactly this.

### Version folding could delete its own destination

After moving the old copy into history, the code removes the vacated type
folder. When the folded document was the *only* one in that folder, the folder
it removed was the one the new copy was about to be written into, and the copy
failed with "path not found". It survived a long time because in a busy library
that `rmdir` almost always fails as non-empty. The fix is the `!= dest_dir`
guard; the test is `test_same_title_folds_into_one_entry`.

### A closure can have no common root at all

On Windows a report can reference an asset on another drive, and then
`os.path.commonpath` raises. Falling back to an arbitrary member of the set can
pick the dependency rather than the entry, at which point the entry has no
relative path to record and archiving fails outright. `_common_root` takes the
entry explicitly and anchors on its directory; dependencies that cannot be
expressed relative to it are skipped by the copy loop instead of bringing the
whole archive down. Keep assets on the same drive as the report if you want
them snapshotted.

### Strip tags before decoding entities

Some exporters write the whole title as `&#36816;&#21160;…`. Skip the decode
and the library lists a row of numeric entities. Worse, the 120-character
truncation then slices the last entity in half (`&#210`), and that fragment is
neither recognisable nor searchable. Order matters: strip tags, *then*
unescape.

### `scan` must archive oldest first

When same-titled documents fold into versions, the last one applied becomes the
current version. Process them newest-first and the "current" version is an old
one.

### Injected styles must be layered

The shared scrollbar CSS is wrapped in `@layer`. Layered rules always lose to
unlayered ones, so a document that styled its own scrollbar keeps its design.
Without the layer, the library would be silently rewriting somebody's visual
work — which is overreach.

Related: **HTML responses cannot serve Range.** Injection changes the length and
`Content-Length` would no longer match. Browsers don't Range the main document,
so nothing is lost. And **non-UTF-8 HTML is returned byte for byte** rather than
force-decoded with replacement characters: better ugly than corrupted.

### A transparent scrollbar trough is not transparent

The body background propagates to the canvas but not into the scrollbar gutter;
what shows through there is the browser's default, which is white under a light
theme and glaring beside a dark report. The server cannot know a document's
colour, and hard-coding dark would break the first light one. So the injected
script measures the computed background at runtime and paints the trough to
match — and leaves it alone when it cannot measure (gradients, images, unset).

### Disk paths do not belong in the index

`TABLESS_HOME` can move. A stored absolute path goes stale, so `server.decorate()`
computes it per response instead.

### Timestamps come from the source file

Using the archiving time instead of the source's mtime flattens the real
timeline of a backlog into "everything happened today".

### `SO_REUSEADDR` means opposite things per platform

Windows lets two processes bind the same port when it is on, so it must be
**off** there — and a failed bind then becomes a reliable "a server is already
running", which is how duplicate startup is prevented.

POSIX never permits two live binds; there the flag only skips TIME_WAIT.
Leaving it off makes a restart within a minute fail to bind, which gets misread
as "another server exists" when there isn't one.

Hence `allow_reuse_address = os.name != "nt"`.

### The browser profile does not belong in the package directory

The reader's Chromium profile grows past 200MB. It lives under
`TABLESS_HOME/.cache/`, not beside the installed code.

### Console encoding will bite on Windows

A console defaulting to a legacy codepage raises `UnicodeEncodeError` on the
first non-ASCII title, before anything useful happens. `cli.main()` reconfigures
both streams to UTF-8. And under a windowless interpreter `sys.stdout` is
`None`, which makes the first `print()` an `AttributeError` — also handled
there.
