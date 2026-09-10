"""Shared fixtures.

Two tiers of test live here. The argv and rendered-template assertions are
ordinary unit tests: they read stencil's own data structures and need nothing
installed. The build assertions need a container runtime, are marked
``integration``, and skip rather than fail when one is unavailable, so a
contributor without docker still gets a useful run.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
import yaml
from bs4 import BeautifulSoup

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


@pytest.fixture(scope="session")
def pdf_workspace(tmp_path_factory):
    """A generated package with the browser image built, shared session-wide.

    Building it installs Chromium, puppeteer and pa11y, which takes minutes.
    Once per session rather than once per test.
    """
    if pipeline.container_runtime() is None:
        pytest.skip("no container runtime found (docker, podman)")

    base = tmp_path_factory.mktemp("pdf")
    package = make_package(base, DEMO_CONFIG)
    install_fixtures(package)

    result = pipeline.build_browser_image(package)
    if result.returncode != 0:
        pytest.fail(f"could not build the browser image:\n{result.stderr[-3000:]}")

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
