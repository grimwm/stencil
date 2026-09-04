"""Shared fixtures.

Two tiers of test live here. The argv and rendered-template assertions are
ordinary unit tests: they read stencil's own data structures and need nothing
installed. The build assertions need a container runtime, are marked
``integration``, and skip rather than fail when one is unavailable, so a
contributor without docker still gets a useful run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stencil import generate, pipeline


def pytest_collection_modifyitems(config, items):
    """Skip the container-backed tests when there is nothing to run them in."""
    if pipeline.container_runtime() is not None:
        return
    skip = pytest.mark.skip(reason="no container runtime found (docker, podman)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def generate_package(tmp_path: Path):
    """Generate a package from an inline config and return its output directory.

    Goes through the same generate_package the CLI calls, so a test asserting on
    a rendered Makefile is asserting on the file a real ``stencil gen`` writes.
    """

    def _generate(config: dict, package_id: str = "demo") -> Path:
        config.setdefault("output_dir", "out")
        (tmp_path / ".config.yaml").write_text(yaml.safe_dump(config))
        env = generate.build_environment(config, tmp_path)
        output_base = tmp_path / config["output_dir"]
        generate.generate_package(env, config, output_base, package_id)
        return output_base / package_id

    return _generate


@pytest.fixture
def doc_package(generate_package):
    """A package with one document and one deck -- the common case under test."""
    return generate_package(
        {
            "templates": [
                {"src": "Makefile.j2"},
                {"src": "docker-compose.yml.j2"},
            ],
            "packages": {
                "demo": {
                    "name": "Demo",
                    "package_type": "none",
                    "docs": ["Guide.md"],
                    "slides": ["Deck.md"],
                }
            },
        }
    )
