"""The mermaid bundle rides along only on documents that draw a diagram.

stn-b4p. The bundle is 3.5 MB of the 5.19 MB a diagram-free handout used to
weigh, and it was inlined into every page unconditionally. Nothing was wrong
with the output -- V8 does not charge for a script it never runs, and the build
time is identical either way (measured: 1776/1738 ms with, 1756/1762 ms
without). What it cost was the point of the file. embed-images.lua and the
asset inlining exist so a handout is one self-contained file somebody can
email, and a 5 MB attachment that should be 1.6 MB is three times harder to
send.

So these tests are about size and about not breaking the four routes a diagram
can take into a page. The one that would hurt is the last: html-to-pdf.js
blocks on window.__mermaidReady, and a page that never sets it does not fail
fast -- it waits out the full two-minute timeout and then fails. That is why
the driver script stays unconditional while only the bundle is gated, and why
the last test here builds an actual PDF from a diagram-free document rather
than asserting something about the markup.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# A symbol from mermaid.min.js's own preamble, which nothing else in a
# generated page contains. Searching for the word "mermaid" would match the
# driver, the CSS and the filter's own figure captions.
BUNDLE = "__esbuild_esm_mermaid_nm"

# The driver, which must be present whether or not the bundle is: it is what
# sets __mermaidReady.
DRIVER = "window.__mermaidReady = true"

DIAGRAM = "```{.mermaid}\nflowchart LR\n  A --> B\n```\n"


def document(body: str) -> str:
    return f'---\ntitle: "T"\n---\n\n## Body\n\n{body}\n'


def html_of(render, body: str, kind: str = "doc") -> str:
    result, path = render(kind, "bundle.md", text=document(body))
    assert result.returncode == 0, result.stderr
    return path.read_text()


def test_a_document_with_no_diagram_does_not_carry_the_bundle(render):
    page = html_of(render, "Just prose, no diagram.")
    assert BUNDLE not in page
    assert DRIVER in page, (
        "the driver was gated along with the bundle; nothing will set "
        "__mermaidReady and make pdf will wait out its timeout"
    )


def test_a_document_with_a_diagram_still_carries_the_bundle(render):
    page = html_of(render, DIAGRAM)
    assert BUNDLE in page
    assert DRIVER in page


def test_dropping_the_bundle_is_most_of_the_file(render):
    """The whole point, stated as a number rather than a principle."""
    without = len(html_of(render, "Just prose, no diagram."))
    with_it = len(html_of(render, DIAGRAM))
    assert without < with_it / 2, (
        f"a diagram-free page is {without} bytes against {with_it} with a "
        f"diagram; the bundle is 3.5 MB, so the saving should be far larger"
    )


# The three routes to a .mermaid element that never pass through CodeBlock. The
# page script reads `pre code.language-mermaid, pre.mermaid` and then every
# `.mermaid`, so all of these draw a diagram at runtime and all of them need
# the bundle. Detecting only fenced code blocks would render them blank.


def test_a_fenced_div_counts_as_a_diagram(render):
    assert BUNDLE in html_of(render, "::: {.mermaid}\nflowchart LR\n  A --> B\n:::\n")


def test_raw_html_counts_as_a_diagram(render):
    assert BUNDLE in html_of(
        render, '<pre class="mermaid">\nflowchart LR\n  A --> B\n</pre>\n'
    )


def test_a_span_in_a_paragraph_counts_as_a_diagram(render):
    """`<span class="mermaid">` is a Span in the AST, not raw HTML.

    This is the case the obvious reading of the markdown gets wrong. The source
    says `<span`, the rendered page says `<span`, and it is tempting to
    conclude the filter sees raw HTML -- but pandoc's native_spans extension
    parses it into a Span element on the way through, exactly as native_divs
    does for a fenced div, so neither RawInline nor RawBlock is ever called for
    it. Instrumenting the filter is what settled it; reading either end of the
    pipeline would not have.
    """
    assert BUNDLE in html_of(
        render,
        'A diagram <span class="mermaid">flowchart LR; A --> B</span> inline.',
    )


def test_an_unknown_inline_tag_counts_as_a_diagram(render):
    """The case that is genuinely RawInline, and the reason that handler stays.

    pandoc has a native AST element for `<span>` and for `<div>`, so those are
    caught by Span and Div above. A tag it has no element for stays raw, and
    the browser still applies the class -- `.mermaid` matches any element. This
    test was added after the Span fix because removing the RawInline handler
    broke nothing: a guard no test can breach is a guess, whether or not it is
    a correct one.
    """
    assert BUNDLE in html_of(
        render,
        'A <mermaid-chart class="mermaid">flowchart LR; A to B</mermaid-chart> here.',
    )


def test_a_deck_is_gated_the_same_way(render):
    """slide-template.html.j2 includes the same partial, and the filter runs on
    both kinds, but the deck build adds slide-sections.lua after it -- so the
    metadata the flag rides on has to survive a second filter."""
    assert BUNDLE not in html_of(render, "Just prose.", kind="slide")
    assert BUNDLE in html_of(render, DIAGRAM, kind="slide")


def test_a_bundle_free_page_still_converts_to_pdf(to_pdf):
    """The guard against this change's own worst failure.

    Not an assertion about markup: html-to-pdf.js waits for __mermaidReady and
    a page that never sets it burns the full timeout before failing, so the
    only honest test is to convert one. The short timeout is deliberate -- if
    the flag stops being set this fails in seconds instead of minutes, and the
    difference between "hung" and "slow" is the whole finding.
    """
    result, pdf = to_pdf(
        "doc", "nobundle.md", text=document("Just prose, no diagram."), timeout=90
    )
    assert result.returncode == 0, (
        "a page with no mermaid bundle never reported __mermaidReady:\n"
        f"{result.stderr[-2000:]}"
    )
    assert pdf.is_file() and pdf.stat().st_size > 0
