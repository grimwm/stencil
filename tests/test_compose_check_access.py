"""The generated check-access SERVICE, run rather than read.

stn-8j4, and the half of it that 0.31.0 deliberately left open.

``make check-access`` could not pass at all for a package that sets
``output_dir``. The service's entrypoint looped over ``/out/*.html`` and then
asked Chromium for ``file:///workspace/$f``, so every page came back

    Error: net::ERR_FILE_NOT_FOUND at file:///workspace//out/document.html

It shipped in 0.30.0 and nothing caught it, because every test in the
repository read the compose file's TEXT and none ran it.

0.31.0 moved the script into ``stencil/pipeline.py`` and
``tests/test_check_access.py`` now runs that script, in the real image, over
both layouts. THAT IS NOT THE SAME CLAIM AS "the service works". The script is
one line of a service definition. The build stanza, the image tag, the mount
list and the argument that tells the script WHICH DIRECTORY to search are the
other lines, and the bug was in the last of those -- a disagreement between
where the products are mounted and where the loop was told to look. A test
that assembles the mounts itself, in Python, cannot see that disagreement: it
supplies the correct answer as an argument and then confirms the script uses
it.

So this file runs ``compose build check-access`` and ``compose run --rm
check-access`` against a real generated package, which is what
``make check-access`` does, and asserts on what the service printed. Nothing
else in the repository has ever run compose.

WHY BOTH LAYOUTS ARE HERE. A package without ``output_dir`` puts its products
under the single ``/workspace`` mount, and that layout was never broken -- so a
test that covers only it proves nothing about the defect. The second layout,
where products are on a separate mount at ``/out`` because a sibling directory
is ``..`` away and ``..`` escapes a bind mount, is the one that could not pass.

PROVEN AGAINST ITS OWN BREACH, four ways, because a test that passes against
the bug is decoration:

- Reintroducing ``file:///workspace/$f`` in ``CHECK_ACCESS_SCRIPT`` fails both
  cases below with the shipped error verbatim --
  ``net::ERR_FILE_NOT_FOUND at file:///workspace//out/document.html``.
- Passing ``/workspace`` to the service in both layouts (dropping the
  ``has_package_output_dir`` arm on the argument) fails the output_dir case
  with ``check-access found no HTML to check``.
- Dropping the ``build:`` stanza and clearing the tag locally, which is what a
  contributor's machine looks like, fails both: the image the file names does
  not exist and nothing built it.
- Comparing the skip-list against ``$f`` rather than ``$(basename "$f")``
  fails the first case with ``Checked 3 HTML file(s)`` -- pa11y pointed at
  stencil's own pandoc templates.

THE LAST ONE IS WHY THIS FILE EXISTS RATHER THAN A FIFTH CASE IN
``tests/test_check_access.py``. The first three are also caught in the fast
tier, by a string assertion standing in for the behaviour. The fourth is
caught HERE AND NOWHERE ELSE: 298 fast-tier tests and all seven script-level
cases stay green, because the script-level tests point the script at a scrubbed
directory holding one copied page, and a skip-list is only wrong in a directory
that has something to skip. A real generated package is that directory.

WHY NO PORT IS BOUND, AND WHY THAT IS ASSERTED. Bringing compose into the
container tier is the point at which a generated service could start taking a
port a developer is already using -- 3000, 5173, 8000, 8080. None of them
declares one today; ``test_no_generated_service_binds_a_host_port`` is in the
fast tier so that stays true without a container to find out. Each run also
gets a compose project name of its own and is torn down afterwards, so two
packages that both generate as ``demo`` under a tmp_path cannot share
containers or a network.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
import warnings
from pathlib import Path

import pytest
import yaml

from stencil import pipeline

# NOT a module-level mark. The port assertion at the bottom reads the generated
# compose file and needs no container, so it belongs in the fast tier -- which
# is where a service that started binding a port should be caught, not behind a
# Chromium build.
integration = pytest.mark.integration

SOURCE = "document.md"
RENDERED = "document.html"

# What the service prints when it has actually opened a page. The count is part
# of the assertion: the generated package also carries html-template.html and
# slide-template.html, which the script skips by basename, and a service that
# checked three files would mean pa11y had been pointed at stencil's own
# scaffolding.
SUMMARY = "Checked 1 HTML file(s) at WCAG 2.1 AA, light and dark."


def runtime_behind(command: list[str]) -> str:
    """The image store the compose implementation writes into.

    NOT ``pipeline.container_runtime()``, which is a different question with a
    different answer. That one reports the runtime this repository's OTHER
    container helpers drive, and it prefers docker. The compose implementation
    is chosen separately and can be podman -- so on a machine holding both
    CLIs where compose resolves to ``podman compose`` or ``podman-compose``,
    asking container_runtime() would look for the image in docker's store while
    compose had just built it into podman's, and the build assertion below
    would fail on a build that worked perfectly.

    Every implementation name says which store it means: `podman compose` and
    `podman-compose` reach podman's, `docker compose` and `docker-compose`
    reach docker's.

    NO FALLBACK TO ANOTHER RUNTIME WHEN THAT CLI IS ABSENT. A standalone
    `docker-compose` is a separate binary that reaches the Docker socket
    directly, so it can build into docker's store on a machine that has no
    `docker` CLI at all -- and `podman compose` failing its probe while podman
    itself is installed is enough to select it. Asking the only runtime left on
    PATH would then query podman about an image docker holds, get "" back, and
    report a build that worked as a build that produced nothing. A wrong store
    is not a weaker answer to this question; it is a different question whose
    answer looks like failure. Skip instead, which is what the container tier
    promises for anything it needs and cannot find.
    """
    wanted = "podman" if command[0].startswith("podman") else "docker"
    if shutil.which(wanted) is None:
        pytest.skip(
            f"compose resolved to {' '.join(command)}, but {wanted} is not on "
            f"PATH, so its image store cannot be queried directly"
        )
    return wanted


def image_id(tag: str, runtime: str) -> str:
    """The local id of ``tag``, or "" when the runtime does not hold it.

    Asks the RUNTIME, not compose, because the question is whether the build
    stanza left a usable image behind under the name the compose file gives it
    -- which is what `compose run` will go looking for on a machine that has
    not built the pdf service first.
    """
    probe = subprocess.run(
        [runtime, "images", "-q", tag], capture_output=True, text=True, timeout=120
    )
    return probe.stdout.strip()


def outcome(label: str, result: subprocess.CompletedProcess) -> str:
    """Both streams, for a failure message worth reading.

    compose splits itself across them -- the service's own output arrives on
    stdout while compose's progress and any build error arrive on stderr -- so
    reporting one of the two is how a failure becomes "exit 1" and nothing else.
    """
    return (
        f"{label} exited {result.returncode}\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )


@pytest.fixture(scope="session")
def compose_impl():
    """The compose implementation to drive, or a skip.

    Separate from the runtime skip in conftest: a machine can have docker and
    not the compose plugin, and the container tier promises a skip rather than
    a failure for anything it needs and cannot find.
    """
    command = pipeline.compose_command()
    if command is None:
        pytest.skip(
            "no compose implementation found "
            "(docker compose, podman compose, docker-compose, podman-compose)"
        )
    return command


@pytest.fixture(scope="session")
def compose_runtime(compose_impl):
    """The image store to ask about a tag compose just built."""
    return runtime_behind(compose_impl)


@pytest.fixture
def compose(compose_impl):
    """Drive a generated package's compose file in a project of its own.

    Hands back a callable so a test reads as the Makefile does --
    ``compose("build", "check-access")``, then ``compose("run", "--rm", "-T",
    "check-access")``. Every project it opens is torn down when the test ends,
    passing or failing, so a run leaves no container and no network behind.
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
        # The image is left alone deliberately. It is minutes of Chromium and
        # npm, its layers are the cache the next run reads, and the repository
        # already leaves BROWSER_IMAGE_TAG in place for the same reason.
        # WARN RATHER THAN RAISE, and warn rather than propagate. A teardown
        # that raises from a finalizer replaces the assertion error the test
        # was reporting, so the run says "could not remove a network" about a
        # failure that was actually the service being broken. But a teardown
        # that says nothing leaks a compose network per run, and nobody finds
        # out until docker refuses to make another one.
        #
        # The timeout is the case that made this a try. `compose down` on a
        # wedged daemon raises TimeoutExpired rather than returning non-zero,
        # and an exception here would abandon every project after this one in
        # the loop -- turning one leaked network into all of them.
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


@integration
def test_the_service_checks_products_beside_their_sources(
    demo_config, generate_package, install_sources, compose, compose_runtime
):
    """The default layout: no output_dir, so the products are under /workspace.

    This one was never broken, and it is here to say so. Without it a failure
    in the output_dir case below cannot be told apart from "the service does
    not work at all" -- which is a different bug with a different fix.
    """
    config = demo_config
    config["packages"] = {"beside": config["packages"].pop("demo")}
    package = generate_package(config, "beside")
    install_sources(package)

    run = compose(package)

    built = run("build", "check-access", timeout=2400)
    assert built.returncode == 0, outcome("compose build check-access", built)
    assert image_id("localhost/beside_browser:latest", compose_runtime), (
        "the build stanza did not produce the tag the compose file names, so "
        "`compose run` would be running some other image or none"
    )

    rendered = run("run", "--rm", "-T", "doc", SOURCE, "-o", f"./{RENDERED}",
                   timeout=900)
    assert rendered.returncode == 0, outcome("compose run doc", rendered)
    assert (package / RENDERED).is_file(), (
        "the doc service reported success and the page is not in the package"
    )

    checked = run("run", "--rm", "-T", "check-access", timeout=1800)
    output = checked.stdout + checked.stderr

    assert "ERR_FILE_NOT_FOUND" not in output, (
        "the service found HTML and then asked the browser for a path that is "
        f"not there:\n{output[-4000:]}"
    )
    assert checked.returncode == 0, outcome("compose run check-access", checked)
    assert SUMMARY in output, (
        f"the service did not report having checked the page:\n{output[-4000:]}"
    )


@integration
def test_the_service_checks_an_output_directory(
    demo_config, generate_package, install_sources, compose, compose_runtime,
    tmp_path,
):
    """THE ONE THAT COULD NOT PASS, driven through the service that shipped it.

    A package with an ``output_dir`` gets a second mount at ``/out``, and the
    check-access service is handed ``/out`` as its argument rather than
    ``/workspace``. Those two lines are the wiring: the mount decides where the
    products are, the argument decides where the script looks, and 0.30.0
    shipped a pair that disagreed.

    Both halves are exercised here rather than described. The doc service
    writes through the ``/out`` mount, the check-access service reads through
    its own, and the page only turns up if the compose file gave the two
    services the same directory.
    """
    config = demo_config
    config["packages"] = {"elsewhere": config["packages"].pop("demo")}
    config["packages"]["elsewhere"]["output_dir"] = "build/elsewhere"
    package = generate_package(config, "elsewhere")
    install_sources(package)

    # The Makefile's `out-dir` target, which every build target depends on. It
    # is not housekeeping: a bind mount whose source does not exist is created
    # by the daemon and owned by root, and then nothing on the host can write
    # to it.
    out_host = tmp_path / "build" / "elsewhere"
    out_host.mkdir(parents=True)

    # The host directory this test created and the one the compose file mounts
    # have to be the same one, or everything below measures a layout the
    # generated package does not have.
    service = yaml.safe_load((package / "docker-compose.yml").read_text())[
        "services"
    ]["check-access"]
    mounted = [v for v in service["volumes"] if v.endswith(":/out:z")]
    assert len(mounted) == 1, f"check-access has no single /out mount: {service}"
    assert (package / mounted[0].split(":")[0]).resolve() == out_host.resolve()

    run = compose(package)

    built = run("build", "check-access", timeout=2400)
    assert built.returncode == 0, outcome("compose build check-access", built)
    assert image_id("localhost/elsewhere_browser:latest", compose_runtime), (
        "the build stanza did not produce the tag the compose file names, so "
        "`compose run` would be running some other image or none"
    )

    rendered = run("run", "--rm", "-T", "doc", SOURCE, "-o", f"/out/{RENDERED}",
                   timeout=900)
    assert rendered.returncode == 0, outcome("compose run doc", rendered)
    assert (out_host / RENDERED).is_file(), (
        "the doc service reported success and the page is not in the output "
        "directory -- the /out mount does not reach the host"
    )
    assert not (package / RENDERED).exists(), (
        "the page landed beside the sources, so this is not the layout under "
        "test and the case below would pass for the wrong reason"
    )

    checked = run("run", "--rm", "-T", "check-access", timeout=1800)
    output = checked.stdout + checked.stderr

    # The exact shape of the shipped defect, and the first thing to look at
    # when this file goes red: the loop found the file and the URL pointed
    # somewhere else.
    assert "ERR_FILE_NOT_FOUND" not in output, (
        "the service found HTML and then asked the browser for a path that is "
        f"not there:\n{output[-4000:]}"
    )
    # The other shape, which is silent: a service pointed at the wrong
    # directory checks nothing, and a glob that matches nothing would exit 0
    # if the script did not refuse.
    assert "found no HTML to check" not in output, (
        "the service searched a directory the products are not in -- the /out "
        f"mount and the argument disagree:\n{output[-4000:]}"
    )
    assert checked.returncode == 0, outcome("compose run check-access", checked)
    assert SUMMARY in output, (
        f"the service did not report having checked the page:\n{output[-4000:]}"
    )


def test_the_rendered_check_access_script_round_trips_the_dollar_doubling(
    doc_package,
):
    """What compose ships must be CHECK_ACCESS_SCRIPT with every `$` doubled.

    THE FAST TIER, for the reason the port test below is. Compose substitutes
    `$VAR` in a service definition before the shell ever sees it, so the
    template doubles every `$` on the way in -- and the zero-file guard's own
    comment records what a missed doubling costs: `$found` reaches the script as
    the empty string and `[ "" -eq 0 ]` is not a comparison that fails safely.
    The only thing that would otherwise catch a doubling mistake is the
    compose-driven tests in this file, and those SKIP when no compose
    implementation is present. AGENTS.md is explicit that a tier which silently
    stops running is the failure this repository already learned from its
    pre-push hook, so the round-trip is asserted where it always runs.

    It is an equality, not a search for a needle: that is what catches a `$`
    doubled where it should not have been as well as one that was missed, and
    it covers the leading `cd` stn-jeq added along with everything below it.
    """
    rendered = yaml.safe_load((doc_package / "docker-compose.yml").read_text())
    script = rendered["services"]["check-access"]["entrypoint"][2]

    assert script.replace("$$", "$") == pipeline.CHECK_ACCESS_SCRIPT, (
        "the rendered check-access script is not pipeline.CHECK_ACCESS_SCRIPT "
        "with its dollars doubled -- either a `$` was missed on the way in, or "
        "one was doubled that should not have been"
    )


def test_no_generated_service_binds_a_host_port(doc_package):
    """Nothing a generated package starts may take a port someone is using.

    The fast tier, on purpose. This file is the first thing in the repository
    to run compose, so it is also the first thing that would start a service
    that published a port -- and 3000, 5173, 8000 and 8080 are exactly the
    ports a developer already has something on. A `ports:` key added to any
    service in docker-compose-html.yml.j2 should fail here, on every
    interpreter and with no container runtime, rather than by locking someone
    out of their own dev server on the day they run `make`.
    """
    services = yaml.safe_load((doc_package / "docker-compose.yml").read_text())[
        "services"
    ]
    published = {
        name: service["ports"]
        for name, service in services.items()
        if service.get("ports")
    }
    assert not published, (
        "a generated service publishes a host port, which takes it from "
        f"whoever is already listening on it: {published}"
    )


# --- the pdf service, through compose, for the first time (stn-7ki) --------
#
# Everything above drives check-access. The pdf service has never been run
# through compose by anything in this repository -- tests/test_pdf.py and
# tests/test_pins.py call pipeline.html_to_pdf, which assembles its own
# `docker run` and never builds an image.
#
# That gap stopped being survivable when stn-7ki moved html-to-pdf.js out of the
# mount and into the image. The script now reaches the running service through
# a chain nothing measured end to end: rendered by `stencil gen` into the
# package directory (from a consumer's templates_dir, if they override it) ->
# COPYd out of the build context by Dockerfile.browser -> executed from
# {{ browser_tools_dir }} by the entrypoint. Break any link and every existing
# pdf test still passes, because they all bypass the first two.

OVERRIDE_MARKER = "<<<OVERRIDDEN-DRIVER>>>"


@integration
def test_a_templates_dir_override_of_the_pdf_driver_reaches_the_service(
    demo_config, generate_package, install_sources, compose, compose_runtime,
    tmp_path,
):
    """A consumer's own html-to-pdf.js must be the one the image runs.

    This is the single claim baking the script rests on: AGENTS.md promises
    that templates resolve through a search path so a consuming project can
    override one template without vendoring the set, and moving this file into
    the image is only safe because `build.context` IS the package directory the
    override renders into.

    THE MARKER IS ADDED, NOT SUBSTITUTED. The override is stencil's own
    template with one `console.log` prepended, so the service still has to do
    the whole job -- wait for window.__mermaidReady, refuse a failed asset,
    write a tagged PDF. A stub that only printed the marker would prove the
    COPY happened and nothing about whether what got copied still works.
    """
    source = (
        Path(__file__).parent.parent / "stencil" / "templates" / "html-to-pdf.js.j2"
    )
    directory = tmp_path / "templates"
    directory.mkdir()
    # AFTER the "use strict" directive, not before it. A directive is only a
    # directive when it is the first statement in the file, so prepending the
    # console.log would demote it to an ordinary expression and this test would
    # be driving a NON-STRICT variant of the script -- while its docstring
    # claims the service still has to do the whole job.
    body = source.read_text()
    marker_call = f'console.log("{OVERRIDE_MARKER}");\n'
    head, sep, tail = body.partition('"use strict";\n')
    assert sep, "html-to-pdf.js.j2 no longer opens with a \"use strict\" directive"
    (directory / "html-to-pdf.js.j2").write_text(head + sep + marker_call + tail)

    config = demo_config
    config["templates_dir"] = "templates"
    config["packages"] = {"override": config["packages"].pop("demo")}
    package = generate_package(config, "override")
    install_sources(package)

    assert OVERRIDE_MARKER in (package / "html-to-pdf.js").read_text(), (
        "the override did not even reach the generated package, so this test "
        "would be measuring the search path rather than the image"
    )

    run = compose(package)

    built = run("build", "pdf", timeout=2400)
    assert built.returncode == 0, outcome("compose build pdf", built)

    rendered = run("run", "--rm", "-T", "doc", SOURCE, "-o", f"./{RENDERED}",
                   timeout=900)
    assert rendered.returncode == 0, outcome("compose run doc", rendered)

    printed = run("run", "--rm", "-T", "pdf", f"./{RENDERED}", "./document.pdf",
                  timeout=900)
    output = printed.stdout + printed.stderr

    assert printed.returncode == 0, outcome("compose run pdf", printed)
    assert (package / "document.pdf").is_file(), (
        "the pdf service reported success and the PDF is not in the package -- "
        "which is what resolving only the INPUT argument against the mount "
        f"looks like, the output having landed beside the script:\n{output[-4000:]}"
    )
    assert OVERRIDE_MARKER in output, (
        "the service ran a pdf driver that is not this package's. The COPY in "
        "Dockerfile.browser, the build context, or the entrypoint path is "
        f"wrong -- or the image was not rebuilt:\n{output[-4000:]}"
    )
