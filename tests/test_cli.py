"""What the CLI does with a config it cannot read.

Every one of these is a mistake made at the terminal rather than a bug in a
package, so the thing under test is the message: a traceback through yaml tells
the person nothing about which file stencil wanted or where to put it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from stencil import generate


REPO_ROOT = Path(generate.__file__).parent.parent


def run_cli(*args: str, cwd: Path, **popen) -> subprocess.CompletedProcess:
    """Invoke stencil the way a shell would, from ``cwd``.

    PYTHONPATH is the load-bearing part (stn-12v). Without it the subprocess
    imports whatever `pip install -e` put on the interpreter's path, which is
    the MAIN CHECKOUT -- so from a git worktree this file ran two different
    copies of stencil at once: the direct-import tests exercised the branch
    and every test in here exercised main, with nothing saying so.

    The dangerous direction is silent. A change that BREAKS the CLI passes in
    a worktree, because the subprocess never sees it. AGENTS.md tells every
    agent to work in a worktree, so that is the default arrangement rather
    than an unusual one -- and it was found the other way round, by a correct
    fix that appeared not to work.
    """
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    env.update(popen.pop("env_extra", {}))
    return subprocess.run(
        [sys.executable, "-m", "stencil.generate", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        **popen,
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
    package-scoped command sail past it while `--all` would have refused.

    stn-p9a NARROWS this for `clean` specifically, and only when the named
    package has its own manifest on disk: since neither package here was
    ever generated, `good` has no manifest, so this case still takes the
    config-derived path and the collective behaviour above still holds --
    that is exactly why this test still asserts non-zero for `clean good`.
    Once a manifest exists for the named package, clean no longer needs the
    sibling's config at all and succeeds despite it; see
    tests/test_manifest.py's
    test_clean_of_a_single_manifest_backed_package_succeeds_despite_a_broken_sibling
    for that case. `gen`'s collective behaviour is unchanged either way --
    gen still fails closed on any broken sibling, manifest or not."""
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


# --- findings from the adversarial review of the implementation -------------


def test_a_missing_packages_key_does_not_let_install_empty_the_section(tmp_path):
    """`package:` for `packages:` is a one-letter typo, and it used to be the
    worst case in the whole ticket: `install` returns before main's own
    `packages` guard, so a populated managed section was REPLACED with an
    empty one, "Updated" was printed, and the exit code was 0. Every generated
    file the section covered silently became committable."""
    write_config(tmp_path, {"templates": _TEMPLATES, "package": {"typo": {}}})

    gitignore = tmp_path / ".gitignore"
    original = (
        f"{generate.GITIGNORE_START}\n"
        "one/Makefile\n"
        f"{generate.GITIGNORE_END}\n"
    )
    gitignore.write_text(original)

    result = run_cli("install", cwd=tmp_path)

    assert result.returncode != 0, "a missing `packages:` key let install exit 0"
    assert "Traceback" not in result.stderr, result.stderr
    assert gitignore.read_text() == original, (
        "a populated managed section was replaced with an empty one: "
        f"{gitignore.read_text()!r}"
    )


def test_a_mistyped_package_beats_an_unrelated_broken_sibling(tmp_path):
    """Both messages are true; only one answers the question asked. Reporting
    the sibling's problem sends someone to fix a file they were not editing,
    and never tells them the name they typed does not exist."""
    write_config(tmp_path, GOOD_AND_BROKEN_CONFIG)

    result = run_cli("clean", "no-such-package", cwd=tmp_path)

    assert result.returncode != 0
    assert "Unknown package" in result.stderr, (
        f"a mistyped package id was answered with something else: {result.stderr!r}"
    )


# --- stn-w4v: a global option before the subcommand -------------------------


def _two_configs(tmp_path):
    """A default config and an alternative, each naming a different package."""
    (tmp_path / ".config.yaml").write_text(
        "packages:\n  demo:\n    name: Demo\n    package_type: none\n"
    )
    (tmp_path / "other.yaml").write_text(
        "packages:\n  other:\n    name: Other\n    package_type: none\n"
    )


def test_a_global_config_before_the_subcommand_is_honoured(tmp_path):
    """The BROKEN order was the DOCUMENTED one, which is what made it bite.

    ``--config`` and ``--dry-run`` are declared twice -- once on the top-level
    parser and again, via ``_add_global_opts``, on every subparser with the
    same defaults. argparse parses the main parser first and stores the real
    value, then parses the subparser, whose default for the same ``dest``
    overwrites it. So::

        stencil --config other.yaml list   # other.yaml silently ignored
        stencil list --config other.yaml   # works

    and generate.py's own module docstring gives the first spelling:
    ``stencil [--config <path>] gen [--all] [pkg]``. Anyone following the
    usage string read the wrong file and got a confident answer about it.
    """
    _two_configs(tmp_path)

    before = run_cli("--config", "other.yaml", "list", cwd=tmp_path)
    after = run_cli("list", "--config", "other.yaml", cwd=tmp_path)

    assert before.returncode == 0, before.stderr
    assert "other" in before.stdout, (
        "a --config before the subcommand was ignored; the default "
        f"overwrote it:\n{before.stdout}"
    )
    assert "demo" not in before.stdout
    assert before.stdout == after.stdout, (
        "the two spellings of the same option disagree:\n"
        f"before: {before.stdout}\nafter:  {after.stdout}"
    )


def test_a_global_dry_run_before_the_subcommand_is_honoured(tmp_path):
    """The same defect on the other shared option, and the one that would
    have been worse to discover: --dry-run silently NOT applying means the
    command someone ran to preview a change actually made it."""
    _two_configs(tmp_path)

    result = run_cli("--dry-run", "install", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".gitignore").exists(), (
        "--dry-run before the subcommand was ignored and install wrote the "
        ".gitignore anyway"
    )


# --- stn-zfc: a package that fails must fail the command --------------------


def _project(tmp_path, template_body="ok\n", packages=("one", "two")):
    """A config with a templates_dir the caller can put a broken template in."""
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "t.j2").write_text(template_body)
    body = "templates_dir: templates\ntemplates:\n  - src: t.j2\npackages:\n"
    for pid in packages:
        body += f"  {pid}:\n    name: {pid.title()}\n    package_type: none\n"
    (tmp_path / ".config.yaml").write_text(body)


def test_a_template_that_fails_to_render_fails_the_command(tmp_path):
    """StrictUndefined is deliberate -- AGENTS.md explains why a renamed
    context key must raise instead of rendering the empty string -- but
    nothing in main() caught it, so `gen` exited on a raw traceback and left
    a half-written package behind.

    The exit code is the part that matters here. A traceback at least tells a
    person something is wrong; a CI step reading `$?` is what this is for.
    """
    _project(tmp_path, template_body="VALUE = {{ pakage_stem }}\n")

    result = run_cli("gen", "--all", cwd=tmp_path)

    assert result.returncode != 0, (
        f"a template that could not render still exited 0:\n{result.stdout}"
    )
    assert "Traceback" not in result.stderr, (
        f"the failure is reported as a traceback:\n{result.stderr}"
    )
    assert "pakage_stem" in result.stderr, (
        f"the message does not name the undefined key:\n{result.stderr}"
    )


def test_the_failing_package_is_named(tmp_path):
    """`gen --all` over five packages that says only 'it failed' makes the
    reader generate them one at a time to find out which."""
    _project(tmp_path, template_body="VALUE = {{ pakage_stem }}\n")

    result = run_cli("gen", "--all", cwd=tmp_path)

    assert "one" in result.stderr, (
        f"the failing package is not named:\n{result.stderr}"
    )


def test_gen_all_reports_every_package_that_failed(tmp_path):
    """Same guarantee package_contexts gives for config errors: fixing one
    problem and rerunning to find the next is the thing being avoided."""
    _project(tmp_path, template_body="VALUE = {{ pakage_stem }}\n")

    result = run_cli("gen", "--all", cwd=tmp_path)

    for pid in ("one", "two"):
        assert pid in result.stderr, (
            f"{pid} is missing from the report:\n{result.stderr}"
        )


# --- stn-12v: the subprocess runs the code under test -----------------------


def test_the_cli_subprocess_imports_the_tree_under_test(tmp_path):
    """The guard for every other test in this file.

    `pip install -e` points at ONE checkout. Run from a git worktree, an
    unguarded subprocess imports that one -- so a test here can pass while the
    branch's own code is never executed, and a break in argument parsing ships
    green. Found the friendly way round while fixing stn-w4v: the fix was
    correct, verified against argparse in isolation, and the new tests kept
    failing because the subprocess was running main.

    Asserted by asking the subprocess where it imported stencil from, which is
    the only answer that cannot be faked by the parent process's own sys.path.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import stencil.generate as g; print(g.__file__)",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )

    assert result.returncode == 0, result.stderr
    imported = Path(result.stdout.strip()).resolve()
    expected = (REPO_ROOT / "stencil" / "generate.py").resolve()
    assert imported == expected, (
        f"the CLI subprocess imported {imported}, but this test run is "
        f"exercising {expected}. Every subprocess test in this file is "
        f"measuring the wrong copy of stencil."
    )
