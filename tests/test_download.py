"""The show_download key, and the coercion trap it inherits from show_date.

show_download DEFAULTS TO TRUE. That is the opposite polarity of every other
front-matter switch in this repository -- show_date defaults to false, and an
absent key there means withhold. Here an absent key means SHOW: a generated
page is already fully self-contained (assets inlined at `stencil gen`, images
base64'd by embed-images.lua), so a self-download control is meant to be there
unless an author or a package explicitly turns it off. Only an explicit
false-ish value hides it -- the words in the FALSE table in
frontmatter-filter.lua.j2 (no, false, off, 0, none, null, compared after
lowercasing and trimming; the table also maps the empty string, which is the
entry this key has to stop short of, see below) -- and truthy() is expected to
grow a `default` argument so this key can resolve the opposite way from
show_date while sharing the same table.

Pandoc reads YAML 1.2, where `true` and `false` are the only real booleans, so
`show_download: no` arrives at the filter as the STRING "no" -- exactly as
truthy as "yes" to a naive `$if(show_download)$`. That string is the case that
matters most here, for the same reason it mattered for show_date: the obvious
spelling would silently mean its own opposite, and it would mean it in the
direction that leaves an unwanted control sitting in the toolbar.

The blank case is the one unique to a default-true key, and it is why this
file exists on its own rather than as a few more parametrize entries bolted
onto test_frontmatter.py. AUTHORING.md states the repo-wide rule that leaving
a key blank is the same as leaving it out, so `show_download:` with nothing
after it must mean ON. But the FALSE table already maps the empty string to
"false-ish" -- that is exactly right for show_date, where absent means off and
blank should agree with absent. For a default-true key the naive reuse of that
table gets it backwards: pandoc hands the filter an empty MetaInlines for a
blank value rather than nil, so a coercion that only asks "is this text in the
FALSE table" cannot tell a blank value from an explicit `no` and renders OFF
when it should render ON. This was measured against a real pandoc build, not
inferred from reading the Lua -- it is the trap the `default` parameter has to
close.

The two static cases below are not about show_download's truth table at all;
they are about where the partial that reads it has to live. _download.html.j2
must contain no `$`, exactly like _theme-toggle.html.j2, which contains zero.
That is a CONVENTION rather than a pandoc law, and the distinction matters:
pandoc's escape for a literal dollar is `$$`, and _page-scripts.html.j2 uses
it deliberately in two regexes (`/^\n|\n$$/g` at line 73, `/^H[1-6]$$/` at
line 287). So a `$` is not forbidden by the format -- it is avoided here so
that nobody writing JavaScript inside a pandoc template has to notice they are
in a context where a dollar needs doubling. Saying "`$` opens a variable" would
be false, and would mislead the next person who genuinely needs an end-anchor.
And its include
in both composition templates must sit strictly between _theme-toggle.html.j2
and _page-scripts.html.j2. That position is the single load-bearing fact of
the whole design: hljs.highlightAll() and the mermaid <pre> -> <div> swap in
_page-scripts.html.j2 both run at PARSE time, not on DOMContentLoaded (see its
own comments, around lines 22-28 and 68-70). A capture of the page's pristine
markup placed after that include would silently record already-transformed
HTML -- no exception, no other failing test, just a downloaded file that is
subtly wrong forever. There is no way to catch that at runtime once the two
partials are adjacent; the ordering has to be pinned as a standing structural
fact.

ACCEPTANCE NOW (read before "fixing" a result that looks wrong):
- RED: show_download absent, blank, and every truthy spelling (true, yes, on,
  1) -- none of them render the control today, because nothing does.
- RED: the no-`$` test and the include-order test -- both error, because
  stencil/templates/_download.html.j2 does not exist yet.
- GREEN, but not yet meaningful: every false-ish spelling (false, no, off, 0,
  none, null) passes, because the control renders nowhere for anybody yet.
  That is a coincidence of the current state, not evidence the feature works --
  do not treat it as license to touch this file's coercion cases once
  _download.html.j2 exists and RED starts turning green for the right reason.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from bs4 import BeautifulSoup

from stencil import pipeline
from stencil.generate import get_template_context

TEMPLATES = Path(__file__).parent.parent / "stencil" / "templates"

DOWNLOAD_MARKER = "mountDownloadButton"


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def deck(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## A slide heading\n\nSome prose.\n"


def source(template: str) -> str:
    return (TEMPLATES / template).read_text()


def has_download_control(soup) -> bool:
    """True if the page defines the download control's mount function.

    BeautifulSoup's ``get_text()`` drops the contents of <script> tags, so a
    function name embedded in inline JavaScript has to be found in the page's
    raw markup rather than its extracted text. That is what the task means by
    "look for mountDownloadButton in the page text": the rendered HTML source,
    not the reader-visible text.
    """
    return DOWNLOAD_MARKER in str(soup)


# --- rendered markup: the truth table, for both a document and a deck ------


@pytest.mark.integration
@pytest.mark.parametrize("kind,build", [("doc", document), ("slide", deck)])
def test_absent_key_renders_the_control(render_soup, kind, build):
    soup = render_soup(kind, "d.md", text=build('title: "T"\n'))
    assert has_download_control(soup), "absent show_download hid the control"


@pytest.mark.integration
@pytest.mark.parametrize("kind,build", [("doc", document), ("slide", deck)])
def test_blank_key_renders_the_control(render_soup, kind, build):
    """The case a naive reuse of show_date's FALSE table gets backwards: a
    blank value is absent, and absent means on for this key."""
    soup = render_soup(kind, "d.md", text=build('title: "T"\nshow_download:\n'))
    assert has_download_control(soup), "blank show_download hid the control"


@pytest.mark.integration
@pytest.mark.parametrize("kind,build", [("doc", document), ("slide", deck)])
@pytest.mark.parametrize("written", ["true", "yes", "on", "1"])
def test_the_ways_of_writing_yes(render_soup, kind, build, written):
    soup = render_soup(
        kind, "d.md", text=build(f'title: "T"\nshow_download: {written}\n')
    )
    assert has_download_control(soup), f"show_download: {written} hid the control"


@pytest.mark.integration
@pytest.mark.parametrize("kind,build", [("doc", document), ("slide", deck)])
@pytest.mark.parametrize("written", ["false", "no", "off", "0", "none", "null"])
def test_the_ways_of_writing_no(render_soup, kind, build, written):
    """The regression this key exists to guard against: pandoc hands the
    filter the string "no", every bit as truthy as "yes" to a naive $if()."""
    soup = render_soup(
        kind, "d.md", text=build(f'title: "T"\nshow_download: {written}\n')
    )
    assert not has_download_control(soup), (
        f"show_download: {written} rendered the control anyway"
    )


# --- static: the partial itself --------------------------------------------


def test_the_partial_contains_no_pandoc_variable_syntax():
    """A convention this partial keeps, not a rule pandoc enforces.

    A literal dollar in a pandoc template is written `$$`, and
    _page-scripts.html.j2 does exactly that in two regexes. So `$` is legal;
    it is simply a thing nobody should have to remember while writing
    JavaScript, and _theme-toggle.html.j2 -- the partial this one mirrors --
    avoids it entirely. Keeping that property means a JS template literal or a
    `$`-anchored regex added later fails here, loudly, rather than emitting
    half-escaped syntax into the page.
    """
    assert "$" not in source("_download.html.j2")


# --- static: the include order ----------------------------------------------


DOWNLOAD_INCLUDE = "{% include '_download.html.j2' %}"


def assert_download_include_between_theme_and_scripts(template: str) -> None:
    text = source(template)
    assert DOWNLOAD_INCLUDE in text, (
        f"{template} does not include _download.html.j2 at all"
    )
    theme_at = text.index("{% include '_theme-toggle.html.j2' %}")
    download_at = text.index(DOWNLOAD_INCLUDE)
    scripts_at = text.index("{% include '_page-scripts.html.j2' %}")
    assert theme_at < download_at < scripts_at, (
        f"{template}: _download.html.j2 must be included strictly between "
        "_theme-toggle.html.j2 and _page-scripts.html.j2"
    )


@pytest.mark.parametrize(
    "template", ["html-template.html.j2", "slide-template.html.j2"]
)
def test_download_is_included_between_theme_toggle_and_page_scripts(template):
    """The single load-bearing fact of the whole design.

    _page-scripts.html.j2 runs hljs.highlightAll() and swaps every mermaid
    <pre> for a rendered <div> at PARSE time, not on DOMContentLoaded -- both
    transformations happen before any script below them ever executes. If the
    download partial's capture of the page's pristine head/body ran after that
    include, it would silently record already-transformed markup: no
    exception, no other test would catch it, and every downloaded file would
    carry highlighted-but-frozen code blocks and rendered-not-source diagrams
    forever. Putting the partial before _page-scripts.html.j2, right after
    _theme-toggle.html.j2, is what lets it capture the untouched page.
    """
    assert_download_include_between_theme_and_scripts(template)


# --- static: the pandoc-level gate -------------------------------------------


@pytest.mark.parametrize(
    "template", ["html-template.html.j2", "slide-template.html.j2"]
)
def test_the_download_include_is_wrapped_in_the_show_download_conditional(template):
    """The interface obligation no other guard can see.

    tests/test_template_contract.py records what each shared partial reads out
    of the JINJA context, and _download.html.j2 reads nothing -- so a correct
    and complete entry there says `set()` and still cannot express the thing
    that actually matters about including this partial: it has to sit inside
    `$if(show_download)$`, which is a PANDOC-level fact invisible to a Jinja
    contract.

    Without this test, a consumer on their own composition template -- which
    AGENTS.md documents as supported, and which one consumer already does --
    could include the partial unconditionally and ship the control on every
    page regardless of front matter, or omit the conditional's `$endif$` and
    silently swallow the scripts that follow. Neither shows up anywhere else.
    """
    text = source(template)
    start = text.index("$if(show_download)$")
    end = text.index("$endif$", start)
    guarded = text[start:end]
    assert DOWNLOAD_INCLUDE in guarded, (
        f"{template} includes _download.html.j2 outside "
        "$if(show_download)$ ... $endif$, so the control would render "
        "whatever the front matter says"
    )


# --- the config-level default: package, then config-wide, then True --------
#
# Everything above this line is per-document: the key lives only in front
# matter, and truthy()'s hardcoded default of true is the only fallback. This
# section is about stn-bd3's later widening -- a package, or a whole
# .config.yaml, gets to change what an ABSENT key means, the same
# narrowest-first shape brand_of() and the `lang` context key already use in
# stencil/generate.py (package wins, then config-wide, then a hardcoded
# default). A document's own front matter still outranks both, in EITHER
# direction: an author can turn the control back on for one handout even
# though their package turned it off, and off even though their package left
# it on.
#
# render_soup (conftest.py) is built on the doc_package fixture, which is
# pinned to the module-level DEMO_CONFIG -- it has no way to vary
# show_download at the config or package level. MATRIX_CONFIG and
# render_matrix() below repeat its shape (generate_package, then render real
# markdown through the real pandoc container) for a config that does, mirroring
# tests/test_config_validation.py::test_a_config_wide_lang_becomes_the_default
# and ::test_a_package_lang_outranks_the_config_wide_one -- the precedent for
# exactly this kind of key -- except the outcome under test is rendered
# markup, not baked-in template text, because show_download's story is not
# complete until a document's front matter has had a chance to override it.

MATRIX_CONFIG = {
    "packages": {
        "demo": {
            "name": "Demo",
            "package_type": "none",
            # A markdown file belongs to exactly one of docs/slides, so the two
            # kinds need separate names even though render_matrix() overwrites
            # whichever one it is asked to render.
            "docs": ["d.md"],
            "slides": ["s.md"],
        }
    },
}


def config_with(config_show_download, package_show_download) -> dict:
    """MATRIX_CONFIG, with show_download optionally set at each level.

    None means "leave the key unset" at that level, not "set it to None" --
    show_download is never legitimately Python None once it reaches this
    resolution, so the sentinel cannot collide with a real case.
    """
    config = copy.deepcopy(MATRIX_CONFIG)
    if config_show_download is not None:
        config["show_download"] = config_show_download
    if package_show_download is not None:
        config["packages"]["demo"]["show_download"] = package_show_download
    return config


def render_matrix(generate_package, config: dict, kind: str, text: str):
    """render_soup's shape, for a config generate_package's fixture caller
    controls directly instead of the fixed DEMO_CONFIG.

    generate_package builds the scaffolding -- including html-template.html
    and slide-template.html, since MATRIX_CONFIG's package has both docs and
    slides -- and pipeline.render() drives the same pandoc container the
    render fixture does, over the markdown written here rather than a fixture
    file.
    """
    package = generate_package(config)
    source = "d.md" if kind == "doc" else "s.md"
    (package / source).write_text(text)
    output = f"{Path(source).stem}.html"
    result = pipeline.render(kind, source, output, workdir=package)
    assert result.returncode == 0, f"pandoc exited {result.returncode}\n{result.stderr}"
    return BeautifulSoup((package / output).read_text(), "html.parser")


@pytest.mark.integration
@pytest.mark.parametrize("kind,build", [("doc", document), ("slide", deck)])
@pytest.mark.parametrize(
    "config_value,package_value,extra_front_matter,expect_button",
    [
        pytest.param(None, None, "", True, id="nothing-set-anywhere"),
        pytest.param(False, None, "", False, id="config-wide-false"),
        pytest.param(
            False, True, "", True, id="package-true-outranks-config-false"
        ),
        pytest.param(
            True, False, "", False, id="package-false-outranks-config-true"
        ),
        pytest.param(
            False,
            None,
            "show_download: true\n",
            True,
            id="front-matter-true-overrides-config-false",
        ),
        pytest.param(
            None,
            False,
            "show_download: true\n",
            True,
            id="front-matter-true-overrides-package-false",
        ),
        pytest.param(
            None,
            True,
            "show_download: false\n",
            False,
            id="front-matter-false-overrides-package-true",
        ),
    ],
)
def test_show_download_resolution_matrix(
    generate_package,
    kind,
    build,
    config_value,
    package_value,
    extra_front_matter,
    expect_button,
):
    """Every combination of config-wide, package-level and front matter that
    can disagree, for both a document and a deck.

    The package/config half of this matrix is the precedent brand_of() and
    lang already set: the narrower setting wins, and nothing here may be
    written with `or`, because `package.get("show_download") or
    config.get("show_download")` reads an explicit package-level False as
    "unset" and falls through to the config value -- exactly the
    package-false-outranks-config-true case below, which exists to catch
    that.

    The front-matter half is the direction that matters most and is easiest
    to get wrong: an author must be able to turn the control back on for one
    handout even when their package or config turned it off, and back off
    even when their package left it on. Both directions are exercised.
    """
    config = config_with(config_value, package_value)
    text = build(f'title: "T"\n{extra_front_matter}')
    soup = render_matrix(generate_package, config, kind, text)
    if expect_button:
        assert has_download_control(soup), (
            f"config={config_value!r} package={package_value!r} "
            f"front matter={extra_front_matter!r} should show the control"
        )
    else:
        assert not has_download_control(soup), (
            f"config={config_value!r} package={package_value!r} "
            f"front matter={extra_front_matter!r} should hide the control"
        )


@pytest.mark.integration
@pytest.mark.parametrize("kind,build", [("doc", document), ("slide", deck)])
def test_yaml_1_1_no_is_false_at_the_config_level(generate_package, kind, build):
    """The config side of the boolean trap, pinned rather than inferred.

    .config.yaml is read by PyYAML, which is YAML 1.1 -- an unquoted `no`
    becomes a real Python False there, unlike front matter, which pandoc
    reads as YAML 1.2 and hands over as the string "no" (see
    test_the_ways_of_writing_no above, and frontmatter-filter.lua.j2's own
    comment). yaml.safe_load is used here rather than writing `False`
    directly, so this test would fail if PyYAML's own coercion ever changed --
    the point is to pin what the config author's literal spelling means, not
    just what a Python bool does.
    """
    parsed = yaml.safe_load("show_download: no\n")["show_download"]
    assert parsed is False, "sanity: PyYAML should parse unquoted `no` as False"
    config = config_with(parsed, None)
    soup = render_matrix(generate_package, config, kind, build('title: "T"\n'))
    assert not has_download_control(soup), (
        "show_download: no at the config level should hide the control"
    )


def test_a_non_boolean_config_value_is_an_error():
    """The config side is not front matter and does not get to guess.

    Front matter's show_download runs through truthy(), which is built to
    accept a spread of spellings pandoc might hand it. .config.yaml is read
    straight by PyYAML, so by the time it reaches stencil the value is
    whatever a config author wrote as YAML -- true/false booleans, or, if they
    quoted it, a plain string. A quoted "no" is exactly that: not a coerced
    False, just a string that happens to say "no". Silently reading it as
    truthy (any non-empty string) or falsy (== "no") would both be guesses
    about intent the config side is not entitled to make, so it must refuse
    and name the key rather than pick a side.
    """
    config = config_with("no", None)
    with pytest.raises(ValueError, match="show_download"):
        get_template_context("demo", config)
