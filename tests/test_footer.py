"""The printed footer a document asks for with `footer:`.

Odd pages put the title on the left and the page number on the right. Even
pages swap them. `pageno` can also sit in the center, and it can name the
total. A deck has no page margin of this kind, so the key does nothing there.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def footer_css(soup) -> str:
    tag = soup.select_one("style.doc-footer")
    assert tag is not None, "expected a footer stylesheet"
    return tag.string or ""


def test_an_absent_footer_adds_no_stylesheet(render_soup):
    soup = render_soup("doc", "none.md", text=document('title: "Midterm"\n'))
    assert soup.select_one("style.doc-footer") is None


def test_an_empty_footer_adds_no_stylesheet(render_soup):
    soup = render_soup("doc", "empty.md", text=document('title: "Midterm"\nfooter:\n'))
    assert soup.select_one("style.doc-footer") is None


def test_title_and_pageno_swap_sides_between_odd_and_even(render_soup):
    soup = render_soup(
        "doc",
        "both.md",
        text=document('title: "Midterm"\nfooter:\n  - pageno\n  - title\n'),
    )
    css = footer_css(soup)
    assert '@page :right{@bottom-left{content:"Midterm";' in css
    assert "@page :right{@bottom-right{content:counter(page);" in css
    assert "@page :left{@bottom-right{content:" in css and '"Midterm"' in css
    assert "@page :left{@bottom-left{content:counter(page);" in css
    assert "counter(pages)" not in css
    assert "@bottom-center" not in css


def test_a_title_string_replaces_the_document_title(render_soup):
    soup = render_soup(
        "doc",
        "rename.md",
        text=document(
            'title: "Midterm"\nfooter:\n  - title: "CS 234 Midterm"\n'
        ),
    )
    css = footer_css(soup)
    assert '"CS 234 Midterm"' in css
    assert '"Midterm"' not in css
    assert "counter(page)" not in css


def test_center_and_of_combine(render_soup):
    soup = render_soup(
        "doc",
        "combo.md",
        text=document('title: "Midterm"\nfooter:\n  - pageno: [center, of]\n'),
    )
    css = footer_css(soup)
    assert '@page{@bottom-center{content:counter(page) " of " counter(pages);' in css
    assert "@bottom-left" not in css
    assert "@bottom-right" not in css


def test_center_alone_keeps_a_plain_number(render_soup):
    soup = render_soup(
        "doc",
        "center.md",
        text=document('title: "Midterm"\nfooter:\n  - pageno: center\n'),
    )
    css = footer_css(soup)
    assert "@page{@bottom-center{content:counter(page);" in css
    assert "counter(pages)" not in css


def test_of_alone_stays_on_the_outside(render_soup):
    soup = render_soup(
        "doc",
        "of.md",
        text=document('title: "Midterm"\nfooter:\n  - pageno: of\n'),
    )
    css = footer_css(soup)
    assert '@page :right{@bottom-right{content:counter(page) " of " counter(pages);' in css
    assert "@page :left{@bottom-left{content:counter(page)" in css
    assert "@bottom-center" not in css


def test_a_deck_ignores_footer(render_soup):
    soup = render_soup(
        "slide",
        "deck.md",
        text=document('title: "Midterm"\nfooter:\n  - pageno\n  - title\n'),
    )
    assert soup.select_one("style.doc-footer") is None


def test_an_unknown_pageno_option_fails(render):
    result, output = render(
        "doc",
        "bad.md",
        text=document('title: "Midterm"\nfooter:\n  - pageno: left\n'),
        output="bad.html",
    )
    assert result.returncode != 0, result.stderr
    assert "not a page-number option" in result.stderr
    assert not output.exists()


def test_a_title_with_no_document_title_fails(render):
    result, _ = render(
        "doc",
        "notitle.md",
        text=document("footer:\n  - title\n"),
        output="notitle.html",
    )
    assert result.returncode != 0, result.stderr
    assert "no title" in result.stderr
