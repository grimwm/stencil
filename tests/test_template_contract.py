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

import re

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
    # pre_build/has_pre_build arrived with stn-gln. A consumer whose own
    # Makefile.j2 includes this partial gets the hook rules for free; one that
    # copied the partial does not, and StrictUndefined is what says so.
    # node_image arrived with stn-s5b. format-md's ensure_image line used to
    # name docker.io/library/node:lts-alpine literally, in one of three files
    # that reach for the same image; a consumer whose own Makefile.j2 includes
    # this partial now pre-pulls the pinned one, and one that copied the partial
    # goes on pulling `lts` and running the pin -- which reads as a slow first
    # build rather than as a defect. StrictUndefined is what says so.
    "Makefile-doc.j2": {
        "docs",
        "has_docs",
        "has_package_output_dir",
        "has_pages",
        "has_pre_build",
        "has_slides",
        "node_image",
        "package_output_dir",
        "pandoc_image",
        "pre_build",
        "slides",
    },
    # OUT_HOST is a make variable defined in Makefile-doc.j2, not a context
    # key, so clean-pkg reads nothing new from the context.
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
    # node_image and format_npm_specs arrived with stn-s5b, and are the same
    # kind of addition: the format-md service used to name an unpinned image and
    # install two unpinned packages, both spelled out here. A consumer including
    # this partial gets the pins; one that copied it keeps a service that
    # reformats every markdown file in the package with whatever prettier npm
    # served that morning.
    #
    # stn-5hv REPLACED format_npm_specs with three keys, and that is a breaking
    # change to this interface rather than a rename. The service no longer
    # installs from a list of names at all: it writes format_manifest as a
    # package.json, copies the lockfile stencil generated beside the compose
    # file, and runs `npm ci`. A consumer whose own composition template
    # includes this partial gets all of that; one that COPIED the partial keeps
    # a service that installs by name -- which still works, and still leaves
    # prettier's own dependencies re-resolving on every run. StrictUndefined
    # cannot tell them so, because a copy reads none of these keys. Say it in
    # the release notes instead.
    #
    # format_lockfile_digest arrived with stn-qge, and is an addition of the
    # same kind with a security consequence rather than a pinning one. The
    # service reads its lockfile out of the mount and `npm ci` fetches whatever
    # host each `resolved` names, so the entrypoint now refuses a lockfile that
    # is not the one stencil wrote. A consumer whose composition INCLUDES this
    # partial gets that refusal; one that COPIED the partial keeps a service
    # that installs from whatever lockfile is in the package directory.
    # StrictUndefined cannot tell them so, for the reason above: a copy reads
    # none of these keys.
    "docker-compose-html.yml.j2": {
        # Added deliberately by stn-jeq/stn-7ki, and it is a real new
        # requirement on a consumer's composition rather than churn. The pdf
        # service's entrypoint is now an absolute path INSIDE the image --
        # `node {{ browser_tools_dir }}/html-to-pdf.js` -- because Node decides
        # CommonJS-vs-ESM from the nearest package.json to the file and, run
        # from the mount, that was the consumer's own. A composition that
        # provides every other key here and not this one renders a pdf service
        # that cannot start.
        "browser_tools_dir",
        "check_access_script",
        "format_lockfile_digest",
        "format_lockfile_name",
        "format_manifest",
        "format_tools_dir",
        "has_slides",
        "node_image",
        "package_id",
        "pandoc_argv_doc",
        "pandoc_argv_slide",
        "pandoc_image",
        "has_package_output_dir",
        "package_output_dir",
        "verapdf_image",
        "verapdf_script",
    },
    "_doc-body.html.j2": set(),
    # The self-download control reads nothing from the Jinja context --
    # everything it needs comes from the DOM at runtime, not from a variable
    # this partial interpolates -- so a consumer including it gets the
    # control for free. Registered anyway, at an empty key set, so a future
    # context key added here becomes a visible line in this table's diff
    # rather than a silent one. (_theme-toggle.html.j2 is the same shape and
    # is not in this table; that omission is pre-existing and out of scope.)
    "_download.html.j2": set(),
    "_page-head.html.j2": {"assets"},
    "_page-scripts.html.j2": {"assets"},
    "_page-style.css.j2": set(),
    "_slide-body.html.j2": set(),
    "_slide-scripts.html.j2": set(),
    "_slide-style.css.j2": set(),
}


# What each Makefile partial requires its INCLUDER to have already defined,
# as a plain make variable -- not a Jinja context key, so CONTRACT above and
# StrictUndefined cannot see it. This is how STENCIL_COMPOSE could have been
# added to Makefile-doc.j2 and Makefile-pkg.j2 invisibly (stn-qli): both used
# to read $(DC) the same way, undeclared anywhere.
#
# THE CEILING: this table documents the requirement, it does not detect it.
# A consumer who COPIED a partial's text into their own Makefile.j2 instead of
# `{% include %}`-ing it still reads $(STENCIL_COMPOSE) -- that copy is
# unpinned forever, because StrictUndefined guards Jinja context keys, and a
# copied partial has none left to guard. Nothing below closes that gap;
# claiming otherwise would be worse than leaving it open.
MAKE_CONTRACT = {
    "Makefile-base.j2": set(),
    "Makefile-doc.j2": {
        # STENCIL_COMPOSE and ensure_image are Makefile-base.j2's: format-md
        # (and, when has_pages, doc/slide/pdf/check-access/check-pdf) call
        # both. A composition that includes this partial without
        # Makefile-base.j2 first gets Makefile-doc.j2's own $(error ...)
        # guard rather than "run: No such file or directory" -- but only
        # because that guard exists; nothing about StrictUndefined catches a
        # missing MAKE variable the way it catches a missing context key.
        "STENCIL_COMPOSE",
        "ensure_image",
        # `with` is never defined by any bundled partial -- it is the
        # command-line variable a user sets with `make with=hidden`, read
        # once into WITH ?= $(with). Recorded here with this comment rather
        # than special-cased out of the extraction, so a future rename of the
        # user-facing spelling is a visible diff line too.
        "with",
    },
    "Makefile-pkg.j2": {
        "STENCIL_COMPOSE",
        "ensure_image",
        # METADATA_FLAGS, OUTPUT_SUFFIX and OUT_HOST are Makefile-doc.j2's:
        # the has_package_sources arm's `pkg` recipe reads the first two, and
        # clean-pkg's product list reads OUT_HOST whenever docs or slides is
        # non-empty. A composition that includes Makefile-pkg.j2 without
        # Makefile-doc.j2 renders a `pkg`/`clean-pkg` recipe referencing
        # variables nothing defined -- make treats an undefined variable as
        # empty rather than failing, so this is silent breakage, not a make
        # error, which is exactly what this table exists to make visible
        # instead.
        "METADATA_FLAGS",
        "OUTPUT_SUFFIX",
        "OUT_HOST",
    },
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


# ---------------------------------------------------------------------------
# A partial's output ends with a newline, because a consumer writes the next
# line.
#
# cs234's own Makefile.j2 does `{% include 'Makefile-pkg.j2' %}` and follows it
# with `pkg: fix lint lint-sql`, the rule that makes `make pkg` lint before it
# archives. Makefile-pkg.j2 ended in the clean-pkg recipe, whose last token was
# a `{% endfor %}` -- and the environment's trim_blocks removes the newline
# after a block tag, so the include ended mid-line and the consumer's rule was
# rendered as the tail of `rm -f ...`. Every one of the course's 15 assignment
# Makefiles carried `... -*.pdf pkg: fix lint lint-sql` on the rm line, and
# `make pkg` linted nothing. The bundled Makefile.j2 never showed it, because
# it happens to leave a blank line after the include.

ZIP_WITH_DOCS = {
    "packages": {
        "demo": {
            "name": "Demo",
            "package_type": "zip",
            "package_name": "demo.zip",
            "docs": ["README.md"],
            "package_sources": ["htdocs"],
        }
    }
}


@pytest.mark.parametrize("partial", sorted(CONTRACT))
def test_a_partial_ends_with_a_newline(env, partial):
    """Whatever line a consumer writes after the include starts on its own line."""
    for config in (ZIP_WITH_DOCS, {"packages": {"demo": {"name": "Demo", "package_type": "doc", "package_name": "hs2.pdf", "package_sources": ["md/*.md"], "docs": ["README.md"], "slides": ["Deck.md"]}}}):
        text = env.get_template(partial).render(get_template_context("demo", config))
        assert text.endswith("\n"), f"{partial} ends mid-line: {text[-80:]!r}"


def test_a_line_written_after_the_include_is_its_own_line(generate_package, tmp_path):
    """The consumer's case, end to end: the dependency line that makes `make pkg`
    lint must survive as a rule, not as the tail of clean-pkg's recipe."""
    directory = tmp_path / "templates"
    directory.mkdir()
    (directory / "Makefile.j2").write_text("{% include 'Makefile-pkg.j2' %}\npkg: fix lint\n")
    config = {"templates_dir": "templates", "templates": [{"src": "Makefile.j2"}], **ZIP_WITH_DOCS}
    lines = (generate_package(config) / "Makefile").read_text().splitlines()
    assert "pkg: fix lint" in lines, [line for line in lines if "pkg: fix lint" in line]


# ---------------------------------------------------------------------------
# MAKE_CONTRACT extraction (stn-144.3).
#
# CONTRACT records Jinja context keys; a rendered Makefile partial can also
# read plain make variables that some OTHER bundled partial defines, and
# neither CONTRACT nor StrictUndefined has anything to say about those --
# make treats an unset variable as empty rather than raising. Extraction,
# prototyped and measured against the real templates:
#
#   collect $(NAME) and $(call NAME,...) where NAME is [A-Za-z_][A-Za-z0-9_-]*
#   subtract names defined in the same rendering  (^NAME := | ?= | += | =)
#   subtract make's builtin functions and its specials
#   subtract $(foreach NAME,...) loop variables
#
# BOTH BRACKET SPELLINGS, `$(NAME)` AND `${NAME}`. make treats the two
# identically, so a pattern that recognizes only `$(` leaves a `${NAME}` call
# site invisible to this guard -- the same blindness the compose scan in
# tests/test_compose_makefile.py had, found by the adversarial review of
# stn-qli and fixed there. Fixing it there and not here would leave the
# CROSS-PARTIAL guard, which is the one that catches a partial quietly
# growing a new requirement on its includer, reading only half the syntax.
# MEASURED: no bundled partial uses a brace spelling today, so widening
# changes no recorded residual -- it closes the hole before something walks
# into it, rather than after.
#
# ESCAPED REFERENCES ARE EXCLUDED, and this is what makes the widening safe.
# `$$` is make's escape: in a recipe `$${VAR}` is a SHELL variable and
# `$$(cmd)` a shell substitution, neither of which is a make variable this
# contract should record. The old pattern got away with having no guard
# because the escaped forms in the real templates -- `$$LASTEXITCODE`,
# `$$matches` -- carry no bracket at all; the moment braces are recognized
# that stops being true, so `(?<!\$)` is load-bearing from here on.
#
# Verified against the real text: $(1)/$(2) never match (NAME must start
# [A-Za-z_]); `comma := ,` satisfies the definition pattern and so does
# `PKG ?= ...`.
#
# COMMENTS COUNT. Extraction reads the rendered text as a whole and does not
# strip `#` lines, so a variable NAMED IN A COMMENT is recorded as a
# requirement exactly like a real reference. That is deliberate to the extent
# that a make comment is a poor place to hide a reference -- but it does mean
# writing `$(FOO)` in prose inside a partial makes this test fail with a
# residual nobody can find in a recipe. It fails loudly rather than silently,
# which is the right direction; if it fails on a name you only wrote in a
# comment, reword the comment rather than recording the name here.
MAKE_BUILTIN_FUNCTIONS = {
    "call", "foreach", "if", "shell", "error", "warning", "strip", "subst",
    "patsubst", "filter", "filter-out", "findstring", "sort", "word", "words",
    "wordlist", "firstword", "lastword", "dir", "notdir", "suffix", "basename",
    "addsuffix", "addprefix", "join", "wildcard", "realpath", "abspath",
    "origin", "flavor", "value", "eval", "file", "guile", "info", "or", "and",
    "intcmp",
}
MAKE_SPECIALS = {"OS", "MAKEFILE_LIST", "MAKE", "CURDIR", "SHELL", "MAKEFLAGS"}

# make with=hidden: read by Makefile-doc.j2 but defined by no bundled
# partial -- it is the caller's, not a cross-partial requirement, so a name
# recorded in MAKE_CONTRACT under this allowance does not have to turn up in
# _make_variables_defined_by_bundled_partials below.
USER_SUPPLIED_MAKE_VARIABLES = {"with"}

_MAKE_PLAIN_REF_RE = re.compile(r"(?<!\$)\$[({]([A-Za-z_][A-Za-z0-9_-]*)")
_MAKE_CALL_TARGET_RE = re.compile(r"(?<!\$)\$[({]call\s+([A-Za-z_][A-Za-z0-9_-]*)")
# LEADING DIRECTIVES ARE PART OF A DEFINITION. `override STENCIL_CONTAINER =`
# in Makefile-base.j2 (stn-3y8) is a definition, and without the
# `(?:(?:override|export)[ \t]+)*` prefix this pattern does not see it -- the
# name then falls out of make_variables_required() as a residual the file
# itself defines, and MAKE_CONTRACT["Makefile-base.j2"] == set() goes red
# pointing at a name that appears in no recipe, which is the hardest version
# of this failure to diagnose. `export` is recognized alongside `override`
# (in either order, and repeated) because it is the same shape of directive;
# leaving it out would plant the identical landmine for whoever reaches for
# it next. The `override` half IS exercised, loudly and by name: narrowing
# this pattern back makes
# test_a_makefile_partial_requires_only_the_recorded_make_variables
# [Makefile-base.j2] fail with `assert {'STENCIL_CONTAINER'} == set()`. An
# earlier draft of this comment claimed the widening changed no recorded
# residual, which was true of the templates it was written against and false
# by the time it landed in the same commit as the `override` it exists for.
# The `export` half has no such cover -- no bundled partial begins a line with
# it -- which is what the direct test below is for.
#
# STILL INVISIBLE TO THIS PATTERN, so the "identical landmine" list is honest
# rather than implied-complete: `define FOO =` / `endef`, `override define`,
# `X ::= 1`, `X != echo 1`, and `private FOO = 1`. All of those fail LOUDLY --
# the name surfaces as an unrecorded residual -- which is the tolerable
# direction. The one wrong-direction case is a `define` BODY: its lines are
# recipe text, so an `export SHELLVAR=1` inside one is recorded as a make
# definition and silently subtracts a real requirement. No bundled partial
# uses `define` today; add handling here before one does.
#
# THE LEADING CLASS IS `[ ]*`, NOT `[ \t]*`, AND THAT NARROWING IS
# LOAD-BEARING. A make variable definition cannot be tab-indented -- a leading
# tab makes the line a RECIPE. With `[ \t]*` still in front, a recipe line
# such as `\texport FOO=bar cmd` (a SHELL export, handed to /bin/sh) would
# register FOO as a make definition and silently subtract it from the
# residual, hiding a real cross-partial requirement. The directives widen what
# counts as a definition; this keeps that widening from reaching into recipes.
_MAKE_DEFINITION_RE = re.compile(
    r"^[ ]*(?:(?:override|export)[ \t]+)*([A-Za-z_][A-Za-z0-9_-]*)[ \t]*(?::=|\?=|\+=|=)",
    re.MULTILINE,
)
_MAKE_FOREACH_LOOP_VAR_RE = re.compile(
    r"(?<!\$)\$[({]foreach\s+([A-Za-z_][A-Za-z0-9_-]*)\s*,"
)


def make_variables_required(text: str) -> set[str]:
    """Make variables `text` reads but does not itself define -- the residual
    left over after subtracting definitions, builtins, specials and foreach
    loop variables. See the extraction rules above."""
    referenced = set(_MAKE_PLAIN_REF_RE.findall(text)) | set(
        _MAKE_CALL_TARGET_RE.findall(text)
    )
    defined = set(_MAKE_DEFINITION_RE.findall(text))
    loop_vars = set(_MAKE_FOREACH_LOOP_VAR_RE.findall(text))
    return referenced - defined - MAKE_BUILTIN_FUNCTIONS - MAKE_SPECIALS - loop_vars


# UNION THE RESIDUALS OVER CONFIG SHAPES, rather than trust one config's
# answer. A variable used only in a branch one shape does not render would
# otherwise escape the guard silently -- the same blindness this whole epic
# is about. These are the two shapes test_a_partial_ends_with_a_newline
# already renders, plus a pre_build shape: has_package_output_dir and
# has_pre_build each gate their own branch in Makefile-doc.j2, and
# package_type "zip" takes Makefile-pkg.j2 down a path that calls neither
# STENCIL_COMPOSE nor ensure_image at all -- measured: the zip shape's own
# residual is empty, which is why unioning matters rather than picking
# whichever shape looks richest.
MAKE_CONTRACT_CONFIGS = (
    ZIP_WITH_DOCS,
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
    {
        "packages": {
            "demo": {
                "name": "Demo",
                "package_type": "doc",
                "docs": ["README.md"],
                "pre_build": [
                    {
                        "run": "python3 gen_figures.py",
                        "outputs": "figures/*.svg",
                        "inputs": "gen_figures.py",
                    }
                ],
            }
        }
    },
)

MAKE_PARTIALS = tuple(sorted(MAKE_CONTRACT))


def test_a_definition_is_recognized_through_its_leading_directives():
    """`override STENCIL_CONTAINER = ...` is a definition, and a tab-indented
    shell `export` is not.

    The `override` half has other cover: narrowing _MAKE_DEFINITION_RE back
    makes test_a_makefile_partial_requires_only_the_recorded_make_variables
    [Makefile-base.j2] fail with `assert {'STENCIL_CONTAINER'} == set()`,
    since that partial now begins a line with `override`. The `export` half
    and the `[ ]*` NARROWING have none -- no bundled partial begins a line
    with `export`, and MEASURED, restoring `[ \t]*` in front of the
    directives is caught by NOTHING in this suite except the SHELLVAR
    assertion below. That narrowing is the piece of this test that earns its
    place.

    Goes red against the narrow pattern on the first assertion (STENCIL_CONTAINER
    missing), and red against a pattern that keeps `[ \t]*` in front of the
    directives on the last (SHELLVAR wrongly recorded as defined, which would
    silently subtract a real cross-partial requirement from every residual).
    """
    text = (
        "override STENCIL_CONTAINER = $(firstword $(subst -, ,$(DC)))\n"
        "export EXPORTED := 1\n"
        "override export BOTH = 2\n"
        "PLAIN ?= 3\n"
        "export NO_OPERATOR\n"
        "\texport SHELLVAR=1 some-command\n"
    )
    defined = set(_MAKE_DEFINITION_RE.findall(text))
    assert "STENCIL_CONTAINER" in defined, "an `override` definition was not seen"
    assert "EXPORTED" in defined, "an `export` definition was not seen"
    assert "BOTH" in defined, "stacked directives were not seen"
    assert "PLAIN" in defined, "an ordinary definition stopped being seen"
    assert "NO_OPERATOR" not in defined, (
        "`export NAME` with no assignment operator is not a definition"
    )
    assert "SHELLVAR" not in defined, (
        "a TAB-indented `export` is a shell command inside a recipe, not a "
        "make definition -- recording it would subtract a real requirement"
    )


@pytest.mark.parametrize("partial", MAKE_PARTIALS)
def test_a_makefile_partial_requires_only_the_recorded_make_variables(env, partial):
    """Fails when a partial starts reading a cross-partial make variable, or
    stops reading one -- the union over every config shape below, so a
    variable used only in one shape's branch cannot escape unnoticed."""
    residual: set[str] = set()
    for config in MAKE_CONTRACT_CONFIGS:
        text = env.get_template(partial).render(get_template_context("demo", config))
        residual |= make_variables_required(text)
    assert residual == MAKE_CONTRACT[partial]


@pytest.mark.parametrize("partial", MAKE_PARTIALS)
def test_every_recorded_make_variable_is_defined_somewhere(env, partial):
    """The other direction: a typo sitting in MAKE_CONTRACT must not look
    satisfied. Every recorded name has to be either defined by some bundled
    Makefile partial (in at least one of the shapes above) or explicitly
    allowed as user-supplied."""
    defined: set[str] = set()
    for config in MAKE_CONTRACT_CONFIGS:
        context = get_template_context("demo", config)
        for other in MAKE_PARTIALS:
            text = env.get_template(other).render(context)
            defined |= set(_MAKE_DEFINITION_RE.findall(text))
    known = defined | USER_SUPPLIED_MAKE_VARIABLES
    unknown = MAKE_CONTRACT[partial] - known
    assert not unknown, f"{partial} records {sorted(unknown)}, which nothing defines"
