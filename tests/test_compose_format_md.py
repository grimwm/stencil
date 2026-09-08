"""The generated format-md SERVICE, run rather than read.

stn-5hv, the other half of it.

``make format-md`` used to be a single ``npm install``:

    npm install --prefix /tmp/fmt prettier@3.9.6 \\
        @awmottaz/prettier-plugin-void-html@2.2.1

That line needed nothing on disk beyond the two version numbers already
spelled out in it, so a test that read the compose file's TEXT and confirmed
those two strings were present was, for once, an honest proxy for "the service
installs the right prettier". stn-5hv's whole point is that an unpinned
install still let 43 other packages in the resolved tree float, so the fix
pins those too -- not by adding more version numbers to the argv, but by
switching the install to ``npm ci`` against a lockfile vendored once, offline,
by ``scripts/vendor_npm_locks.py``. The entrypoint is now:

    mkdir -p /tmp/fmt && \\
    printf '%s\\n' '{...}' > /tmp/fmt/package.json && \\
    cp format-package-lock.json /tmp/fmt/package-lock.json && \\
    (cd /tmp/fmt && npm ci --ignore-scripts --no-audit --no-fund) && \\
    /tmp/fmt/node_modules/.bin/prettier ... --write "**/*.md"

Three of those four lines depend on things a text assertion cannot see:

- The ``cp`` names its source as a bare relative path, ``format-package-lock.json``.
  That resolves against the service's ``working_dir`` -- ``/workspace`` -- which
  is where ``generate_package`` writes the lockfile stencil vendors, and only a
  disagreement between the two would show up as ``cp: can't stat``. A test that
  builds the command in Python and hands it the right path, the way a unit test
  for a shell one-liner would, cannot see that disagreement either -- it has to
  read the compose file's own mount and working_dir.
- The manifest is a JSON object being ``printf``'d through a YAML block scalar
  (the ``|`` after ``entrypoint: - sh - -c -``). YAML's block scalar and the
  shell's own quoting both get a vote on what actually reaches ``printf``
  before npm ever sees it, and a mismatch there is a YAML or Jinja escaping bug
  wearing an npm error message.
- ``npm ci`` reads whatever manifest and lockfile are in ITS OWN CURRENT
  DIRECTORY. The ``(cd /tmp/fmt && ...)`` subshell is what makes that
  ``/tmp/fmt`` rather than ``/workspace``, where the service's ``working_dir``
  would otherwise leave it. Get that subshell wrong -- drop it, or make the
  ``cd`` and the ``npm ci`` disagree about which directory -- and ``npm ci``
  starts resolving against whatever the mounted package directory happens to
  contain instead.

tests/test_compose_check_access.py exists because exactly this class of bug --
a disagreement between where files are mounted and where a script goes looking
for them -- shipped in 0.30.0 and nothing caught it, because every test in the
repository at the time read the compose file's text and none ran it. format-md
now has the same shape: a script, a mount, and a working directory that all
have to agree, and as of stn-5hv it also copies a file across two of those
before running anything. Nothing has run it yet.

So this file runs ``compose run --rm -T format-md`` against a real generated
package -- which is what ``make format-md`` does -- and asserts on what
prettier actually did to a file on disk, not on what the entrypoint says it
will do. There is no ``compose build`` step here the way there is for
check-access: format-md names ``image:`` directly rather than a local ``build:``
stanza, so ``compose run`` pulls it like any other pull rather than building
one that could silently be stale.

WHY THE SECOND TEST EXISTS -- THE DECOY PACKAGE.JSON. A course package
directory legitimately containing its own Node project is ordinary: someone
building interactive exercises beside their markdown, say. Before ``npm ci``
was pointed at ``/tmp/fmt`` by name, or if the ``(cd ...)`` subshell it runs in
were ever dropped, ``npm ci`` invoked from ``/workspace`` would read whatever
manifest and lockfile live THERE instead -- so a consumer's own
``package.json`` could silently steer what gets installed into the formatter,
or fail the build outright over a dependency that has nothing to do with
formatting markdown. ``test_a_consumers_package_json_does_not_hijack_the_install``
plants exactly that: a ``package.json`` in the package directory naming a
dependency that does not exist anywhere on the registry. If ``npm ci`` ever
read it, the service would fail trying to resolve
``this-package-does-not-exist-9x7``. It has to keep succeeding, which is the
only way to show ``npm ci`` never looked.

WHY NO PORT IS BOUND, AND WHY THAT IS ASSERTED ELSEWHERE.
``test_no_generated_service_binds_a_host_port`` in
tests/test_compose_check_access.py already covers every service the compose
template renders, format-md included, so a ``ports:`` key added to this
service would fail there -- in the fast tier, with no container needed -- and
does not need repeating here.

WHY EACH COMPOSE PROJECT GETS A UUID AND A TEARDOWN. Two packages that both
generate as ``demo`` under a tmp_path would otherwise share a compose project
name, and with it, containers and a network. Each test here opens its own
project and tears it down in a ``finally``, the same as
tests/test_compose_check_access.py.

WHY EVERY CALL PASSES AN EXPLICIT TIMEOUT. ``npm ci`` pulls straight from the
network, and pulling the node image can too if it is not already cached
locally. A hung install is worse than a failed one: it burns the whole test
run's budget silently instead of reporting a number back.
"""

from __future__ import annotations

import json
import subprocess
import uuid
import warnings
from pathlib import Path

import pytest

from stencil import pipeline

# A single unwrapped line, well past --print-width 100, so prettier's
# --prose-wrap always has unambiguous work to do: rewrap it into several
# shorter lines rather than leave it exactly as written. If this test ever
# starts failing because the line is "already short enough", make it longer --
# the point is that the source is deliberately hostile to the formatter's
# configured width, not that it happens to be long today.
UNWRAPPED_PARAGRAPH = (
    "This paragraph is deliberately written as a single unwrapped line so "
    "that prettier's --prose-wrap always flag, run at --print-width 100, has "
    "unambiguous work to do: rewrap it into several shorter lines instead of "
    "leaving it exactly as written, which is the whole point of running a "
    "markdown formatter at all instead of just reading the compose file's "
    "text."
)

# The loose-bullet half of the fixture. Two spaces after the marker and two
# spaces inside an item are both things prettier's markdown printer collapses
# to one -- and, by default, normalizes the marker itself to "-". Verified
# against the real prettier 3.9.6 + void-html plugin pair stencil pins: this
# becomes "- loose bullets\n- another item\n".
LOOSE_BULLETS = "*  loose  bullets\n*  another   item\n"

BADLY_FORMATTED_MARKDOWN = f"# Report\n\n{UNWRAPPED_PARAGRAPH}\n\n{LOOSE_BULLETS}"

DECOY_PACKAGE_JSON = json.dumps(
    {
        "name": "decoy",
        "version": "1.0.0",
        "dependencies": {"this-package-does-not-exist-9x7": "1.0.0"},
    }
)


def outcome(label: str, result: subprocess.CompletedProcess) -> str:
    """Both streams, for a failure message worth reading.

    Same rationale as tests/test_compose_check_access.py's helper of the same
    name: compose splits itself across stdout and stderr, so reporting only
    one turns a real failure into "exit 1" and nothing else.
    """
    return (
        f"{label} exited {result.returncode}\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )


@pytest.fixture(scope="session")
def compose_impl():
    """The compose implementation to drive, or a skip.

    Copied from tests/test_compose_check_access.py rather than imported from
    it: `from tests.test_compose_check_access import ...` resolves locally
    (the repo root is on sys.path) while failing on CI with
    ModuleNotFoundError, the same trap conftest.py documents for its own
    fixtures.
    """
    command = pipeline.compose_command()
    if command is None:
        pytest.skip(
            "no compose implementation found "
            "(docker compose, podman compose, docker-compose, podman-compose)"
        )
    return command


@pytest.fixture
def compose(compose_impl):
    """Drive a generated package's compose file in a project of its own.

    Hands back a callable so a test reads as the Makefile does --
    ``compose("run", "--rm", "-T", "format-md")``. Every project it opens is
    torn down when the test ends, passing or failing, so a run leaves no
    container and no network behind.
    """
    opened: list[tuple[Path, str]] = []

    def _for(package: Path):
        project = f"stencil-test-{uuid.uuid4().hex[:12]}"
        opened.append((package, project))

        def _compose(*args: str, timeout: float | None = None):
            return pipeline.compose(
                list(args),
                workdir=package,
                project=project,
                command=compose_impl,
                timeout=timeout,
            )

        return _compose

    yield _for

    for package, project in opened:
        # Warn rather than raise for the same reason
        # tests/test_compose_check_access.py's identical teardown does: a
        # teardown that raises replaces the assertion error the test was
        # actually reporting, and a wedged daemon raises TimeoutExpired rather
        # than returning non-zero, which would otherwise abandon every
        # project after this one in the loop.
        try:
            removed = pipeline.compose(
                ["down", "--remove-orphans", "--volumes"],
                workdir=package,
                project=project,
                command=compose_impl,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as error:
            warnings.warn(
                f"compose down left project {project} behind: {error!r}",
                stacklevel=1,
            )
            continue

        if removed.returncode != 0:
            warnings.warn(
                f"compose down left project {project} behind "
                f"(exit {removed.returncode}): {removed.stderr[-500:]}",
                stacklevel=1,
            )


@pytest.mark.integration
def test_the_service_reformats_a_badly_formatted_file(
    demo_config, generate_package, compose
):
    """The ordinary case: one badly-formatted markdown file, actually rewritten.

    Generates a real package -- so the compose file AND the vendored
    format-package-lock.json it ``cp``s from are both the ones stencil ships,
    not a copy assembled in Python -- writes markdown prettier has unambiguous
    work to do on, runs the service, and diffs the file. A passing test proves
    the whole chain: the lockfile landed where the ``cp`` looks for it, the
    printf'd manifest survived the YAML block scalar intact enough for
    ``npm ci`` to accept it, and ``npm ci`` installed a prettier that ran.
    """
    config = demo_config
    config["packages"] = {"fmt": config["packages"].pop("demo")}
    package = generate_package(config, "fmt")

    target = package / "scratch.md"
    target.write_text(BADLY_FORMATTED_MARKDOWN)
    before = target.read_text()

    run = compose(package)
    formatted = run("run", "--rm", "-T", "format-md", timeout=1800)

    assert formatted.returncode == 0, outcome("compose run format-md", formatted)

    after = target.read_text()
    assert after != before, (
        "the file on disk is byte-for-byte what this test wrote, so prettier "
        f"never touched it:\n{outcome('compose run format-md', formatted)}"
    )

    # --prose-wrap always at --print-width 100: no line of the rewrapped
    # paragraph should still run past the configured width. (The lines this
    # test itself wrote as long input are gone by construction; anything left
    # over 100 columns would be prettier's own output.)
    body_lines = [line for line in after.splitlines() if line]
    longest = max((len(line) for line in body_lines), default=0)
    assert longest <= 100, (
        f"a line survived at {longest} columns, past --print-width 100:\n{after}"
    )

    # The loose bullets are the whitespace-collapsing half of the same claim,
    # independent of line wrapping: prettier's markdown printer normalizes the
    # marker to a single "-" and collapses the doubled internal spaces.
    assert "*  loose  bullets" not in after
    assert "- loose bullets" in after, (
        f"expected prettier's normalized bullet, got:\n{after}"
    )


@pytest.mark.integration
def test_a_consumers_package_json_does_not_hijack_the_install(
    demo_config, generate_package, compose
):
    """A package.json IN THE WORKSPACE must not steer npm ci in /tmp/fmt.

    Plants a package.json in the generated package directory -- an ordinary
    thing for a course package to contain on its own account -- naming a
    dependency that does not exist on the registry. If the service's
    ``npm ci`` ever ran against the workspace's manifest instead of the one it
    writes into /tmp/fmt, this install would fail trying to resolve
    ``this-package-does-not-exist-9x7``. The service has to keep succeeding,
    and prettier still has to run, for this to prove anything: a service that
    merely tolerated the decoy without still installing prettier would be
    passing for the wrong reason.
    """
    config = demo_config
    config["packages"] = {"decoy": config["packages"].pop("demo")}
    package = generate_package(config, "decoy")

    (package / "package.json").write_text(DECOY_PACKAGE_JSON)

    target = package / "scratch.md"
    target.write_text(BADLY_FORMATTED_MARKDOWN)
    before = target.read_text()

    run = compose(package)
    formatted = run("run", "--rm", "-T", "format-md", timeout=1800)

    assert formatted.returncode == 0, (
        "npm ci failed, which is what happens when it resolves the "
        f"workspace's decoy package.json instead of /tmp/fmt's own:\n"
        f"{outcome('compose run format-md', formatted)}"
    )

    after = target.read_text()
    assert after != before, (
        "the service exited 0 but never actually reformatted the file, so "
        "this proves nothing about which manifest npm ci read:\n"
        f"{outcome('compose run format-md', formatted)}"
    )
    assert "this-package-does-not-exist-9x7" not in (
        formatted.stdout + formatted.stderr
    ), "the decoy dependency was resolved at all, which it never should be"
