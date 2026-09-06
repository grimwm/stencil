"""The document header across every front-matter combination.

stn-rxn.6. _doc-body.html.j2 branches on title, subtitle, author and date, and
joins author names with a middot. The interesting cases are the empty ones: a
separator with nothing on one side of it, or a header rendered for a document
that never asked for one.

The byline is two columns now, not one line: authors sit left in .doc-meta and
the date is pushed right in .doc-date, so the middot that used to join them is
gone and the two are read separately below. What did not change is the rule
those assertions exist for -- an absent half leaves no trace of itself, and no
combination produces a separator with nothing on one side.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

MIDDOT = "\u00b7"


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def header_of(soup):
    return soup.select_one("header.doc-title")


def _text(soup, selector: str) -> str | None:
    """The whitespace-normalized text of one header element, or None if absent.

    Pandoc hard-wraps its output, so a long author list arrives with a newline
    in the middle of it. That is formatting, not content.

    None rather than "" on purpose: these tests are mostly about the difference
    between an element that is empty and an element that was never emitted, and
    a helper that flattens the two hides exactly what is being asked.
    """
    header = header_of(soup)
    assert header is not None, "expected a document header"
    found = header.select(selector)
    if not found:
        return None
    return " ".join(" ".join(f.get_text().split()) for f in found)


def authors(soup) -> str | None:
    return _text(soup, ".doc-meta")


def when(soup) -> str | None:
    """The Issued line, which is what `date:` renders as.

    Reads .doc-issued rather than .doc-date: the latter is now the wrapper
    holding both Issued and Due, and its text would be a concatenation of the
    two rather than either one.
    """
    return _text(soup, ".doc-issued")


def test_author_and_date(render_soup):
    soup = render_soup(
        "doc",
        "byline.md",
        text=document('title: "T"\nauthor: Ada Lovelace\ndate: 2026-09-02\n'),
    )
    assert authors(soup) == "Author Ada Lovelace"
    assert when(soup) == "Issued Sep 02"


def test_an_author_list_is_joined_with_middots(render_soup):
    soup = render_soup(
        "doc",
        "byline.md",
        text=document(
            'title: "T"\nauthor:\n  - Ada Lovelace\n  - Grace Hopper\n'
            "date: 2026-09-02\n"
        ),
    )
    assert authors(soup) == f"Author Ada Lovelace {MIDDOT} Grace Hopper"
    assert when(soup) == "Issued Sep 02"


def test_author_only_emits_no_date_element(render_soup):
    """A missing date leaves no empty right-hand column behind."""
    soup = render_soup(
        "doc", "byline.md", text=document('title: "T"\nauthor: Ada Lovelace\n')
    )
    assert authors(soup) == "Author Ada Lovelace"
    assert MIDDOT not in authors(soup)
    assert when(soup) is None


def test_date_only_renders_on_its_own(render_soup):
    """And still right-aligned: .doc-date carries its own margin-left, so it
    does not slide left just because nothing shares the row with it."""
    soup = render_soup(
        "doc", "byline.md", text=document('title: "T"\ndate: 2026-09-02\n')
    )
    assert authors(soup) is None
    assert when(soup) == "Issued Sep 02"


def test_neither_renders_exactly_as_if_the_keys_were_absent(render):
    """A guard. An empty author must produce no byline, not an empty one.

    Written as a byte comparison rather than an assertion about the markup
    because the failure mode is a stray element or separator, and enumerating
    the ways that could look is how you miss one.
    """
    absent, absent_path = render(
        "doc", "absent.md", text=document('title: "T"\n'), output="absent.html"
    )
    empty, empty_path = render(
        "doc",
        "empty.md",
        text=document('title: "T"\nauthor:\ndate:\n'),
        output="empty.html",
    )

    assert absent.returncode == 0, absent.stderr
    assert empty.returncode == 0, empty.stderr
    assert empty_path.read_text() == absent_path.read_text(), (
        "an empty author/date rendered differently from omitting the keys"
    )


def test_no_title_means_no_header_at_all(render_soup):
    """Documented in AUTHORING.md: the byline is part of the title header.

    An author with no title must not smuggle a header into the page.
    """
    soup = render_soup(
        "doc",
        "byline.md",
        text=document("author: Ada Lovelace\ndate: 2026-09-02\n"),
    )
    assert header_of(soup) is None, (
        "a document with no title rendered a header anyway"
    )
    assert "Ada Lovelace" not in soup.get_text()


def test_the_author_precedes_the_context_in_source_order(render_soup):
    """Visually the byline has always sat under the title. In source order it
    did not -- .doc-context came between them, which is the order a screen
    reader announces and pdftotext extracts.
    """
    soup = render_soup(
        "doc",
        "order.md",
        text=document(
            'title: "T"\nsubtitle: "S"\nauthor: Ada Lovelace\n'
            'program: "CS 425"\nterm: "Fall 2026"\n'
        ),
    )
    header = header_of(soup)
    classes = [
        el["class"][0]
        for el in header.find_all(True, recursive=False)
        if el.get("class")
    ]
    assert classes == ["doc-identity", "doc-byline", "doc-context"]


def test_the_header_row_wrapper_is_gone(render_soup):
    soup = render_soup(
        "doc",
        "order.md",
        text=document('title: "T"\nprogram: "CS 425"\nauthor: Ada Lovelace\n'),
    )
    assert header_of(soup).select_one(".doc-headrow") is None


def test_the_separators_hide_the_middot_without_hiding_the_gap(render_soup):
    """A screen reader must still hear a boundary between two names.

    The author separator is aria-hidden, which removes its whole subtree from
    the accessibility tree. Put the space between two names inside it -- the
    obvious way to make that space reach the PDF text layer -- and the PDF is
    fixed while a screen reader is handed "Ada LovelaceGrace Hopper". That is
    the same defect as the one this release fixes, wearing the other coat, and
    `make check-access` cannot see it: neither pa11y engine has a rule for
    adjacent text with no separating whitespace.

    So this asserts the property the templates actually rely on -- that a
    whitespace character survives with every aria-hidden subtree removed --
    rather than predicting what any particular screen reader announces.
    Measured against Chromium's own accessibility tree while this was written:
    the 0.12.0 markup gave the runs 'Author', 'Ada Lovelace', 'Grace Hopper'
    with no whitespace anywhere between them, and it now gives 'Author ',
    'Ada Lovelace ', 'Grace Hopper '.
    """
    soup = render_soup(
        "doc",
        "a.md",
        text=document(
            'title: "T"\nauthor:\n  - Ada Lovelace\n  - Grace Hopper\n'
            "date: 2026-09-01\n"
        ),
    )
    meta = header_of(soup).select_one(".doc-meta")
    assert meta is not None

    for hidden in meta.select('[aria-hidden="true"]'):
        hidden.decompose()

    # Not normalized: the point is that a whitespace character is present, and
    # " ".join(x.split()) would insert one wherever the DOM has none.
    accessible = meta.get_text()
    assert "LovelaceGrace" not in accessible, (
        "the only gap between two authors lives inside the aria-hidden "
        f"separator, so it is not in the accessibility tree: {accessible!r}"
    )
    assert "AuthorAda" not in accessible, (
        f"the label runs into the first author: {accessible!r}"
    )
