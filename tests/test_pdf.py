"""What the browser has to do before a PDF is worth keeping.

stn-rxn.9, .10 and .11. All three share a failure shape: the HTML is perfect and
only the PDF is wrong, so nothing in the build or the page tells you. They need
the container, so they are marked integration.
"""

from __future__ import annotations

import re
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


# stn-40n --------------------------------------------------------------------

HEADER = """---
title: "Kanban Board Simulation"
subtitle: "Flow and limits"
author:
  - Ada Lovelace
  - Grace Hopper
date: 2026-09-05
due: 2026-09-12T23:59
points: 50
program: "CS 425"
section: "001"
term: "Fall 2026"
---

## Body

Some prose.
"""


def spaced(text: str) -> str:
    """Extracted PDF text with horizontal whitespace collapsed -- newlines kept.

    The header's gaps are non-breaking characters on purpose (see the comment
    in _doc-body.html.j2), and pypdf also synthesizes an ordinary space from a
    wide enough horizontal jump, so runs of both have to collapse before the
    header can be compared to anything.

    Newlines deliberately do NOT collapse. ``" ".join(text.split())`` was the
    first version of this and it is unsound for the thing being asserted:
    pypdf inserts line breaks from layout heuristics, so if Chromium emitted
    "Issued" and "Sep 05" as two positioned runs with no space glyph between
    them and pypdf broke the line there, collapsing newlines would manufacture
    the very space this file exists to prove is present. Keeping the newline
    means the positive assertions below demand a real space character and fail
    loudly if a pypdf upgrade starts breaking lines differently -- which is the
    right direction to fail in.
    """
    return re.sub(r"[ \t\u00a0]+", " ", text)


@pytest.fixture(scope="module")
def header_pdf(to_pdf):
    result, pdf = to_pdf("doc", "header.md", text=HEADER)
    assert result.returncode == 0, result.stderr
    return PdfReader(pdf)


@pytest.fixture(scope="module")
def header_deck_pdf(to_pdf):
    result, pdf = to_pdf("slide", "headerdeck.md", text=HEADER)
    assert result.returncode == 0, result.stderr
    return PdfReader(pdf)


# Every jam measured on the 0.12.0 build, verbatim. The positive assertions
# below are the real guard -- they demand a literal space character in a
# newline-preserving normalization, so a missing space cannot pass. These make
# the failure legible: "IssuedSep is jammed" names the gap that went, where a
# diff of two long header lines does not.
DOC_JAMS = ("AuthorAda", "IssuedSep", "DueSep", "Points50", "Lovelace·", "001·")
DECK_JAMS = ("IssuedSep", "DueSep", "Points50", "Lovelace·", "001·")


def header_line(text: str, needle: str) -> str:
    """The one extracted line containing `needle`, for a legible failure.

    Passing the whole page as the assertion message buries a one-word finding
    in a wall of prose.
    """
    for line in text.splitlines():
        if needle in line:
            return line
    return text[:300]


def test_the_document_pdf_header_extracts_as_words(header_pdf):
    """Every gap in the printed header is a character, not only a painted space.

    The HTML has had real spaces here since 0.11.0 and
    test_dates.py::test_the_header_extracts_as_words_not_a_run_on holds them
    there. They still did not reach the PDF: a whitespace-only text node
    between two inline boxes is dropped from the text layer, so a reader
    copying from the PDF, or a screen reader reading it, got "IssuedSep 05".
    Nothing in the HTML or in the build could show that.
    """
    text = spaced(header_pdf.pages[0].extract_text())

    assert "Author Ada Lovelace · Grace Hopper" in text, header_line(text, "Author")
    assert "Issued Sep 05 · Due Sep 12 · 23:59 · Points 50 pts" in text, (
        header_line(text, "Issued")
    )
    assert "CS 425.001 · Fall 2026" in text, header_line(text, "425")

    # The identity and context blocks sit in two grid columns on one baseline,
    # separated by column-gap: 1.5rem -- painted space with no character behind
    # it, the same shape as the defect above. Nothing was changed there: they
    # stay apart in the text layer because they are far apart in the content
    # stream, not because a character separates them. Asserted so that if that
    # ever stops being true it is a test failure rather than a printout.
    assert "SimulationCS 425.001" not in text, header_line(text, "Simulation")

    for jam in DOC_JAMS:
        assert jam not in text, (
            f"{jam!r} is jammed in the PDF text layer: {header_line(text, jam)}"
        )


def test_the_deck_pdf_header_extracts_as_words(header_deck_pdf):
    """The generated title slide, which has the same gaps in different markup."""
    text = spaced(header_deck_pdf.pages[0].extract_text())

    assert "Ada Lovelace · Grace Hopper" in text, header_line(text, "Lovelace")
    assert "Issued Sep 05 · Due Sep 12 · 23:59 · Points 50 pts" in text, (
        header_line(text, "Issued")
    )
    assert "CS 425.001 · FALL 2026" in text, header_line(text, "425")
    for jam in DECK_JAMS:
        assert jam not in text, (
            f"{jam!r} is jammed in the PDF text layer: {header_line(text, jam)}"
        )


def catalog(reader: PdfReader) -> dict:
    """The PDF's document catalog, indirect references resolved."""
    return reader.trailer["/Root"].get_object()


def is_true(value) -> bool:
    """Whether a PDF boolean is true.

    pypdf hands back a ``BooleanObject``, not a ``bool``. It reprs as ``True``
    and it is truthy, so ``assert value is True`` fails against a document that
    genuinely says true and prints ``assert True is True`` while doing it. The
    inverse is the dangerous one: ``assert value`` passes for *any* present
    object, including ``BooleanObject(False)``, which would make this whole file
    assert nothing.
    """
    return getattr(value, "value", value) is True


def test_the_pdf_is_tagged(header_pdf):
    """A tagged PDF is the whole basis of its reading order and heading structure.

    Nothing in this repository looked at a PDF before stn-40n: `make
    check-access` is pa11y, an HTML checker. The tagging measured when the
    ticket was filed was Puppeteer's default rather than anything asserted
    here, so a version bump could have turned every generated handout into an
    untagged one and no check would have noticed. html-to-pdf.js now pins
    `tagged: true`; this is what makes the pin mean something.
    """
    root = catalog(header_pdf)

    assert "/StructTreeRoot" in root, (
        "no structure tree -- the PDF is untagged, so it carries no heading "
        "structure or reading order"
    )
    mark_info = root.get("/MarkInfo")
    assert mark_info is not None, "no /MarkInfo, so nothing declares the PDF marked"
    assert is_true(mark_info.get_object().get("/Marked")), (
        f"/MarkInfo does not declare /Marked true: {mark_info.get_object()}"
    )


def test_the_pdf_declares_its_language(header_pdf):
    """WCAG 3.1.1, carried from <html lang> into the catalog.

    html-template.html.j2 never omits the attribute; this is the check that it
    survives the print.
    """
    assert catalog(header_pdf).get("/Lang") == "en"


def test_the_pdf_shows_its_title_rather_than_its_filename(header_pdf):
    """PDF/UA requires DisplayDocTitle, and a reader with it unset announces the
    file name -- "header.pdf" -- instead of the document's own title.
    """
    prefs = catalog(header_pdf).get("/ViewerPreferences")
    assert prefs is not None, "no /ViewerPreferences, so DisplayDocTitle is unset"
    assert is_true(prefs.get_object().get("/DisplayDocTitle")), (
        f"/DisplayDocTitle is not true: {prefs.get_object()}"
    )
    # The program prefix comes from the front matter, and a reader announcing
    # the document says the whole thing.
    assert header_pdf.metadata.title == "CS 425: Kanban Board Simulation"
