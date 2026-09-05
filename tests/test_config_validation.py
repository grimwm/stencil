"""A mistyped custom key must fail, not quietly render nothing.

`template_env` merges arbitrary keys into the top-level context and `when:`
tests them, which is how a consumer drives its own conditional templates. Both
halves used to be silent: an unknown key in `template_env` was accepted and
read by nothing, and a `when:` naming a key nobody set read as None, so the
template it guarded was skipped with no output and no error.

cs234 drives has_vscode, has_install_scripts and deps_script through this path,
so the shapes exercised here are the ones in real use -- in particular a key
set on one package and tested by a `when:` that every package is checked
against, which must stay legal.
"""

from __future__ import annotations

import pytest

from stencil.generate import build_environment, validate_config

BUNDLED = [{"src": "Makefile.j2"}, {"src": "docker-compose.yml.j2"}]


def package(**overrides):
    base = {"name": "Demo", "package_type": "none", "docs": ["README.md"]}
    base.update(overrides)
    return base


@pytest.fixture
def check(tmp_path):
    """Validate a config, optionally against local templates written on the fly."""

    def _check(config: dict, local: dict[str, str] | None = None):
        if local:
            directory = tmp_path / "templates"
            directory.mkdir(exist_ok=True)
            for name, body in local.items():
                (directory / name).write_text(body)
            config = {**config, "templates_dir": "templates"}
        validate_config(config, build_environment(config, tmp_path))

    return _check


# --- a when: that names a key nothing defines ------------------------------


def test_a_when_condition_naming_nothing_is_an_error(check):
    """Before this the guarded template was skipped for every package."""
    with pytest.raises(ValueError):
        check(
            {
                "templates": BUNDLED + [{"src": "Makefile.j2", "when": "has_vscde"}],
                "packages": {"demo": package(template_env={"has_vscode": True})},
            }
        )


def test_the_error_names_the_key_that_is_wrong(check):
    with pytest.raises(ValueError, match="has_vscde"):
        check(
            {
                "templates": BUNDLED + [{"src": "Makefile.j2", "when": "has_vscde"}],
                "packages": {"demo": package(template_env={"has_vscode": True})},
            }
        )


def test_a_derived_key_is_a_valid_when_condition(check):
    """has_web and friends are computed by stencil, not declared by anyone."""
    check(
        {
            "templates": BUNDLED + [{"src": "Makefile.j2", "when": "has_web"}],
            "packages": {"demo": package(services=["web"])},
        }
    )


def test_a_key_set_on_one_package_still_guards_every_package(check):
    """The cs234 shape: one package sets has_vscode, the rest do not, and the
    when: is checked against all of them. Legal, and must stay legal."""
    check(
        {
            "templates": BUNDLED + [{"src": "Makefile.j2", "when": "has_vscode"}],
            "packages": {
                "workspace": package(template_env={"has_vscode": True}),
                "plain": package(),
            },
        }
    )


# --- a template_env key that nothing reads ---------------------------------


def test_a_template_env_key_nothing_reads_is_an_error(check):
    with pytest.raises(ValueError, match="has_vscde"):
        check(
            {
                "templates": BUNDLED,
                "packages": {"demo": package(template_env={"has_vscde": True})},
            }
        )


def test_a_key_read_only_by_a_template_body_counts_as_read(check):
    """deps_script is never a when: condition -- cs234's Makefile.j2 tests it
    directly. Requiring a when: for every key would break that."""
    check(
        {
            "templates": BUNDLED + [{"src": "deps.j2"}],
            "packages": {"demo": package(template_env={"deps_script": ["x"]})},
        },
        local={"deps.j2": "{% if deps_script %}yes{% endif %}\n"},
    )


def test_a_key_read_only_through_an_include_counts_as_read(check):
    """cs234 overrides Makefile.j2 and composes stencil's partials into it, so
    a key can be referenced a level below the template the config names."""
    check(
        {
            "templates": BUNDLED + [{"src": "outer.j2"}],
            "packages": {"demo": package(template_env={"deps_script": ["x"]})},
        },
        local={
            "outer.j2": "{% include 'inner.j2' %}\n",
            "inner.j2": "{% if deps_script %}yes{% endif %}\n",
        },
    )


# --- config-level template_env: declaring a key without setting it ---------
#
# cs234 points three configs at one _generator/templates, and its shared
# Makefile.j2 reads has_playwright (set only by answers/.config.yaml) and
# deps_script (set only by assignments/.config.yaml). Each config therefore
# needs to say "my templates may read this key" without any package setting it.


def test_a_config_level_key_is_a_default_for_every_package(check):
    check(
        {
            "template_env": {"has_playwright": False},
            "templates": BUNDLED + [{"src": "Makefile.j2", "when": "has_playwright"}],
            "packages": {"demo": package()},
        }
    )


def test_a_config_level_key_read_only_by_a_template_body_counts_as_read(check):
    check(
        {
            "template_env": {"deps_script": None},
            "templates": BUNDLED + [{"src": "deps.j2"}],
            "packages": {"demo": package()},
        },
        local={"deps.j2": "{% if deps_script %}yes{% endif %}\n"},
    )


def test_a_config_level_key_nothing_reads_is_still_an_error(check):
    """Declaring it does not excuse it from having to do something."""
    with pytest.raises(ValueError, match="has_playwrigt"):
        check(
            {
                "template_env": {"has_playwrigt": False},
                "templates": BUNDLED,
                "packages": {"demo": package()},
            }
        )
