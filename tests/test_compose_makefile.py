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

The templates carry this spelling now, so every test below passes against
them. Each one names, in its own docstring, the mutation it goes red for --
a test that cannot fail against the bug it describes is decoration.
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

# A DC value containing a dash, so CONTAINER/STENCIL_CONTAINER (derived from
# DC by `$(firstword $(subst -, ,$(DC)))`, used for the `image inspect`
# probe) reads as "sentinel" while the compose sentinel itself reads as
# "sentinel-compose". Without the dash, both would be the same string and a
# search for the sentinel could not tell a runtime probe apart from a compose
# invocation -- exactly the ambiguity `docker image inspect ... || docker
# compose pull` already has for the literal word "docker".
SENTINEL = "sentinel-compose"

# Scrubbed from every `make -n` subprocess: a developer with any of these set
# in their shell would otherwise make this whole tier pass vacuously, seeing
# their own override rather than the Makefile's default. CONTAINER and
# STENCIL_CONTAINER are the image-probe runtime this module's CONTAINER tier
# exists to pin (stn-9o5); GNUMAKEFLAGS and MAKEFILES are two more
# injection/behavior surfaces `make` reads from the environment that nothing
# here deliberately exercises via a developer's own shell.
_SCRUBBED_ENV_VARS = (
    "COMPOSE_FILES",
    "DC",
    "MAKEFLAGS",
    "CONTAINER",
    "STENCIL_CONTAINER",
    "GNUMAKEFLAGS",
    "MAKEFILES",
)

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
    sentinel carries a dash so it cannot be confused with
    CONTAINER/STENCIL_CONTAINER, which `ensure_image`'s `image inspect` probe
    derives from DC and which the ticket's own investigation flags as
    indistinguishable from a plain substring search for "docker".

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

        # THE SAME QUESTION ASKED OF THE EXPANSION, not of the template text.
        # DC is the sentinel here, so every honest compose invocation reads
        # as `sentinel-compose`; a REAL implementation name in this output
        # can only have come from a line that spelled one out literally and
        # bypassed $(DC) altogether. This catches what the text scan cannot
        # see at all -- a continuation line, a canned recipe, anything make
        # assembles -- and it asks it of both `ifeq ($(OS),Windows_NT)` arms
        # because the sweep is parametrized over OS.
        literal = _LITERAL_COMPOSE_IMPLEMENTATION_RE.search(result.stdout)
        assert literal is None, (
            f"target {target!r} (OS={os_name}) expanded to a literal "
            f"{literal.group(0)!r} instead of going through $(DC)/"
            f"$(STENCIL_COMPOSE):\n{result.stdout}"
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


@pytest.mark.parametrize(
    "flagged_dc",
    [
        "docker compose -f evil.yml",
        "docker compose --file evil.yml",
        "docker compose --file=evil.yml",
        # NOT A COMPOSE FILE BY NAME, and the reason this guard refuses any
        # dash rather than an -f/--file denylist: --project-directory moves
        # the base every relative volume source resolves against, and
        # --env-file re-points the variable file. A denylist of the two
        # obvious spellings would let both of these through.
        "docker compose --project-directory /tmp",
        "docker compose --env-file evil.env",
        # The flag need not be last, or even after the subcommand word.
        "-f evil.yml docker compose",
        "docker -f evil.yml compose",
    ],
)
def test_dc_carrying_a_flag_is_a_hard_error(
    require_make, compose_driving_package, flagged_dc
):
    """DC names a compose implementation only; a flag inside it is prepended
    ahead of the pin by `STENCIL_COMPOSE = $(DC) $(addprefix -f ,...)`, so it
    MERGES with the pinned file rather than being overridden by it -- see
    Makefile-base.j2's own comment on this guard, and STENCIL.md's Compose
    File Pinning section.

    PARAMETRIZED OVER THE SPELLINGS THE GUARD CLAIMS TO REFUSE, rather than
    asserting them in prose. An earlier version of this test said "MEASURED:
    rejects -f, --file, --project-directory and --env-file alike" while
    exercising only `-f`. The claim was true, but a repository that treats a
    test passing against its own bug as a defect should not take a comment's
    word for the other three.
    """
    result = make_n(compose_driving_package, "format-md", dc=flagged_dc)
    combined = result.stdout + result.stderr
    assert result.returncode != 0, outcome(
        f"make -n format-md DC={flagged_dc!r}", result
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

def makefile_templates() -> list[Path]:
    """Every template make itself ever reads, wherever it lives.

    `Makefile*.j2` RATHER THAN `*.j2`, and rglob rather than glob, so a future
    `Makefile-check.j2` -- including one nested under a subdirectory such as
    templates/make/ -- is covered without anyone remembering to list it, while
    a template make never reads is not scanned for make syntax.

    THE NARROWING IS NOT COSMETIC. `$(DC)` inside a `.js.j2` or a `.yml.j2` is
    not a make variable reference at all: make never reads those files, so
    nothing there can unpin anything. MEASURED, and the reason this helper
    exists: `html-to-pdf.js.j2` carries the line

        // package directory and the Makefile runs `$(DC) build pdf` before every

    -- a JavaScript comment *describing* the Makefile -- and a scan over every
    `*.j2` flagged it as an offender. The scans below skip `#` comments, which
    is make's comment character and not JavaScript's, so no amount of
    comment-stripping fixes that; the file simply is not a Makefile. Compose
    is invoked from recipes, and recipes only exist in these files.
    """
    return sorted(TEMPLATES_DIR.rglob("Makefile*.j2"))


# `$(DC)` AND `${DC}` ALIKE -- make treats the two bracket spellings of a
# variable reference identically, so a scan that only recognizes `$(DC)`
# leaves a `${DC}`-spelled call site invisible to it. MEASURED: mutating the
# Windows arm of `ensure_image` to `${DC} pull $(2)` left all 566 tests
# passing before this pattern was widened.
_DC_REFERENCE_RE = re.compile(r"\$[({]DC[)}]")

# Everywhere a bare $(DC) (or ${DC}) is allowed to appear in any *.j2
# template: deriving CONTAINER/STENCIL_CONTAINER from it, and the
# STENCIL_COMPOSE definition itself. Deliberately NOT a subcommand denylist
# ("run", "build", "pull") -- that would miss exec, ps, cp, logs, and
# whatever compose adds next.
_ALLOWED_DC_WINDOWS = (
    re.compile(r"\$\(firstword \$\(subst -, ,\$[({]DC[)}]\)\)"),
    re.compile(
        r"^STENCIL_COMPOSE\s*=\s*(?:\$\(_stencil_pin_check\))?\s*\$[({]DC[)}]\s*"
        r"\$\(addprefix -f ,\$\(COMPOSE_FILES\)\)\s*$",
        re.MULTILINE,
    ),
    # The POINT-OF-USE half of the same two guards. `_stencil_pin_check` reads
    # DC to VALIDATE it, exactly as the parse-time `ifneq` below does, and
    # expands to nothing when the value is sound. It exists because the
    # parse-time guards are snapshots: a composition that includes this
    # partial and then sets DC or COMPOSE_FILES -- the arrangement
    # Makefile-base.j2's own comment advertises -- walks past them, and so
    # does a DC whose flag arrives by deferred expansion. Found by the
    # adversarial review of the first round of review fixes.
    re.compile(r"^_stencil_pin_check\s*=\s*.*$", re.MULTILINE),
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

    Scans every `Makefile*.j2` rather than naming Makefile-base/doc/pkg.j2,
    so a future Makefile-check.j2 is covered without anyone remembering to
    list it -- see makefile_templates() for why it is scoped to the templates
    make actually reads rather than to every `*.j2`.

    This reads the raw template TEXT rather than a rendered Makefile, which is
    the only way to see both branches of `ifeq ($(OS),Windows_NT)` --
    `make -n` only ever expands the branch matching the host it runs on, so
    the Windows arm of Makefile-base.j2's `ensure_image` is otherwise
    unreachable by any test in this repository.
    """
    offenders: dict[str, list[str]] = {}
    for template in makefile_templates():
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
            # is for and why CONTAINER/STENCIL_CONTAINER is derived from it
            # the long way. Only a line make would RUN can leave compose
            # unpinned.
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
#
# `(?![\w.-])` so a FILENAME containing the same letters is not an offender:
# `docker-compose.yml` (the COMPOSE_FILES default, a compose file rather than
# an implementation) and `docker-compose-html.yml.j2` (a Jinja include) both
# continue past "compose" into a name, and neither is a command.
_LITERAL_COMPOSE_IMPLEMENTATION_RE = re.compile(
    r"(?:docker|podman)(?:[ \t]+|-)compose(?![\w.-])"
)

# The one line allowed to name an implementation: DC's own default. Anything
# else that needs to SAY one -- an $(error) message explaining what DC is for
# -- is allowed by the `$(error` test below rather than by name, because an
# $(error) aborts the build and so can never be the invocation this guards
# against.
_DC_DEFAULT_DEFINITION_RE = re.compile(r"^DC\s*\?=\s*docker compose\s*$")


def test_no_template_line_spells_out_a_compose_implementation_literally():
    """Neither scan above catches a call site that spells the implementation
    out by name instead of going through $(DC) at all -- there is no `$(DC)`
    reference in `docker compose run --rm doc` for either of them to see.

    SCOPED TO NON-COMMENT LINES, NOT TO RECIPE LINES. An earlier version of
    this test looked only at tab-indented lines, on the reasoning that those
    are the only lines make hands to a shell. That reasoning is wrong, and
    wrong in the one place that matters: `ensure_image` is a COLUMN-0
    variable assignment expanded into recipes by `$(call ensure_image,...)`,
    and its two `ifeq ($(OS),Windows_NT)` arms are the only column-0
    recipe-producing assignments in the whole template set. So the tab
    scoping covered every harmless line and no dangerous one. MEASURED
    against that version: mutating either arm to a literal
    `|| docker compose pull $(2)` left the entire suite green while the
    generated Makefile pulled through an unpinned, auto-discovering compose.
    Found by the adversarial review of the first round of review fixes.

    A tab-indented line is not the only thing that reaches a shell either --
    a backslash continuation inside a recipe needs no tab of its own.

    The widened scope costs an allowlist of exactly two shapes, measured
    against the real templates: DC's own default definition, and the
    `$(error ...)` messages that name the four spellings to explain what DC
    is and is not for.
    """
    offenders: dict[str, list[str]] = {}
    for template in makefile_templates():
        for lineno, line in enumerate(template.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("{%"):
                continue
            if _DC_DEFAULT_DEFINITION_RE.match(stripped) or "$(error" in line:
                continue
            if _LITERAL_COMPOSE_IMPLEMENTATION_RE.search(line):
                offenders.setdefault(template.name, []).append(
                    f"line {lineno}: {stripped}"
                )

    assert not offenders, (
        "a template line spells out a compose implementation literally, "
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
    match = re.search(
        r"^STENCIL_COMPOSE\s*(:?=)\s*(?:\$\(_stencil_pin_check\))?\s*\$\(DC\)",
        text,
        re.MULTILINE,
    )
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


def _run_make_on_rendered_partial(
    env, tmp_path, template_name: str, context: dict, *, appendix: str = "", targets=()
):
    """Render one Makefile partial ALONE -- the way a consumer's own
    Makefile.j2 could `{% include %}` it without Makefile-base.j2 first, per
    AGENTS.md -- write it as a real Makefile, and run `make` on it directly.

    No target is passed by default: the guard each partial carries is a
    top-level `ifeq`/`$(error ...)`, evaluated while make READS the file,
    before any goal is even chosen -- so this fails during parsing
    regardless of which target a caller would have picked.

    `appendix` is a consumer's OWN Makefile text, appended after the
    rendered partial -- the same a-la-carte composition AGENTS.md documents,
    used by the STENCIL_CONTAINER escape-hatch test below to add an
    `override` directive and a target that actually calls `ensure_image`.
    `targets` names the goal(s) to run when a caller needs the appendix's own
    target evaluated rather than the default (first) one.
    """
    text = env.get_template(template_name).render(context) + appendix
    (tmp_path / "Makefile").write_text(text)
    return subprocess.run(
        ["make", "--no-print-directory", "-n", *targets],
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


# === (g): the image probe's runtime cannot be repointed -------------------
#
# stn-9o5. `ensure_image`'s `image inspect` probe USED TO READ
# `CONTAINER ?= $(firstword $(subst -, ,$(DC)))` -- an environment value, a
# `make -e`, a command-line assignment or a `MAKEFLAGS=VAR=value` all win over
# `?=`, so the probe ran whatever binary name was planted, with the package
# directory as cwd, regardless of what DC/STENCIL_COMPOSE actually pulled.
# IT NOW READS `override STENCIL_CONTAINER = $(firstword $(subst -, ,$(DC)))`,
# with both `ensure_image` arms on `$(STENCIL_CONTAINER)`.
#
# Both names are checked below, and they carry different weight now that the
# rename has landed. STENCIL_CONTAINER is the LIVE invariant: it is the name
# the template reads, and every route below is a real assertion about it.
# CONTAINER is a REGRESSION PIN: the template no longer reads that name, so
# those instances pass because nothing looks at it, and they exist to fail if
# the old spelling ever comes back. They are deliberately kept and
# deliberately not load-bearing -- said plainly here because this module's
# standing rule is that a test which cannot fail is decoration, and the honest
# answer is which half is which.

# Every route by which a value can reach a make variable without editing the
# Makefile text itself. GNUMAKEFLAGS is included deliberately even though it
# is inert on this repository's most common local `make` -- see
# _skip_if_gnumakeflags_dead below.
_INJECTION_ROUTES = (
    "environment",
    "environment_dash_e",
    "command_line",
    "MAKEFLAGS",
    "GNUMAKEFLAGS",
)

# A value distinguishing "the probe ran something planted" from "the probe
# ran the DC-derived runtime" at a glance in a failure message -- deliberately
# not a real binary name, since every test below runs `make -n` and never
# executes the line it inspects.
PLANTED_RUNTIME = "PLANTED-RUNTIME-MARKER"


def _make_major_version() -> int:
    """Best-effort major version parsed from `make --version`'s first line.

    Feature-detection input for the GNUMAKEFLAGS route alone. MEASURED: every
    other route in _INJECTION_ROUTES delivers an assignment identically on
    GNU Make 3.81 (macOS's system make) and 4.3 (this project's CI image), so
    nothing else here needs to know the version at all.
    """
    result = subprocess.run(["make", "--version"], capture_output=True, text=True)
    match = re.search(r"GNU Make (\d+)\.", result.stdout)
    return int(match.group(1)) if match else 0


def _skip_if_gnumakeflags_dead(route: str) -> None:
    """Skip (never xfail) a GNUMAKEFLAGS case on a `make` that ignores it.

    MEASURED directly against a real generated package, on the `?=` template
    as it stands today: `GNUMAKEFLAGS=CONTAINER=evil` and
    `GNUMAKEFLAGS=COMPOSE_FILES=` both leave GNU Make 3.81 (macOS's system
    make) completely unaffected -- CONTAINER still derives from DC and
    COMPOSE_FILES still defaults to docker-compose.yml, so even the KNOWN,
    already-fixed COMPOSE_FILES empty-value guard does not fire. On GNU Make
    4.3 (this project's CI image) the identical assignment IS honored. A
    route that does not exist on this host cannot be asserted against here
    without lying about what was checked, so this skips rather than either
    xfailing (which would report a route problem that isn't one) or asserting
    (which would fail for a reason unrelated to stn-9o5's fix). THE ROUTE
    ITSELF IS NOT REMOVED FROM THE PARAMETRIZATION -- it still runs, and
    still matters, in CI on GNU Make 4.3. Do not delete it because a local
    run shows a string of skips.
    """
    if route == "GNUMAKEFLAGS" and _make_major_version() < 4:
        pytest.skip(
            "GNUMAKEFLAGS is inert on this host's make (GNU Make "
            f"{_make_major_version() or '?'}.x); MEASURED live on GNU Make "
            "4.3 in CI. See _skip_if_gnumakeflags_dead's docstring."
        )


def _deliver(
    package: Path,
    route: str,
    target: str,
    var_name: str,
    var_value: str,
    *,
    dc: str = SENTINEL,
    os_name: str | None = None,
):
    """`<var_name>=<var_value>` delivered to `make -n <target> DC=<dc>` by `route`.

    Deliberately NOT built on make_n(): make_n() has no env hook at all, and
    -- the finding that matters most in this tier -- make_n() builds its
    environment from clean_env(), whose _SCRUBBED_ENV_VARS now contains
    MAKEFLAGS (and GNUMAKEFLAGS) precisely so a developer's own shell cannot
    steer this tier. A MAKEFLAGS/GNUMAKEFLAGS test built on make_n() would
    therefore never deliver either variable to make AT ALL, and would pass
    both "marker absent" and "derived runtime present" vacuously against the
    unfixed template -- a dead route indistinguishable from a closed one.
    This helper starts from the same clean_env() and then adds back exactly
    the one variable the route under test is supposed to carry.
    """
    argv = ["make", "--no-print-directory", "-n"]
    env = clean_env()
    if route == "environment_dash_e":
        argv.append("-e")
    argv.append(target)
    argv.append(f"DC={dc}")
    if os_name is not None:
        argv.append(f"OS={os_name}")
    if route in ("environment", "environment_dash_e"):
        env[var_name] = var_value
    elif route == "command_line":
        argv.append(f"{var_name}={var_value}")
    elif route == "MAKEFLAGS":
        env["MAKEFLAGS"] = f"{var_name}={var_value}"
    elif route == "GNUMAKEFLAGS":
        env["GNUMAKEFLAGS"] = f"{var_name}={var_value}"
    else:
        raise ValueError(f"unknown route {route!r}")
    return subprocess.run(argv, cwd=package, capture_output=True, text=True, env=env)


@pytest.mark.parametrize("os_name", ["Darwin", "Windows_NT"])
@pytest.mark.parametrize("route", _INJECTION_ROUTES)
@pytest.mark.parametrize("name", ["CONTAINER", "STENCIL_CONTAINER"])
def test_a_planted_runtime_never_reaches_the_probe(
    require_make, pages_package, name, route, os_name
):
    """A planted CONTAINER/STENCIL_CONTAINER must never reach `ensure_image`.

    name="STENCIL_CONTAINER" IS THE LIVE HALF. The template reads that name,
    and `override` beats every origin below -- environment, `make -e`, the
    command line, `MAKEFLAGS=VAR=value` and `GNUMAKEFLAGS=VAR=value`.
    MEASURED: mutating the template's `override` back to a plain `=` turns
    the environment_dash_e, command_line and MAKEFLAGS instances red on both
    OS arms, so this is asserting `override` specifically and not merely the
    rename.

    name="CONTAINER" IS A REGRESSION PIN, and passes because the template no
    longer reads that name at all. Before the rename these were the red ones:
    `?=` only skips assignment when the variable is ALREADY defined, which an
    exported, -e, command-line or MAKEFLAGS-delivered CONTAINER is before make
    reaches the derivation, and each route printed the planted value
    immediately before ` image inspect`. They are kept so that restoring the
    old spelling fails here rather than silently reopening stn-3y8.

    NOT CLOSED, and deliberately not asserted here: `MAKEFLAGS='--eval %:
    STENCIL_CONTAINER = x'` beats `override` on every GNU Make 4.x, because
    `--eval` can carry a pattern-specific assignment. Filed as stn-2je with
    its reproduction. It is absent from _INJECTION_ROUTES because it would be
    red, not because it does not exist.

    EVERY ROUTE CARRIES ITS OWN POSITIVE CONTROL, in the same test instance,
    and this is the finding that matters most for this tier. make_n() builds
    its environment from clean_env(), whose _SCRUBBED_ENV_VARS already
    contains MAKEFLAGS -- so a MAKEFLAGS test built on make_n() never
    delivers MAKEFLAGS to make at all, and, MEASURED against today's UNFIXED
    template, it would satisfy BOTH assertions below (marker absent, derived
    runtime present) for having delivered nothing. A dead route is
    indistinguishable from a closed one without a control that proves
    delivery. So the SAME route is used, in the SAME test, to send
    `COMPOSE_FILES=` and require the existing, already-fixed (stn-qli)
    empty-value $(error) to fire -- MEASURED to fire for every route this
    host's make honors. `_deliver()` (not make_n()) is used for both halves
    specifically so neither can go through make_n()'s scrubbing by accident.

    GNUMAKEFLAGS is skipped rather than asserted on a `make` that ignores it
    -- see _skip_if_gnumakeflags_dead. The route stays in the
    parametrization because it is live, and checked, in CI.
    """
    _skip_if_gnumakeflags_dead(route)

    planted = _deliver(
        pages_package, route, "format-md", name, PLANTED_RUNTIME, os_name=os_name
    )
    assert planted.returncode == 0, outcome(
        f"make -n format-md ({name}={PLANTED_RUNTIME} via {route}, OS={os_name})",
        planted,
    )
    assert PLANTED_RUNTIME not in planted.stdout, (
        f"a planted {name}={PLANTED_RUNTIME!r} via {route} (OS={os_name}) "
        f"reached the image probe:\n{planted.stdout}"
    )
    derived_marker = (
        f'"{SENTINEL.split("-")[0]} image inspect'
        if os_name == "Windows_NT"
        else f'{SENTINEL.split("-")[0]} image inspect'
    )
    assert derived_marker in planted.stdout, (
        f"the DC-derived runtime ({SENTINEL.split('-')[0]!r}, from "
        f"DC={SENTINEL!r}) does not appear before ' image inspect' -- the "
        f"probe line did not run at all, which would make the assertion "
        f"above vacuous:\n{planted.stdout}"
    )
    # THE POSIX MARKER IS A SUBSTRING OF THE WINDOWS ONE, so the assertion
    # above cannot tell the arms apart on its own. MEASURED: inverting
    # `ifeq ($(OS),Windows_NT)` in Makefile-base.j2 left every Darwin instance
    # GREEN with the PowerShell line rendered, and only the Windows arms went
    # red. Asserting the other arm is ABSENT is what closes that.
    if os_name != "Windows_NT":
        assert "powershell" not in planted.stdout, (
            f"OS={os_name} rendered the Windows arm of ensure_image -- the "
            f"ifeq is inverted:\n{planted.stdout}"
        )

    control = _deliver(
        pages_package, route, "format-md", "COMPOSE_FILES", "", os_name=os_name
    )
    combined = control.stdout + control.stderr
    assert control.returncode != 0, outcome(
        f"POSITIVE CONTROL: COMPOSE_FILES= via {route} (OS={os_name}) did "
        "not error -- the route does not reach make at all, so the "
        "assertions above prove nothing about it",
        control,
    )
    assert "COMPOSE_FILES" in combined, (
        f"the control's failure does not name COMPOSE_FILES, so the route "
        f"may not be the one that fired:\n{combined}"
    )


@pytest.mark.parametrize("os_name", ["Darwin", "Windows_NT"])
@pytest.mark.parametrize(
    ("dc_spelling", "runtime"),
    [
        ("docker compose", "docker"),
        ("docker-compose", "docker"),
        ("podman compose", "podman"),
        ("podman-compose", "podman"),
    ],
)
def test_the_probe_names_the_implementation_dc_names(
    require_make, pages_package, dc_spelling, runtime, os_name
):
    """The probe-and-pull-agree invariant, asserted rather than argued.

    All four spellings pipeline.compose_command() falls through must derive
    the SAME runtime `ensure_image` uses for `image inspect` as the one
    STENCIL_COMPOSE actually pulls with -- "docker compose"/"docker-compose"
    -> docker, "podman compose"/"podman-compose" -> podman. This holds on the
    template today (the derivation itself is not the bug; an override
    reaching around it is) and must keep holding once CONTAINER becomes
    STENCIL_CONTAINER, so it is pinned here independently of the injection
    tests above.

    PARAMETRIZED OVER OS: the Windows arm wraps the same derivation in a
    powershell -Command string, and a POSIX-only run would never exercise it.
    """
    result = make_n(pages_package, "format-md", f"OS={os_name}", dc=dc_spelling)
    assert result.returncode == 0, outcome(
        f"make -n format-md DC={dc_spelling!r} OS={os_name}", result
    )
    expected = (
        f'"{runtime} image inspect \''
        if os_name == "Windows_NT"
        else f"{runtime} image inspect '"
    )
    assert expected in result.stdout, (
        f"DC={dc_spelling!r} (OS={os_name}) did not derive {runtime!r} "
        f"immediately before \"image inspect '\":\n{result.stdout}"
    )


@pytest.mark.parametrize("os_name", ["Darwin", "Windows_NT"])
def test_a_composition_may_override_the_runtime_with_an_override_directive(
    require_make, env, tmp_path, os_name
):
    """The documented escape hatch: a consumer's own
    `override STENCIL_CONTAINER = ...`, written after including
    Makefile-base.j2, must win.

    LIVE, not decoration: MEASURED, reverting either `ensure_image` arm to
    `$(CONTAINER)` turns this red on the matching OS arm, because the
    consumer's override then reaches a name the recipe no longer reads. It was
    red before the rename too, when Makefile-base.j2 defined no
    STENCIL_CONTAINER at all and the consumer's override was simply inert.

    Renders Makefile-base.j2 ALONE, via _run_make_on_rendered_partial's
    `appendix`, the same a-la-carte composition AGENTS.md documents a
    consumer using -- so this is not testing stencil's own Makefile.j2, it is
    testing what a consumer who only includes the base partial can do after
    it.
    """
    context = get_template_context(
        "demo", {"packages": {"demo": {"name": "Demo", "package_type": "none"}}}
    )
    appendix = (
        "\n\noverride STENCIL_CONTAINER = nerdctl\n\n"
        "probe: ## probe target\n"
        "\t$(call ensure_image,test-image,probe)\n"
    )
    result = _run_make_on_rendered_partial(
        env,
        tmp_path,
        "Makefile-base.j2",
        context,
        appendix=appendix,
        targets=("probe", f"OS={os_name}"),
    )
    assert result.returncode == 0, outcome(
        f"make -n probe (consumer override, OS={os_name})", result
    )
    expected = (
        '"nerdctl image inspect \''
        if os_name == "Windows_NT"
        else "nerdctl image inspect '"
    )
    assert expected in result.stdout, (
        f"the consumer's `override STENCIL_CONTAINER = nerdctl` (OS={os_name}) "
        f"did not reach the probe:\n{result.stdout}"
    )


@pytest.mark.parametrize("os_name", ["Darwin", "Windows_NT"])
def test_the_runtime_is_reread_when_dc_is_assigned_later(
    require_make, env, tmp_path, os_name
):
    """`override STENCIL_CONTAINER =` must stay RECURSIVE, not `:=`.

    Makefile-base.j2's comment claims a DC assigned after the include is still
    read where the probe is used. MEASURED, and the reason this test exists:
    mutating that `=` to `:=` left the ENTIRE suite green -- 737 passed --
    while the generated probe silently froze on DC's default. This is the
    composition path AGENTS.md documents, so the claim is reachable and now
    pinned.

    Goes red against `:=`, which derives "docker" from the default
    `DC ?= docker compose` at the point of definition rather than "podman"
    from the consumer's later assignment.
    """
    context = get_template_context(
        "demo", {"packages": {"demo": {"name": "Demo", "package_type": "none"}}}
    )
    appendix = (
        "\n\nDC = podman-compose\n\n"
        "probe: ## probe target\n"
        "\t$(call ensure_image,test-image,probe)\n"
    )
    result = _run_make_on_rendered_partial(
        env,
        tmp_path,
        "Makefile-base.j2",
        context,
        appendix=appendix,
        targets=("probe", f"OS={os_name}"),
    )
    assert result.returncode == 0, outcome(
        f"make -n probe (DC assigned after the include, OS={os_name})", result
    )
    expected = (
        '"podman image inspect \''
        if os_name == "Windows_NT"
        else "podman image inspect '"
    )
    assert expected in result.stdout, (
        f"a DC assigned AFTER the include was not re-read (OS={os_name}) -- "
        f"the derivation is simply-expanded rather than recursive:\n"
        f"{result.stdout}"
    )


def test_a_target_specific_assignment_still_wins(require_make, tmp_path):
    """DOCUMENTED BEHAVIOUR, not a guard -- GREEN before and after stn-9o5's
    fix, because it pins a fact about GNU Make itself rather than about
    stencil's templates.

    Makefile-base.j2's comment states that a consumer's
    `override STENCIL_CONTAINER = ...` is the supported escape hatch, and
    that a plain assignment after it is silently ignored. That second half is
    an unqualified claim worth pinning precisely, because it is measurably
    NOT the whole story: a consumer's TARGET-SPECIFIC assignment
    (`probe: STENCIL_CONTAINER = x`) beats the global override for that one
    target, and a PATTERN-SPECIFIC assignment (`patterned-%: STENCIL_CONTAINER = x`)
    beats it for every target matching the pattern -- both of those are
    assignments too, and neither is "ignored". Only an unqualified, global,
    non-override, non-target-specific assignment loses. MEASURED on GNU Make
    3.81 and (per this ticket's own investigation) 4.3.

    Reaching this requires a consumer's OWN Makefile, independent of any
    stencil template, which can already run anything -- so this is not
    exercising stencil's templates at all, only the GNU Make semantics the
    template comment relies on to make its claim.
    """
    (tmp_path / "Makefile").write_text(
        "override STENCIL_CONTAINER = base\n"
        "STENCIL_CONTAINER = ignored\n"
        "\n"
        "direct: STENCIL_CONTAINER = direct_value\n"
        "direct:\n"
        "\t@echo VALUE=$(STENCIL_CONTAINER)\n"
        "\n"
        "patterned-%: STENCIL_CONTAINER = pattern_value\n"
        "patterned-x:\n"
        "\t@echo VALUE=$(STENCIL_CONTAINER)\n"
        "\n"
        "plain:\n"
        "\t@echo VALUE=$(STENCIL_CONTAINER)\n"
    )
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "direct", "patterned-x", "plain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        # clean_env() like every other make call in this module: harmless
        # today because `override` wins anyway, but this test is about
        # precedence, and a developer's own STENCIL_CONTAINER is precisely the
        # thing that must not be able to steer a precedence test.
        env=clean_env(),
    )
    assert result.returncode == 0, outcome("make -n direct patterned-x plain", result)
    assert "VALUE=direct_value" in result.stdout, (
        f"a target-specific assignment did not beat the global override:\n{result.stdout}"
    )
    assert "VALUE=pattern_value" in result.stdout, (
        f"a pattern-specific assignment did not beat the global override:\n{result.stdout}"
    )
    assert "VALUE=base" in result.stdout, (
        f"a plain global assignment AFTER `override` was not ignored -- the "
        f"override itself did not hold:\n{result.stdout}"
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
