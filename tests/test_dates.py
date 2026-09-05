"""The date grammar shared by `date` and `due`.

Both keys accept yyyy-mm-dd or yyyy-mm-ddThh:mm and nothing else. The calendar
gate is an os.time round-trip rather than a days-in-month table -- os.time
normalizes a day the month does not have, so comparing the fields back is what
detects it.

These tests pin all four branches of the Gregorian leap rule and both ends of
the time_t range. That is not redundant with having checked it once by hand:
the round-trip's correctness is inherited from the container's C library, so
the only thing that can break it is bumping the pandoc image, and this is what
would notice.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def build(render, key: str, value: str):
    """Render one document carrying a single date key.

    The value is quoted on purpose. Unquoted, YAML would type some of these
    itself -- `2026-09-01 21:45` is a YAML timestamp, not a string -- and the
    test would then be measuring the YAML parser rather than this filter.
    Quoting is also how an author writes them.
    """
    return render(
        "doc",
        "d.md",
        text=document(f'title: "T"\n{key}: "{value}"\n'),
        output="d.html",
    )


def line(soup, selector: str) -> str:
    """One header line, whitespace-normalized.

    Pandoc hard-wraps its output, so `Issued Sep 12` can arrive as
    `Issued Sep\\n12`. That is formatting, not content -- the same reason
    test_byline.py normalizes before comparing.
    """
    return " ".join(soup.select_one(selector).get_text().split())


ACCEPTED = [
    "2026-09-01",
    "2026-09-01T21:45",
    "2024-02-29",  # leap, % 4
    "2000-02-29",  # leap, % 400
    "1900-02-28",  # pre-1970, negative time_t
    "2038-12-31",  # past the 32-bit time_t cliff
    # The last real day of every month, so a wrong table would show up as a
    # rejected date rather than only as an accepted impossible one.
    "2026-01-31",
    "2026-02-28",
    "2026-03-31",
    "2026-04-30",
    "2026-05-31",
    "2026-06-30",
    "2026-07-31",
    "2026-08-31",
    "2026-09-30",
    "2026-10-31",
    "2026-11-30",
    "2026-12-31",
]

REJECTED = [
    "2026-9-1",  # not zero-padded
    "2026/09/01",  # wrong separator
    "09-01-2026",  # wrong order
    "2026-09-01 21:45",  # space instead of T
    "2026-09-01T21:45:30",  # seconds
    "next Friday",
    "2026-13-01",  # month out of range
    "2026-00-01",
    "2026-09-01T24:00",  # hour out of range
    "2026-09-01T21:60",  # minute out of range
    "2026-02-30",  # not a real date
    "2026-02-29",  # not a leap year
    "1900-02-29",  # century, not a leap year
    "2100-02-29",  # century, not a leap year
    # The day after the last real day of every month. February's is the
    # 2026-02-29 already listed above as the non-leap case.
    "2026-01-32",
    "2026-03-32",
    "2026-04-31",
    "2026-05-32",
    "2026-06-31",
    "2026-07-32",
    "2026-08-32",
    "2026-09-31",
    "2026-10-32",
    "2026-11-31",
    "2026-12-32",
]


@pytest.mark.parametrize("key", ["date", "due"])
@pytest.mark.parametrize("value", ACCEPTED)
def test_accepted_shapes_build(render, key, value):
    result, _ = build(render, key, value)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("key", ["date", "due"])
@pytest.mark.parametrize("value", REJECTED)
def test_rejected_shapes_fail_the_build(render, key, value):
    result, _ = build(render, key, value)
    assert result.returncode != 0, f"{key}: {value} was accepted"
    assert key in result.stderr


def test_a_bad_shape_names_both_accepted_forms(render):
    result, _ = build(render, "due", "next Friday")
    assert "yyyy-mm-dd" in result.stderr
    assert "yyyy-mm-ddThh:mm" in result.stderr


def test_a_bad_month_names_the_field_not_the_grammar(render):
    result, _ = build(render, "due", "2026-13-01")
    assert "month" in result.stderr


def test_an_impossible_day_names_the_month_and_its_length(render):
    result, _ = build(render, "due", "2026-02-30")
    assert "Feb" in result.stderr
    assert "28" in result.stderr


def test_blank_keys_render_as_if_absent(render):
    absent, absent_path = render(
        "doc", "a.md", text=document('title: "T"\n'), output="a.html"
    )
    blank, blank_path = render(
        "doc",
        "b.md",
        text=document('title: "T"\ndate:\ndue:\n'),
        output="b.html",
    )
    assert absent.returncode == 0, absent.stderr
    assert blank.returncode == 0, blank.stderr
    assert blank_path.read_text() == absent_path.read_text()


def test_issued_and_due_render_labelled(render_soup):
    soup = render_soup(
        "doc",
        "d.md",
        text=document('title: "T"\ndate: 2026-09-01\ndue: 2026-09-12T23:59\n'),
    )
    assert line(soup, ".doc-issued") == "Issued Sep 01"
    assert line(soup, ".doc-due") == "Due Sep 12 · 23:59"


def test_a_written_time_renders_and_a_missing_one_does_not(render_soup):
    soup = render_soup(
        "doc",
        "d.md",
        text=document('title: "T"\ndate: 2026-09-01T21:45\ndue: 2026-09-12\n'),
    )
    assert "21:45" in line(soup, ".doc-issued")
    assert ":" not in line(soup, ".doc-due")


def test_the_time_element_carries_the_original_iso(render_soup):
    soup = render_soup(
        "doc", "d.md", text=document('title: "T"\ndue: 2026-09-12T23:59\n')
    )
    assert soup.select_one(".doc-due time")["datetime"] == "2026-09-12T23:59"


def test_show_date_stamps_date_only(render_soup):
    soup = render_soup(
        "doc", "d.md", text=document('title: "T"\nshow_date: true\n')
    )
    issued = line(soup, ".doc-issued")
    assert issued.startswith("Issued ")
    assert ":" not in issued


def test_a_written_date_still_beats_show_date(render_soup):
    soup = render_soup(
        "doc",
        "d.md",
        text=document('title: "T"\ndate: 2026-09-01\nshow_date: true\n'),
    )
    assert "Sep 01" in line(soup, ".doc-issued")


def test_due_alone_opens_the_byline(render_soup):
    soup = render_soup(
        "doc", "d.md", text=document('title: "T"\ndue: 2026-09-12\n')
    )
    assert soup.select_one(".doc-byline") is not None
    assert soup.select_one(".doc-issued") is None
    assert "Due Sep 12" in line(soup, ".doc-due")


def test_the_deck_byline_never_strands_a_separator(render_soup):
    soup = render_soup(
        "slide", "deck.md", text=document('title: "T"\ndue: 2026-09-12\n')
    )
    meta = line(soup, ".deck-meta")
    assert meta == "Due Sep 12"


def test_the_deck_byline_joins_all_three(render_soup):
    soup = render_soup(
        "slide",
        "deck.md",
        text=document(
            'title: "T"\nauthor: Ada Lovelace\ndate: 2026-09-01\n'
            "due: 2026-09-12T23:59\n"
        ),
    )
    meta = line(soup, ".deck-meta")
    assert meta == (
        "Author Ada Lovelace · Issued Sep 01 · Due Sep 12 · 23:59"
    )
