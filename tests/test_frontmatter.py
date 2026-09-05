"""The context keys, and the boolean that is not one.

`program`, `section` and `term` say what a document belongs to, which instance
of it, and when. They render in a right-hand column opposite the title, which
means the header has to know whether *any* of them is set -- a question a
pandoc template cannot ask, so frontmatter-filter.lua answers it as
`has-context`.

`show_date` is the interesting one. Pandoc reads YAML 1.2, where `true` and
`false` are the only booleans, so `show_date: no` arrives as the string "no"
and `$if(show_date)$` fires on it. Every test below that writes a word rather
than a bare `true` exists because that word used to mean its own opposite --
and it failed in the direction that publishes a date the author asked to
withhold, which is the direction you do not get to find out about later.
"""

from __future__ import annotations

import re

import pytest
from pypdf import PdfReader

pytestmark = pytest.mark.integration

MIDDOT = "·"

BUILD_DATE = {"build-date": "2026-03-14"}


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def deck(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## A slide heading\n\nSome prose.\n"


def context_of(soup) -> str | None:
    """The right-hand context column, whitespace-normalized, or None."""
    found = soup.select_one("header.doc-title .doc-context")
    return " ".join(found.get_text().split()) if found else None


def span(scope, selector: str) -> str | None:
    """One context span, whitespace-normalized.

    Pandoc hard-wraps its output and the three spans are emitted with no
    separator between them, so a value reaches the markup as `CS\n425/499`.
    That is formatting, not content -- the same reason test_byline.py
    normalizes before comparing.
    """
    found = scope.select_one(selector)
    return " ".join(found.get_text().split()) if found else None


def date_of(soup) -> str | None:
    found = soup.select_one("header.doc-title .doc-date")
    return " ".join(found.get_text().split()) if found else None


# --- the context column ----------------------------------------------------


def test_all_three_render_in_their_own_spans(render_soup):
    soup = render_soup(
        "doc",
        "ctx.md",
        text=document(
            'title: "T"\nprogram: "CS 425/499"\nsection: "001"\nterm: "Spring 2026"\n'
        ),
    )
    header = soup.select_one("header.doc-title")
    assert span(header, ".doc-program") == "CS 425/499"
    assert span(header, ".doc-section") == "001"
    assert span(header, ".doc-term") == "Spring 2026"


@pytest.mark.parametrize(
    "front_matter",
    [
        'program: "CS 425/499"\n',
        'section: "001"\n',
        'term: "Spring 2026"\n',
        'program: "CS 425/499"\nterm: "Spring 2026"\n',
    ],
    ids=["program", "section", "term", "program+term"],
)
def test_any_one_of_them_opens_the_column(render_soup, front_matter):
    """has-context is an OR across three keys, so each has to be able to open
    the column alone. A template can only ask about one key at a time, which is
    how a partial combination would go missing."""
    soup = render_soup(
        "doc", "ctx.md", text=document(f'title: "T"\n{front_matter}')
    )
    assert context_of(soup), f"{front_matter!r} rendered no context column"


def test_none_of_them_emits_no_column(render_soup):
    soup = render_soup("doc", "ctx.md", text=document('title: "T"\n'))
    assert context_of(soup) is None


def test_the_separator_never_hangs_off_an_edge(render_soup):
    """Separators are generated between siblings rather than written into the
    template, so a lone value cannot end up with a middot on one side."""
    soup = render_soup(
        "doc", "ctx.md", text=document('title: "T"\nsection: "001"\n')
    )
    assert MIDDOT not in context_of(soup)


def test_blank_context_keys_render_as_if_absent(render):
    """The same guard test_byline.py holds over author and date. An empty
    `program:` must produce no column, not a column containing nothing."""
    absent, absent_path = render(
        "doc", "absent.md", text=document('title: "T"\n'), output="absent.html"
    )
    empty, empty_path = render(
        "doc",
        "empty.md",
        text=document('title: "T"\nprogram:\nsection:\nterm:\n'),
        output="empty.html",
    )

    assert absent.returncode == 0, absent.stderr
    assert empty.returncode == 0, empty.stderr
    assert empty_path.read_text() == absent_path.read_text(), (
        "a blank program/section/term rendered differently from omitting them"
    )


def test_a_deck_carries_the_same_three(render_soup):
    soup = render_soup(
        "slide",
        "ctx.md",
        text=deck(
            'title: "T"\nprogram: "CS 425/499"\nsection: "001"\nterm: "Spring 2026"\n'
        ),
    )
    line = soup.select_one(".slide--title .deck-context")
    assert line is not None, "the title slide rendered no context line"
    assert span(line, ".deck-program") == "CS 425/499"
    assert span(line, ".deck-section") == "001"
    assert span(line, ".deck-term") == "Spring 2026"


# --- show_date -------------------------------------------------------------


@pytest.mark.parametrize("written", ["true", "yes", "Yes", "on", "1"])
def test_the_ways_of_writing_yes(render_soup, written):
    soup = render_soup(
        "doc",
        "d.md",
        text=document(f'title: "T"\nshow_date: {written}\n'),
        metadata=BUILD_DATE,
    )
    assert date_of(soup) == "2026-03-14", f"show_date: {written} withheld the date"


@pytest.mark.parametrize("written", ["false", "no", "No", "off", "0", "none"])
def test_the_ways_of_writing_no(render_soup, written):
    """The regression this filter exists for. Pandoc hands the template the
    string "no", which is every bit as truthy as "yes"."""
    soup = render_soup(
        "doc",
        "d.md",
        text=document(f'title: "T"\nshow_date: {written}\n'),
        metadata=BUILD_DATE,
    )
    assert date_of(soup) is None, f"show_date: {written} published a date anyway"


def test_absent_means_no(render_soup):
    soup = render_soup(
        "doc", "d.md", text=document('title: "T"\n'), metadata=BUILD_DATE
    )
    assert date_of(soup) is None


def test_blank_means_no(render_soup):
    soup = render_soup(
        "doc", "d.md", text=document('title: "T"\nshow_date:\n'), metadata=BUILD_DATE
    )
    assert date_of(soup) is None


def test_an_explicit_date_outranks_the_build_date(render_soup):
    """show_date fills a date in; it does not overwrite one. An author who
    wrote a date meant that date."""
    soup = render_soup(
        "doc",
        "d.md",
        text=document('title: "T"\nshow_date: yes\ndate: 2020-01-01\n'),
        metadata=BUILD_DATE,
    )
    assert date_of(soup) == "2020-01-01"


def test_without_a_build_date_the_filter_supplies_one(render_soup):
    """The Makefile passes the build host's day. A hand-run pandoc does not,
    and must still produce a date rather than an empty byline."""
    soup = render_soup(
        "doc", "d.md", text=document('title: "T"\nshow_date: yes\n')
    )
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_of(soup) or "")


def test_a_deck_stamps_itself_the_same_way(render_soup):
    soup = render_soup(
        "slide",
        "d.md",
        text=deck('title: "T"\nshow_date: yes\n'),
        metadata=BUILD_DATE,
    )
    assert "2026-03-14" in soup.select_one(".slide--title .deck-meta").get_text()


# --- stn-tum: the deck title's printed size --------------------------------


DECK = """---
title: "Decktitle"
{context}---

## Slideheading

Some prose.
"""


def drawn_sizes(pdf) -> dict[str, float]:
    """Each word in the deck against the font size it was actually drawn at.

    Every page, not just the first: a deck prints one slide per page, so the
    title is on page one and the slide heading it has to outrank is on page
    two. Reading page one alone finds the title, finds no heading, and fails
    for the wrong reason.
    """
    sizes: dict[str, float] = {}

    def visit(text, cm, tm, font_dict, font_size):
        word = text.strip()
        if word and font_size:
            sizes.setdefault(word, round(float(font_size), 1))

    for page in PdfReader(pdf).pages:
        page.extract_text(visitor_text=visit)
    return sizes


@pytest.mark.parametrize(
    "context", ["", 'program: "CS 425/499"\n'], ids=["no-program", "with-program"]
)
def test_a_deck_title_prints_larger_than_a_slide_heading(to_pdf, context):
    """The title slide's .deck-title is an h1, and it is :first-child exactly
    when no context line precedes it -- so the print rule for slide headings
    used to capture it and drop it to 20pt, the same size as the heading it is
    supposed to outrank. Adding `program:` pushed a <p> in front and quietly
    gave the title its size back.

    Parametrized on that key for the same reason: the bug was not that the
    title printed small, it was that its size depended on something unrelated.
    """
    result, pdf = to_pdf(
        "slide", "stn-tum-deck.md", text=DECK.format(context=context), stem="stn-tum-deck"
    )
    assert result.returncode == 0, result.stderr

    sizes = drawn_sizes(pdf)
    assert sizes.get("Decktitle"), f"title not found in {sorted(sizes)[:20]}"
    assert sizes.get("Slideheading"), f"heading not found in {sorted(sizes)[:20]}"
    assert sizes["Decktitle"] > sizes["Slideheading"], (
        f"title drew at {sizes['Decktitle']}, slide heading at "
        f"{sizes['Slideheading']}"
    )


def test_the_deck_title_prints_the_same_size_either_way(to_pdf):
    """The acceptance criterion in its own right: not merely large enough in
    both cases, but the same size in both."""
    sizes = {}
    for label, context in (("bare", ""), ("program", 'program: "CS 425/499"\n')):
        result, pdf = to_pdf(
            "slide",
            f"stn-tum-deck-{label}.md",
            text=DECK.format(context=context),
            stem=f"stn-tum-deck-{label}",
        )
        assert result.returncode == 0, result.stderr
        sizes[label] = drawn_sizes(pdf).get("Decktitle")

    assert sizes["bare"] == sizes["program"], (
        f"the title printed at {sizes['bare']} without a program and "
        f"{sizes['program']} with one"
    )
