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
import yaml

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


# --- a broken config fails closed (stn-ox1 / stn-k73) -----------------------
#
# `show_download: "no"` is quoted, so YAML 1.1 reads it as the STRING "no",
# not the boolean False -- and show_download_default() raises ValueError on
# exactly that. Today two call sites swallow that ValueError and `continue`,
# so `install` drops the package from the managed .gitignore section without
# a word, `clean --all` cleans only the good packages, and `gen --all`
# prints the error to stderr and still exits 0. Every case below is about
# that fail-open becoming fail-closed: non-zero, no traceback, and a message
# that names every broken package -- see stn-ox1's design decision.

_TEMPLATES = [{"src": "Makefile.j2"}, {"src": "docker-compose.yml.j2"}]

GOOD_AND_BROKEN_CONFIG = {
    "templates": _TEMPLATES,
    "packages": {
        "good": {"package_type": "none"},
        "broken": {"package_type": "none", "show_download": "no"},
    },
}


def write_config(base: Path, config: dict) -> Path:
    path = base / ".config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


@pytest.mark.parametrize(
    "args",
    [("install",), ("clean", "--all"), ("gen", "--all")],
    ids=["install", "clean --all", "gen --all"],
)
def test_a_broken_package_fails_closed_instead_of_silently_dropping(tmp_path, args):
    """The headline bug: none of these three used to say anything was wrong."""
    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli(*args, cwd=tmp_path)

    assert result.returncode != 0, (
        f"{' '.join(args)} exited 0 with `broken` silently dropped"
    )
    assert "Traceback" not in result.stderr, result.stderr
    assert "broken" in result.stderr, (
        f"the message does not name the broken package: {result.stderr!r}"
    )


@pytest.mark.parametrize(
    "args",
    [("install", "--dry-run"), ("clean", "--all", "--dry-run")],
    ids=["install --dry-run", "clean --all --dry-run"],
)
def test_dry_run_also_fails_closed(tmp_path, args):
    """--dry-run previews a write; it must not preview past a broken config."""
    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli(*args, cwd=tmp_path)

    assert result.returncode != 0, f"{' '.join(args)} exited 0"
    assert "Traceback" not in result.stderr, result.stderr


@pytest.mark.parametrize(
    "args",
    [("gen", "good"), ("clean", "good")],
    ids=["gen good", "clean good"],
)
def test_a_single_good_package_still_fails_when_a_sibling_is_broken(tmp_path, args):
    """Decision d-adf7c52b: deliberate collective behaviour, pinned so the
    next contributor does not "fix" it. A package-scoped command still reads
    every package's context, not only the one named -- the aggregated
    pre-flight has no narrower mode, and a broken sibling should not let a
    package-scoped command sail past it while `--all` would have refused."""
    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli(*args, cwd=tmp_path)

    assert result.returncode != 0, (
        f"{' '.join(args)} exited 0 while `broken` was still broken"
    )
    assert "Traceback" not in result.stderr, result.stderr


def test_clean_unknown_package_still_reports_with_no_broken_packages(tmp_path):
    """The pre-existing behaviour (generate.py's own sys.exit(1) around line
    1094) must survive the reorder that makes the pre-flight run first."""
    config = {
        "templates": _TEMPLATES,
        "packages": {"good": {"package_type": "none"}},
    }
    write_config(tmp_path, config)

    result = run_cli("clean", "no-such-package", cwd=tmp_path)

    assert result.returncode != 0
    assert "Unknown package" in result.stderr


def test_install_leaves_no_gitignore_section_when_a_package_is_broken(tmp_path):
    """The create path: no .gitignore existed yet. An abort must not create
    one carrying only the good packages -- that reads as complete and is not."""
    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli("install", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr

    gitignore = tmp_path / ".gitignore"
    if gitignore.exists():
        assert generate.GITIGNORE_START not in gitignore.read_text(), (
            "a broken config wrote a partial managed section instead of none"
        )


def test_install_leaves_an_existing_gitignore_section_unchanged_on_abort(tmp_path):
    """The update path is a DIFFERENT case from the create path above: here a
    managed section already exists, and an abort must leave it exactly as it
    was rather than replacing it with a truncated one."""
    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    gitignore = tmp_path / ".gitignore"
    original = (
        "node_modules/\n"
        "\n"
        f"{generate.GITIGNORE_START}\n"
        "good/Makefile\n"
        "good/docker-compose.yml\n"
        f"{generate.GITIGNORE_END}\n"
    )
    gitignore.write_text(original)

    result = run_cli("install", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert gitignore.read_text() == original, (
        "an aborted install must not touch an existing managed section, even "
        "to replace it with an incomplete one"
    )


def test_a_dangling_brand_leaves_no_output_directory_behind(tmp_path):
    """Objective 3's real test. A brand pointing at a file that does not
    exist must be caught before `output_dir.mkdir` -- not after the package
    is half-generated, which is what raised a bare traceback today."""
    config = {
        "output_dir": "out",
        "templates": _TEMPLATES,
        "brand": "file://img/absent.svg",
        "brand-alt": "Some Institution",
        "packages": {"demo": {"package_type": "none", "docs": ["a.md"]}},
    }
    write_config(tmp_path, config)

    result = run_cli("gen", "--all", cwd=tmp_path)

    assert result.returncode != 0, "a dangling brand file let gen --all exit 0"
    assert "Traceback" not in result.stderr, result.stderr
    assert not (tmp_path / "out" / "demo").exists(), (
        "the package directory exists, so it was created before the brand "
        "was checked -- the pre-flight ran too late, or not at all"
    )


def test_a_non_iterable_docs_value_fails_closed_without_a_traceback(tmp_path):
    """`docs: 7` raises TypeError inside get_template_context (iterating an
    int), not ValueError. A fix that only catches ValueError still tracebacks
    on this -- which is the point of testing it separately from show_download."""
    config = {
        "templates": _TEMPLATES,
        "packages": {"demo": {"package_type": "none", "docs": 7}},
    }
    write_config(tmp_path, config)

    result = run_cli("gen", "--all", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr, result.stderr


def test_a_list_valued_packages_key_fails_closed_without_a_traceback(tmp_path):
    """`packages:` is supposed to be a mapping. A list raises AttributeError
    the first time anything calls .get() on it -- again not a ValueError."""
    config = {
        "templates": _TEMPLATES,
        "packages": ["demo"],
    }
    write_config(tmp_path, config)

    result = run_cli("gen", "--all", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr, result.stderr


def test_a_string_valued_package_fails_closed_without_a_traceback(tmp_path):
    """A package that is a bare string instead of a mapping raises
    AttributeError on the first .get() stencil calls on it."""
    config = {
        "templates": _TEMPLATES,
        "packages": {"demo": "oops"},
    }
    write_config(tmp_path, config)

    result = run_cli("gen", "--all", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr, result.stderr


def test_a_control_character_in_a_package_id_does_not_reach_the_terminal_raw(
    tmp_path,
):
    """The aggregation guarantee is about what the reader SEES, not just the
    Python string: `\\x1b[1A` is a real cursor-up escape, and a terminal that
    receives it raw could overwrite or hide the very line reporting the
    problem. Built with yaml.safe_dump so the escaping in the config file
    itself is real, not a Python string literal stencil never has to parse."""
    config = {
        "templates": _TEMPLATES,
        "packages": {
            "good": {"package_type": "none"},
            "bad\x1b[1Aid": {"package_type": "none", "show_download": "no"},
        },
    }
    write_config(tmp_path, config)

    result = run_cli("gen", "--all", cwd=tmp_path)

    assert result.returncode != 0
    assert "\x1b" not in result.stderr, (
        f"a raw escape sequence reached the terminal: {result.stderr!r}"
    )


def test_one_config_level_typo_produces_one_message_not_one_per_package(tmp_path):
    """A single top-level `show_download` typo is read by every package's
    context, so a naive aggregation would repeat the identical complaint once
    per package. This is the dedupe guard: one broken CONFIG produces one
    line, however many packages read it."""
    config = {
        "show_download": "no",
        "templates": _TEMPLATES,
        "packages": {
            "a": {"package_type": "none"},
            "b": {"package_type": "none"},
            "c": {"package_type": "none"},
        },
    }
    write_config(tmp_path, config)

    result = run_cli("gen", "--all", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr, result.stderr
    count = result.stderr.count("show_download must be true or false")
    assert count == 1, (
        f"one config-level typo produced {count} copies of the same "
        f"complaint instead of one: {result.stderr!r}"
    )


# --- the other direction: a VALID config must still sail through ------------
#
# Every test above asserts a non-zero exit, so a pre-flight that rejected
# EVERYTHING would satisfy all of them. These are the guards that make the
# suite two-sided: the gate has to open as reliably as it closes, and until
# now nothing exercised install/gen/clean at the CLI level on a config that
# is simply fine.

VALID_CONFIG = {
    "output_dir": "out",
    "templates": _TEMPLATES,
    "packages": {
        "one": {"package_type": "none", "docs": ["a.md"]},
        "two": {"package_type": "none"},
    },
}


@pytest.mark.parametrize(
    "args",
    [("install",), ("gen", "--all"), ("clean", "--all"), ("gen", "one")],
    ids=["install", "gen --all", "clean --all", "gen one"],
)
def test_a_valid_config_still_succeeds(tmp_path, args):
    """The pre-flight is a gate, and a gate that never opens is not a gate."""
    write_config(tmp_path, VALID_CONFIG)

    result = run_cli(*args, cwd=tmp_path)

    assert result.returncode == 0, (
        f"{' '.join(args)} rejected a perfectly valid config: {result.stderr!r}"
    )
    assert "Traceback" not in result.stderr, result.stderr


def test_a_valid_config_installs_a_section_naming_every_package(tmp_path):
    """The failure this pairs with is subtle: a pre-flight that quietly
    dropped a package rather than raising would leave the managed section
    short again -- the original bug, reintroduced behind a gate that reports
    success. Both packages have to be in there."""
    write_config(tmp_path, VALID_CONFIG)

    result = run_cli("install", cwd=tmp_path)
    assert result.returncode == 0, result.stderr

    section = (tmp_path / ".gitignore").read_text()
    assert generate.GITIGNORE_START in section
    assert "one/Makefile" in section, f"package `one` is missing: {section!r}"
    assert "two/Makefile" in section, f"package `two` is missing: {section!r}"


def test_a_valid_config_with_a_real_brand_file_still_generates(tmp_path):
    """The brand pre-flight's own happy path. A config-level image brand with
    an alt AND a file that exists must pass -- otherwise the gen-only
    existence check would be indistinguishable from one that always fails."""
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "logo.svg").write_text("<svg/>")
    config = {
        "output_dir": "out",
        "templates": _TEMPLATES,
        "brand": "file://img/logo.svg",
        "brand-alt": "Some Institution",
        "packages": {"demo": {"package_type": "none", "docs": ["a.md"]}},
    }
    write_config(tmp_path, config)

    result = run_cli("gen", "--all", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "demo" / "logo.svg").is_file(), (
        "the brand image was not copied into the generated package"
    )
