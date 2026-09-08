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

from pathlib import Path

import pytest

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
