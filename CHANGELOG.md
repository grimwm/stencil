# Changelog

Notable changes to stencil, newest first.

This file starts at 0.3.0 and is not retroactive. The 170-odd commits before that
tag are the history of the tool arriving at this shape; git is the record of them,
and the closed epics in `.beads/issues.jsonl` are the readable index.

How the version gets bumped is written down in
[AGENTS.md](AGENTS.md#cutting-a-release), not here.

## 0.10.0

`brand` can be set once for a project instead of in every document.

- **`brand` and `brand-alt` in `.config.yaml`**, config-wide or per package,
  resolved the way `lang` is: package first, then config-wide, and front matter
  over both. The default only fills the key in when a document left it out,
  after which it is treated exactly as though the document had written it --
  which is what keeps a configured brand and a written one from drifting into
  two behaviours.

  A package that names its own brand does **not** inherit the config's
  `brand-alt`. "This logo, no alt yet" is what that means, and inheriting there
  would label one logo with another's name.

- **A configured logo is copied into every package that renders markdown**,
  without being asked to, and the copies are added to the managed `.gitignore`.
  The folders stencil generates are routinely handed to someone as a project of
  their own, separate from the repository that produced them -- so a logo
  living only next to `.config.yaml` would leave the recipient with a document
  referring to a file they were never given. A copy per package is the cost of
  each folder standing alone, and the filter names the copy rather than the
  source path so nothing looks outside the folder.

  Paths are resolved relative to the config file's directory. A config-level
  logo with no `brand-alt`, or one that is not a file, fails at `stencil gen`
  rather than at render -- a config mistake should reach whoever ran the
  generator, not whoever builds a document three packages away.

## 0.9.0

A document can carry the mark of what it belongs to.

- **`brand` front matter, a name or a logo.** It renders at the top of the
  header's right-hand column, above `program`, pushing the rest of the context
  and the byline down. On a deck it sits above the title slide's context line.

  The value decides which it is, rather than a second key: a `file://` prefix
  or an image extension makes it a picture, anything else is a name. So
  `St. Louis U.` stays text.

  A picture is inlined as a `data:` URI like any other image. `file://img/logo.svg`
  is rewritten to `img/logo.svg` before it is resolved, because
  `embed-images.lua` classes anything matching `scheme://` as remote and leaves
  it alone — a `file://` URI would have been the single spelling that never got
  bundled, leaving the page carrying a path and `make pdf` failing on an asset
  it could not fetch.

  A logo is capped on both axes and never distorted -- whichever cap binds
  first, the other dimension scales with it. Verified by measurement rather
  than by reading the spec: a 900x60 wordmark renders 223.95x14.93 and a
  50x600 crest renders 3.66x44, each at its exact natural ratio. A test guards
  the `width: auto` / `height: auto` pair that makes that true, because pinning
  either one turns the cap into a stretch silently, and only for whichever
  logo happens to be the wrong shape.

- **`brand-alt` is required when `brand` is a picture**, and the build fails
  naming the key when it is missing. Deliberately not defaulted to `alt=""`: a
  logo is frequently the only thing naming the institution on the page, so an
  empty alt drops that for a screen reader, and `make check-access` runs pa11y
  at WCAG 2.1 AA. A name needs none — it is already text.

## 0.8.0

Ordered task lists get the styling bullet ones have had.

- **`1. [ ]` no longer renders with the box jammed against the text.** Pandoc
  puts `class="task-list"` on a `<ul>` only; an ordered task list arrives as a
  bare `<ol type="1">` carrying the identical
  `<label><input type=checkbox>` items. Every rule from 0.4.0's task-list work
  was spelled `ul.task-list`, so an ordered list matched none of them — no gap
  beside the control, no accent tint, and a checked box printed empty because
  the print opt-out was scoped the same way.

  The two are still styled differently, on purpose. A bullet list's disc is
  decoration, so the checkbox replaces it and the text hangs off it. An ordered
  list's number is content the author asked for, so it stays and the box takes
  room beside it: `1. [x] done`, not `[x] done`.

  The gap is on both sides of the control. With only the reported side fixed,
  the box lands against the ordinal instead and the same complaint reads
  `1.[ ] text`.

## 0.7.0

A document says what it belongs to, and `show_date: no` means no.

- **`program`, `section` and `term` front matter.** The institutional context a
  handout carries: what it belongs to, which instance of that, and when. A
  document renders them top right, opposite the title; a deck puts them on one
  line above it. The names avoid `course`/`semester` deliberately — they read
  the same to an instructor and to anyone running a training program.

- **The document header is two columns.** Title and subtitle on the left of the
  first row with the context opposite them, author on the second with the date
  opposite. A file setting everything costs two lines instead of five. Below
  roughly 640px it collapses back to one left-aligned stack.

  The date moved out of `.doc-meta` and into `.doc-date`, so the middot that
  joined it to the author list is gone.

- **`show_date: yes` stamps the build date.** And `show_date: no` withholds it,
  which is worth stating because it did not used to. Pandoc reads YAML 1.2,
  where `true` and `false` are the only booleans, so `no` arrived at the
  template as the *string* `"no"` — as truthy as `"yes"`. The new
  `frontmatter-filter.lua` settles it before any template asks. An explicit
  `date:` still wins; the stamp is the build host's day, not the container's.

- **The package `name` is no longer injected as a document's `course`.** It was
  reaching pandoc as `--metadata course=`, which overrides front matter, so an
  author who set `course:` had it silently discarded — and a package name was
  never a course in the first place. `name` is a `stencil --list` label again.
  This is the one behavior change to look at when upgrading: a deck that showed
  its package name above the title now shows nothing there until it sets
  `program:`.

- **A deck's title no longer prints at slide-heading size** (stn-tum). The title
  slide's `.deck-title` is an `h1`, and `.slide > h1:first-child` outscores
  `.slide--title .deck-title` on specificity — so the title was sized as a
  section heading exactly when no context line preceded it. Adding a `course:`
  pushed a `<p>` in front and gave the title its size back, which is why it
  looked like a print-only bug. Both media now exclude the title slide.

- **`lang` and `dir` front matter, with a config-wide default.** Both templates
  hardcoded `<html lang="en">`, so a page written in Spanish asserted it was
  English — a screen reader pronounced it with English phonetics and sounded
  confidently wrong rather than obviously broken. Resolved narrowest first: a
  document's `lang:`, then a package's or the config's, then `en`. The
  attribute is never omitted, so nothing that renders today changes.

  `dir` is front matter only and emitted only when set. There is no
  config-level spelling for it because a package's `dir:` is already its output
  subdirectory, and giving one key two meanings is the mistake this release
  undoes elsewhere.

- **AUTHORING.md lists the keys stencil does *not* render.** `abstract`,
  `keywords`, `lang`, `toc` and the rest parse fine and do nothing, which reads
  a lot like working.

## 0.6.0

One type scale instead of two, and a way to ask which stencil you are running.

- **Print rescales the root rather than restating every size.** Sizes are in
  `rem` throughout, and a `rem` is the root font size, so `@media print` now
  sets `html { font-size: 9.78pt }` once and the whole scale follows. 9.78pt is
  what puts body text, at `1.125rem`, on the 11pt it has always printed at.

  This removes the defect class 0.5.0 fixed three instances of. There is no
  longer a second list of sizes that can fall out of step with the first, and a
  test now fails if print restates a size the shared scale already gives.

  Two visible consequences. Print inherits the screen proportions, so a
  document's title prints at 21.5pt rather than 18pt. And the root carries
  Bootstrap's `rem`-based padding and margins with it, so spacing tightens by
  about 18% and documents get shorter — a 12-page handout measured 11 pages.
  Everything else lands within ~2% of 0.5.0.

- **`stencil version`.** Prints the installed version, and answers before
  reading a config, because the question is usually asked when an install is
  suspect. The version now lives in `stencil/__init__.py` with `pyproject.toml`
  reading it, so the module and the installed distribution cannot disagree
  except by a stale install — which is the thing the command exists to reveal.
  Four course venvs were found running three different stencils while a
  template fix appeared not to work.

## 0.5.0

Typography fixes in the document stylesheet. Every generated document and deck
renders differently, on screen and on paper, so this is a minor bump.

Three instances of one mistake: a size stated in `pt` inside `@media print` for
some selectors, with the neighbouring ones left on `rem`. A `rem` is measured
against the root font size, not against the element beside it, so the two units
do not compare and the hierarchy inverts once the page is printed.

- **A document's subtitle printed larger than its title.** `.doc-title` scaled
  to 18pt while `.doc-subtitle` kept its screen `1.8rem` — 21.6pt. The student's
  name printed 20% larger than the assignment. Only the PDF was wrong, which is
  why it lasted (`stn-09g`).
- **The printed heading scale inverted at `h4`.** `h1`, `h2` and `h3` had `pt`
  sizes; `h4`, `h5` and `h6` did not, so they kept Bootstrap's `rem`. Measured
  off a real PDF: `h1` 21.3, `h2` 18.7, `h3` 16.0, then `h4` 22.3 — larger than
  `h1` and just under the title.
- **On screen a section heading outranked the document title.** stencil set no
  size for `h1`–`h6` at all, so they inherited Bootstrap's viewport-relative
  scale, `calc(1.375rem + 1.5vw)`. `h1` measured 39.4px against the title's
  35.2px, and resized with the window while the title stayed put.

The subtitle is also no longer bold: at `1.8rem`/700 under a `2.2rem`/700 title
it read as a second title rather than a subordinate line, and being the longer
of the two it took the eye. It is now `1.4rem`/400.

Headings now state their own sizes on screen and in print, stepping down
monotonically under the title in both, with the screen steps tracking the print
block's ratios against body text.

Known and not fixed: a deck's title prints at the same size as a slide heading,
and whether it does depends on an unrelated frontmatter key (`stn-tum`).

## 0.4.0

An undefined name in a template is now an error rather than the empty string,
which is a behaviour change for anyone whose config or templates were relying
on the old silence. No generated output changes: every package of six consumer
configs was generated before and after, 530 files, byte-identical.

- **A consumer's composition template is a checked interface.** Overriding
  `Makefile.j2` or `docker-compose.yml.j2` and including stencil's partials is
  the intended way to extend a build, and the keys those partials read are now
  recorded in `tests/test_template_contract.py`. Stencil's own suite fails when
  that set changes, and `StrictUndefined` makes a stale composition fail the
  consumer's `stencil gen` instead of emitting a Makefile with a recipe
  missing.
- **Custom keys can be declared.** A config-level `template_env` declares a key
  and supplies the value every package gets unless its own `template_env`
  overrides it. This is what lets one `templates_dir` serve several configs
  that each set a different subset of the flags their shared templates read.
- **Configuration mistakes are rejected instead of ignored.** A `when:` naming
  a key that is neither derived nor declared anywhere, and a `template_env` key
  whose name appears in no template, both now fail with the key named. Each was
  previously silent: the guarded template was skipped for every package, or the
  key sat there doing nothing.

Upgrading: if `stencil gen` now reports an undefined key, declare it in the
config-level `template_env` rather than setting it to `false` on every package
— a key some package sets stays undefined for the ones that do not, so
`{{ key | default('x') }}` keeps working, and a concrete `false` would break it.

Removed: `TEMPLATE-PACKS.md`, a design for packaging scaffolding as a
dependency. It recommended against building the thing it described, and the
reasoning is preserved in `stn-zza` and in git history.

## 0.3.0

The first tagged release. Not a set of changes so much as a name for what the
tool already does, so "the stencil that built this handout" is answerable.

What is in it:

- **Scaffolding generator.** Jinja2 templates rendered into per-package output
  directories from a YAML config, with a templates search path — every
  configured `templates_dir`, then the bundled set, first match wins — so a
  consuming project overrides one template without vendoring all of them.
- **Documents and decks.** Two pandoc HTML templates over shared partials.
  Decks are grouped by `slide-sections.lua` and CSS rather than by pandoc's
  slide-show support, which does not apply here.
- **A PDF stage.** `make pdf` prints the generated HTML through headless
  Chromium, with the print stylesheet as the page geometry rather than as
  decoration.
- **Self-contained output.** Bootstrap, highlight.js, Mermaid and the webfonts
  are vendored and inlined at `stencil gen` time, so a handout makes no network
  request and a build does not depend on a CDN being up.
- **Hidden variants.** `WITH=hidden` builds an answer key from the same source,
  with the filter order arranged so citeproc cannot leak a hidden citation's
  source into the visible build's reference list.
- **A pinned toolchain.** `docker.io/pandoc/core:3.10.0.0`, asserted by a test
  rather than trusted, so a pandoc release cannot change rendered output or
  break CI on a commit that changed nothing.
- **An authoring contract.** `AUTHORING.md` for the person writing markdown,
  `STENCIL.md` for the person configuring a package.
- **A test suite and CI.** Unit assertions on the rendered scaffolding, plus
  container-backed integration tests behind an `integration` marker that skip
  rather than fail when there is no runtime.

Fixed just before the tag:

- A doc package built only from `package_sources` generated five shared page
  files that `clean` and the managed `.gitignore` section could not see, and a
  `pkg` target naming a pandoc template that was never written (`stn-633`).
