"""The pandoc invocation, as data.

The argv used to exist only as an entrypoint array inside
``docker-compose-html.yml.j2``. Nothing could assert on it without building a
container, so two ordering constraints with silent failure modes were defended
by nothing but a comment. They live here now, with the comments attached to the
arguments they explain, and ``docker-compose-html.yml.j2`` renders from this
module -- so the compose file a package builds with and the argv a test asserts
on cannot drift apart.

``render`` runs the same argv through the same image the generated
``docker-compose.yml`` declares. Using the container rather than a local pandoc
is deliberate: a native binary would be a second pandoc, on a different version,
that the real build never uses.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

# Pinned, and bumped deliberately. Against :latest a build is not reproducible
# across time, and a pandoc release can change rendered output or emit a new
# warning that --fail-if-warnings promotes to a failure -- breaking CI on a
# commit that changed nothing, at whatever hour the release landed.
#
# To bump: edit this line, run `python3 scripts/resolve_image_digests.py` to
# record the new manifest digest, run the suite, and read what changed in the
# rendered fixtures. The four-part tag is the specific release; :3.10 would
# still float across patch releases.
#
# This is the TAG, not the full reference a build actually pulls -- see
# pinned_image() and IMAGE_TAGS below for why the digest is looked up by tag
# rather than kept beside it, and PANDOC_IMAGE (read lazily through module
# __getattr__) for the tag@digest string itself.
PANDOC_TAG = "docker.io/pandoc/core:3.10.0.0"

# The pdf and check-access services share one image, built from this Dockerfile
# in the generated package. Tests build it once and reuse the tag.
BROWSER_DOCKERFILE = "Dockerfile.browser"
BROWSER_IMAGE_TAG = "localhost/stencil_browser:test"

# ...unless something asks for a different one. Two suites running at once --
# two worktrees, which is what AGENTS.md tells every agent to work in -- both
# built and overwrote this single tag, so one run could rebuild the image out
# from under another that was still using it (stn-zim).
#
# A per-run tag would be a bad trade if it meant rebuilding the image each
# time. Measured: 0.5s against the existing tag, 0.6s against a brand-new one
# -- the layer cache keys on the Dockerfile and the context rather than on the
# name, so the tag costs nothing.
#
# Read through a function rather than as a default argument, which is the
# whole point: `def f(tag=BROWSER_IMAGE_TAG)` binds at IMPORT, so setting the
# module attribute afterwards looks like it works and does nothing. The same
# late-binding mistake was made and caught in tests/conftest.py's low-space
# threshold, which is why it is spelled out here.
BROWSER_IMAGE_TAG_ENV = "STENCIL_BROWSER_IMAGE_TAG"


def browser_image_tag() -> str:
    """The image tag the browser-backed helpers build and run.

    Overridden per run by $STENCIL_BROWSER_IMAGE_TAG so concurrent suites do
    not share one mutable name. Nothing in a GENERATED package reads this --
    the compose file builds its own image -- so this is a test-harness knob
    and changing it cannot affect a consumer's build.
    """
    return os.environ.get(BROWSER_IMAGE_TAG_ENV) or BROWSER_IMAGE_TAG

# Every Node in a generated package: the browser image's base, the format-md
# service, and the ensure_image line that pre-pulls it. One constant, because
# three copies of a floating name is three chances for `make format-md` to pull
# one image and run another -- a defect that reads as a slow first build.
#
# Pinned for the reason PANDOC_IMAGE is, and one more. `lts` moves across Node
# major releases, and pa11y declares `engines: ^22.13.0 || >=24`, so an LTS
# rollover can make the image simply refuse to build. `alpine` moves across
# Alpine releases, and the Alpine branch is what decides which Chromium
# `apk add chromium` installs and which font packages exist -- and the fonts
# are already documented, three lines into Dockerfile.browser.j2, as the thing
# that moves every page break.
#
# To bump: edit this line, run `python3 scripts/resolve_image_digests.py` to
# record the new digest, rebuild the browser image, and run the container
# tier. Read what moved in the PDF geometry and the PDF/UA results.
#
# PINNED BY DIGEST NOW, NOT JUST BY TAG -- stn-8vi, reversing the argument that
# used to sit here. That argument was: a registry tag is mutable and a digest
# is not, so a digest is strictly stronger, but pinning only one of the three
# images by digest would buy defence in depth for a third of the surface while
# leaving the convention inconsistent for whoever bumps the next one. That is
# an argument for doing all three together, not for doing none -- and stn-8vi
# is that: NODE_IMAGE, PANDOC_IMAGE and VERAPDF_IMAGE (all read lazily below,
# through module __getattr__) now carry `<tag>@sha256:<digest>`.
#
# THE DIGEST IS KEYED BY THE TAG, in stencil/assets/image-digests.json, rather
# than written inline as one `tag@digest` string beside each constant. A
# registry resolves `name:tag@digest` BY THE DIGEST and ignores the tag
# entirely, so an inline pair is one edit that can go stale without failing:
# bump the tag, forget to re-resolve, and the build silently keeps pulling the
# OLD image under a name that now says something else. Keyed by the tag, that
# state cannot be expressed -- pinned_image() has nothing to look up for a tag
# with no entry, so a forgotten re-resolve fails loudly and offline the way
# `npm ci` refuses a lockfile the manifest does not satisfy, rather than
# quietly shipping a stale digest that happens to parse.
#
# NOT at import, though -- at the first READ of one of the three constants.
# The distinction is the whole reason __getattr__ is down there rather than
# three eager assignments up here: failing at import is what would deadlock
# the resolver script that exists to fix the failure. `stencil version` and
# `stencil list` keep working; `stencil gen` is what stops.
#
# THE COST BEING ACCEPTED, so nobody later "fixes" this by going back to a
# tag. A digest pin gives a CONSUMER three new ways to fail that a tag pin did
# not:
#
#   1. Registry garbage collection. A tag pin degrades to different bytes
#      under the same name; a digest pin degrades to "manifest unknown". Once
#      upstream repushes the tag, the old manifest is untagged, and untagged
#      manifests do get reclaimed -- so the pin has an expiry date a tag did
#      not.
#   2. `docker save` -> transfer -> `docker load` -> `docker tag` loses
#      RepoDigests, so both `docker image inspect <ref>@sha256:...` and the
#      compose pull fail against a reference that worked fine as a tag. A
#      pull-through cache preserves digests; a save/load air-gap does not.
#   3. No override. generate.py's reject_derived deliberately refuses
#      `template_env: {pandoc_image: ...}`, so a consumer behind a mirror has
#      no supported knob, and hand-editing the generated Makefile is undone by
#      the next `stencil gen`. AGENTS.md already records this exact regret
#      about Chromium -- "neither the Makefile nor the compose file gives a
#      consumer a build argument to work around it with" -- and this pins the
#      same regret onto three more images rather than pretending it does not
#      apply here too.
#
# Deliberately NO escape hatch for any of the three: rendering through
# overridable variables would need Makefile-doc.j2, Makefile-pkg.j2 and
# docker-compose-html.yml.j2, and would let an environment variable downgrade
# the very pin this exists to create. What is written down instead is the
# RECOVERY PATH: a pull failing with "manifest unknown" means the digest was
# garbage-collected upstream -- re-resolve with
# `python3 scripts/resolve_image_digests.py` and regenerate.
#
# stn-5hv, which this comment used to point at, closed the equivalent gap one
# layer in -- the npm tree installed INTO this image is fixed by a committed
# lockfile and verified by hash. That made the image itself the remaining
# floating input; this closes that one too.
NODE_TAG = "docker.io/library/node:24.20.0-alpine3.24"

# What the browser image installs. Exact, not `^`: stn-s5b was filed because
# html-to-pdf.js pins `tagged: true` on page.pdf() to stop a version bump
# silently removing an accessibility property, and `tagged` is exactly the kind
# of option a MINOR release adds or drops. A caret satisfies "the same major"
# and not the argument the pin was made for.
#
# Measured on a --no-cache rebuild, 2026-09-07: node v24.20.0, Alpine 3.24.1,
# chromium 152.0.7977.82-r0, and these three. pa11y 10.0.0 had been released
# ten days earlier and had floated in unnoticed; it was run against both
# generated theme configs before being pinned to, rather than pinned to on the
# strength of npm having served it that morning.
#
# puppeteer and pa11y are coupled: pa11y DEPENDS on puppeteer, npm dedupes the
# two onto one copy only while the pinned puppeteer satisfies pa11y's declared
# range, and a tree with two copies means `make check-access` drives a
# different browser than `make pdf`. tests/test_pins.py asserts there is one.
#
# THESE THREE NAMES ARE NOT WHAT FIXES THE TREE ANY MORE. An exact version
# here fixes these three and the exact puppeteer-core puppeteer declares, and
# nothing below that: every transitive dependency used to resolve within a
# range at build time, so a rebuild months apart installed different code and
# nothing verified integrity (stn-5hv). What fixes it now is
# stencil/assets/browser-package-lock.json, committed, with a sha512 for all
# 47 packages in the resolved tree, installed by `npm ci`.
#
# So this map has become the REQUEST and the lockfile is the ANSWER. They are
# checked against each other two ways: tests/test_pins.py fails when the
# lock's root dependencies stop equalling this dict, and `npm ci` itself
# refuses a manifest the lock does not satisfy --
#
#     npm error `npm ci` can only install packages when your package.json and
#     package-lock.json [...] are in sync.
#     npm error Invalid: lock file's pdf-lib@1.17.1 does not satisfy
#     pdf-lib@1.17.0
#
# -- so editing a version here without re-vendoring breaks the build loudly
# rather than quietly installing something else. Re-vendor with
# `python3 scripts/vendor_npm_locks.py` and commit both files together.
#
# WHEN BUMPING: pa11y 10.0.0 was ten days old when it was pinned to, which is
# inside the window where a compromised release is usually still being found.
# Read the advisories for all three before moving a pin, not only the release
# notes.
BROWSER_NPM_PINS = {
    "pa11y": "10.0.0",
    "pdf-lib": "1.17.1",
    "puppeteer": "25.10.0",
}

# The formatter, pinned for the same reason with a wider blast radius: an
# unpinned prettier decides how every markdown file in a package is rewritten.
FORMAT_NPM_PINS = {
    "@awmottaz/prettier-plugin-void-html": "2.2.1",
    "prettier": "3.9.6",
}

# ---------------------------------------------------------------------------
# The lockfiles the two installs above actually resolve through.
#
# Written once by scripts/vendor_npm_locks.py, committed under stencil/assets/,
# and shipped into a generated package with no network access at generation
# time -- the same shape as the page assets in stencil/assets.py, and for the
# same reason. `stencil gen` does not touch the network; a maintainer running a
# vendoring script does.
#
# `npm ci` NEEDS A MANIFEST AS WELL AS A LOCK, and refuses when the two
# disagree. The manifest is derived from the pin maps above rather than
# shipped, so a generated package gains two files rather than four and there is
# still exactly one place a version is written down. It is rendered inline --
# a `printf` in Dockerfile.browser, a `printf` in the format-md entrypoint --
# from npm_manifest() below.
#
# THE COST BEING ACCEPTED, so nobody later "fixes" it by relaxing to a floating
# install. A lockfile records URLs and hashes, not bytes. A version unpublished
# from the registry, or a registry that cannot be reached, now fails the build
# permanently, where installing by name would have succeeded with different
# code. That is the trade this ticket exists to make -- a build that fails is
# better than a handout rendered by something nobody chose -- and the way out
# of it is to re-vendor, not to install by name again.
#
# What the lockfile does NOT do is judge what it pins. A re-vendor that pulls
# in a newer transitive tree while the pins are unchanged passes every test
# here and every build; reading that diff is a person's job, and
# stencil/assets/README.md says what to look for.
BROWSER_LOCKFILE = "browser-package-lock.json"
FORMAT_LOCKFILE = "format-package-lock.json"

# The `name` each manifest declares. It ends up in the lockfile's root entry,
# so changing one means re-vendoring: `npm ci` compares them.
BROWSER_MANIFEST_NAME = "stencil-browser-tools"
FORMAT_MANIFEST_NAME = "stencil-format-md"

# Where each install lands inside its container.
#
# NOT A GLOBAL PREFIX, and that is the cost stn-5hv named up front: `npm ci`
# has no `--global`, so `npm install --global --prefix /opt/tools` -- which put
# packages under /opt/tools/lib/node_modules and binaries under /opt/tools/bin
# -- becomes an ordinary local install rooted at /opt/tools. The layout moves
# with it: modules to node_modules, binaries to the node_modules/.bin symlinks
# npm writes itself. Dockerfile.browser's NODE_PATH and PATH are built from
# these constants for that reason; a rewiring that misses one leaves
# `require("puppeteer")` or `pa11y` unresolvable at run time, which
# tests/test_compose_check_access.py is what finds.
BROWSER_TOOLS_DIR = "/opt/tools"
BROWSER_NODE_MODULES = f"{BROWSER_TOOLS_DIR}/node_modules"
FORMAT_TOOLS_DIR = "/tmp/fmt"

# WHERE THE PDF DRIVER RUNS FROM, AND WHY IT IS NOT THE MOUNT (stn-jeq, stn-7ki).
#
# html-to-pdf.js is still RENDERED into the package directory -- a consuming
# project overrides it from its own templates_dir, and Dockerfile.browser COPYs
# it from there, so that override still reaches the image. What changed is where
# it is RUN from, because two separate loaders take their answer from the script's
# own location and from the process's working directory:
#
# - Node decides CommonJS-vs-ESM from the nearest package.json TO THE FILE. With
#   the script at /workspace, that is the consumer's, and a course package
#   legitimately has one: `{"type": "module"}` turned the whole script into a
#   parse error before any guard inside it could speak (stn-7ki).
# - puppeteer's getConfiguration() searches UPWARD FROM process.cwd() for
#   .puppeteerrc.cjs and twelve siblings, and `require`s the JavaScript ones. It
#   calls lilconfig(...).search() with no argument -- measured against the pinned
#   puppeteer, where `search(searchFrom = process.cwd())` and `stopDir` is the
#   home directory -- so there is no environment variable and no launch option
#   that turns it off. cwd is the only lever (stn-jeq).
#
# Baking the script here answers the first. The second is answered by the script
# chdir()ing to BROWSER_TOOLS_DIR, and by CHECK_ACCESS_SCRIPT's leading `cd`:
# from there the upward walk is /opt/tools -> /opt -> / and stops, and every
# directory on it belongs to the image rather than to the mount.
#
# The filename is repeated in Dockerfile.browser.j2 and docker-compose-html.yml.j2
# rather than reaching them through a new template context key, because adding one
# means editing generate.py. tests/test_pins.py asserts that what those two render
# equals BROWSER_SCRIPT_PATH, so the three cannot drift apart quietly.
BROWSER_SCRIPT = "html-to-pdf.js"
BROWSER_SCRIPT_PATH = f"{BROWSER_TOOLS_DIR}/{BROWSER_SCRIPT}"

# PDF/UA-1 conformance checking. Pinned, because veraPDF's rule set is the
# thing being asserted against: an unpinned tag lets a build go red or green
# on someone else's release rather than on a change here.
#
# PINNED BY DIGEST TOO (stn-8vi), and this one is the odd image out.
# `docker.io/verapdf/cli:v1.30.2` is NOT a manifest list -- measured, not
# assumed: the registry answers with a single
# `application/vnd.docker.distribution.manifest.v2+json`, `linux/amd64` only.
# There is no index to resolve per architecture, so the only digest that
# exists for this tag is the one pinned; image-digests.json records the
# single-arch media type explicitly rather than treating "not an index" as an
# error, and tests/test_pins.py checks for it by name so a future multi-arch
# veraPDF release is a deliberate edit here rather than a silent pass. (An
# earlier version of the source ticket assumed this image carried a manifest
# list like the other two; it does not, and this comment is the correction.)
#
# On arm64 this already runs emulated today -- pinning the digest makes that
# fact visible rather than causing it. The freeze is the point, not a defect
# to fix: with a tag, a future multi-arch repush of v1.30.2 would silently
# start running verapdf NATIVE on arm64, changing what is being measured
# without a line in any diff. With the digest, it stays emulated until
# someone deliberately re-resolves and reads what changed.
VERAPDF_TAG = "docker.io/verapdf/cli:v1.30.2"

# verapdf is the image's ENTRYPOINT and is not on PATH. Measured: `sh -c
# verapdf ...` inside this image exits 127.
VERAPDF_BIN = "/opt/verapdf/verapdf"

# The script the generated check-pdf service runs, kept here so a test runs the
# SAME text rather than a re-typed approximation of it. tests/test_pdf_ua_gate.py
# asserts the rendered compose file carries it verbatim.
#
# THE ZERO-FILE GUARD IS WHY THIS IS A SCRIPT AND NOT A BARE COMMAND. Measured
# against verapdf/cli:v1.30.2: invoked with no file arguments, veraPDF exits 0
# and prints nothing. So `check-pdf` run before `pdf`, or in a directory whose
# PDFs were cleaned, or behind a glob that matched nothing, would report a
# clean bill of health having opened no file -- the same shape as the
# getContentsString() no-op that 0.21.0 nearly shipped. Nothing to check is a
# build error here, not a pass.
#
# THE FILES ARE NAMED, NOT GLOBBED. A package directory is also just a
# directory, and people put things in it: cs425/classroom carries an 11 MB
# third-party book, and `for f in *.pdf` failed the build on it. The report was
# not wrong -- that PDF is not conformant -- but it was not actionable, and an
# unactionable red is how a gate gets switched off.
#
# Naming them also makes the guard say more. `make pdf` writes a known list, so
# the check can tell "you gave me nothing" from "you gave me six and one of
# them is missing", and name the one that is missing.
VERAPDF_SCRIPT = f"""\
expected=0
found=0
failed=0
for f in "$@"; do
  expected=$((expected + 1))
  if [ ! -f "$f" ]; then
    echo "check-pdf expected $f and it is not there." >&2
    failed=1
    continue
  fi
  found=$((found + 1))
  echo "Checking $f (PDF/UA-1)..."
  {VERAPDF_BIN} --flavour ua1 --format text "$f" || failed=1
done
if [ "$expected" -eq 0 ]; then
  echo "check-pdf was given no file to check." >&2
  echo "Run 'make pdf' first. veraPDF exits 0 on an empty file list, so this" >&2
  echo "would otherwise have reported success having checked nothing." >&2
  exit 1
fi
echo "Checked $found of $expected PDF(s) against PDF/UA-1."
exit $failed
"""

# The script the generated check-access service runs, here for the reason
# VERAPDF_SCRIPT is: so a test can run the SAME text the compose file ships
# rather than an approximation of it. tests/test_check_access.py runs it inside
# the browser image, over both layouts.
#
# THAT IS NOT A THEORETICAL BENEFIT. Inlined in the compose file, this loop was
# `for f in /out/*.html` paired with `file:///workspace/$f`, which for a package
# with an output_dir asks Chromium for file:///workspace//out/foo.html and gets
#
#     Error: net::ERR_FILE_NOT_FOUND at file:///workspace//out/document.html
#
# so `make check-access` could not pass at all for such a package. It shipped in
# 0.30.0 and no test noticed, because every test here read the compose file's
# TEXT and none ran it. The directory now arrives as $1 and is absolute, and the
# URL is built from it -- one path, not two that have to agree.
#
# Every $ is doubled on the way into the compose file, exactly as for veraPDF;
# the doubling happens in the template so this text stays runnable through sh.
#
# THE FIRST LINE IS THE WHOLE stn-jeq FIX FOR THIS SERVICE, and it is one `cd`
# only because everything below it was already written in absolute paths.
#
# pa11y requires the puppeteer WRAPPER (lib/pa11y.js) and calls launch() inside
# it, so this half cannot be closed by resolving a different module -- the
# configuration puppeteer executes is found by searching upward from
# process.cwd(), which was /workspace, the consumer's own package directory.
# Measured on the pinned pa11y and puppeteer: a one-line .puppeteerrc.cjs there
# ran as uid 0 on every `make check-access`, and the check then printed its usual
# "No issues found!".
#
# pa11y ALSO resolves three things of its own from process.cwd() -- loadConfig's
# `./pa11y.json` default, loadReporter's path.join(process.cwd(), name) and
# loadRunnerFile's. None is reachable as this service is invoked, because the
# --config below is absolute and the reporter and runner are built-ins. Moving
# the working directory closes them by construction instead of leaving them one
# flag change away.
#
# Nothing after this line is cwd-relative: $directory arrives absolute in both
# layouts and the file:// URL is built from it, which is the property the rest of
# this comment block already argues for. Do not add a relative path below without
# revisiting this.
# IT REFUSES RATHER THAN CARRYING ON, and that matters more here than the `cd`
# itself. This script runs under `sh -c` with no `set -e`, so a bare `cd` that
# fails prints one line to stderr and CONTINUES from /workspace -- which reopens
# stn-jeq in full and still exits 0 with "Checked 2 HTML file(s) at WCAG 2.1 AA,
# light and dark." Every test stays green while the hole is open. That is the
# same shape of silent-guard failure the doubled-`$` comment in
# docker-compose-html.yml.j2 already argues about for the zero-file check: a
# guard that stops guarding without saying so.
CHECK_ACCESS_SCRIPT = f"""\
cd {BROWSER_TOOLS_DIR} || {{
  echo "check-access: {BROWSER_TOOLS_DIR} is not there, so this cannot move out" >&2
  echo "of the mount before pa11y launches Chromium. Dockerfile.browser did not" >&2
  echo "install the tools where this expects them." >&2
  exit 1
}}
""" """\
directory=${1:?check-access needs a directory to search}
failed=0
checked=0
for f in "$directory"/*.html; do
  [ -f "$f" ] || continue
  # Basename, because $f carries a directory prefix. Comparing the full path
  # would stop skipping the templates and pa11y would report on stencil's own
  # scaffolding.
  case "$(basename "$f")" in html-template.html|slide-template.html) continue ;; esac
  checked=$((checked + 1))
  # Both themes, each forced by clicking the control -- so these are rendered
  # pages rather than claims about them, and neither pass is left to resolve
  # `system` into whatever the runner happens to report. A light pass that
  # inherited a dark runner would measure dark twice and still go green.
  for theme in light dark; do
    echo "Checking $f ($theme)..."
    pa11y --config "/opt/pa11y-$theme.json" --standard WCAG2AA \\
      "file://$f" || failed=1
  done
done
# Zero files checked is a build error, not a pass. The loop is over a glob, and
# a glob that matches nothing leaves the body unrun and $failed at 0 -- which
# exits 0 and reads exactly like success. That is the same silent pass veraPDF
# has when handed no files, and moving the output somewhere this loop does not
# look is precisely how it would happen.
if [ "$checked" -eq 0 ]; then
  echo "check-access found no HTML to check." >&2
  echo "Run 'make doc' first. A glob that matches nothing exits 0, so this" >&2
  echo "would otherwise have reported success having checked nothing at all." >&2
  exit 1
fi
echo "Checked $checked HTML file(s) at WCAG 2.1 AA, light and dark."
exit $failed
"""

# The two constraints this module exists to protect. Both were reproduced by
# hand once and would otherwise be reproducible only by hand again.
_CITEPROC_AFTER_HIDDEN = (
    "Must follow hidden-filter. Citeproc collects a reference entry for every\n"
    "citation it can still see, so running it first would list the sources an\n"
    "answer key cites in the build that drops the answer key."
)
_CITEPROC_BEFORE_SLIDES = (
    "Between the two, and for two separate reasons. After hidden-filter, so a\n"
    "presenter-only citation stays out of the handout's reference list; before\n"
    "slide-sections, so the generated list is grouped into the last slide\n"
    "rather than landing outside every card, where present mode never shows it."
)
_FAIL_IF_WARNINGS = (
    "A mistyped citation key is otherwise a console warning and a\n"
    '"(**key?**)" in the page -- easy to miss, and it ships. Promote pandoc\'s\n'
    "warnings to build failures instead. This catches unclosed fenced divs\n"
    "too. It does not touch embed-images.lua's missing-figure notice, which\n"
    "goes straight to stderr rather than through pandoc's warning system."
)

_FIGURE_NAME_AFTER_MERMAID = (
    "Must follow mermaid-figure-filter, which is what turns a mermaid code\n"
    "block into a Figure. Run first and those five figures do not exist yet,\n"
    "so they reach the PDF as the one thing this filter exists to prevent: a\n"
    "/Figure with no /Alt, which PDF/UA rejects and no HTML checker reports."
)

_CODE_BUNDLE_AFTER_HIDDEN = (
    "Must follow hidden-filter, which is the only ordering constraint it has.\n"
    "The flag decides whether the 141 KB highlighter rides along, and a code\n"
    "block inside a `::: {.hidden}` div is not on the page unless the build\n"
    "asked for it -- run this first and `make doc` carries the bundle for a\n"
    "listing only `make doc WITH=hidden` ever shows.\n"
    "\n"
    "It does NOT need to follow mermaid-figure-filter. That filter wraps a\n"
    "mermaid CodeBlock in a Figure rather than consuming it, so the block is\n"
    "there either way; the exclusion is by class, which is true before and\n"
    "after. Placed last because nothing else wants it earlier."
)

_FRONTMATTER_FIRST = (
    "Metadata only, and first: it decides what the header rows are and\n"
    "resolves show_date into the date the byline asks for, so every later\n"
    "filter and the template itself read one settled set of keys. It touches\n"
    "no blocks, so it is outside the hidden/citeproc/slide-sections ordering\n"
    "constraints below rather than another link in that chain."
)

KINDS = ("doc", "slide")


# An exact version and nothing else: no caret, no tilde, no range, no tag, no
# `file:`/`git+ssh:` URL. Applied to the pin maps by npm_manifest, because a
# manifest is the one place where a range would be accepted by npm and would
# quietly reopen everything the lockfile closes. Prerelease and build metadata
# are both allowed -- they are legal semver npm resolves, and refusing them
# would be refusing a pin somebody may need rather than refusing a range.
_EXACT_VERSION = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

# A lowercase npm package name, optionally scoped, and nothing that could
# escape the shell single-quoting the manifest is written into. See
# npm_manifest. Uppercase is refused as well: npm has not accepted it in a new
# name for years, and every pin here is lowercase, so allowing it would widen
# the pattern for no case that exists.
_SAFE_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")


def npm_manifest(name: str, pins: dict[str, str]) -> str:
    """The package.json ``npm ci`` reads, as one line of JSON.

    Deterministic: ``sort_keys`` and a fixed separator, so the rendered
    scaffolding does not change because a dict was edited in a different order.
    That property used to belong to ``npm_specs``, which sorted an install
    line; the install line is gone and the manifest inherited the job.

    ONE LINE, because both call sites write it with ``printf '%s\\n' '<json>'``
    -- a Dockerfile ``RUN`` and a compose entrypoint. That makes the JSON a
    single-quoted shell word, so a ``'`` anywhere in it would end the quoting
    and hand the rest to sh, and a newline would end the printf. Both are
    refused below rather than escaped: every name and version here comes from a
    dict a maintainer edits by hand, so the honest answer to a value that
    cannot be represented is to fail at generation time.

    ``version`` is checked for exactness for a second reason. npm accepts
    ``^25.10.0`` in a manifest quite happily, and ``npm ci`` would then install
    whatever the lockfile holds while the manifest claimed a range -- the pin
    would read as satisfied and mean nothing.
    """
    if not _SAFE_NAME.match(name):
        raise ValueError(f"manifest name {name!r} is not a plain npm package name")
    for package, version in sorted(pins.items()):
        if not _SAFE_NAME.match(package):
            raise ValueError(
                f"{package!r} is not a plain npm package name (lowercase, "
                f"optionally scoped). Anything else would be written into a "
                f"shell-quoted printf in Dockerfile.browser and the format-md "
                f"entrypoint."
            )
        if not _EXACT_VERSION.match(version):
            raise ValueError(
                f"{package} is pinned to {version!r}, which is not an exact "
                f"version. A range in the manifest reopens exactly what the "
                f"lockfile closes: npm ci would install the locked tree while "
                f"the pin claimed something looser."
            )
    return json.dumps(
        {
            "name": name,
            "version": "0.0.0",
            "private": True,
            # Sorted here rather than by sort_keys, which would also reorder
            # the four keys above into something no one writes a package.json
            # in. Insertion order is deterministic in Python; the sort is what
            # keeps a re-ordered pin map from changing the rendered text.
            "dependencies": dict(sorted(pins.items())),
        },
        separators=(",", ":"),
    )


ASSETS_DIR = Path(__file__).parent / "assets"


class VendoredAssetError(RuntimeError):
    """A file stencil ships is missing or damaged -- not a config mistake.

    stn-hwo. read_lockfile used to raise FileNotFoundError for one of its two
    failures and ValueError for the other, and ValueError is the channel
    generate.package_contexts collects CONFIG problems on. So a damaged
    install was reported to the reader as a broken .config.yaml, under a
    heading that says so and a trailer telling them to fix it and delete a
    directory by hand -- for a fault fixed by
    `python3 scripts/vendor_npm_locks.py`, in a file a consumer of stencil may
    not be able to edit at all.

    Deliberately NOT a subclass of ValueError. Making it one would keep the
    old assertions passing and leave the bug exactly where it was: the point
    is that this must not travel on the config channel.
    """


def read_lockfile(filename: str) -> str:
    """A committed npm lockfile, ready to be rendered into a package.

    The trailing newline is stripped and the template puts it back, so the
    generated file is byte-identical to the committed one while the template
    itself stays an ordinary text file ending in a newline.
    """
    path = ASSETS_DIR / filename
    if not path.is_file():
        raise VendoredAssetError(
            f"npm lockfile not vendored: {filename}; "
            f"run python3 scripts/vendor_npm_locks.py"
        )
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n") or text.endswith("\n\n"):
        raise VendoredAssetError(
            f"{filename} must end with exactly one newline; npm writes it that "
            f"way, and the generated copy is asserted byte-identical to this "
            f"one. Re-vendor it: python3 scripts/vendor_npm_locks.py"
        )
    return text[:-1]


def lockfile_digest(filename: str) -> str:
    """The sha256 of the lockfile a generated package receives.

    stn-qge. `npm ci` fetches whatever host each `resolved` names and checks
    `integrity` against a value in the same file, so whoever can edit the
    lockfile decides which bytes get installed. Both installs read their
    lockfile out of the consumer's package directory -- format-md `cp`s it out
    of the mount -- which made a consumer-editable file the thing that chose
    what prettier is. The scaffolding therefore carries this digest and checks
    the copy before installing from it.

    HASHED FROM ``read_lockfile(...) + "\n"``, NOT FROM THE BYTES ON DISK, and
    that is the whole reason this is a function rather than a constant. The
    file a package gets is what the template writes: read_lockfile strips the
    trailing newline and the template puts one back. Hashing the asset
    directly would agree with that today and diverge the moment either side of
    that dance changes -- and a digest that disagrees with the file it guards
    refuses every honest build, which is how a checksum kept by hand always
    fails. Deriving both from one call cannot drift, and re-vendoring moves
    them together with no extra step: scripts/vendor_npm_locks.py writes the
    asset and this reads it.

    It inherits read_lockfile's validation, VendoredAssetError included -- a
    damaged install is not a config mistake, stn-hwo.
    """
    return hashlib.sha256((read_lockfile(filename) + "\n").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# stn-8vi: the three pulled images, pinned by manifest digest.
#
# IMAGE_TAGS is the plain, human-edited tags -- always readable, eagerly. The
# digests that turn a tag into the reference a build actually pulls live in
# stencil/assets/image-digests.json, written by
# scripts/resolve_image_digests.py and read lazily below, the same
# request/answer shape read_lockfile() above uses for the npm locks.
IMAGE_TAGS = {
    "node": NODE_TAG,
    "pandoc": PANDOC_TAG,
    "verapdf": VERAPDF_TAG,
}

_IMAGE_DIGESTS_FILE = "image-digests.json"

# Lowercase hex, exactly 64 characters, no separators -- the only shape a
# sha256 digest takes in a registry reference.
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@lru_cache(maxsize=1)
def _image_digests() -> dict[str, dict]:
    path = ASSETS_DIR / _IMAGE_DIGESTS_FILE
    if not path.is_file():
        raise VendoredAssetError(
            f"image digests not vendored: {_IMAGE_DIGESTS_FILE}; "
            f"run python3 scripts/resolve_image_digests.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def pinned_image(tag: str) -> str:
    """``<tag>@sha256:<digest>`` for a tag this scaffolding pulls.

    Looks the tag up in stencil/assets/image-digests.json rather than taking
    an inline ``tag@digest`` pair, because a registry resolves
    ``name:tag@digest`` BY THE DIGEST and ignores the tag entirely -- an
    inline pair that goes stale (tag bumped, digest not re-resolved) would
    silently keep pulling the OLD image under a name that now says something
    else. Keyed by the tag, that state cannot be expressed: a tag with no
    entry has nothing to look up, so a forgotten re-resolve fails here, loudly
    and before any network call, instead of shipping a digest that used to be
    right.

    Raises VendoredAssetError, not KeyError, for the same reason
    read_lockfile() raises it instead of FileNotFoundError: generate.py's
    ``main()`` already catches this exception and prints a clean "this is
    stencil's own installation, not your config" message rather than a
    traceback pointing at a dict lookup the reader did not write.
    """
    try:
        digest = _image_digests()[tag]["digest"]
    except KeyError:
        raise VendoredAssetError(
            f"no recorded digest for {tag}; "
            f"run python3 scripts/resolve_image_digests.py"
        ) from None
    # Checked HERE, at the point of use, and not only in the resolver that
    # writes the file. What comes back is interpolated into a Makefile, a
    # compose file and a Dockerfile FROM line, so the one thing that must
    # never happen is an arbitrary string reaching all three. The resolver
    # validates what it writes, but the file it writes is committed, and a
    # committed file gets hand-edited and mis-merged; a check the reader
    # performs costs one regex and does not depend on the writer having been
    # careful.
    if not _DIGEST.fullmatch(digest):
        raise VendoredAssetError(
            f"{_IMAGE_DIGESTS_FILE} records {digest!r} for {tag}, which is not "
            f"a sha256 digest; re-resolve it: "
            f"python3 scripts/resolve_image_digests.py"
        )
    return f"{tag}@{digest}"


# NODE_IMAGE / PANDOC_IMAGE / VERAPDF_IMAGE are resolved lazily, through
# module __getattr__ (PEP 562), rather than computed eagerly as
# `NODE_IMAGE = pinned_image(NODE_TAG)` right after NODE_TAG. This is a
# bootstrap requirement, not a style choice.
#
# pinned_image() raises the moment a tag has no recorded digest. If the three
# constants were eager, bumping a tag and running
# `python3 scripts/resolve_image_digests.py` to record its digest would
# already be too late: that script does `from stencil import pipeline` to
# read IMAGE_TAGS, and `import stencil.pipeline` would raise before the
# script's first line ever ran, on the exact tag it exists to resolve.
# scripts/vendor_npm_locks.py imports this module the same way. The
# documented bump procedure would deadlock on its own import on the first
# bump after this landed -- confirmed by construction, not assumed, which is
# what tests/test_pins.py::test_the_tags_are_readable_without_resolving_them
# guards against.
#
# __dir__ lists the three names too, so `dir(pipeline)` and tab-completion
# still show them even though `vars(pipeline)` does not.
_LAZY_IMAGES = {
    "NODE_IMAGE": NODE_TAG,
    "PANDOC_IMAGE": PANDOC_TAG,
    "VERAPDF_IMAGE": VERAPDF_TAG,
}


def __getattr__(name: str) -> str:
    try:
        tag = _LAZY_IMAGES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    return pinned_image(tag)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_IMAGES))


_TEMPLATE = {"doc": "html-template.html", "slide": "slide-template.html"}


def annotated_argv(kind: str) -> list[tuple[str, str | None]]:
    """The pandoc argv as (argument, explanation) pairs.

    The compose template renders the explanations as YAML comments so the
    generated file still reads as well as the hand-written one it replaced.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown render kind: {kind!r} (expected one of {KINDS})")

    argv: list[tuple[str, str | None]] = [
        ("--standalone", None),
        (f"--template={_TEMPLATE[kind]}", None),
        ("--fail-if-warnings", _FAIL_IF_WARNINGS),
        ("--lua-filter=frontmatter-filter.lua", _FRONTMATTER_FIRST),
        ("--lua-filter=hidden-filter.lua", None),
        (
            "--citeproc",
            _CITEPROC_BEFORE_SLIDES if kind == "slide" else _CITEPROC_AFTER_HIDDEN,
        ),
        ("--lua-filter=mermaid-figure-filter.lua", None),
        ("--lua-filter=figure-name-filter.lua", _FIGURE_NAME_AFTER_MERMAID),
        ("--lua-filter=embed-images.lua", None),
    ]
    if kind == "slide":
        argv.append(("--lua-filter=slide-sections.lua", None))
    argv.append(("--lua-filter=code-bundle-filter.lua", _CODE_BUNDLE_AFTER_HIDDEN))
    argv.append(("--mathml", None))
    return argv


def pandoc_argv(kind: str) -> list[str]:
    """The pandoc argv, without the explanations."""
    return [arg for arg, _ in annotated_argv(kind)]


def container_runtime() -> str | None:
    """The container CLI to drive, or None when neither is installed."""
    for exe in ("docker", "podman"):
        if shutil.which(exe):
            return exe
    return None


# The compose implementations a generated package can be driven by, in the
# order the generated Makefile prefers them: `DC ?= docker compose` is its
# default and `DC="podman compose"` the override it documents, and the
# standalone binaries are the fallback for a machine that has one of those
# without the plugin.
COMPOSE_IMPLEMENTATIONS = (
    ("docker", "compose"),
    ("podman", "compose"),
    ("docker-compose",),
    ("podman-compose",),
)


def compose_command() -> list[str] | None:
    """The compose implementation to drive, or None when there is none.

    PROBED BY RUNNING `<impl> version`, not by asking shutil.which whether the
    first word exists. `docker compose` is a CLI plugin: docker can be
    installed, on PATH and perfectly functional while `docker compose` exits
    125 with "unknown docker command". A which() check reports that machine as
    having compose, which turns a missing plugin into a failing test rather
    than the skip the container tier promises.
    """
    for command in COMPOSE_IMPLEMENTATIONS:
        if shutil.which(command[0]) is None:
            continue
        try:
            probe = subprocess.run(
                [*command, "version"], capture_output=True, text=True, timeout=120
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return list(command)
    return None


def compose(
    args: list[str],
    *,
    workdir: Path,
    project: str,
    command: list[str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run a compose subcommand against the docker-compose.yml in ``workdir``.

    This is the counterpart to ``render`` and ``check_access`` one level up. In
    those, a test drives the same image, script and mounts the compose file
    declares -- assembled here, in Python. This drives the compose file itself,
    so the build stanza, the image tag, the mount list and the arguments a
    service is handed are executed rather than read. Everything between the
    script and the argv was, until stn-8j4, asserted only as text.

    ``-f`` IS ABSOLUTE AND THE PROJECT DIRECTORY FOLLOWS IT. Compose resolves a
    relative volume source and a `.` build context against the project
    directory, which defaults to the directory holding the file -- exactly what
    a package's own `- .:/workspace:z` and `- ../../build/demo:/out:z` need. A
    relative -f from some other cwd would silently resolve both somewhere else.

    ``project`` IS REQUIRED, AND IS THE ISOLATION. Compose otherwise names the
    project after the directory basename, so two generated packages that happen
    to share one -- which every `demo` under a tmp_path does -- would share
    containers and a network. Pass a unique name per run and tear it down with
    ``down``; the generated services bind no host port, so nothing here can
    take a port a developer is using, and tests/test_compose_check_access.py
    asserts that stays true.

    stdin is /dev/null because `compose run` attaches it by default, and a
    service that read from a terminal would otherwise hang a test run rather
    than fail it.
    """
    command = command or compose_command()
    if command is None:
        raise RuntimeError(
            "no compose implementation found "
            "(looked for docker compose, podman compose, docker-compose, "
            "podman-compose)"
        )

    workdir = Path(workdir).resolve()

    return subprocess.run(
        [*command, "-f", str(workdir / "docker-compose.yml"), "-p", project, *args],
        cwd=workdir,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def render(
    kind: str,
    source: str,
    output: str,
    *,
    workdir: Path,
    metadata: dict[str, str] | None = None,
    runtime: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the pandoc pipeline over ``source`` in ``workdir``, writing ``output``.

    Paths are relative to ``workdir``, which is mounted at /workspace exactly as
    the generated docker-compose.yml mounts the package directory. Returns the
    completed process rather than raising, because several tests are about what
    a failing build does.
    """
    runtime = runtime or container_runtime()
    if runtime is None:
        raise RuntimeError("no container runtime found (looked for docker, podman)")

    argv = [
        runtime,
        "run",
        "--rm",
        "-v",
        f"{Path(workdir).resolve()}:/workspace:z",
        "-w",
        "/workspace",
        pinned_image(PANDOC_TAG),
        *pandoc_argv(kind),
    ]
    for key, value in (metadata or {}).items():
        argv += ["--metadata", f"{key}={value}"]
    argv += [source, "-o", output]

    return subprocess.run(argv, capture_output=True, text=True)


def build_browser_image(
    workdir: Path,
    *,
    tag: str | None = None,
    runtime: str | None = None,
) -> subprocess.CompletedProcess:
    """Build the Chromium image the pdf and check-access services share.

    The dockerfile is passed as an absolute path because the two runtimes
    disagree about what a relative -f is relative to: podman resolves it
    against the build context, docker against the current working directory.
    A bare "Dockerfile.browser" therefore works under podman and fails under
    docker with "no such file or directory".
    """
    tag = tag or browser_image_tag()
    runtime = runtime or container_runtime()
    if runtime is None:
        raise RuntimeError("no container runtime found (looked for docker, podman)")

    context = Path(workdir).resolve()

    return subprocess.run(
        [
            runtime,
            "build",
            "-f",
            str(context / BROWSER_DOCKERFILE),
            "-t",
            tag,
            str(context),
        ],
        capture_output=True,
        text=True,
    )


def run_in_browser(
    script: str,
    *,
    workdir: Path,
    tag: str | None = None,
    runtime: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run a node script in the browser image, over the package directory.

    The same image and mount html_to_pdf uses, with the script supplied rather
    than fixed. It exists because a deck's behaviour -- present mode, the theme
    control, the tab panes -- had no instrument at all: every test in this
    repository reads markup or a built PDF, and none of them can press a key.
    The first bug it was written for was a keyboard collision that only appears
    once a reader has focused one control and then used another.

    The script is written into the workdir rather than piped, so a failure
    leaves it on disk beside the page it was driving.

    IT RUNS FROM THE MOUNT, DELIBERATELY, AND IS THE ONE THING HERE THAT STILL
    DOES. Writing the script into the workdir and running `node <name>` from
    /workspace is exactly the shape the pdf service had before stn-jeq -- so
    this helper still sees a `.puppeteerrc.cjs` planted in the workdir, and a
    consumer package.json still decides whether its script is an ES module.
    That is not an oversight to finish tidying up: this is test
    instrumentation rather than shipped scaffolding, and
    tests/test_browser_isolation.py's CONTROL depends on it -- the control's
    whole job is to prove a planted decoy WOULD execute, so that the tests
    asserting it does not are measuring the fix rather than a malformed
    fixture. Harden this and that control can no longer fail.
    """
    tag = tag or browser_image_tag()
    runtime = runtime or container_runtime()
    if runtime is None:
        raise RuntimeError("no container runtime found (looked for docker, podman)")

    workdir = Path(workdir).resolve()
    name = "_browser-probe.js"
    (workdir / name).write_text(script)

    return subprocess.run(
        [
            runtime,
            "run",
            "--rm",
            "-v",
            f"{workdir}:/workspace:z",
            "-w",
            "/workspace",
            tag,
            "node",
            name,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def html_to_pdf(
    source: str,
    output: str,
    *,
    workdir: Path,
    out_dir: Path | None = None,
    script: str | None = None,
    tag: str | None = None,
    runtime: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Convert an HTML file to PDF the way the generated pdf service does.

    Same image, same entrypoint, same mounts and the same working directory --
    the compose service is `node BROWSER_SCRIPT_PATH` with `working_dir:
    /workspace`.

    THE WORKING DIRECTORY IS STILL /workspace AND THE SCRIPT IS NOT. That pair
    is the point rather than an inconsistency: the Makefile passes `$(OUT)/x.html`,
    which is relative for a package without an output_dir, so the process has to
    START in the mount for a relative argument to mean anything. html-to-pdf.js
    resolves its two arguments against that and then moves out of the mount
    itself, before puppeteer can read a configuration from it. See
    BROWSER_SCRIPT_PATH.

    ``out_dir`` mounts a second directory at /out, exactly as ``check_access``
    does and for the same reason -- a package with an ``output_dir`` puts its
    products in a sibling of its sources, and a sibling is ``..`` away, which
    escapes a bind mount. Pass ``/out/...`` paths to reach it.

    ``script`` overrides the entrypoint's path, and exists for one caller:
    tests/test_pins.py runs the generated script under the PLAIN node image,
    which has no {BROWSER_TOOLS_DIR} and therefore no baked copy, to prove the
    missing-tools guard fires. Every other caller wants the default.
    """
    tag = tag or browser_image_tag()
    runtime = runtime or container_runtime()
    if runtime is None:
        raise RuntimeError("no container runtime found (looked for docker, podman)")

    mounts = ["-v", f"{Path(workdir).resolve()}:/workspace:z"]
    if out_dir is not None:
        mounts += ["-v", f"{Path(out_dir).resolve()}:/out:z"]

    return subprocess.run(
        [
            runtime,
            "run",
            "--rm",
            *mounts,
            "-w",
            "/workspace",
            tag,
            "node",
            script or BROWSER_SCRIPT_PATH,
            source,
            output,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def check_access(
    *,
    workdir: Path,
    directory: str = "/workspace",
    out_dir: Path | None = None,
    tag: str | None = None,
    runtime: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run the generated check-access service's script over a directory.

    The counterpart to ``verapdf`` above, and it exists for the same reason:
    same image, same script, same mounts as the compose service, so a test
    measures the gate rather than something resembling it.

    ``out_dir`` mounts a second directory at /out, which is what a package with
    an ``output_dir`` gets -- its products are a sibling of the sources, and a
    sibling is ``..`` away, which escapes a bind mount. Pass ``directory``
    ``/out`` to search it.
    """
    tag = tag or browser_image_tag()
    runtime = runtime or container_runtime()
    if runtime is None:
        raise RuntimeError("no container runtime found (looked for docker, podman)")

    mounts = ["-v", f"{Path(workdir).resolve()}:/workspace:z"]
    if out_dir is not None:
        mounts += ["-v", f"{Path(out_dir).resolve()}:/out:z"]

    return subprocess.run(
        [
            runtime,
            "run",
            "--rm",
            *mounts,
            "-w",
            "/workspace",
            "--entrypoint",
            "sh",
            tag,
            "-c",
            CHECK_ACCESS_SCRIPT,
            # $0 for the script, then the directory as $1 -- exactly the two
            # arguments the compose service passes.
            "check-access",
            directory,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def verapdf(
    *,
    workdir: Path,
    files: list[str],
    runtime: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run the generated check-pdf service's script over a directory.

    Same image, same script, same mount as the compose service, so a test
    here measures the gate rather than something resembling it.
    """
    runtime = runtime or container_runtime()
    if runtime is None:
        raise RuntimeError("no container runtime found (looked for docker, podman)")

    return subprocess.run(
        [
            runtime,
            "run",
            "--rm",
            "-v",
            f"{Path(workdir).resolve()}:/workspace:z",
            "-w",
            "/workspace",
            "--entrypoint",
            "sh",
            pinned_image(VERAPDF_TAG),
            "-c",
            VERAPDF_SCRIPT,
            # $0 for the script; the files land in "$@" exactly as the compose
            # service passes them.
            "check-pdf",
            *files,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
