"""Shared fixtures.

Two tiers of test live here. The argv and rendered-template assertions are
ordinary unit tests: they read stencil's own data structures and need nothing
installed. The build assertions need a container runtime, are marked
``integration``, and skip rather than fail when one is unavailable, so a
contributor without docker still gets a useful run.
"""

from __future__ import annotations

import copy
import os
import shutil
import time
from pathlib import Path

import pytest
import yaml
from bs4 import BeautifulSoup
from filelock import FileLock

from stencil import generate, pipeline

FIXTURES = Path(__file__).parent / "fixtures"

DEMO_CONFIG = {
    "output_dir": "out",
    "templates": [{"src": "Makefile.j2"}, {"src": "docker-compose.yml.j2"}],
    "packages": {
        "demo": {
            "name": "Demo",
            "package_type": "none",
            "docs": ["Guide.md"],
            "slides": ["Deck.md"],
        }
    },
}


# --- when the disk is the reason, say so (stn-0ot / stn-7im) ---------------
#
# A run that exhausts the filesystem holding the basetemp does not fail like a
# disk failure. It fails as OSError: [Errno 28] scattered across dozens of
# unrelated tests -- measured: `51 failed, 540 passed, 157 errors`, none of
# which mentions the disk in a way a reader connects to the cause, and the
# harness capturing the output can hit ENOSPC of its own and lose even that.
#
# So the number goes in the header of every run, where the log of the run that
# failed already has it, and a low-space failure gets told what happened.

LOW_SPACE_BYTES = 512 * 1024 * 1024


def _free_bytes(path: Path) -> int | None:
    """Free space on the filesystem holding `path`, or None if it cannot say.

    Walks upward: with retention="failed" the basetemp may not exist yet, or
    may have been removed by the time this is asked, and a guard that raises
    while explaining a failure is worse than one that stays quiet.
    """
    current = Path(path)
    for candidate in (current, *current.parents):
        try:
            return shutil.disk_usage(candidate).free
        except (OSError, ValueError):
            continue
    return None


def _low_space_note(basetemp: Path, threshold: int | None = None) -> str | None:
    """The message to attach to a failure, or None when the disk is fine.

    None above the threshold is the load-bearing half: an annotation on every
    failure is noise, and noise is how people learn to skip a section that
    will one day be the answer.

    `threshold=None` reads LOW_SPACE_BYTES at CALL time rather than binding it
    as a default at import time. A default argument would freeze the value at
    module load, which makes the constant look adjustable while being nothing
    of the kind -- tests/test_tmp_footprint.py caught exactly that by setting
    it and watching nothing change.
    """
    if threshold is None:
        threshold = LOW_SPACE_BYTES
    free = _free_bytes(basetemp)
    if free is None or free >= threshold:
        return None
    return (
        f"Only {free / 1e6:.0f}MB free on the filesystem holding {basetemp}.\n"
        f"The container tier writes a ~10.75MB package per generated test, so "
        f"this failure may be the disk rather than the code.\n"
        f"Point the tree somewhere with room and run again:\n"
        f"    pytest --basetemp=~/.cache/stencil-pytest"
    )


def pytest_report_header(config):
    """Where the tree goes and how much room it has, in every run's log."""
    basetemp = config.getoption("basetemp") or Path(
        config._tmp_path_factory.getbasetemp()
        if hasattr(config, "_tmp_path_factory")
        else "/tmp"
    )
    free = _free_bytes(Path(basetemp))
    if free is None:
        return f"basetemp: {basetemp}"
    return f"basetemp: {basetemp} ({free / 1e9:.1f}GB free)"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Annotate a FAILING test when the disk is nearly full.

    At the moment of failure rather than in the terminal summary, and that is
    not a stylistic choice: with retention="failed" a passing test frees its
    tree as it goes, so a run that genuinely exhausted the disk mid-way can
    look perfectly healthy by the time the summary is written.

    wrapper=True because pyproject pins a pytest new enough for it, and
    because the report object has to exist before a section can be added to
    it.
    """
    report = yield
    if report.when == "call" and report.failed:
        note = _low_space_note(Path(item.config._tmp_path_factory.getbasetemp()))
        if note:
            report.sections.append(("Disk space", note))
    return report


# --- two runs at once must not corrupt each other (stn-zim) ----------------
#
# The neighbouring problem to the one above, and it arrives through the same
# door: this file's own advice for a full disk is "pass --basetemp somewhere
# with room", and doing that from two worktrees is what turned the first of
# these up.
#
# Measured: two suites given the same --basetemp delete each other's fixture
# trees, because pytest rotates that directory at startup. It read as
# `22 failed, 789 passed, 79 errors`, almost all FileNotFoundError under the
# shared path -- a catastrophic-looking regression rather than two runs
# fighting, and it cost a re-run to tell the difference.
#
# The image tag is the same hazard, reasoned from the code rather than
# observed: pipeline's browser helpers built and ran ONE fixed tag, so a
# second run could rebuild the image out from under a first still using it.
# AGENTS.md tells every agent to work in a worktree, so two suites at once is
# the arrangement that setup exists to support rather than an unusual one.

RUN_OWNER = ".pytest-run-owner"


def _run_id() -> str:
    """Stable within a run, different between runs."""
    return f"{os.getpid()}-{int(time.time())}"


def _is_xdist_worker() -> bool:
    """Whether this process is an xdist worker rather than the controller.

    xdist sets this in every worker and never in the controller, which is the
    distinction the guard below needs. Nothing coarser will do: keying on the
    xdist plugin being loaded, or on `-n` appearing in the command line, would
    switch the guard off for the whole of a parallel run -- and a parallel run
    is exactly as capable of being pointed at another worktree's basetemp as a
    serial one.
    """
    return bool(os.environ.get("PYTEST_XDIST_WORKER"))


def _pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists.

    Not `/proc/<pid>`, which is Linux-only: on macOS that path never exists,
    so the guard below silently never fired -- the exact failure AGENTS.md
    records for the drift guard, a check that reads as protection while
    doing nothing. Signal 0 is the portable existence probe on POSIX, and
    EPERM means the process exists and belongs to someone else, which still
    counts as alive. Windows has no equivalent -- os.kill there is
    TerminateProcess for anything but the two console events -- so no
    liveness is claimed and a marker alone does not block.
    """
    if os.name == "nt":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _claim_basetemp(basetemp: Path) -> None:
    """Refuse a basetemp a live pytest already owns, then claim it.

    Called twice per run, which is the fix for stn-6fs and not belt-and-
    braces. See the two hooks below.
    """
    marker = basetemp / RUN_OWNER
    if marker.exists():
        pid = marker.read_text().strip().partition("-")[0]
        # A run that crashed leaves its marker behind, and refusing forever
        # afterwards would teach people to delete the guard rather than the
        # file. Only a LIVE owner blocks.
        #
        # And never our own: the second call below can find the marker the
        # first one wrote, and a run that refuses itself is a worse failure
        # than the one being guarded against.
        if pid.isdigit() and int(pid) != os.getpid() and _pid_alive(int(pid)):
            raise pytest.UsageError(
                f"--basetemp {basetemp} is in use by a running pytest "
                f"(pid {pid}). Two runs sharing one basetemp delete each "
                f"other's fixture trees at startup, and the failures do not "
                f"name the cause. Pass a different --basetemp."
            )
    basetemp.mkdir(parents=True, exist_ok=True)
    marker.write_text(_run_id())


XDIST_WORKER_ENV = (
    "PYTEST_XDIST_WORKER",
    "PYTEST_XDIST_WORKER_COUNT",
    "PYTEST_XDIST_TESTRUNUID",
)


def inner_pytest_env(**overrides) -> dict[str, str]:
    """The environment for a pytest this suite runs as a SUBPROCESS.

    Several tests answer questions that can only be answered from outside the
    process, by running an inner pytest. Those inner runs must not inherit
    this one's xdist identity: under `-n auto` every test executes inside a
    worker with `$PYTEST_XDIST_WORKER` set, a subprocess inherits the whole
    environment, and the inner run would then announce itself as somebody
    else's worker -- exempt itself from the basetemp guard above, and quietly
    stop testing the thing it was written to test.

    Found the first time the fast tier was run under `-n auto --dist
    loadfile`: four guard tests failed at once, all of them inner runs that
    had been handed a worker identity belonging to their own parent.
    """
    env = {k: v for k, v in os.environ.items() if k not in XDIST_WORKER_ENV}
    env.update(overrides)
    return env


def pytest_configure(config):
    if not os.environ.get(pipeline.BROWSER_IMAGE_TAG_ENV):
        # An explicit tag wins: a CI job that builds the image once and reuses
        # it across invocations should be able to say so.
        os.environ[pipeline.BROWSER_IMAGE_TAG_ENV] = (
            f"localhost/stencil_browser:run-{_run_id()}"
        )

    basetemp = config.getoption("basetemp")
    if not basetemp or _is_xdist_worker():
        # pytest's own default is already per-run.
        #
        # And a worker has nothing to claim. xdist invents a --basetemp for
        # every worker -- `<run-dir>/popen-gwN` -- even when the top-level run
        # passed none, so without this a plain `pytest -n auto` would write
        # owner markers where a plain `pytest` writes none. The question this
        # guard answers is whether a PERSON pointed two runs at one directory,
        # and that cannot be asked of a path xdist made up and nothing else can
        # be aimed at. Measured, in case the obvious reason is assumed instead:
        # a worker never sees its controller's marker, because the controller's
        # basetemp is the parent directory rather than the worker's own.
        return

    # The CHECK belongs here and nowhere later, because the thing that makes
    # a shared basetemp catastrophic is pytest's own rotation: the first call
    # to `TempPathFactory.getbasetemp()` rmtree()s the directory. A guard that
    # refused AFTER that point would have already destroyed the run it was
    # about to protect.
    _claim_basetemp(Path(basetemp))


def pytest_sessionstart(session):
    """Claim the basetemp again, on the far side of pytest's own rotation.

    stn-6fs: the claim in `pytest_configure` cannot survive. `getbasetemp()`
    rmtree()s the given basetemp on FIRST use and recreates it, and
    `pytest_configure` runs strictly before that -- so a run deleted its own
    marker the moment any test asked for `tmp_path`, and the window in which
    the guard could fire was milliseconds. It was dead rather than racy: two
    concurrent runs on one --basetemp both passed, with no marker on disk
    while the first was mid-test.

    Touching `getbasetemp()` here forces the rotation to happen now, while
    the run is still starting and nothing has been written that could be
    lost, so the marker written after it is the one a second run reads. The
    configure-time claim is kept because it closes the gap between the two:
    for those few milliseconds a marker that will shortly be deleted is
    still better than no marker at all.

    tests/test_tmp_footprint.py proves this with two real overlapping runs.
    The older hand-written-marker test cannot: it plants the marker in a
    directory no pytest rotates, so it passed throughout the years this guard
    did nothing.
    """
    basetemp = session.config.getoption("basetemp")
    if not basetemp or _is_xdist_worker():
        return

    session.config._tmp_path_factory.getbasetemp()
    _claim_basetemp(Path(basetemp))


def pytest_collection_modifyitems(config, items):
    """Skip the container-backed tests when there is nothing to run them in."""
    if pipeline.container_runtime() is not None:
        return
    skip = pytest.mark.skip(reason="no container runtime found (docker, podman)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


def make_package(base: Path, config: dict, package_id: str = "demo") -> Path:
    """Generate a package under ``base`` and return its output directory.

    Goes through the same generate_package the CLI calls, so a test asserting on
    a rendered Makefile is asserting on the file a real ``stencil gen`` writes.
    """
    config = copy.deepcopy(config)
    config.setdefault("output_dir", "out")
    (base / ".config.yaml").write_text(yaml.safe_dump(config))
    env = generate.build_environment(config, base)
    output_base = base / config["output_dir"]
    generate.generate_package(env, config, output_base, package_id)
    return output_base / package_id


def install_fixtures(package: Path) -> None:
    """Copy the fixture markdown and its assets into a generated package."""
    for item in FIXTURES.iterdir():
        dest = package / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)


@pytest.fixture
def demo_config():
    """A fresh copy of DEMO_CONFIG, for tests that need to vary it.

    A fixture rather than an import: nothing else in this suite imports from
    conftest, and `from tests.conftest import ...` resolves locally (the repo
    root is on sys.path) while failing on CI with ModuleNotFoundError. Handing
    it out as a fixture is both consistent and portable, and the deep copy
    stops one test's edits reaching another.
    """
    import copy

    return copy.deepcopy(DEMO_CONFIG)


@pytest.fixture
def install_sources():
    """``install_fixtures`` as a fixture, for a test that builds its own package.

    The ``render`` fixture below installs them on the way past, which serves
    every test that renders through it. A test driving the generated compose
    file needs the same markdown in a package it generated itself, and
    ``from tests.conftest import install_fixtures`` resolves locally while
    failing on CI with ModuleNotFoundError -- the same trap ``demo_config``
    documents. Handing the function out is the portable spelling.
    """
    return install_fixtures


@pytest.fixture
def generate_package(tmp_path: Path):
    def _generate(config: dict, package_id: str = "demo") -> Path:
        return make_package(tmp_path, config, package_id)

    return _generate


@pytest.fixture
def doc_package(generate_package):
    """A package with one document and one deck -- the common case under test."""
    return generate_package(DEMO_CONFIG)


@pytest.fixture
def render(doc_package):
    """Render markdown through the real pandoc container and return the result.

    A generated package already carries the html templates and the four lua
    filters, so it doubles as the working directory pandoc needs -- the same one
    the generated docker-compose.yml would mount. Returns the CompletedProcess
    alongside the output path rather than raising, because several tests here
    are about what a *failing* build does.
    """

    def _render(
        kind: str,
        source: str = "document.md",
        *,
        text: str | None = None,
        metadata: dict[str, str] | None = None,
        output: str | None = None,
    ):
        install_fixtures(doc_package)
        if text is not None:
            (doc_package / source).write_text(text)

        output = output or f"{Path(source).stem}.html"
        result = pipeline.render(
            kind, source, output, workdir=doc_package, metadata=metadata
        )
        return result, doc_package / output

    return _render


@pytest.fixture
def render_soup(render):
    """render(), asserting the build succeeded and handing back parsed HTML."""

    def _render_soup(kind: str, source: str = "document.md", **kwargs):
        result, path = render(kind, source, **kwargs)
        assert result.returncode == 0, (
            f"pandoc exited {result.returncode}\n{result.stderr}"
        )
        return BeautifulSoup(path.read_text(), "html.parser")

    return _render_soup


class BuildFailed(RuntimeError):
    """A build that failed, reported identically to everyone who waited on it."""


def build_once(shared_dir: Path, key: str, build) -> str:
    """Run `build` once across every process sharing `shared_dir`.

    The problem it solves: `pdf_workspace` below is session-scoped, and under
    xdist "session" means per WORKER PROCESS. Building the browser image
    installs Chromium, puppeteer and pa11y -- minutes -- so four workers would
    start four cold builds simultaneously, with no layer cache to share
    because none of them has finished yet. The image TAG is already common to
    the whole run, so the work is genuinely redundant rather than merely
    duplicated.

    A failure is recorded and re-raised to every caller, which is the half
    that matters. If only the worker that attempted the build learned it
    failed, the other three would carry on against an image that is not there
    and bury the real reason under a wall of container errors.
    """
    shared_dir = Path(shared_dir)
    shared_dir.mkdir(parents=True, exist_ok=True)
    sentinel = shared_dir / f".{key}.outcome"

    with FileLock(str(shared_dir / f".{key}.lock")):
        if not sentinel.exists():
            try:
                build()
            except Exception as exc:
                # Recorded before it is raised, so the callers still blocked on
                # the lock get the same answer rather than each retrying a
                # build that has already been shown not to work.
                sentinel.write_text(f"failed\n{exc}")
                raise BuildFailed(str(exc)) from exc
            sentinel.write_text("ok")
        recorded = sentinel.read_text()

    status, _, detail = recorded.partition("\n")
    if status != "ok":
        raise BuildFailed(detail)
    return status


def _shared_run_dir(tmp_path_factory) -> Path:
    """A directory every process in THIS run shares, and no other run does.

    In a worker that is `getbasetemp().parent`: worker basetemps are
    `<run-dir>/popen-gwN`, so the parent is the run-scoped directory the
    controller owns -- true both with an explicit `--basetemp` and with
    pytest's own default.

    In a serial run it is `getbasetemp()` itself, and the difference is not
    cosmetic. A serial run's parent is the system temp root
    (`/tmp/pytest-of-<user>`), shared by every run that user has ever made, so
    locking there would make one run's recorded build outcome answer for the
    next one's -- against an image tag that no longer exists.
    """
    basetemp = Path(tmp_path_factory.getbasetemp())
    return basetemp.parent if _is_xdist_worker() else basetemp


@pytest.fixture(scope="session")
def pdf_workspace(tmp_path_factory):
    """A generated package with the browser image built, shared session-wide.

    Building it installs Chromium, puppeteer and pa11y, which takes minutes.
    Once per session rather than once per test -- and, under xdist, once per
    RUN rather than once per worker.

    The workspace directory stays per-worker: `to_pdf` writes source files
    into it, and it costs 32ms to make. Only the image build is shared. The
    worker that wins the lock builds from its own copy, which is the same
    package every other worker generated from the same DEMO_CONFIG.
    """
    if pipeline.container_runtime() is None:
        pytest.skip("no container runtime found (docker, podman)")

    base = tmp_path_factory.mktemp("pdf")
    package = make_package(base, DEMO_CONFIG)
    install_fixtures(package)

    def _build():
        result = pipeline.build_browser_image(package)
        if result.returncode != 0:
            raise RuntimeError(
                f"could not build the browser image:\n{result.stderr[-3000:]}"
            )

    try:
        build_once(_shared_run_dir(tmp_path_factory), "browser-image", _build)
    except BuildFailed as exc:
        pytest.fail(str(exc))

    return package


@pytest.fixture(scope="session")
def to_pdf(pdf_workspace):
    """Render markdown to HTML, then convert it the way the pdf service does."""

    def _to_pdf(
        kind: str,
        source: str = "document.md",
        *,
        text: str | None = None,
        metadata: dict[str, str] | None = None,
        stem: str | None = None,
        timeout: float | None = None,
    ):
        if text is not None:
            (pdf_workspace / source).write_text(text)

        stem = stem or Path(source).stem
        built = pipeline.render(
            kind, source, f"{stem}.html", workdir=pdf_workspace, metadata=metadata
        )
        assert built.returncode == 0, f"pandoc failed\n{built.stderr}"

        # The generated pages used to fetch Bootstrap, highlight.js, Mermaid and
        # the webfonts from CDNs, and html-to-pdf.js correctly refuses to write a
        # PDF when any request fails. Those assets are now inlined, so a transient
        # network blip can no longer turn a geometry assertion into a flake.
        result = pipeline.html_to_pdf(
            f"{stem}.html", f"{stem}.pdf", workdir=pdf_workspace, timeout=timeout
        )

        return result, pdf_workspace / f"{stem}.pdf"

    return _to_pdf
