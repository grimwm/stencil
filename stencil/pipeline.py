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

import shutil
import subprocess
from pathlib import Path

# Pinned, and bumped deliberately. Against :latest a build is not reproducible
# across time, and a pandoc release can change rendered output or emit a new
# warning that --fail-if-warnings promotes to a failure -- breaking CI on a
# commit that changed nothing, at whatever hour the release landed.
#
# To bump: edit this line, run the suite, and read what changed in the rendered
# fixtures. The four-part tag is the specific release; :3.10 would still float
# across patch releases.
PANDOC_IMAGE = "docker.io/pandoc/core:3.10.0.0"

# The pdf and check-access services share one image, built from this Dockerfile
# in the generated package. Tests build it once and reuse the tag.
BROWSER_DOCKERFILE = "Dockerfile.browser"
BROWSER_IMAGE_TAG = "localhost/stencil_browser:test"

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
# To bump: edit this line, rebuild the browser image, and run the container
# tier. Read what moved in the PDF geometry and the PDF/UA results.
#
# A TAG, NOT A DIGEST -- deliberately, and not because a digest would be worse.
# A registry tag is mutable and `@sha256:...` is not, so a digest is strictly
# stronger here. It is left off because the same is true of PANDOC_IMAGE and
# VERAPDF_IMAGE, and pinning one of the three by digest buys defence in depth
# for a third of the surface while making the convention inconsistent for
# whoever bumps the next one. Raised by review; moving all three together is
# stn-5hv, with the lockfile.
NODE_IMAGE = "docker.io/library/node:24.20.0-alpine3.24"

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
# WHAT THIS DOES NOT PIN: the transitive tree. An exact version fixes these
# three and the exact puppeteer-core puppeteer itself declares; everything
# below that still resolves within a range at build time. `npm audit` over the
# resolved tree reported no known advisory at any severity on the day these
# were chosen, which is a measurement of that day and not a property of the
# pin. Closing the gap properly means a committed lockfile and `npm ci` --
# stencil already vendors its page assets exactly that way, in
# scripts/vendor_page_assets.py -- and is tracked as stn-5hv.
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

# PDF/UA-1 conformance checking. Pinned, because veraPDF's rule set is the
# thing being asserted against: an unpinned tag lets a build go red or green
# on someone else's release rather than on a change here.
VERAPDF_IMAGE = "docker.io/verapdf/cli:v1.30.2"

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
CHECK_ACCESS_SCRIPT = """\
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

_FRONTMATTER_FIRST = (
    "Metadata only, and first: it decides what the header rows are and\n"
    "resolves show_date into the date the byline asks for, so every later\n"
    "filter and the template itself read one settled set of keys. It touches\n"
    "no blocks, so it is outside the hidden/citeproc/slide-sections ordering\n"
    "constraints below rather than another link in that chain."
)

KINDS = ("doc", "slide")


def npm_specs(pins: dict[str, str]) -> list[str]:
    """``{"pa11y": "10.0.0"}`` -> ``["pa11y@10.0.0"]``, for an install line.

    Sorted, so the rendered scaffolding does not change because a dict was
    edited in a different order.
    """
    return [f"{name}@{version}" for name, version in sorted(pins.items())]


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
        PANDOC_IMAGE,
        *pandoc_argv(kind),
    ]
    for key, value in (metadata or {}).items():
        argv += ["--metadata", f"{key}={value}"]
    argv += [source, "-o", output]

    return subprocess.run(argv, capture_output=True, text=True)


def build_browser_image(
    workdir: Path,
    *,
    tag: str = BROWSER_IMAGE_TAG,
    runtime: str | None = None,
) -> subprocess.CompletedProcess:
    """Build the Chromium image the pdf and check-access services share.

    The dockerfile is passed as an absolute path because the two runtimes
    disagree about what a relative -f is relative to: podman resolves it
    against the build context, docker against the current working directory.
    A bare "Dockerfile.browser" therefore works under podman and fails under
    docker with "no such file or directory".
    """
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
    tag: str = BROWSER_IMAGE_TAG,
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
    """
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
    tag: str = BROWSER_IMAGE_TAG,
    runtime: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Convert an HTML file to PDF the way the generated pdf service does.

    Same image, same entrypoint, same mount -- the compose service is
    `node html-to-pdf.js` over the package directory at /workspace.
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
            tag,
            "node",
            "html-to-pdf.js",
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
    tag: str = BROWSER_IMAGE_TAG,
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
            VERAPDF_IMAGE,
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
