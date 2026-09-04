"""The document header across every front-matter combination.

stn-rxn.6. _doc-body.html.j2 branches on title, subtitle, author and date, and
joins author names with a middot. The interesting cases are the empty ones: a
separator with nothing on one side of it, or a header rendered for a document
that never asked for one.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

MIDDOT = "\u00b7"


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def header_of(soup):
    return soup.select_one("header.doc-title")


def meta_text(soup) -> str:
    """The byline as one whitespace-normalized line.

    Pandoc hard-wraps its output, so a long author list arrives with a newline
    in the middle of it. That is formatting, not content.
    """
    header = header_of(soup)
    assert header is not None, "expected a document header"
    metas = header.select(".doc-meta")
    return " ".join(" ".join(m.get_text().split()) for m in metas)


def test_author_and_date(render_soup):
    soup = render_soup(
        "doc",
        "byline.md",
        text=document('title: "T"\nauthor: Ada Lovelace\ndate: 2026-09-02\n'),
    )
    assert meta_text(soup) == f"Ada Lovelace {MIDDOT} 2026-09-02"


def test_an_author_list_is_joined_with_middots(render_soup):
    soup = render_soup(
        "doc",
        "byline.md",
        text=document(
            'title: "T"\nauthor:\n  - Ada Lovelace\n  - Grace Hopper\n'
            "date: 2026-09-02\n"
        ),
    )
    assert meta_text(soup) == (
        f"Ada Lovelace {MIDDOT} Grace Hopper {MIDDOT} 2026-09-02"
    )


def test_author_only_has_no_trailing_separator(render_soup):
    """The date's middot is inside the date branch, so it must not appear."""
    soup = render_soup(
        "doc", "byline.md", text=document('title: "T"\nauthor: Ada Lovelace\n')
    )
    assert meta_text(soup) == "Ada Lovelace"
    assert MIDDOT not in meta_text(soup)


def test_date_only_renders_on_its_own(render_soup):
    soup = render_soup(
        "doc", "byline.md", text=document('title: "T"\ndate: 2026-09-02\n')
    )
    assert meta_text(soup) == "2026-09-02"
    assert MIDDOT not in meta_text(soup)


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
