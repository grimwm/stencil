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

WHAT PANDOC ACTUALLY HANDS THE FILTER, measured on the pinned pandoc (3.10)
with a probe reporting the exact Lua type rather than inferred from the YAML
spec:

    true false yes no on off   (any case)  -> a real boolean
    1 0 none                               -> a string
    null ~ and a blank value               -> an empty string, all three
                                              indistinguishable from one another
    absent                                 -> nil

That is YAML 1.1 boolean resolution, and it is worth stating plainly because
this repository documents the opposite. frontmatter-filter.lua.j2's header
comment, AUTHORING.md's show_date section and stn-ejv itself all say pandoc
reads YAML 1.2, where `true` and `false` are the only booleans and
`show_download: no` therefore arrives as the STRING "no". That was presumably
true when show_date was written; it is not true of the pandoc this project
pins today, and it is tracked separately rather than rewritten here.

The FALSE table is not thereby dead. It still decides `0` and `none`, which
arrive as strings, and it still catches a QUOTED "no" or "null" -- which is
the spelling an author reaches for when they have been bitten once and are
being careful. truthy() checks the boolean branch first and falls through to
the table, so every spelling lands correctly whichever way pandoc resolves it,
and the feature does not depend on which YAML version a future pandoc picks.

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
import json
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
@pytest.mark.parametrize("written", ["false", "no", "off", "0", "none", '"null"'])
def test_the_ways_of_writing_no(render_soup, kind, build, written):
    """The regression this key exists to guard against: pandoc hands the
    filter the string "no", every bit as truthy as "yes" to a naive $if()."""
    soup = render_soup(
        kind, "d.md", text=build(f'title: "T"\nshow_download: {written}\n')
    )
    assert not has_download_control(soup), (
        f"show_download: {written} rendered the control anyway"
    )


@pytest.mark.integration
@pytest.mark.parametrize("kind,build", [("doc", document), ("slide", deck)])
@pytest.mark.parametrize("written", ["null", "~"])
def test_a_yaml_null_is_absent_rather_than_false(render_soup, kind, build, written):
    """`null` and `~` mean ABSENT, which for this key means on.

    Measured against the pinned pandoc with a probe filter reporting the exact
    Lua type it hands the Meta function: `show_download: null`,
    `show_download: ~` and `show_download:` with nothing after it are
    INDISTINGUISHABLE -- all three arrive as an empty string. So they cannot
    mean anything different from a blank value, and a blank value is the same
    as leaving the key out.

    The FALSE table's "null" entry is not dead: it catches the QUOTED string
    "null", which does arrive as a real string, and which is covered in
    test_the_ways_of_writing_no above. But an unquoted null is a YAML null,
    not the word.
    """
    soup = render_soup(
        kind, "d.md", text=build(f'title: "T"\nshow_download: {written}\n')
    )
    assert has_download_control(soup), (
        f"show_download: {written} is a YAML null, which means absent -- and "
        "absent means the configured default, which ships as on"
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


def test_a_blank_config_value_is_the_same_as_an_absent_one():
    """The one non-boolean the config side does NOT refuse.

    PyYAML hands over None for `show_download:` with nothing after it, which
    is the same spelling AUTHORING.md's repo-wide rule -- and the front-matter
    half of this very feature, above -- treat as "leave it out". Refusing it
    here would make one spelling mean "I have not decided yet" in a markdown
    file and "your build is broken" in the .config.yaml beside it.

    It is also what the first implementation got wrong, in a way that was
    worse than the behaviour: None fell into the non-boolean branch and
    produced advice to remove quotes that were never there.

    Blank falls through to the wider scope, so a blank package-level key
    still lets a config-wide setting decide.
    """
    blank_everywhere = config_with(None, None)
    blank_everywhere["show_download"] = None
    blank_everywhere["packages"]["demo"]["show_download"] = None
    assert get_template_context("demo", blank_everywhere)["config_show_download"] is True

    blank_package_over_config_false = config_with(False, None)
    blank_package_over_config_false["packages"]["demo"]["show_download"] = None
    context = get_template_context("demo", blank_package_over_config_false)
    assert context["config_show_download"] is False, (
        "a blank package-level show_download should defer to the config-wide "
        "setting, not reset it to the default"
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


# --- runtime: order, radiogroup boundary, downloaded bytes -----------------
#
# stn-bd3.3. Everything above this line reads markup pandoc wrote, straight
# off disk. None of it can tell whether the control lands in the right place
# in a real toolbar, whether it sits outside the theme radiogroup a screen
# reader would otherwise misread it into, or whether clicking it produces the
# file the ticket actually asks for -- a self-contained copy of the page as
# pandoc wrote it, not the live DOM with a presenter's toolbar and theme
# baked in. Mirrors tests/test_present_mode.py: one module-scoped fixture
# renders into the session-wide pdf_workspace, drives one PROBE through
# pipeline.run_in_browser(), and json.loads the marker-prefixed last stdout
# line; individual tests below read that one result rather than each paying
# for a container start of their own.
#
# TWO SOURCES, per the task. AUTHORING.md line 122 says a deck already
# paginates and tabbed sections are document-only, so the two page kinds get
# different bodies: the deck is any multi-slide deck (three headings, so a
# present-mode-style deck-toolbar has something to page through); the
# document carries BOTH a mermaid diagram and a tabbed block, so the parse-
# time transform (mermaid's <pre> -> <div> swap) and the DOMContentLoaded one
# (the tab-pane builder) both actually run before anything downloads it.
# Built on top of document()/deck() above rather than duplicating their front
# matter and opening paragraph -- appending more sections after Some prose.
# is enough to turn deck()'s single slide into three and document()'s single
# paragraph into a page with a diagram and two tabs.
#
# THE DOWNLOAD BUTTON'S SELECTOR. Nothing renders it yet, so nothing here can
# read it off a live page. `.download-button` is the class the epic's
# approved plan names for it (the CSS rule list is `.download-button, its
# visually-hidden label, .theme-toggle-host display:flex, and the @media
# print rule`), so the probe looks for that selector. If stn-bd3.4 ships a
# different class, the fix is here, once, not scattered across every
# assertion that currently spells it out.
#
# WHY THE PROBE CANNOT THROW EVEN THOUGH THE BUTTON DOES NOT EXIST. A raw
# `page.click('.download-button')` on a page with no such element rejects
# with an opaque Puppeteer "no node found for selector" error, which would
# surface here as a broken fixture rather than a readable test failure. The
# probe checks `buttonFound` first and only attempts a click when it is true,
# so every test below fails on an assertion this file wrote, with a message
# this file wrote, rather than on a stack trace from inside node_modules.

RUNTIME_DECK = deck('title: "Deck"\n') + (
    "\n"
    "## Two\n"
    "\n"
    "Second slide.\n"
    "\n"
    "## Three\n"
    "\n"
    "Third slide.\n"
)

# The exact tabbed-block shape the task specifies as verified working:
# markdown links inside <nav class="nav-tabs">, not <a href> written as raw
# HTML -- the shape tests/test_pdf.py's CLIENT_RENDERED fixture uses -- and
# heading ids that match the link targets.
RUNTIME_DOCUMENT = document('title: "Doc"\n') + (
    "\n"
    "```{.mermaid}\n"
    "flowchart LR\n"
    "  client --> api --> db\n"
    "```\n"
    "\n"
    '<nav class="nav-tabs">\n'
    "[One](#tab-one)\n"
    "[Two](#tab-two)\n"
    "</nav>\n"
    "\n"
    "### One {#tab-one}\n"
    "\n"
    "First pane.\n"
    "\n"
    "### Two {#tab-two}\n"
    "\n"
    "Second pane.\n"
)

PROBE_MARKER = "<<<DOWNLOAD-RUNTIME>>>"

# One script, both page kinds -- the same shape tests/test_check_access.py's
# `accessibility` fixture uses for pa11y, so this pays for one container
# start rather than two.
#
# CDP DOWNLOAD MECHANICS, per the corrections from the adversarial review:
# Browser.setDownloadBehavior with eventsEnabled so Browser.downloadProgress
# fires, and the probe waits for state === "completed" rather than polling
# the filesystem -- Chrome writes a .crdownload partial first, and polling
# for *a* file on disk risks reading that partial instead of the finished
# download. Each of the two downloads per page kind gets its OWN directory
# (kind-1, kind-2): a repeated filename overwrites under
# setDownloadBehavior, and both downloads derive the same name from
# location.pathname, so reusing one directory would let the second click
# silently clobber the first file before the non-compounding assertion ever
# reads it.
RUNTIME_PROBE = r"""
const puppeteer = require("puppeteer");
const fs = require("fs");
const path = require("path");

// The suggested filename arrives on Browser.downloadWillBegin and NOWHERE
// else. Browser.downloadProgress carries {guid, totalBytes, receivedBytes,
// state} and never the name, so reading event.suggestedFilename off the
// completed progress event yields undefined -- which surfaces far away, as
// path.join throwing ERR_INVALID_ARG_TYPE, rather than as anything that
// mentions downloads. Correlate the two events by guid instead.
function waitForDownload(client, timeoutMs) {
  return new Promise((resolve, reject) => {
    var settled = false;
    var names = {};

    function onBegin(event) {
      names[event.guid] = event.suggestedFilename;
    }

    function finish(fn, arg) {
      settled = true;
      clearTimeout(timer);
      client.off("Browser.downloadWillBegin", onBegin);
      client.off("Browser.downloadProgress", onProgress);
      fn(arg);
    }

    function onProgress(event) {
      if (settled) return;
      if (event.state === "completed") {
        var suggested = names[event.guid];
        if (!suggested) {
          finish(reject, new Error(
            "download " + event.guid + " completed but no downloadWillBegin " +
            "carried a suggestedFilename for it"
          ));
          return;
        }
        finish(resolve, { guid: event.guid, suggestedFilename: suggested });
      } else if (event.state === "canceled") {
        finish(reject, new Error("download canceled: " + JSON.stringify(event)));
      }
    }

    var timer = setTimeout(function () {
      if (settled) return;
      finish(reject, new Error("timed out waiting for Browser.downloadProgress"));
    }, timeoutMs);

    client.on("Browser.downloadWillBegin", onBegin);
    client.on("Browser.downloadProgress", onProgress);
  });
}

async function downloadViaClick(page, client, downloadDir, selector) {
  fs.mkdirSync(downloadDir, { recursive: true });
  await client.send("Browser.setDownloadBehavior", {
    behavior: "allow",
    downloadPath: downloadDir,
    eventsEnabled: true,
  });
  var waiter = waitForDownload(client, 20000);
  await page.click(selector);
  var event = await waiter;
  var filename = event.suggestedFilename;
  var filePath = path.join(downloadDir, filename);
  if (!fs.existsSync(filePath)) {
    throw new Error(
      "Browser.downloadProgress reported completed but " + filePath + " is not on disk"
    );
  }
  return { filename: filename, filePath: filePath };
}

function describeChildrenInPage(el) {
  return Array.from(el.children).map(function (child) {
    var cls = (child.className || "").toString().trim();
    return cls ? child.tagName + "." + cls.split(/\s+/).join(".") : child.tagName;
  });
}

async function probeOne(browser, kind, filename, downloadRoot) {
  var page = await browser.newPage();
  var pageErrors = [];
  page.on("pageerror", function (err) { pageErrors.push(String(err)); });
  var client = await page.target().createCDPSession();

  await page.goto("file:///workspace/" + filename, { waitUntil: "networkidle0" });

  // MEASURE THE HEAD: captured as soon as the page has loaded, then again
  // after __mermaidReady, so a future change that starts mutating <head>
  // between parse and mermaid-ready shows up as a number here instead of an
  // assumption nobody wrote down.
  var headBefore = await page.evaluate(function () { return document.head.innerHTML; });
  await page
    .waitForFunction(function () { return window.__mermaidReady === true; }, { timeout: 30000 })
    .catch(function () {});
  var mermaidReady = await page.evaluate(function () { return window.__mermaidReady === true; });
  var headAfter = await page.evaluate(function () { return document.head.innerHTML; });

  var structure = await page.evaluate(function (kindArg) {
    function describeChildrenInPage2(el) {
      return Array.from(el.children).map(function (child) {
        var cls = (child.className || "").toString().trim();
        return cls ? child.tagName + "." + cls.split(/\s+/).join(".") : child.tagName;
      });
    }
    var out = {};
    if (kindArg === "slide") {
      var toolbar = document.querySelector(".deck-toolbar");
      out.containerChildren = toolbar ? describeChildrenInPage2(toolbar) : null;
    } else {
      var host = document.querySelector(".theme-toggle-host");
      out.containerChildren = host ? describeChildrenInPage2(host) : null;
    }
    var button = document.querySelector(".download-button");
    if (!button) {
      out.buttonFound = false;
    } else {
      out.buttonFound = true;
      out.insideRadiogroup = button.closest('[role="radiogroup"]') !== null;
      // The accessible name: the first descendant text that is not marked
      // aria-hidden, mirroring _theme-toggle.html.j2's glyph-hidden,
      // label-visible pattern rather than assuming a class name for it.
      var named = null;
      Array.from(button.querySelectorAll("*")).some(function (node) {
        if (node.getAttribute("aria-hidden") === "true") return false;
        var text = (node.textContent || "").trim();
        if (text) {
          named = text;
          return true;
        }
        return false;
      });
      out.accessibleName = named !== null ? named : (button.textContent || "").trim();
    }
    return out;
  }, kind);

  var result = {
    pageErrors: pageErrors,
    mermaidReady: mermaidReady,
    headMatchesAfterMermaidReady: headBefore === headAfter,
    containerChildren: structure.containerChildren,
    buttonFound: structure.buttonFound,
    insideRadiogroup:
      structure.insideRadiogroup !== undefined ? structure.insideRadiogroup : null,
    accessibleName:
      structure.accessibleName !== undefined ? structure.accessibleName : null,
  };

  result.download = { attempted: false };
  if (structure.buttonFound) {
    result.download.attempted = true;
    try {
      var dir1 = path.join(downloadRoot, kind + "-1");
      var first = await downloadViaClick(page, client, dir1, ".download-button");
      result.download.filename = first.filename;

      // THE DOWNLOADED COPY LOADS CLEAN: page.goto it and check for
      // pageerror and __mermaidReady, the only real check on the "nothing
      // may throw" constraint html-to-pdf.js depends on.
      var page2 = await browser.newPage();
      var page2Errors = [];
      page2.on("pageerror", function (err) { page2Errors.push(String(err)); });
      await page2.goto("file://" + first.filePath, { waitUntil: "networkidle0" });
      await page2
        .waitForFunction(function () { return window.__mermaidReady === true; }, { timeout: 30000 })
        .catch(function () {});
      result.download.loadedMermaidReady = await page2.evaluate(function () {
        return window.__mermaidReady === true;
      });
      result.download.loadPageErrors = page2Errors;

      // DOWNLOAD FROM THE DOWNLOADED COPY, into a SEPARATE directory so this
      // click cannot overwrite the first file before it has been compared.
      var buttonOnDownload = await page2.evaluate(function () {
        return !!document.querySelector(".download-button");
      });
      result.download.buttonFoundOnDownload = buttonOnDownload;
      if (buttonOnDownload) {
        var client2 = await page2.target().createCDPSession();
        var dir2 = path.join(downloadRoot, kind + "-2");
        var second = await downloadViaClick(page2, client2, dir2, ".download-button");
        result.download.secondFilename = second.filename;
      }
      await page2.close();
    } catch (err) {
      result.download.error = String(err);
    }
  }

  await page.close();
  return result;
}

(async () => {
  var browser = await puppeteer.launch({ args: ["--no-sandbox"] });
  var downloadRoot = "/workspace/_download-probe";
  var out = {};
  try {
    out.doc = await probeOne(browser, "doc", "document.html", downloadRoot);
    out.slide = await probeOne(browser, "slide", "deck.html", downloadRoot);
  } finally {
    await browser.close();
  }
  console.log("<<<DOWNLOAD-RUNTIME>>>" + JSON.stringify(out));
})().catch(function (err) {
  console.log("<<<DOWNLOAD-RUNTIME>>>" + JSON.stringify({ error: String(err) }));
  process.exit(1);
});
"""


@pytest.fixture(scope="module")
def download_runtime(pdf_workspace):
    """Render both fixtures once and drive one browser probe over both.

    The rendered HTML is snapshotted into the returned payload's "_sources"
    key right after render() returns, rather than left for a test to re-read
    off disk later. pdf_workspace is session-scoped and shared with every
    other test module in this suite -- test_check_access.py and
    test_present_mode.py both render into "document.html"/"deck.html" of
    their own -- so a lazy re-read could race a different module's fixture
    rewriting the same two names. Reading immediately after this module's own
    render() call cannot.
    """
    (pdf_workspace / "document.md").write_text(RUNTIME_DOCUMENT)
    (pdf_workspace / "deck.md").write_text(RUNTIME_DECK)

    doc_built = pipeline.render(
        "doc", "document.md", "document.html", workdir=pdf_workspace
    )
    assert doc_built.returncode == 0, f"pandoc failed on document.md\n{doc_built.stderr}"
    deck_built = pipeline.render(
        "slide", "deck.md", "deck.html", workdir=pdf_workspace
    )
    assert deck_built.returncode == 0, f"pandoc failed on deck.md\n{deck_built.stderr}"

    sources = {
        "doc": (pdf_workspace / "document.html").read_text(),
        "slide": (pdf_workspace / "deck.html").read_text(),
    }

    result = pipeline.run_in_browser(RUNTIME_PROBE, workdir=pdf_workspace, timeout=300)
    line = next(
        (ln for ln in result.stdout.splitlines() if ln.startswith(PROBE_MARKER)), None
    )
    assert line is not None, (
        f"the download probe printed no result (exit {result.returncode})\n"
        f"stdout: {result.stdout[-3000:]}\nstderr: {result.stderr[-3000:]}"
    )
    payload = json.loads(line[len(PROBE_MARKER) :])
    assert "error" not in payload, f"the probe errored inside the image: {payload}"
    payload["_sources"] = sources
    return payload


@pytest.fixture(scope="module")
def download_runtime_doc(download_runtime):
    return download_runtime["doc"]


@pytest.fixture(scope="module")
def download_runtime_slide(download_runtime):
    return download_runtime["slide"]


def downloaded_path(pdf_workspace: Path, kind: str, slot: int, filename: str) -> Path:
    """Where the probe's CDP download behaviour wrote a file.

    /workspace/_download-probe/<kind>-<slot>/<filename> inside the container
    is pdf_workspace/_download-probe/<kind>-<slot>/<filename> on the host,
    since pdf_workspace IS what /workspace is bind-mounted from.
    """
    return pdf_workspace / "_download-probe" / f"{kind}-{slot}" / filename


def parsed_tree(html_text: str) -> str:
    """A stable, parser-normalized form for comparing two HTML documents.

    NOT byte equality -- the corrections from the adversarial review measured
    real serializer drift on the round trip (&#160; -> &nbsp;, &middot; -> a
    literal middot character, <path/> -> <path></path>, a newline immediately
    after <pre> dropped), so a byte comparison is red against a correct
    implementation and would be the same always-red mistake as the substring
    searches this file is written to avoid. Parsing both sides through the
    same parser normalizes that drift away.
    """
    return BeautifulSoup(html_text, "html.parser").prettify()


def _require_attempted_download(download: dict, page_desc: str) -> None:
    assert download.get("attempted"), (
        f"no download was attempted on the {page_desc} because no element "
        "matching .download-button was found -- expected until stn-bd3.4 "
        "lands the control"
    )
    assert "error" not in download, (
        f"the download probe for the {page_desc} errored: {download.get('error')}"
    )


# --- precondition: these are the pages the earlier sections already test ---


def test_the_runtime_fixtures_still_have_teeth(download_runtime):
    """A precondition for every absence assertion in this section.

    The downloaded-bytes tests below are mostly negatives: no rendered
    mermaid <svg>, no built [role="tabpanel"], no data-slide-number. Every
    one of them also passes on a page that never had a diagram, a tabbed
    block or a slide in the first place -- so if these fixtures ever stop
    carrying the constructs the runtime transforms, this whole section goes
    quietly green while testing nothing.

    So assert the SOURCE pandoc wrote still contains what the transformations
    act on. This is deliberately about the built file rather than the live
    DOM: the live DOM is where those constructs have already been replaced,
    which is the entire thing under test.

    (It replaces a red-phase sanity check that asserted the control was
    absent. That one was true only until the feature existed, and would have
    had to be deleted rather than kept -- a test that must be removed to make
    the build pass is a trap for whoever hits it.)
    """
    deck_source = BeautifulSoup(download_runtime["_sources"]["slide"], "html.parser")
    assert deck_source.select(".slide") != [], (
        "the deck fixture rendered no .slide sections, so the "
        "data-slide-number assertions below would pass vacuously"
    )

    doc_source = BeautifulSoup(download_runtime["_sources"]["doc"], "html.parser")
    assert doc_source.select("pre.mermaid, code.language-mermaid") != [], (
        "the document fixture carries no mermaid source, so the "
        "'no rendered <svg>' assertion below would pass vacuously"
    )
    assert doc_source.select("nav.nav-tabs") != [], (
        "the document fixture carries no tabbed block, so the "
        "'no [role=tabpanel]' assertion below would pass vacuously"
    )


# --- deck: toolbar order and the radiogroup boundary ------------------------

EXPECTED_DECK_TOOLBAR_ORDER = [
    "SPAN.deck-position",
    "DIV.theme-toggle",
    "BUTTON.download-button",
    "BUTTON.deck-present",
]


@pytest.mark.integration
def test_deck_toolbar_order_is_position_theme_download_present(download_runtime_slide):
    """stn-ejv's placement rule, read off a real toolbar rather than a diff.

    position, [Light|Dark|System], Download, Present -- in that order, as
    children of .deck-toolbar. Structural (tag + class per child), never a
    substring search: _slide-style.css.j2 defines .deck-toolbar and
    _slide-scripts.html.j2 sets toolbar.className = 'deck-toolbar', so the
    string "deck-toolbar" is already present in every generated page via the
    inlined stylesheet and script, whether or not the download control ever
    mounts.
    """
    assert download_runtime_slide["containerChildren"] == EXPECTED_DECK_TOOLBAR_ORDER, (
        ".deck-toolbar's children are not [position, theme, download, "
        f"present]: got {download_runtime_slide['containerChildren']!r}"
    )


@pytest.mark.integration
def test_deck_download_button_is_not_inside_the_radiogroup(download_runtime_slide):
    """The accessibility requirement stn-ejv states explicitly.

    A plain button inside role=radiogroup reads to a screen reader as one of
    the theme choices. closest('[role="radiogroup"]') must be null.
    """
    assert download_runtime_slide["buttonFound"], (
        "no element matching .download-button was found in the deck toolbar"
    )
    assert download_runtime_slide["insideRadiogroup"] is False, (
        "the deck's download control is inside the theme radiogroup"
    )


# --- document: theme-toggle-host order, radiogroup boundary, naming --------

EXPECTED_DOC_HOST_ORDER = ["DIV.theme-toggle", "BUTTON.download-button"]


@pytest.mark.integration
def test_document_theme_toggle_host_order_is_theme_then_download(download_runtime_doc):
    """stn-ejv: 'a document ... simply follows the theme toggle'."""
    assert download_runtime_doc["containerChildren"] == EXPECTED_DOC_HOST_ORDER, (
        ".theme-toggle-host's children are not [theme, download]: "
        f"got {download_runtime_doc['containerChildren']!r}"
    )


@pytest.mark.integration
def test_document_download_button_is_not_inside_the_radiogroup(download_runtime_doc):
    assert download_runtime_doc["buttonFound"], (
        "no element matching .download-button was found in .theme-toggle-host"
    )
    assert download_runtime_doc["insideRadiogroup"] is False, (
        "the document's download control is inside the theme radiogroup"
    )


@pytest.mark.integration
def test_document_download_button_has_an_accessible_name(download_runtime_doc):
    """A decorative glyph plus a visually-hidden label, per _theme-toggle's
    own pattern -- announced as "Download", not as a character nobody can
    pronounce."""
    assert download_runtime_doc["buttonFound"], "no .download-button to name"
    assert download_runtime_doc["accessibleName"] == "Download", (
        "expected the button's non-aria-hidden descendant text to read "
        f"'Download', got {download_runtime_doc['accessibleName']!r}"
    )


# --- the filename: deck.html, never deck.html.html --------------------------


@pytest.mark.integration
def test_deck_download_is_named_deck_html(download_runtime_slide):
    """The suffix check in stn-bd3.4 must not double-append.

    Measured against real Chrome: a downloaded file's name comes from the
    page's own location.pathname, so a naive indexOf('.html') !== -1 guard
    (the only kind of check writable without a `$` end-anchor, since these
    templates cannot contain one) would still append and produce
    "deck.html.html".
    """
    download = download_runtime_slide["download"]
    _require_attempted_download(download, "deck")
    assert download.get("filename") == "deck.html", (
        f"expected the download named deck.html, got {download.get('filename')!r}"
    )


@pytest.mark.integration
def test_document_download_is_named_document_html(download_runtime_doc):
    download = download_runtime_doc["download"]
    _require_attempted_download(download, "document")
    assert download.get("filename") == "document.html", (
        f"expected the download named document.html, got {download.get('filename')!r}"
    )


# --- positive #1: the downloaded bytes are the file pandoc wrote -----------


@pytest.mark.integration
def test_deck_downloaded_bytes_match_the_parsed_source(
    download_runtime_slide, pdf_workspace, download_runtime
):
    download = download_runtime_slide["download"]
    _require_attempted_download(download, "deck")
    path = downloaded_path(pdf_workspace, "slide", 1, download["filename"])
    assert path.exists(), f"{path} was never written"
    assert parsed_tree(path.read_text()) == parsed_tree(
        download_runtime["_sources"]["slide"]
    ), (
        "the downloaded deck's parsed tree does not match the file pandoc "
        "wrote -- it captured the live, JS-mutated DOM instead"
    )


@pytest.mark.integration
def test_document_downloaded_bytes_match_the_parsed_source(
    download_runtime_doc, pdf_workspace, download_runtime
):
    download = download_runtime_doc["download"]
    _require_attempted_download(download, "document")
    path = downloaded_path(pdf_workspace, "doc", 1, download["filename"])
    assert path.exists(), f"{path} was never written"
    assert parsed_tree(path.read_text()) == parsed_tree(
        download_runtime["_sources"]["doc"]
    ), (
        "the downloaded document's parsed tree does not match the file "
        "pandoc wrote -- it captured the live, JS-mutated DOM instead"
    )


# --- positive #2: the downloaded copy loads clean ---------------------------


@pytest.mark.integration
def test_deck_downloaded_copy_loads_with_no_page_errors(download_runtime_slide):
    """The only real check on the 'nothing may throw' constraint.

    html-to-pdf.js treats an uncaught page error as a failed build, so a
    downloaded copy that throws would be silently broken for exactly the
    workflow this repository cares most about.
    """
    download = download_runtime_slide["download"]
    _require_attempted_download(download, "deck")
    assert download.get("loadPageErrors") == [], (
        f"the downloaded deck threw on load: {download.get('loadPageErrors')}"
    )
    assert download.get("loadedMermaidReady") is True, (
        "the downloaded deck never reported window.__mermaidReady"
    )


@pytest.mark.integration
def test_document_downloaded_copy_loads_with_no_page_errors(download_runtime_doc):
    download = download_runtime_doc["download"]
    _require_attempted_download(download, "document")
    assert download.get("loadPageErrors") == [], (
        f"the downloaded document threw on load: {download.get('loadPageErrors')}"
    )
    assert download.get("loadedMermaidReady") is True, (
        "the downloaded document never reported window.__mermaidReady"
    )


# --- negatives, each paired with the presence that proves the page is real -

# stn-bd3's own trap, restated by the adversarial review: an empty page also
# satisfies "no rendered SVG" and "no built tab panes". Every negative below
# is paired with the positive that proves the corresponding SOURCE content
# actually survived the round trip.


@pytest.mark.integration
def test_deck_downloaded_bytes_carry_no_injected_runtime_chrome(
    download_runtime_slide, pdf_workspace
):
    download = download_runtime_slide["download"]
    _require_attempted_download(download, "deck")
    soup = BeautifulSoup(
        downloaded_path(pdf_workspace, "slide", 1, download["filename"]).read_text(),
        "html.parser",
    )
    # Presence first: an empty document would also pass every negative below.
    assert soup.select(".slide") != [], (
        "the downloaded deck has no .slide elements at all"
    )
    assert soup.select(".deck-toolbar") == [], (
        "the downloaded deck carries a second .deck-toolbar -- it captured "
        "the live, JS-mutated DOM rather than the file pandoc wrote"
    )
    assert soup.select(".theme-toggle") == [], (
        "the downloaded deck carries a mounted .theme-toggle radiogroup -- "
        "in a deck this is built entirely at runtime and appended to "
        "document.body, so its presence means the live DOM was captured"
    )
    assert soup.select("[data-slide-number]") == [], (
        "the downloaded deck carries data-slide-number, which "
        "_slide-scripts.html.j2 stamps onto every .slide at runtime"
    )
    assert soup.html.get("data-theme") is None, (
        "the downloaded deck froze a data-theme onto <html>"
    )
    assert soup.html.get("data-theme-pref") is None, (
        "the downloaded deck froze a data-theme-pref onto <html>"
    )
    assert "presenting" not in (soup.html.get("class") or []), (
        "the downloaded deck carries the presenting class on <html>"
    )


@pytest.mark.integration
def test_document_downloaded_bytes_carry_no_injected_runtime_chrome(
    download_runtime_doc, pdf_workspace
):
    download = download_runtime_doc["download"]
    _require_attempted_download(download, "document")
    soup = BeautifulSoup(
        downloaded_path(pdf_workspace, "doc", 1, download["filename"]).read_text(),
        "html.parser",
    )
    # Mermaid: the source block survives; the rendered SVG does not.
    assert soup.select("pre.mermaid") != [], (
        "the downloaded document has no pre.mermaid source block at all"
    )
    assert soup.select(".mermaid svg") == [], (
        "the downloaded document carries a rendered mermaid <svg> -- it "
        "captured the live DOM after mermaid.run(), not the file pandoc "
        "wrote"
    )
    assert soup.select(".mermaidTooltip") == [], (
        "the downloaded document carries mermaid's tooltip div, which "
        "mermaid.js appends directly to <body> on every page that draws a "
        "diagram"
    )
    # Tabs: the nav survives; the built Bootstrap tab panes do not.
    assert soup.select("nav.nav-tabs") != [], (
        "the downloaded document has no nav.nav-tabs at all"
    )
    assert soup.select('[role="tabpanel"]') == [], (
        "the downloaded document carries built tab panes -- "
        "_page-scripts.html.j2 only creates role=tabpanel elements after "
        "moving pane content into them at runtime"
    )
    # Theme control: the empty host survives; anything mounted into it does
    # not, because show_download's own control mounts into the same host.
    assert soup.select(".theme-toggle-host") != [], (
        "the downloaded document has no .theme-toggle-host mount point at "
        "all"
    )
    assert soup.select(".theme-toggle-host > *") == [], (
        "the downloaded document's .theme-toggle-host is not empty -- it "
        "captured the live DOM after the theme toggle and download control "
        "mounted into it"
    )
    assert soup.html.get("data-theme") is None, (
        "the downloaded document froze a data-theme onto <html>"
    )
    assert soup.html.get("data-theme-pref") is None, (
        "the downloaded document froze a data-theme-pref onto <html>"
    )
    assert "presenting" not in (soup.html.get("class") or []), (
        "the downloaded document carries the presenting class on <html>"
    )


# --- positive #3: downloading FROM the downloaded copy does not compound ---


@pytest.mark.integration
def test_downloading_from_the_downloaded_deck_does_not_compound(
    download_runtime_slide, pdf_workspace
):
    """The ticket asks for this explicitly: a second download from the first
    downloaded copy must equal the first, not carry a second layer of
    stripped-and-reapplied chrome."""
    download = download_runtime_slide["download"]
    _require_attempted_download(download, "deck")
    assert download.get("buttonFoundOnDownload"), (
        "the downloaded deck has no .download-button of its own, so "
        "re-downloading from it is not even possible yet"
    )
    assert download.get("secondFilename") == download.get("filename"), (
        "downloading from the downloaded copy produced a different filename: "
        f"{download.get('secondFilename')!r} vs {download.get('filename')!r}"
    )
    first = downloaded_path(pdf_workspace, "slide", 1, download["filename"])
    second = downloaded_path(pdf_workspace, "slide", 2, download["secondFilename"])
    assert second.exists(), f"{second} was never written"
    assert parsed_tree(first.read_text()) == parsed_tree(second.read_text()), (
        "downloading from the downloaded deck produced different bytes -- "
        "the operation compounds"
    )


@pytest.mark.integration
def test_downloading_from_the_downloaded_document_does_not_compound(
    download_runtime_doc, pdf_workspace
):
    download = download_runtime_doc["download"]
    _require_attempted_download(download, "document")
    assert download.get("buttonFoundOnDownload"), (
        "the downloaded document has no .download-button of its own, so "
        "re-downloading from it is not even possible yet"
    )
    assert download.get("secondFilename") == download.get("filename"), (
        "downloading from the downloaded copy produced a different filename: "
        f"{download.get('secondFilename')!r} vs {download.get('filename')!r}"
    )
    first = downloaded_path(pdf_workspace, "doc", 1, download["filename"])
    second = downloaded_path(pdf_workspace, "doc", 2, download["secondFilename"])
    assert second.exists(), f"{second} was never written"
    assert parsed_tree(first.read_text()) == parsed_tree(second.read_text()), (
        "downloading from the downloaded document produced different bytes "
        "-- the operation compounds"
    )


# --- standing measurements, not gated on the feature existing --------------
#
# Both of these are expected to pass ALREADY, before stn-bd3.4 lands anything.
# They are not evidence the feature works -- see this file's own note above
# about the false-ish coercion cases passing "for the wrong reason" today --
# they are the numbers stn-bd3.4's design leans on, pinned here so a future
# regression (a mermaid or bootstrap bump that starts touching <head>, or a
# page that starts throwing before anything downloads) is caught by name.


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["doc", "slide"])
def test_head_is_unchanged_between_load_and_mermaid_ready(download_runtime, kind):
    """Justifies capturing document.head.innerHTML at parse time in
    stn-bd3.4: if this ever goes False, that capture point needs to move
    earlier, and this is the test that will say so with a number."""
    assert download_runtime[kind]["headMatchesAfterMermaidReady"] is True, (
        f"the {kind} page's <head> changed between load and __mermaidReady -- "
        "a parse-time capture of it would be stale by the time a reader "
        "could click download"
    )


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["doc", "slide"])
def test_no_page_errors_on_the_original_page_load(download_runtime, kind):
    """The standing check on html-to-pdf.js's 'nothing may throw' constraint,
    on the page as it ships today -- independent of whether a download ever
    happens."""
    assert download_runtime[kind]["pageErrors"] == [], (
        f"the {kind} page threw on its original load: "
        f"{download_runtime[kind]['pageErrors']}"
    )
