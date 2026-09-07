"""The highlighter rides along only on documents that have code.

stn-uje. highlight.min.js plus the four language packs is 141,445 bytes, and it
was inlined into every page unconditionally. Four of the six handouts in cs425
contain no ``<pre>`` or ``<code>`` at all, including the deck the page-weight
ticket was filed about -- so most generated pages carried a syntax highlighter
that had nothing to highlight.

Same shape as tests/test_mermaid_bundle.py, and the last test here is the same
test for the same reason: gating a library while leaving the call that uses it
is a ReferenceError in a page that otherwise looks finished, and reading the
markup cannot tell you whether the script runs.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# From highlight.min.js's own preamble. Searching for "hljs" would match
# _page-style.css.j2's .hljs-string colour rules, which are stencil's own and
# stay on every page.
BUNDLE = "Highlight.js v11.9.0"

# The call. Gated together with the bundle; on its own it throws.
CALL = "hljs.highlightAll();"

# From highlight-github.min.css, which is gated with them.
THEME = "pre code.hljs{display:block"

CODE = "```python\nprint('hello')\n```\n"
DIAGRAM = "```{.mermaid}\nflowchart LR\n  A --> B\n```\n"


def document(body: str) -> str:
    return f'---\ntitle: "T"\n---\n\n## Body\n\n{body}\n'


def html_of(render, body: str, kind: str = "doc", metadata=None) -> str:
    result, path = render(kind, "bundle.md", text=document(body), metadata=metadata)
    assert result.returncode == 0, result.stderr
    return path.read_text()


def test_a_document_with_no_code_carries_no_highlighter(render):
    page = html_of(render, "Just prose, no listing.")
    assert BUNDLE not in page
    assert THEME not in page
    assert CALL not in page, (
        "the bundle was gated and the call was not; this page throws a "
        "ReferenceError on load"
    )


def test_a_document_with_a_listing_still_carries_it(render):
    page = html_of(render, CODE)
    assert BUNDLE in page
    assert THEME in page
    assert CALL in page


def test_inline_code_alone_does_not_pull_the_bundle(render):
    """`hljs.highlightAll()` highlights `pre code`, and pandoc writes inline
    code as a bare <code> outside any <pre>. Keying on it would set the flag
    for nearly every document ever written and gate nothing."""
    page = html_of(render, "Prose mentioning `a_variable` and `make doc`.")
    assert BUNDLE not in page


def test_raw_html_counts_as_a_listing(render):
    """A hand-written listing never becomes a CodeBlock."""
    assert BUNDLE in html_of(render, "<pre><code>hand written</code></pre>\n")


def test_a_diagram_is_not_a_listing(render):
    """The false positive that would undo the whole change on the decks that
    are largest.

    mermaid-figure-filter.lua wraps its CodeBlock in a Figure -- it does not
    consume it -- so the block survives to the page as
    <pre class="mermaid"><code class="language-mermaid">, which is exactly what
    the mermaid driver looks for. A filter that counted every CodeBlock would
    therefore set has-code on every deck that draws a diagram, and those pages
    would keep the highlighter forever for markup the driver deletes from the
    DOM before a reader sees it.
    """
    page = html_of(render, DIAGRAM)
    assert BUNDLE not in page, (
        "a diagram-only document is carrying the highlighter; the filter is "
        "counting mermaid code blocks as listings"
    )


def test_a_deck_is_gated_the_same_way(render):
    """slide-template.html.j2 includes the same two partials, but the deck
    build adds slide-sections.lua, so the metadata the flag rides on has to
    survive a filter that regroups every block."""
    assert BUNDLE not in html_of(render, "Just prose.", kind="slide")
    assert BUNDLE in html_of(render, CODE, kind="slide")


def test_hidden_code_counts_only_in_the_build_that_shows_it(render):
    """The one assertion that makes the filter's ordering true rather than
    aspirational: it runs after hidden-filter, so a listing inside a
    `::: {.hidden}` div is not on the page unless the build asked for it."""
    body = "Prose.\n\n::: {.hidden}\n" + CODE + ":::\n"
    assert BUNDLE not in html_of(render, body)
    assert BUNDLE in html_of(
        render, body, metadata={"include-hidden": "true"}
    ), "WITH=hidden revealed a listing the highlighter was not shipped for"


def test_dropping_the_highlighter_is_a_measurable_share_of_the_page(render):
    """The point, as a number rather than a principle.

    A ratio rather than a byte count: a hard constant goes red on the next
    re-vendor, and a gate that goes red for the wrong reason is a gate somebody
    switches off.
    """
    without = len(html_of(render, "Just prose, no listing."))
    with_it = len(html_of(render, CODE))
    assert with_it - without > 130_000, (
        f"a code-free page is {without} bytes against {with_it} with a "
        f"listing; the bundle and its four language packs are 141,445 bytes, "
        f"so the gap should be close to that"
    )


def test_a_highlighter_free_page_still_converts_to_pdf(to_pdf):
    """The guard against this change's own worst failure, and the reason this
    file is an integration test rather than a markup assertion.

    If `hljs.highlightAll()` ever outlives the bundle, the page throws on load.
    html-to-pdf.js listens for pageerror and refuses to write, so `make pdf`
    fails -- but `make doc` does not, and it would ship the broken page. The
    only check that runs the script rather than reading it is to convert one.
    """
    result, pdf = to_pdf(
        "doc", "nohljs.md", text=document("Just prose, no listing."), timeout=90
    )
    assert result.returncode == 0, (
        "a page with no highlighter failed to convert; if this is a "
        "ReferenceError for hljs, the call outlived the bundle:\n"
        f"{result.stderr[-2000:]}"
    )
    assert pdf.is_file() and pdf.stat().st_size > 0


def test_a_page_with_a_listing_still_converts_to_pdf(to_pdf):
    """The other half: the bundle still loads and runs where it is wanted."""
    result, pdf = to_pdf("doc", "withhljs.md", text=document(CODE), timeout=90)
    assert result.returncode == 0, result.stderr[-2000:]
    assert pdf.is_file() and pdf.stat().st_size > 0


def test_an_uppercase_raw_listing_counts(render):
    """HTML tag names are case-insensitive and hljs matches `pre code` however
    it was typed, so a hand-written `<PRE><CODE>` is a listing. The filter
    lowercases before searching; without that this renders uncoloured."""
    assert BUNDLE in html_of(render, "<PRE><CODE>hand written</CODE></PRE>\n")
