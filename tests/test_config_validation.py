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

import re

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


def test_a_dynamic_include_suspends_the_unread_key_check(check):
    """`{% include which %}` is opaque to static analysis -- Jinja reports the
    reference as None because the name is only known at render time. What that
    template reads cannot be known, so a key must not be rejected on the
    evidence that we did not see it. A missed typo is the better failure here;
    the alternative refuses a config that is perfectly correct."""
    check(
        {
            "templates": BUNDLED + [{"src": "dyn.j2"}],
            "packages": {
                "demo": package(template_env={"which": "chosen.j2", "unseen": True})
            },
        },
        local={
            "dyn.j2": "{% include which %}\n",
            "chosen.j2": "{% if unseen %}x{% endif %}\n",
        },
    )


def test_a_key_read_through_the_nested_template_env_dict_counts_as_read(check):
    """cs234's nginx.conf.j2 writes
    `(template_env | default({})).get('docroot_subdir')`. The key is a dict
    lookup, not a variable, so no analysis of variable names can see it. What
    this check can soundly reject is a name that appears nowhere in any
    template in any form, which is what a typo looks like."""
    check(
        {
            "templates": BUNDLED + [{"src": "nginx.j2"}],
            "packages": {"demo": package(template_env={"docroot_subdir": "htdocs"})},
        },
        local={
            "nginx.j2": "{% if (template_env | default({})).get('docroot_subdir') %}x{% endif %}\n"
        },
    )


# --- the config-wide language default --------------------------------------
#
# Resolved at generation time rather than render time: the value is baked into
# the pandoc template as the `$else$` branch of its lang attribute, so these
# assert on the generated template rather than on a rendered page. The front
# matter half of the precedence is in tests/test_frontmatter.py, which needs a
# real pandoc to exercise the `$if$`.

LANG_CONFIG = {
    "templates": [{"src": "html-template.html.j2"}],
    "packages": {"demo": {"name": "Demo", "package_type": "none", "docs": ["a.md"]}},
}


def generated_lang_default(generate_package, config) -> str:
    """The language the template falls back to when front matter names none."""
    text = (generate_package(config) / "html-template.html").read_text()
    match = re.search(r'<html lang="\$if\(lang\)\$\$lang\$\$else\$([^"$]*)\$', text)
    assert match, "the lang attribute is not in the shape these tests assume"
    return match.group(1)


def test_english_when_nothing_says_otherwise(generate_package):
    assert generated_lang_default(generate_package, LANG_CONFIG) == "en"


def test_a_config_wide_lang_becomes_the_default(generate_package):
    config = {**LANG_CONFIG, "lang": "es"}
    assert generated_lang_default(generate_package, config) == "es"


def test_a_package_lang_outranks_the_config_wide_one(generate_package):
    """One section taught in another language, without moving every other
    package off the shared default."""
    config = {
        **LANG_CONFIG,
        "lang": "es",
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["a.md"],
                "lang": "fr",
            }
        },
    }
    assert generated_lang_default(generate_package, config) == "fr"


def test_a_package_dir_is_not_read_as_a_text_direction(generate_package):
    """`dir:` on a package is its output subdirectory and has been for the life
    of the tool. It must not leak into the html tag as a direction."""
    config = {
        **LANG_CONFIG,
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "none",
                "docs": ["a.md"],
                "dir": "rtl",
            }
        },
    }
    # make_package returns out/<package_id> and does not follow `dir:`, so
    # find the template wherever generation actually put it.
    pkg = generate_package(config)
    generated = list(pkg.parent.rglob("html-template.html"))
    assert len(generated) == 1, f"expected one generated template, got {generated}"
    text = generated[0].read_text()

    assert 'dir="rtl"' not in text, "a package's output dir leaked into <html dir>"
    assert generated[0].parent.name == "rtl", "`dir:` should still pick the folder"
