"""What the shared partials require of whoever includes them.

A consuming project may override stencil's composition templates while keeping
its partials: a consumer's Makefile.j2 can be its own file that `{% include %}`s
Makefile-base, Makefile-doc and Makefile-pkg. That is the search path working
as intended, and it means the partials have an interface -- the context keys
they read -- that stencil can change without anything failing on either side.
Rename one and the consumer renders a Makefile missing a recipe, discovered by
whoever next runs make.

Two things guard it. The table below is the contract, so a change to it is a
visible line in a diff rather than a surprise; and the context check underneath
fails if stencil stops providing a key some partial still reads.

The other half of the guard is StrictUndefined (see the end of this file):
an unknown variable raises at generation time instead of rendering the empty
string, so a consumer's stale composition fails loudly in its own build too.
"""

from __future__ import annotations

import pytest
from jinja2 import UndefinedError

from stencil.generate import (
    build_environment,
    get_template_context,
    referenced_variables,
)

# What each shared partial reads out of the context it is included into.
# Update deliberately: every entry here is something a consumer's overriding
# composition template must keep providing.
CONTRACT = {
    "Makefile-base.j2": set(),
    # `name` left this set when the course metadata flag did: the package name
    # was being injected as a document's course, which it never was -- see
    # AUTHORING.md. It is a --list label again, and no template reads it.
    "Makefile-doc.j2": {
        "docs",
        "has_docs",
        "has_pages",
        "has_slides",
        "pandoc_image",
        "slides",
    },
    "Makefile-pkg.j2": {
        "docs",
        "has_package_sources",
        "name",
        "package_name",
        "package_sources",
        "package_stem",
        "package_type",
        "pandoc_image",
        "slides",
    },
    # verapdf_image and verapdf_script arrived with check-pdf in 0.22.0, and
    # they are a real addition to the interface: a consumer whose own
    # docker-compose.yml.j2 includes this partial gets the new service for
    # free, but a consumer that copied the partial instead of including it
    # will not, and StrictUndefined is what tells them so.
    "docker-compose-html.yml.j2": {
        "has_slides",
        "package_id",
        "pandoc_argv_doc",
        "pandoc_argv_slide",
        "pandoc_image",
        "verapdf_image",
        "verapdf_script",
    },
    "_doc-body.html.j2": set(),
    "_page-head.html.j2": {"assets"},
    "_page-scripts.html.j2": {"assets"},
    "_page-style.css.j2": set(),
    "_slide-body.html.j2": set(),
    "_slide-scripts.html.j2": set(),
    "_slide-style.css.j2": set(),
}


@pytest.fixture
def env(tmp_path):
    return build_environment({}, tmp_path)


@pytest.mark.parametrize("partial", sorted(CONTRACT))
def test_a_partial_reads_only_what_the_contract_records(env, partial):
    """Fails when a partial starts requiring a key, or stops requiring one."""
    assert referenced_variables(env, partial) == CONTRACT[partial]


@pytest.mark.parametrize("partial", sorted(CONTRACT))
def test_stencil_still_provides_what_each_partial_reads(partial):
    """The other direction: a renamed context key must not leave a partial
    reading something nothing sets. A consumer would see it before we did."""
    context = get_template_context(
        "demo",
        {
            "packages": {
                "demo": {
                    "name": "Demo",
                    "package_type": "doc",
                    "package_name": "hs2.pdf",
                    "package_sources": ["md/*.md"],
                    "docs": ["README.md"],
                    "slides": ["Deck.md"],
                }
            }
        },
    )
    missing = CONTRACT[partial] - set(context)
    assert not missing, f"{partial} reads {sorted(missing)}, which no longer exists"


# --- the runtime half: an unknown variable is an error, not an empty string --


def test_an_unknown_variable_fails_the_build(generate_package, tmp_path):
    """Without this a stale composition renders a Makefile with a hole in it."""
    directory = tmp_path / "templates"
    directory.mkdir()
    (directory / "oops.j2").write_text("VALUE = {{ pakage_stem }}\n")
    with pytest.raises(UndefinedError, match="pakage_stem"):
        generate_package(
            {
                "templates_dir": "templates",
                "templates": [{"src": "oops.j2"}],
                "packages": {"demo": {"name": "Demo", "package_type": "none"}},
            }
        )


def test_a_template_env_key_left_unset_is_falsy_rather_than_undefined(
    generate_package, tmp_path
):
    """A consumer can set deps_script on one package and test it on all, so an
    unset custom key has to stay usable in a condition."""
    directory = tmp_path / "templates"
    directory.mkdir()
    (directory / "deps.j2").write_text("{% if deps_script %}yes{% else %}no{% endif %}\n")
    package = generate_package(
        {
            "templates_dir": "templates",
            "templates": [{"src": "deps.j2"}],
            "packages": {
                "with": {
                    "name": "With",
                    "package_type": "none",
                    "template_env": {"deps_script": ["x"]},
                },
                "without": {"name": "Without", "package_type": "none"},
            },
        },
        package_id="without",
    )
    assert (package / "deps").read_text().strip() == "no"


def test_a_config_level_default_is_overridden_per_package(generate_package, tmp_path):
    """The declaration supplies the value a package does not; a package that
    does set the key wins. This is what lets one shared template serve configs
    that define different subsets of the flags it reads."""
    directory = tmp_path / "templates"
    directory.mkdir()
    (directory / "flag.j2").write_text("{% if has_playwright %}on{% else %}off{% endif %}\n")
    config = {
        "templates_dir": "templates",
        "template_env": {"has_playwright": False},
        "templates": [{"src": "flag.j2"}],
        "packages": {
            "graded": {
                "name": "Graded",
                "package_type": "none",
                "template_env": {"has_playwright": True},
            },
            "plain": {"name": "Plain", "package_type": "none"},
        },
    }
    assert (generate_package(config, "graded") / "flag").read_text().strip() == "on"
    assert (generate_package(config, "plain") / "flag").read_text().strip() == "off"


def test_an_unset_key_still_triggers_the_default_filter(generate_package, tmp_path):
    """A key some package sets must stay *undefined* for the packages that do
    not, rather than becoming False. A template writing
    `{{ front_controller | default('index.html') }}`, and a concrete False
    satisfies the filter -- which put the literal "False" into generated
    TypeScript where a filename belonged."""
    directory = tmp_path / "templates"
    directory.mkdir()
    (directory / "entry.j2").write_text("{{ front_controller | default('index.html') }}\n")
    package = generate_package(
        {
            "templates_dir": "templates",
            "templates": [{"src": "entry.j2"}],
            "packages": {
                "custom": {
                    "name": "Custom",
                    "package_type": "none",
                    "template_env": {"front_controller": "app.php"},
                },
                "plain": {"name": "Plain", "package_type": "none"},
            },
        },
        package_id="plain",
    )
    assert (package / "entry").read_text().strip() == "index.html"


# ---------------------------------------------------------------------------
# stn-lcz: a custom key may not take a derived key's name.
#
# Raised by a review bot against verapdf_image/verapdf_script in 0.22.0 and
# true of every derived key: a package's template_env was merged with
# context.update, so it overwrote pandoc_image, package_type, docs and assets
# alike. The comment three lines above that merge claimed "setdefault
# throughout, so a config cannot shadow a derived key" -- true of the two
# passes above it, false of the line beneath it.
#
# Measured before changing the behaviour: across cs234 and cs425, 17 distinct
# template_env keys are in use and NONE collides with a derived key. So
# raising costs no existing config anything, and it is the same call
# StrictUndefined makes elsewhere -- fail at generation time rather than in
# whatever the template rendered.


def context_for(template_env: dict | None = None, config_env: dict | None = None):
    package = {"package_type": "doc", "docs": ["a.md"]}
    if template_env is not None:
        package["template_env"] = template_env
    config = {"packages": {"demo": package}}
    if config_env is not None:
        config["template_env"] = config_env
    return get_template_context("demo", config)


def test_a_custom_key_still_reaches_the_context():
    """The feature itself, so the guard cannot be mistaken for it working."""
    context = context_for({"front_controller": "index.php"})
    assert context["front_controller"] == "index.php"


def test_a_package_may_not_shadow_a_derived_key():
    with pytest.raises(ValueError) as caught:
        context_for({"pandoc_image": "somebody/else:latest"})
    assert "pandoc_image" in str(caught.value)


def test_a_config_may_not_shadow_a_derived_key_either():
    """Both levels, or the rule is a suggestion. This one already could not
    shadow -- setdefault silently dropped it -- so the change here is that it
    says so instead of ignoring you."""
    with pytest.raises(ValueError) as caught:
        context_for(config_env={"package_type": "zip"})
    assert "package_type" in str(caught.value)


def test_the_error_names_every_collision_not_just_the_first():
    """A config with two mistakes should need one round trip, not two."""
    with pytest.raises(ValueError) as caught:
        context_for({"pandoc_image": "x", "assets": "y", "harmless": "z"})
    message = str(caught.value)
    assert "assets" in message and "pandoc_image" in message
    assert "harmless" not in message


def test_a_package_value_still_beats_the_config_wide_default():
    """The behaviour the update() is FOR, which a careless fix would break by
    turning that line into a setdefault as well."""
    context = context_for(
        {"has_playwright": True}, config_env={"has_playwright": False}
    )
    assert context["has_playwright"] is True


def test_the_keys_the_real_consumers_use_are_all_still_accepted():
    """Measured from cs234 and cs425 rather than imagined. If a future derived
    key takes one of these names, this fails here instead of in a course
    repository the next time someone runs make gen."""
    in_use = {
        "assignment_id": "hs4",
        "assignment_name": "hs6",
        "assignment_title": "HS4",
        "assignment_total_points": 20,
        "base_database": "world",
        "content_index": "index.php",
        "db_dialect": "mysql",
        "deps_script": False,
        "docroot_subdir": "active",
        "front_controller": True,
        "has_db_grading": True,
        "has_install_scripts": True,
        "has_playwright": False,
        "has_vscode": True,
        "needs_world_database": False,
        "problems": 3,
        "testing": True,
    }
    context = context_for(in_use)
    for key, value in in_use.items():
        assert context[key] == value
