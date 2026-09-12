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

WHY THE TWO HOSTILE-CONFIG TESTS EXIST -- A CONFIG FILE IS CODE (stn-20h).
The decoy above closes the question of which MANIFEST npm resolves. It stops
one loader short. Prettier's own configuration discovery is still rooted in the
mount: it searches upward from each formatted file for ``.prettierrc``,
``.prettierrc.json``, ``.prettierrc.cjs``, ``prettier.config.js`` and
``package.json#prettier``. Two of those are JavaScript, evaluated at load; and
ANY of them, including the ones that are only data, may name a ``plugins``
entry, which prettier then ``require``s out of the consumer's own
``node_modules``. The service runs as uid 0 over a read-write bind mount with
the network up, and ``make pkg`` runs it on every build -- so a course
repository that carries a ``.prettierrc.json`` for reasons of its own was
handing the formatter a choice of what code to be.

``test_a_consumers_js_prettier_config_is_not_executed`` and
``test_a_plugin_named_by_a_json_prettier_config_is_not_loaded`` plant each of
those two shapes and assert the consumer's JavaScript did not run. The fix they
pin is one flag, ``--no-config``.

THEY NEED TWO PACKAGES, NOT TWO FILES IN ONE. Prettier stops at the first
config file it finds, and ``.prettierrc.json`` outranks ``.prettierrc.cjs`` in
that search order. Planting both in one directory would silently exercise only
the JSON case and report it as two passing tests.

WHY EACH ONE ALSO SETS ``endOfLine: crlf`` -- THE POSITIVE CONTROL. Three
absence-assertions (no sentinel, no marker, still reformatted) prove "the flag
is on and prettier ran". They do NOT prove prettier's config search would ever
have reached that directory, so any later change to ``working_dir`` or to where
the fixture markdown is written would keep them green while testing nothing at
all. ``endOfLine`` is an option the service's argv does not override, unlike
``proseWrap`` and ``printWidth``, so its effect is visible in the output bytes:
honour the config and the file comes back CRLF, ignore it and the file stays
LF. Asserting no ``\r`` survived is what makes these tests fail if the config
was never in the search path to begin with.

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
import yaml

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

# The two stn-20h fixtures. Each writes a sentinel into the mount and prints a
# marker, so "this did not execute" is checked on the host's own filesystem as
# well as in the service's output -- compose interleaves stdout and stderr and
# a later change to -T or to logging could swallow a line, but it cannot
# swallow a file. The sentinel is the load-bearing assertion; the marker is
# corroboration.
#
# Neither sentinel ends in .md, so neither is a file prettier would format.
CONFIG_SENTINEL = "prettier-config-executed"
PLUGIN_SENTINEL = "prettier-plugin-executed"

# `endOfLine: crlf` is the positive control, and it is why both fixtures carry
# a real setting rather than an empty object. The service's argv already
# dictates --prose-wrap and --print-width, so a config naming those proves
# nothing either way; endOfLine is one prettier honours and the argv does not
# mention, so it is visible in the output bytes. See the module docstring.
HOSTILE_JSON_CONFIG = json.dumps(
    {"plugins": ["./node_modules/hostile-plugin/index.js"], "endOfLine": "crlf"}
)


def hostile_cjs_config(marker: str) -> str:
    """A .prettierrc.cjs that announces itself and then behaves.

    It MUST return a valid config after its side effect. A config file that
    throws makes prettier fail the file, which would leave the RED run failing
    on "the markdown was not reformatted" instead of on the sentinel -- two
    failures that read identically in a log, where the second invites someone
    to "fix" the fixture into something inert that never proved anything.
    """
    return (
        'const fs = require("fs");\n'
        f'fs.writeFileSync("/workspace/{CONFIG_SENTINEL}", '
        '"uid=" + process.getuid() + "\\n");\n'
        f'console.log("{marker}: .prettierrc.cjs evaluated as uid=" '
        "+ process.getuid());\n"
        'module.exports = { endOfLine: "crlf" };\n'
    )


def hostile_plugin(marker: str) -> str:
    """A prettier plugin that announces itself at require time.

    Exports the empty shape of a real plugin so that, in the RED run, prettier
    loads it and carries on formatting rather than erroring out -- again so the
    failure that fires is the sentinel and not a collapsed build.
    """
    return (
        'const fs = require("fs");\n'
        f'fs.writeFileSync("/workspace/{PLUGIN_SENTINEL}", '
        '"uid=" + process.getuid() + "\\n");\n'
        f'console.log("{marker}: plugin named by .prettierrc.json required '
        'as uid=" + process.getuid());\n'
        "module.exports = { languages: [], parsers: {}, printers: {} };\n"
    )


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


@pytest.mark.integration
def test_a_consumers_js_prettier_config_is_not_executed(
    demo_config, generate_package, compose
):
    """A .prettierrc.cjs in the package must never be evaluated (stn-20h).

    The most direct shape of the hole: a configuration file that is JavaScript,
    sitting in the directory the service mounts read-write and runs as uid 0
    over. Prettier evaluates it at load, so before ``--no-config`` this was the
    consumer's code running in stencil's container on every ``make pkg``.

    The fixture writes a sentinel into /workspace and prints a marker, and this
    test asserts neither arrived. It also asserts the markdown was still
    reformatted and the service still exited 0 -- without those, a service that
    failed to start, or a prettier that rejected the flag, would satisfy "the
    sentinel is absent" while proving nothing.

    WHY THIS CANNOT SHARE A PACKAGE WITH THE JSON TEST BELOW: prettier stops at
    the first configuration file it finds, and .prettierrc.json outranks
    .prettierrc.cjs. Planted together, only the JSON one would ever be read.

    WHAT THE RED RUN DEPENDS ON: that ``generate_package`` writes none of the
    files prettier ranks ABOVE .prettierrc.cjs -- package.json, .prettierrc,
    .prettierrc.json, .prettierrc.yaml, .prettierrc.json5, .prettierrc.js,
    .prettierrc.mjs. It writes none of them today. If one is ever added to a
    generated package, this test goes quietly green and stops meaning anything.
    """
    config = demo_config
    config["packages"] = {"jsconfig": config["packages"].pop("demo")}
    package = generate_package(config, "jsconfig")

    marker = f"MARKER-CJS-{uuid.uuid4().hex}"
    (package / ".prettierrc.cjs").write_text(hostile_cjs_config(marker))

    target = package / "scratch.md"
    target.write_text(BADLY_FORMATTED_MARKDOWN)
    before = target.read_bytes()

    run = compose(package)
    formatted = run("run", "--rm", "-T", "format-md", timeout=1800)

    assert formatted.returncode == 0, outcome("compose run format-md", formatted)

    assert not (package / CONFIG_SENTINEL).exists(), (
        "the .prettierrc.cjs in the package was evaluated: it wrote "
        f"{CONFIG_SENTINEL} into the mount as uid 0. That is stn-20h, still "
        f"open:\n{outcome('compose run format-md', formatted)}"
    )
    assert marker not in (formatted.stdout + formatted.stderr), (
        "the .prettierrc.cjs printed its marker, so it ran:\n"
        f"{outcome('compose run format-md', formatted)}"
    )

    after = target.read_bytes()
    assert after != before, (
        "the service exited 0 but never reformatted the file, so the absence "
        "of the sentinel proves nothing:\n"
        f"{outcome('compose run format-md', formatted)}"
    )
    assert b"\r" not in after, (
        "the file came back with CRLF line endings, which only the planted "
        "config asks for -- so prettier read it. This is the positive control: "
        "it is what tells the assertions above apart from a config that was "
        f"never in the search path at all:\n{after.decode()!r}"
    )


@pytest.mark.integration
def test_a_plugin_named_by_a_json_prettier_config_is_not_loaded(
    demo_config, generate_package, compose
):
    """A plugin named by a JSON config must never be required (stn-20h).

    The case that makes "the package only ships JSON" useless as a defence. The
    configuration file here contains no JavaScript at all -- it is a
    .prettierrc.json, a file a course repository plausibly carries for reasons
    that have nothing to do with stencil -- but its ``plugins`` entry names a
    path, and prettier ``require``s that path out of the consumer's own
    node_modules. Same uid, same mount, same build.

    THE PLUGIN PATH RESOLVES RELATIVE TO THE CONFIG FILE, not to the process's
    working directory. Both are /workspace here, so the distinction does not
    bite today -- but a later change that moved the fixture markdown into a
    subdirectory would move the search origin with it, and a ``./node_modules``
    path that no longer resolves would make this test pass for the wrong
    reason. If that move ever happens, move the plugin too.
    """
    config = demo_config
    config["packages"] = {"jsonconfig": config["packages"].pop("demo")}
    package = generate_package(config, "jsonconfig")

    marker = f"MARKER-PLUGIN-{uuid.uuid4().hex}"
    (package / ".prettierrc.json").write_text(HOSTILE_JSON_CONFIG)
    plugin = package / "node_modules" / "hostile-plugin"
    plugin.mkdir(parents=True)
    (plugin / "index.js").write_text(hostile_plugin(marker))

    target = package / "scratch.md"
    target.write_text(BADLY_FORMATTED_MARKDOWN)
    before = target.read_bytes()

    run = compose(package)
    formatted = run("run", "--rm", "-T", "format-md", timeout=1800)

    assert formatted.returncode == 0, outcome("compose run format-md", formatted)

    assert not (package / PLUGIN_SENTINEL).exists(), (
        "the plugin named by the package's .prettierrc.json was required: it "
        f"wrote {PLUGIN_SENTINEL} into the mount as uid 0. A config file that "
        "is pure data still chose what code the formatter would run -- that is "
        f"stn-20h's second case, still open:\n"
        f"{outcome('compose run format-md', formatted)}"
    )
    assert marker not in (formatted.stdout + formatted.stderr), (
        "the plugin printed its marker, so it was loaded:\n"
        f"{outcome('compose run format-md', formatted)}"
    )

    after = target.read_bytes()
    assert after != before, (
        "the service exited 0 but never reformatted the file, so the absence "
        "of the sentinel proves nothing:\n"
        f"{outcome('compose run format-md', formatted)}"
    )
    assert b"\r" not in after, (
        "the file came back with CRLF line endings, which only the planted "
        "config asks for -- so prettier read it, and would have read its "
        f"plugins entry too:\n{after.decode()!r}"
    )


def test_the_entrypoint_refuses_a_consumers_prettier_config(doc_package):
    """--no-config reaches the prettier ARGV, asserted where a comment cannot.

    The two tests above need a container and skip without one, which leaves the
    machine most likely to be running the fast tier -- a laptop with no compose
    implementation, CI's unit job -- with no coverage of stn-20h at all. This
    is that coverage.

    IT PARSES THE YAML RATHER THAN GREPPING THE FILE, and that is the whole
    design. The same change that adds this flag also adds a comment above the
    service explaining it, so ``"--no-config" in compose.read_text()`` would be
    satisfied by the comment forever -- including after someone deleted the
    flag from the argv it is meant to pin. Comments do not survive
    ``yaml.safe_load``; the entrypoint's script does.
    """
    compose_file = yaml.safe_load((doc_package / "docker-compose.yml").read_text())
    script = compose_file["services"]["format-md"]["entrypoint"][-1]

    invocation = f"{pipeline.FORMAT_TOOLS_DIR}/node_modules/.bin/prettier"
    assert invocation in script, (
        "the format-md entrypoint no longer invokes prettier from the tools "
        f"directory, so there is nothing here to pin:\n{script}"
    )
    argv = script[script.index(invocation) :]

    assert "--no-config" in argv.split(), (
        "the prettier invocation does not pass --no-config, so prettier will "
        "discover and honour a configuration file from the mounted package "
        "directory -- executing it, or executing a plugin it names, as uid 0. "
        f"That is stn-20h:\n{argv}"
    )
    assert argv.index("--no-config") < argv.index("--write"), (
        f"--no-config must precede --write to apply to the run:\n{argv}"
    )
