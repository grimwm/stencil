"""What the browser has to do before a PDF is worth keeping.

stn-rxn.9, .10 and .11. All three share a failure shape: the HTML is perfect and
only the PDF is wrong, so nothing in the build or the page tells you. They need
the container, so they are marked integration.
"""

from __future__ import annotations

import time

import pytest
from pypdf import PdfReader

from stencil import pipeline

pytestmark = pytest.mark.integration

LETTER_PORTRAIT = (612, 792)
LETTER_LANDSCAPE = (792, 612)


def page_sizes(path) -> list[tuple[int, int]]:
    """Each page's MediaBox, rounded to whole points."""
    return [
        (round(page.mediabox.width), round(page.mediabox.height))
        for page in PdfReader(path).pages
    ]


def text_of(path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


# stn-rxn.9 ------------------------------------------------------------------


def test_a_document_prints_letter_portrait(to_pdf):
    """preferCSSPageSize makes @page in _page-style.css.j2 the page geometry.

    Not a hint the browser may override. Drop preferCSSPageSize and Chromium
    falls back to its own default, silently repaginating every handout.
    """
    result, pdf = to_pdf("doc", "document.md")
    assert result.returncode == 0, result.stderr

    sizes = set(page_sizes(pdf))
    assert sizes == {LETTER_PORTRAIT}, (
        f"expected every page letter portrait, got {sizes}"
    )


def test_a_deck_prints_letter_landscape_one_slide_per_page(to_pdf, render_soup):
    """A deck that repaginates folds two slides onto a page or splits one.

    Neither shows up in the HTML, and both are obvious only to whoever prints
    the deck -- usually minutes before presenting it.
    """
    result, pdf = to_pdf("slide", "deck.md")
    assert result.returncode == 0, result.stderr

    sizes = page_sizes(pdf)
    assert set(sizes) == {LETTER_LANDSCAPE}, (
        f"expected every page letter landscape, got {set(sizes)}"
    )

    slides = len(render_soup("slide", "deck.md").select(".slide"))
    assert len(sizes) == slides, (
        f"{slides} slides became {len(sizes)} pages -- the deck repaginated"
    )


# stn-rxn.10 -----------------------------------------------------------------

CLIENT_RENDERED = """---
title: "Rendering"
---

<nav class="nav-tabs">
  <a href="#problem-1">Problem 1</a>
  <a href="#problem-2">Problem 2</a>
</nav>

## Problem 1

The first pane says ALPHAPANE.

```{.mermaid caption="Requests flow from the client through the API to the database"}
flowchart LR
  client --> api --> db
```

## Problem 2

The second pane says BETAPANE.
"""


@pytest.fixture(scope="module")
def client_rendered_pdf(to_pdf):
    result, pdf = to_pdf("doc", "rendering.md", text=CLIENT_RENDERED)
    assert result.returncode == 0, result.stderr
    return text_of(pdf)


def test_mermaid_is_rendered_before_printing(client_rendered_pdf):
    """The diagram must reach the PDF as a diagram, not as its source.

    This asserts the outcome. It does not, on its own, prove the
    __mermaidReady wait is doing the work: removing the wait still passes here,
    because networkidle0 happens to leave mermaid enough time on an idle
    machine. The race it protects against is real but not reproducible on
    demand, so the wait's presence is asserted directly in test_pipeline.py.
    """
    text = client_rendered_pdf

    assert "flowchart LR" not in text, (
        "the PDF captured raw mermaid source; it printed before the diagram "
        "rendered"
    )
    for label in ("client", "api", "db"):
        assert label in text, f"the rendered diagram is missing the node {label!r}"


def test_every_tab_pane_is_printed(client_rendered_pdf):
    """_page-scripts.html.j2 assembles the panes only after mermaid-ready.

    Print before that and the tab content never got moved into its pane. The
    print stylesheet shows every pane and hides the bar, so a printed copy is
    supposed to be complete.
    """
    text = client_rendered_pdf

    assert "ALPHAPANE" in text
    assert "BETAPANE" in text, (
        "only the first tab reached the PDF -- the panes had not been "
        "assembled when the page was printed"
    )


# stn-rxn.11 -----------------------------------------------------------------

UNRESOLVABLE = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Broken</title>
    <script src="https://cdn.invalid.example/nonexistent.js"></script>
  </head>
  <body><p>A page whose assets did not load.</p></body>
</html>
"""


@pytest.fixture
def unresolvable_asset(pdf_workspace):
    (pdf_workspace / "broken.html").write_text(UNRESOLVABLE)
    return pdf_workspace


def test_a_failed_asset_refuses_to_write_a_pdf(unresolvable_asset):
    """A dropped request would otherwise degrade silently.

    The generated pages are self-contained -- Bootstrap, highlight.js, Mermaid
    and the webfonts are inlined -- so a normal handout never makes a network
    request. html-to-pdf.js still refuses to write when *any* request fails,
    which catches a page that has been edited to fetch something that is not
    there: without the check it would produce a finished-looking PDF missing
    whatever that fetch was supposed to provide.
    """
    result = pipeline.html_to_pdf(
        "broken.html", "broken.pdf", workdir=unresolvable_asset, timeout=300
    )

    assert result.returncode != 0, "a page with a dead asset produced a PDF"
    assert not (unresolvable_asset / "broken.pdf").exists(), (
        "a refused conversion still left a PDF on disk"
    )
    assert "cdn.invalid.example" in result.stderr, (
        f"the failed URL should be named on stderr, got:\n{result.stderr}"
    )


def test_the_failure_is_reported_without_waiting_out_the_timeout(
    unresolvable_asset,
):
    """The early check exists because a dead fetch can stall the ready flag.

    A page that never sets __mermaidReady would otherwise sit for the full 120s
    timeout before reporting anything, which reads as a hang rather than as a
    failed asset. The check right after load is easy to lose in a refactor, and
    losing it shows up only as a slow, confusing failure -- which is exactly
    the kind of thing nobody files a bug about.
    """
    started = time.monotonic()
    result = pipeline.html_to_pdf(
        "broken.html", "broken.pdf", workdir=unresolvable_asset, timeout=300
    )
    elapsed = time.monotonic() - started

    assert result.returncode != 0
    assert elapsed < 60, (
        f"took {elapsed:.0f}s to report a dead asset -- the fail-fast check "
        "after page load is gone, so this waited on the 120s ready-flag timeout"
    )
