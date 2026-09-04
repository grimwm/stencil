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
import time
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

        # The generated pages fetch Bootstrap, highlight.js, Mermaid and the
        # webfonts from CDNs, and html-to-pdf.js correctly refuses to write a
        # PDF when any request fails -- which is the behaviour the last two
        # tests in test_pdf.py assert on. The cost is that a test about page
        # geometry fails whenever a request drops, and running containers in
        # quick succession provokes net::ERR_NETWORK_CHANGED often enough to
        # matter.
        #
        # So the tests that expect a PDF retry. The ones that expect a refusal
        # call pipeline.html_to_pdf directly and are not retried, so the
        # refusal is still tested exactly once, on the first attempt.
        for attempt in range(5):
            result = pipeline.html_to_pdf(
                f"{stem}.html", f"{stem}.pdf", workdir=pdf_workspace, timeout=timeout
            )
            if result.returncode == 0 or "assets failed to load" not in result.stderr:
                break
            time.sleep(3)

        return result, pdf_workspace / f"{stem}.pdf"

    return _to_pdf
