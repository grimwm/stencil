"""What `stencil show` prints.

`show` is the inspection counterpart to `list`: where `list` prints one
fixed line per package, `show` prints configuration -- everything by
default, one dotted path per package with `-k`, one package with `-p` --
so a wrapper like a config-level `make pkg` can read `dir` without parsing
YAML itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from stencil import generate


REPO_ROOT = Path(generate.__file__).parent.parent


def run_cli(*args: str, cwd: Path, **popen) -> subprocess.CompletedProcess:
    """Invoke stencil the way a shell would, from ``cwd``.

    PYTHONPATH pins the subprocess to the tree under test, the way
    tests/test_cli.py's helper does: without it the subprocess imports
    whatever `pip install -e .` registered, which may be another checkout.
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


SHOW_CONFIG = {
    "templates": [{"src": "Makefile.j2"}],
    "packages": {
        "one": {
            "name": "One",
            "dir": "alpha",
            "package_type": "zip",
            "package_name": "one.zip",
            "services": ["web", "mysql"],
            "template_env": {"has_vscode": True},
        },
        "two": {
            "name": "Two",
            "package_type": "none",
            "services": ["web"],
        },
    },
}


def write_config(base: Path, config: dict) -> Path:
    path = base / ".config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def test_show_prints_every_packages_full_details(tmp_path):
    write_config(tmp_path, SHOW_CONFIG)

    result = run_cli("show", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    shown = yaml.safe_load(result.stdout)
    assert set(shown) == {"one", "two"}
    assert shown["one"]["dir"] == "alpha"
    assert shown["one"]["template_env"] == {"has_vscode": True}
    assert shown["two"]["services"] == ["web"]


def test_show_key_dir_lists_effective_directories(tmp_path):
    """`two` sets no `dir`, so it reports its package ID -- the directory
    gen, clean and list resolve for it."""
    write_config(tmp_path, SHOW_CONFIG)

    result = run_cli("show", "--key", "dir", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["one: alpha", "two: two"]


def test_show_key_short_flag_matches_long_flag(tmp_path):
    write_config(tmp_path, SHOW_CONFIG)

    short = run_cli("show", "-k", "package_name", cwd=tmp_path)
    long = run_cli("show", "--key", "package_name", cwd=tmp_path)

    assert short.returncode == 0, short.stderr
    assert long.stdout == short.stdout
    assert short.stdout.splitlines() == ["one: one.zip", "two: "]


def test_show_key_reads_nested_paths_and_list_indices(tmp_path):
    write_config(tmp_path, SHOW_CONFIG)

    nested = run_cli("show", "-k", "template_env.has_vscode", cwd=tmp_path)
    indexed = run_cli("show", "-k", "services.0", cwd=tmp_path)

    assert nested.returncode == 0, nested.stderr
    assert nested.stdout.splitlines() == ["one: true", "two: "]
    assert indexed.returncode == 0, indexed.stderr
    assert indexed.stdout.splitlines() == ["one: web", "two: web"]


def test_show_project_selects_one_package(tmp_path):
    write_config(tmp_path, SHOW_CONFIG)

    result = run_cli("show", "--project", "one", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    shown = yaml.safe_load(result.stdout)
    assert shown["dir"] == "alpha"
    assert "two" not in result.stdout


def test_show_project_and_key_print_one_line(tmp_path):
    write_config(tmp_path, SHOW_CONFIG)

    result = run_cli("show", "-p", "one", "-k", "dir", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["one: alpha"]


def test_show_unknown_project_fails_naming_it(tmp_path):
    write_config(tmp_path, SHOW_CONFIG)

    result = run_cli("show", "-p", "no-such-package", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "Unknown package no-such-package" in result.stderr


def test_show_missing_key_prints_empty_at_exit_zero(tmp_path):
    """A key no package holds is visibly all-empty, not silently short and
    not an error: the line still names every package."""
    write_config(tmp_path, SHOW_CONFIG)

    result = run_cli("show", "-k", "no.such.key", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["one: ", "two: "]


def test_show_still_reads_a_config_that_gen_refuses(tmp_path):
    """`show` is read-only like `list`: it inspects the config rather than
    validating it, so it answers on exactly the config `gen` fails closed
    on -- quoted `show_download: \"no\"` reads as the string "no"."""
    write_config(
        tmp_path,
        {
            "templates": [{"src": "Makefile.j2"}],
            "packages": {
                "good": {"package_type": "none"},
                "broken": {"package_type": "none", "show_download": "no"},
            },
        },
    )

    result = run_cli("show", "-k", "show_download", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    # safe_dump sorts mapping keys when writing the fixture, so `broken`
    # prints first; config order is preserved from a real hand-written file.
    assert result.stdout.splitlines() == ["broken: no", "good: "]


def test_show_reports_a_missing_config_without_a_traceback(tmp_path):
    result = run_cli("show", cwd=tmp_path)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert ".config.yaml" in result.stderr


def test_lookup_package_key_dir_defaults_to_the_package_id():
    assert generate.lookup_package_key("two", {"package_type": "none"}, "dir") == "two"
    assert generate.lookup_package_key("one", {"dir": "alpha"}, "dir") == "alpha"


def test_lookup_package_key_missing_paths_are_missing():
    package = {"services": ["web"]}
    lookup = generate.lookup_package_key
    assert lookup("p", package, "absent") is generate._KEY_MISSING
    assert lookup("p", package, "services.5") is generate._KEY_MISSING
    assert lookup("p", package, "services.name") is generate._KEY_MISSING
    assert lookup("p", package, "services.0.x") is generate._KEY_MISSING


def test_format_key_value_renders_scalars_plainly():
    assert generate.format_key_value(True) == "true"
    assert generate.format_key_value(False) == "false"
    assert generate.format_key_value(3) == "3"
    assert generate.format_key_value(generate._KEY_MISSING) == ""
    assert generate.format_key_value(None) == ""


def test_format_key_value_escapes_terminal_controls_without_truncation():
    value = "a\x1bb\x07c\x00d\ne\rf\tf" + "x" * 200
    text = generate.format_key_value(value)

    for raw in ("\x1b", "\x07", "\x00", "\n", "\r", "\t"):
        assert raw not in text
    assert "x" * 200 in text
    assert text.startswith("a\\x1bb\\x07c\\x00d\\ne\\rf\\tf")
    assert generate.format_key_value("alpha") == "alpha"


def test_show_key_escapes_controls_in_package_ids_and_values(capsys):
    config = {"packages": {"pkg\x1b]0;pwned\x07": {"dir": "v\x1bal"}}}

    generate.show_packages(config, key="dir")

    (line,) = capsys.readouterr().out.splitlines()
    assert "\x1b" not in line and "\x07" not in line
    assert line == "pkg\\x1b]0;pwned\\x07: v\\x1bal"
