"""What the CLI does with a config it cannot read.

Every one of these is a mistake made at the terminal rather than a bug in a
package, so the thing under test is the message: a traceback through yaml tells
the person nothing about which file stencil wanted or where to put it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from stencil import generate


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke stencil the way a shell would, from ``cwd``."""
    return subprocess.run(
        [sys.executable, "-m", "stencil.generate", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def test_a_missing_config_is_an_error_message_not_a_traceback(tmp_path):
    """The reported bug: any subcommand outside a project raised FileNotFoundError.

    Run as a subprocess rather than by calling main(), because a traceback is
    exactly what an uncaught exception looks like from out here and nothing
    else reproduces that.
    """
    result = run_cli("list", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert ".config.yaml" in result.stderr, (
        "the message has to name the file stencil looked for"
    )
    assert "--config" in result.stderr, (
        "and how to point it somewhere else, since the default is only a default"
    )


@pytest.mark.parametrize("command", ["list", "install", "gen", "clean"])
def test_every_subcommand_reports_the_missing_config(command, tmp_path):
    """load_config runs before the command dispatch, so none of them may leak one."""
    result = run_cli(command, "--all", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_malformed_yaml_names_the_file(tmp_path):
    config = tmp_path / ".config.yaml"
    config.write_text("packages:\n  demo: [unclosed\n")

    with pytest.raises(SystemExit) as exc:
        generate.load_config(config)

    assert str(config) in str(exc.value)


def test_an_empty_config_is_rejected_before_it_becomes_a_None(tmp_path):
    """yaml.safe_load returns None for an empty file, which fails later as a
    TypeError from `"packages" not in config` -- further from the cause."""
    config = tmp_path / ".config.yaml"
    config.write_text("")

    with pytest.raises(SystemExit) as exc:
        generate.load_config(config)

    assert "empty" in str(exc.value)


def test_a_scalar_config_is_rejected(tmp_path):
    """Valid YAML, wrong shape: config.get() would fail on a str."""
    config = tmp_path / ".config.yaml"
    config.write_text("just a string\n")

    with pytest.raises(SystemExit) as exc:
        generate.load_config(config)

    assert "mapping" in str(exc.value)


def test_a_config_that_loads_is_returned_unchanged(tmp_path):
    config = tmp_path / ".config.yaml"
    config.write_text("output_dir: out\npackages:\n  demo:\n    package_type: none\n")

    assert generate.load_config(config) == {
        "output_dir": "out",
        "packages": {"demo": {"package_type": "none"}},
    }


# --- stencil version -------------------------------------------------------


def test_version_reports_the_version(tmp_path):
    """Asked from anywhere, including outside a project.

    It is usually asked because something is wrong with the install -- four
    course venvs were found running three different stencils while a template
    fix appeared not to work -- so requiring a .config.yaml to answer it would
    withhold the answer exactly when it is wanted.
    """
    import stencil

    result = run_cli("version", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"stencil {stencil.__version__}"


def test_the_reported_version_is_the_installed_one():
    """pyproject reads __version__, so the module and the distribution agree by
    construction. When they do not, the install is stale -- reinstall it."""
    from importlib.metadata import version

    import stencil

    assert version("stencil") == stencil.__version__, (
        "installed distribution disagrees with the source; re-run pip install"
    )
