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

PANDOC_IMAGE = "docker.io/pandoc/core:latest"

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
        ("--lua-filter=hidden-filter.lua", None),
        (
            "--citeproc",
            _CITEPROC_BEFORE_SLIDES if kind == "slide" else _CITEPROC_AFTER_HIDDEN,
        ),
        ("--lua-filter=mermaid-figure-filter.lua", None),
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
