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
