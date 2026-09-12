"""The generated Makefile's compose invocations, PINNED to their file list.

stn-qli. `make` runs every compose subcommand with no ``-f`` at all, from the
package directory:

    Makefile-pkg.j2:91-93   $(DC) run --rm doc ... / $(DC) build pdf / $(DC) run --rm pdf ...
    pipeline.compose()      [*command, '-f', str(workdir / 'docker-compose.yml'), '-p', project, *args]

Compose with no ``-f`` auto-discovers and MERGES ``docker-compose.override.yml``
from the current directory, and reads ``.env`` for ``COMPOSE_FILE``.
``pipeline.compose()`` already passes an explicit ``-f``, which suppresses
both -- so every container test in this repository that drives compose
through Python exercises only the pinned-file path, and none of them can see
what ``make`` itself actually runs.

So a ``docker-compose.override.yml`` sitting next to the markdown -- the same
directory stn-20h's ``.prettierrc.json`` lives in -- rewrites any service's
image, entrypoint, user, volumes, or environment, or adds ``privileged:
true``, for every ``make`` invocation, for a generated package that is
downloaded, unzipped, or cloned by someone who never heard of compose file
resolution.

REPRODUCED this session against a real generated ``demo`` package, before any
fix: planted

    services:
      format-md:
        image: docker.io/library/alpine:latest
        entrypoint: ["sh", "-c", "echo STN-144-OVERRIDE-RAN uid=$(id -u)"]

beside the markdown and ran ``make format-md``:

    docker compose run --rm format-md
    STN-144-OVERRIDE-RAN uid=0

The override replaced the image and the entrypoint -- stn-20h's ``--no-config``
hardening gone -- from a file nothing in the package, the docs, or the
tracker mentions. ``uid=0`` IS NOT A RESTORED ROOT, and the first version of this
reproduction said it was. NO generated service in
``docker-compose-html.yml.j2`` sets ``user:`` at all -- measured, all six are
``user=None`` -- so the real format-md entrypoint already runs as uid 0. The
override took no user boundary because there is none to take.

THE SPELLING UNDER TEST (settled in review, not re-derived here):

    COMPOSE_FILES ?= docker-compose.yml
    STENCIL_COMPOSE = $(DC) $(addprefix -f ,$(COMPOSE_FILES))

Every compose call site becomes ``$(STENCIL_COMPOSE)``. The prefix is
``STENCIL_COMPOSE``, not ``COMPOSE``: a consumer composition defining its own
``COMPOSE`` would override this one and silently unpin it, with nothing here
to catch a wrong value.

None of the templates carry this spelling yet, so every test below fails
against the current templates -- that is the point of this file for now.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
import warnings
from pathlib import Path

import pytest
import yaml

from stencil import pipeline
from stencil.generate import build_environment, get_template_context

integration = pytest.mark.integration

TEMPLATES_DIR = Path(__file__).parent.parent / "stencil" / "templates"

# A DC value containing a dash, so CONTAINER (derived from DC by
# `$(firstword $(subst -, ,$(DC)))`, used for the `image inspect` probe) reads
# as "sentinel" while the compose sentinel itself reads as "sentinel-compose".
# Without the dash, both would be the same string and a search for the
# sentinel could not tell a runtime probe apart from a compose invocation --
# exactly the ambiguity `docker image inspect ... || docker compose pull`
# already has for the literal word "docker".
SENTINEL = "sentinel-compose"

# Scrubbed from every `make -n` subprocess: a developer with any of these set
# in their shell would otherwise make this whole tier pass vacuously, seeing
# their own override rather than the Makefile's default.
_SCRUBBED_ENV_VARS = ("COMPOSE_FILES", "DC", "MAKEFLAGS")

# What every generated compose invocation must expand to, immediately after
# the sentinel, once this task's fix lands.
PIN = " -f docker-compose.yml"

MAKEFILE_TEMPLATES = [{"src": "Makefile.j2"}, {"src": "docker-compose.yml.j2"}]

# The help pattern every target line matches, and the ONLY thing target
# derivation may read. MEASURED: `check-pdf` is in neither of the generated
# Makefile's two .PHONY lines (one lists help/format-md/doc/slide/pdf/
# check-access/clean/clean-pkg; the other is `.PHONY: out-dir`), so a
# derivation keyed off .PHONY silently drops it and the sweep below would
# never touch check-pdf's `$(DC) run --rm check-pdf ...` line at all.
_TARGET_PATTERN = re.compile(r"^([a-zA-Z0-9_-]+):.*?## ", re.MULTILINE)


def derived_targets(makefile_text: str) -> list[str]:
    """Every help-documented target name, in the order the Makefile lists them."""
    return _TARGET_PATTERN.findall(makefile_text)


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV_VARS}


def make_n(package: Path, target: str, *extra_args: str, dc: str = SENTINEL):
    """``make --no-print-directory -n <target> DC=<dc> <extra_args>`` in ``package``."""
    return subprocess.run(
        ["make", "--no-print-directory", "-n", target, f"DC={dc}", *extra_args],
        cwd=package,
        capture_output=True,
        text=True,
        env=clean_env(),
    )


def outcome(label: str, result: subprocess.CompletedProcess) -> str:
    """Both streams, so a failure message says more than "exit 1"."""
    return (
        f"{label} exited {result.returncode}\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )


@pytest.fixture(scope="module")
def require_make():
    if shutil.which("make") is None:
        pytest.skip("make is not installed")


# --- the two package shapes -------------------------------------------------
#
# MEASURED: DEMO_CONFIG's package_type is "none", so a guard built only on the
# shared `doc_package` fixture renders NONE of Makefile-pkg.j2's three compose
# call sites (`$(DC) run --rm doc`, `$(DC) build pdf`, `$(DC) run --rm pdf` in
# its `pkg:` recipe) -- a third of the surface this task pins would go
# unchecked. The second shape, package_type "doc" with package_sources, is
# the one that renders them; its config shape is copied from
# tests/test_package_sources.py's `doc_pkg_makefile` fixture.


@pytest.fixture
def pages_package(demo_config, generate_package, install_sources):
    """package_type "none" with docs and slides -- Makefile-doc.j2's call sites."""
    package = generate_package(demo_config)
    install_sources(package)
    return package


@pytest.fixture
def pkg_sources_package(generate_package):
    """package_type "doc" with package_sources -- Makefile-pkg.j2's call sites.

    `pkg`'s recipe expands `$(wildcard ...)` for PKG_SOURCES even under
    `make -n`, and an empty expansion trips `$(error package_sources matched
    nothing ...)` before anything prints -- so real files matching the glob
    have to exist on disk, not just be named in the config.
    """
    config = {
        "output_dir": "out",
        "templates": MAKEFILE_TEMPLATES,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "doc",
                "package_name": "hs2.pdf",
                "package_sources": ["md/*.md"],
                "docs": ["README.md"],
            }
        },
    }
    package = generate_package(config)
    (package / "md").mkdir()
    (package / "md" / "a.md").write_text("# a\n")
    (package / "README.md").write_text("# readme\n")
    return package


@pytest.fixture(params=["pages", "pkg_sources"])
def compose_driving_package(request):
    return request.getfixturevalue(f"{request.param}_package")


# --- (a)+(b): the make -n sweep --------------------------------------------


@pytest.mark.parametrize("os_name", ["Darwin", "Windows_NT"])
def test_every_compose_invocation_names_its_file(
    require_make, compose_driving_package, os_name
):
    """Every `$(DC)`-turned-`$(STENCIL_COMPOSE)` invocation must pin `-f`.

    Reads the EXPANSION (`make -n DC=<sentinel>`), not the template text, so a
    variable that is defined and merely unused cannot satisfy this. The
    sentinel carries a dash so it cannot be confused with CONTAINER, which
    `ensure_image`'s `image inspect` probe derives from DC and which the
    ticket's own investigation flags as indistinguishable from a plain
    substring search for "docker".

    PARAMETRIZED OVER OS, on the make command line (the same idiom
    tests/test_pipeline.py, tests/test_pdf_ua_gate.py and
    tests/test_package_sources.py already use for the same reason): a sweep
    that only ever runs `make -n` on the host it happens to execute on expands
    just one arm of Makefile-base.j2's `ifeq ($(OS),Windows_NT)` in
    `ensure_image` -- on any POSIX CI runner that is forever the `else` arm,
    and a `${DC}`-spelled or otherwise unpinned mutation of the Windows arm
    would pass this sweep with 566 tests green and nothing here to say so.
    """
    makefile_text = (compose_driving_package / "Makefile").read_text()
    targets = derived_targets(makefile_text)
    assert targets, "derived no targets from the help pattern -- derivation is broken"

    seen_any_invocation = False
    for target in targets:
        result = make_n(compose_driving_package, target, f"OS={os_name}")
        # A Makefile that dies partway through can still have printed some
        # matching lines before it did -- checking those alone would let a
        # broken Makefile pass.
        assert result.returncode == 0, outcome(f"make -n {target} OS={os_name}", result)

        for match in re.finditer(re.escape(SENTINEL), result.stdout):
            seen_any_invocation = True
            tail = result.stdout[match.end() : match.end() + len(PIN)]
            assert tail == PIN, (
                f"target {target!r} (OS={os_name}) ran the compose sentinel "
                f"without pinning a file immediately after it (got {tail!r}):"
                f"\n{result.stdout}"
            )

    assert seen_any_invocation, (
        f"the sentinel never appeared in any target's expansion for OS={os_name} "
        "-- the sweep checked nothing, which proves nothing about the pin"
    )


# --- (c): the empty-value guard ---------------------------------------------


def test_empty_compose_files_is_a_hard_error(require_make, compose_driving_package):
    """`make COMPOSE_FILES=` satisfies `?=` and would silently unpin compose.

    MEASURED by review on a patched Makefile: `?=` only skips assignment when
    the variable is already defined, and a command-line `COMPOSE_FILES=`
    counts as defined -- empty. `STENCIL_COMPOSE` would then expand to
    `$(DC)` with no `-f` at all: a real, working, UNPINNED invocation that
    silently reopens the override/`.env` hole this task exists to close. So an
    empty value must be refused outright, not merely left to `?=`.
    """
    result = make_n(compose_driving_package, "format-md", "COMPOSE_FILES=")
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"make -n format-md COMPOSE_FILES= succeeded:\n{outcome('make -n format-md', result)}"
    )
    assert "COMPOSE_FILES" in combined, (
        f"the failure does not name COMPOSE_FILES, so a consumer would not "
        f"know what to fix:\n{combined}"
    )


def test_compose_files_from_the_environment_is_a_hard_error(
    require_make, compose_driving_package
):
    """The `origin(COMPOSE_FILES) == environment` guard, exercised for real.

    This module's own `clean_env()` scrubs COMPOSE_FILES from every `make_n`
    subprocess precisely so a developer's shell cannot make this whole tier
    pass vacuously -- which means no other test here ever puts COMPOSE_FILES
    into the environment at all, and a deleted guard would look satisfied.
    This test reintroduces it deliberately, into the child process's
    environment rather than its command line, to prove the guard fires on
    that origin specifically.
    """
    env = {**clean_env(), "COMPOSE_FILES": "evil.yml"}
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "format-md", f"DC={SENTINEL}"],
        cwd=compose_driving_package,
        capture_output=True,
        text=True,
        env=env,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, outcome(
        "make -n format-md (COMPOSE_FILES=evil.yml in the environment)", result
    )
    assert "COMPOSE_FILES" in combined, (
        f"the failure does not name COMPOSE_FILES, so a consumer would not "
        f"know what to fix:\n{combined}"
    )


# --- the DC guard: DC may carry an implementation, never a flag -------------


def test_dc_carrying_a_flag_is_a_hard_error(require_make, compose_driving_package):
    """DC names a compose implementation only; a flag inside it is prepended
    ahead of the pin by `STENCIL_COMPOSE = $(DC) $(addprefix -f ,...)`, so it
    MERGES with the pinned file rather than being overridden by it -- see
    Makefile-base.j2's own comment on this guard, and STENCIL.md's Compose
    File Pinning section. MEASURED: `ifneq ($(filter -%,$(DC)),)` rejects
    `-f`, `--file`, `--project-directory` and `--env-file` alike, because the
    check is "any word starting with a dash", not a list of known flags.
    """
    result = make_n(
        compose_driving_package, "format-md", dc="docker compose -f evil.yml"
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, outcome(
        "make -n format-md DC='docker compose -f evil.yml'", result
    )
    assert "DC" in combined, f"the failure does not name DC:\n{combined}"


@pytest.mark.parametrize(
    "spelling", ["docker compose", "podman compose", "docker-compose", "podman-compose"]
)
def test_every_legitimate_dc_spelling_passes_the_flag_guard(
    require_make, compose_driving_package, spelling
):
    """The other direction of the same guard: none of the four spellings
    `pipeline.compose_command()` falls through has a word beginning with a
    dash -- `docker-compose` and `podman-compose` begin with a letter and are
    one word each -- so the guard above must let all four through untouched.
    """
    result = make_n(compose_driving_package, "format-md", dc=spelling)
    assert result.returncode == 0, outcome(f"make -n format-md DC={spelling!r}", result)


# --- (d): the template scan, as an allowlist --------------------------------

# `$(DC)` AND `${DC}` ALIKE -- make treats the two bracket spellings of a
# variable reference identically, so a scan that only recognizes `$(DC)`
# leaves a `${DC}`-spelled call site invisible to it. MEASURED: mutating the
# Windows arm of `ensure_image` to `${DC} pull $(2)` left all 566 tests
# passing before this pattern was widened.
_DC_REFERENCE_RE = re.compile(r"\$[({]DC[)}]")

# Everywhere a bare $(DC) (or ${DC}) is allowed to appear in any *.j2
# template: deriving CONTAINER from it, and the STENCIL_COMPOSE definition
# itself. Deliberately NOT a subcommand denylist ("run", "build", "pull") --
# that would miss exec, ps, cp, logs, and whatever compose adds next.
_ALLOWED_DC_WINDOWS = (
    re.compile(r"\$\(firstword \$\(subst -, ,\$[({]DC[)}]\)\)"),
    re.compile(
        r"^STENCIL_COMPOSE\s*=\s*\$[({]DC[)}]\s*"
        r"\$\(addprefix -f ,\$\(COMPOSE_FILES\)\)\s*$",
        re.MULTILINE,
    ),
    # The DC flag guard's own reference. `ifneq ($(filter -%,$(DC)),)` reads
    # DC to VALIDATE it -- before STENCIL_COMPOSE is even defined -- rather
    # than to invoke anything. MEASURED: without this entry, the guard's own
    # definition line flags itself as an offender and this test fails
    # against the real templates, which is a false positive rather than a
    # bug: the guard is what closes the sibling hole $(DC) carrying a flag
    # would open, documented at length in Makefile-base.j2 itself.
    re.compile(r"^ifneq \(\$\(filter -%,\$[({]DC[)}]\),\)\s*$", re.MULTILINE),
)


def test_bare_dc_only_appears_in_the_two_sanctioned_forms():
    """A future compose call site must go through $(STENCIL_COMPOSE) or fail here.

    Recurses (`rglob`, not `glob`) rather than naming Makefile-base/doc/pkg.j2,
    so a future Makefile-check.j2 -- including one nested under a subdirectory
    such as templates/make/ -- is covered without anyone remembering to list
    it.

    This reads the raw template TEXT rather than a rendered Makefile, which is
    the only way to see both branches of `ifeq ($(OS),Windows_NT)` --
    `make -n` only ever expands the branch matching the host it runs on, so
    the Windows arm of Makefile-base.j2's `ensure_image` is otherwise
    unreachable by any test in this repository.
    """
    offenders: dict[str, list[str]] = {}
    for template in sorted(TEMPLATES_DIR.rglob("*.j2")):
        text = template.read_text()
        sanctioned = {
            span
            for allowed in _ALLOWED_DC_WINDOWS
            for span in (m.span() for m in allowed.finditer(text))
        }
        lines = text.splitlines()
        for match in _DC_REFERENCE_RE.finditer(text):
            if any(start <= match.start() < end for start, end in sanctioned):
                continue
            line = text[: match.start()].count("\n") + 1
            source = lines[line - 1]
            # A make comment cannot invoke anything, and Makefile-base.j2's
            # own comments necessarily quote $(DC) while explaining what DC
            # is for and why CONTAINER is derived from it the long way. Only
            # a line make would RUN can leave compose unpinned.
            if source.lstrip().startswith("#"):
                continue
            offenders.setdefault(template.name, []).append(
                f"line {line}: {source.strip()}"
            )

    assert not offenders, (
        "a bare $(DC)/${DC} reaches compose outside $(STENCIL_COMPOSE), so a "
        f"consumer's override/.env can still unpin it there: {offenders}"
    )


# Every spelling of a compose implementation itself -- as opposed to $(DC),
# which the scan above already covers -- that a recipe could name literally
# and bypass $(DC)/$(STENCIL_COMPOSE) entirely.
_LITERAL_COMPOSE_IMPLEMENTATIONS = (
    "docker compose",
    "podman compose",
    "docker-compose",
    "podman-compose",
)


def test_no_recipe_spells_out_a_compose_implementation_literally():
    """Neither scan above catches a call site that spells the implementation
    out by name instead of going through $(DC) at all -- there is no `$(DC)`
    reference in `docker compose run --rm doc ...` for either of them to see.

    Scoped to RECIPE lines (tab-indented -- the only lines make ever hands to
    a shell) rather than every line in every template: Makefile-base.j2's own
    comments and its `$(error ...)` guard messages name all four spellings
    (e.g. `DC="podman compose"` in the DC-guard's own error text) to explain
    what DC is and is not for, and its `COMPOSE_FILES ?= docker-compose.yml`
    default names a compose FILE, not an implementation -- none of those
    lines make ever runs as a command, so none of them are the hole this
    closes, and a scan over every line would flag them as false offenders.
    MEASURED: no template's recipe lines contain any of the four spellings
    today, so this starts green against the real templates.
    """
    offenders: dict[str, list[str]] = {}
    for template in sorted(TEMPLATES_DIR.rglob("*.j2")):
        for lineno, line in enumerate(template.read_text().splitlines(), start=1):
            if not line.startswith("\t"):
                continue
            if line.lstrip().startswith("#"):
                continue
            if any(impl in line for impl in _LITERAL_COMPOSE_IMPLEMENTATIONS):
                offenders.setdefault(template.name, []).append(
                    f"line {lineno}: {line.strip()}"
                )

    assert not offenders, (
        "a recipe line spells out a compose implementation literally, "
        f"bypassing $(DC)/$(STENCIL_COMPOSE) entirely: {offenders}"
    )


# --- (e): recursive assignment -----------------------------------------------


def test_stencil_compose_uses_recursive_assignment():
    """':=' would freeze COMPOSE_FILES at Makefile-base.j2's own include point.

    A composition includes Makefile-base.j2 and then sets COMPOSE_FILES
    afterward -- from its own text, the command line, or the environment.
    With ':=' (simply expanded) that later value is silently ignored while
    every other way of setting a make variable keeps working, which is the
    kind of mistake that hides because it looks like it works.
    """
    text = (TEMPLATES_DIR / "Makefile-base.j2").read_text()
    match = re.search(r"^STENCIL_COMPOSE\s*(:?=)\s*\$\(DC\)", text, re.MULTILINE)
    assert match, "Makefile-base.j2 defines no STENCIL_COMPOSE variable yet"
    assert match.group(1) == "=", (
        f"STENCIL_COMPOSE is defined with {match.group(1)!r} (simply expanded) "
        "rather than recursive '='"
    )


# --- (f): the pinned file must actually exist -------------------------------


def test_the_pinned_file_is_one_the_package_actually_has(compose_driving_package):
    """The pin must name a file `stencil gen` wrote, not one it used to write.

    A hardcoded filename and the file the compose template renders to are two
    separate literals with nothing tying them together, so this is the
    assertion that keeps them equal. Rename the compose template's output and
    forget this line, and every generated package gets a Makefile whose every
    target dies with `open .../docker-compose.yml: no such file or directory`
    -- the error an explicit `-f` naming an absent file produces. MEASURED:
    `no configuration file provided` is the auto-DISCOVERY failure, printed
    only when compose is given no `-f` at all and finds nothing to fall back
    on; a pinned-but-missing file fails differently.

    THE `dest:` CASE IS A DOCUMENTED LIMITATION, NOT A BUG THIS CATCHES.
    STENCIL.md:187 lets a consumer render `docker-compose.yml.j2` under
    another name; such a package worked before this change through compose's
    own auto-discovery of `compose.yaml`, and after it must set COMPOSE_FILES
    to match. Making the default follow `dest:` would mean rendering the name
    out of the config, which is a `stencil/generate.py` change and outside
    this ticket. STENCIL.md says so instead -- see stn-144.4.
    """
    makefile_text = (compose_driving_package / "Makefile").read_text()

    match = re.search(r"^COMPOSE_FILES\s*\?=\s*(.+)$", makefile_text, re.MULTILINE)
    assert match, "no COMPOSE_FILES default in the generated Makefile yet"

    names = match.group(1).split()
    assert names, "COMPOSE_FILES ?= names no file at all"
    missing = [name for name in names if not (compose_driving_package / name).is_file()]
    assert not missing, (
        f"COMPOSE_FILES names {missing}, which this package does not contain, "
        "so every compose target would die with `open .../"
        f"{missing[0] if missing else ''}: no such file or directory`. "
        f"Package contains: "
        f"{sorted(p.name for p in compose_driving_package.iterdir())}"
    )


# --- the two Makefile-doc.j2 / Makefile-pkg.j2 STENCIL_COMPOSE guards -------


@pytest.fixture
def env(tmp_path):
    return build_environment({}, tmp_path)


def _run_make_on_rendered_partial(env, tmp_path, template_name: str, context: dict):
    """Render one Makefile partial ALONE -- the way a consumer's own
    Makefile.j2 could `{% include %}` it without Makefile-base.j2 first, per
    AGENTS.md -- write it as a real Makefile, and run `make` on it directly.

    No target is passed: the guard each partial carries is a top-level
    `ifeq`/`$(error ...)`, evaluated while make READS the file, before any
    goal is even chosen -- so this fails during parsing regardless of which
    target a caller would have picked.
    """
    text = env.get_template(template_name).render(context)
    (tmp_path / "Makefile").write_text(text)
    return subprocess.run(
        ["make", "--no-print-directory", "-n"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def test_makefile_doc_guard_fires_without_makefile_base(require_make, env, tmp_path):
    """Makefile-doc.j2's own `STENCIL_COMPOSE` guard, run for real rather than
    merely existing in the source text. MEASURED by deleting the guard: this
    test goes red while the other 566 stay green.
    """
    context = get_template_context(
        "demo", {"packages": {"demo": {"name": "Demo", "package_type": "none"}}}
    )
    result = _run_make_on_rendered_partial(env, tmp_path, "Makefile-doc.j2", context)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, outcome("make -n (Makefile-doc.j2 alone)", result)
    assert "STENCIL_COMPOSE" in combined, (
        f"the failure does not name STENCIL_COMPOSE:\n{combined}"
    )


def test_makefile_pkg_guard_fires_without_makefile_base(require_make, env, tmp_path):
    """Makefile-pkg.j2's own `STENCIL_COMPOSE` guard, run for real. Needs
    package_type "doc" with package_sources: that is the only arm of
    Makefile-pkg.j2 that calls compose at all (the zip arm archives with tar
    or zip, and package_type "none" renders nothing here but clean-pkg), and
    is the arm the guard actually sits in. MEASURED by deleting this guard:
    this test goes red while the other 566 stay green.
    """
    context = get_template_context(
        "demo",
        {
            "packages": {
                "demo": {
                    "name": "Demo",
                    "package_type": "doc",
                    "package_name": "hs2.pdf",
                    "package_sources": ["md/*.md"],
                }
            }
        },
    )
    result = _run_make_on_rendered_partial(env, tmp_path, "Makefile-pkg.j2", context)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, outcome("make -n (Makefile-pkg.j2 alone)", result)
    assert "STENCIL_COMPOSE" in combined, (
        f"the failure does not name STENCIL_COMPOSE:\n{combined}"
    )


# === CONTAINER TIER =========================================================


@pytest.fixture(scope="session")
def compose_impl():
    """The compose implementation to drive, or a skip.

    Session-scoped and separate from the `make` skip below, mirroring
    tests/test_compose_check_access.py: a machine can have `make` and no
    compose plugin at all, and the container tier promises a skip rather than
    a failure for anything it needs and cannot find.
    """
    command = pipeline.compose_command()
    if command is None:
        pytest.skip(
            "no compose implementation found "
            "(docker compose, podman compose, docker-compose, podman-compose)"
        )
    return command


MARKER = "STN-144-OVERRIDE-RAN"


@integration
def test_a_planted_override_is_inert_only_when_not_named(
    demo_config, generate_package, install_sources, compose_impl
):
    """The reproduction in this file's module docstring, run through `make` itself.

    Deliberately NOT `pipeline.compose()`, which already passes an explicit
    `-f` and so can never see this hole -- this drives the generated MAKEFILE
    as a subprocess, from the package directory, the way a person typing
    `make format-md` would.

    Two runs of the SAME target:

      1. `make format-md`                                       -> real formatter, no marker
      2. `make format-md COMPOSE_FILES="docker-compose.yml docker-compose.override.yml"`
                                                                  -> marker present

    Run 2 is the POSITIVE CONTROL and is not optional: without it, run 1
    passing proves nothing -- the override could be malformed, the service
    misnamed, or compose could be ignoring extra files for an unrelated
    reason. It is also the only test of the COMPOSE_FILES escape hatch
    stn-144.4 documents, by which a consumer can still merge an override,
    explicitly.

    PROVEN AGAINST ITS OWN BREACH: on the templates as they stand today (no
    `-f` at all), compose auto-discovers the planted override regardless of
    which command line runs it, so run 1 already fails here with the marker
    present -- reproducing the exact bug this task is red for, not a
    hypothetical one.
    """
    if shutil.which("make") is None:
        pytest.skip("make is not installed")

    package = generate_package(demo_config, "demo")
    install_sources(package)

    (package / "docker-compose.override.yml").write_text(
        yaml.safe_dump(
            {
                "services": {
                    "format-md": {
                        "image": "docker.io/library/alpine:latest",
                        "entrypoint": ["sh", "-c", f"echo {MARKER}"],
                    }
                }
            }
        )
    )

    project = f"stencil-test-{uuid.uuid4().hex[:12]}"
    env = {**clean_env(), "COMPOSE_PROJECT_NAME": project}

    try:
        plain = subprocess.run(
            ["make", "format-md"],
            cwd=package,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        plain_output = plain.stdout + plain.stderr
        assert MARKER not in plain_output, (
            "a planted docker-compose.override.yml rewrote format-md on a "
            f"plain `make format-md`, with no -f named at all:\n{plain_output}"
        )
        assert plain.returncode == 0, outcome("make format-md", plain)
        assert "Formatting markdown files..." in plain_output, (
            f"the real format-md entrypoint did not run:\n{plain_output}"
        )

        named = subprocess.run(
            [
                "make",
                "format-md",
                "COMPOSE_FILES=docker-compose.yml docker-compose.override.yml",
            ],
            cwd=package,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        named_output = named.stdout + named.stderr
        assert MARKER in named_output, (
            "naming the override explicitly in COMPOSE_FILES did not merge "
            f"it -- the escape hatch stn-144.4 documents does not work "
            f"either:\n{named_output}"
        )
    finally:
        # WARN RATHER THAN RAISE. A teardown that raises from a finalizer
        # replaces the assertion error the test was reporting, turning a
        # broken service into a mysterious network-removal failure.
        try:
            removed = pipeline.compose(
                ["down", "--remove-orphans", "--volumes"],
                workdir=package,
                project=project,
                command=compose_impl,
                timeout=600,
            )
            if removed.returncode != 0:
                warnings.warn(
                    f"compose down left project {project} behind "
                    f"(exit {removed.returncode}): {removed.stderr[-500:]}",
                    stacklevel=1,
                )
        except (OSError, subprocess.SubprocessError) as error:
            warnings.warn(
                f"compose down left project {project} behind: {error!r}",
                stacklevel=1,
            )
