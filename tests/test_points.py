"""The points badge.

`points` is a number in the front matter; the badge beside the title is the
only place it renders. The interesting cases are the plural boundary and the
non-numeric escape hatch -- "1 pts" is the bug this file exists to prevent.

This replaces a `subtitle: "Points: 50"` convention, which overloaded a
presentation field with a data field so that nothing could query or validate
it. Which is also why there is no test here asserting a subtitle: the two are
unrelated now, and that is the point.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def document(front_matter: str) -> str:
    return f"---\n{front_matter}---\n\n## Body\n\nSome prose.\n"


def badge(soup, selector: str = ".doc-points") -> str | None:
    found = soup.select_one(selector)
    return None if found is None else " ".join(found.get_text().split())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", "1 pt"),
        ("0", "0 pts"),
        ("50", "50 pts"),
        ("100", "100 pts"),
        ("1.5", "1.5 pts"),
    ],
)
def test_points_pluralize(render_soup, value, expected):
    soup = render_soup(
        "doc", "pts.md", text=document(f'title: "T"\npoints: {value}\n')
    )
    assert badge(soup) == expected


def test_a_non_numeric_value_renders_verbatim(render_soup):
    soup = render_soup(
        "doc", "pts.md", text=document('title: "T"\npoints: "extra credit"\n')
    )
    assert badge(soup) == "extra credit"


def test_absent_points_emits_no_badge(render_soup):
    soup = render_soup("doc", "pts.md", text=document('title: "T"\n'))
    assert badge(soup) is None


def test_blank_points_renders_as_if_absent(render):
    """A blank key is an absent key, the rule BLANKABLE exists to keep.

    Written as a byte comparison rather than an assertion about the markup
    because the failure mode is a stray empty element, and enumerating the
    ways that could look is how you miss one.
    """
    absent, absent_path = render(
        "doc", "a.md", text=document('title: "T"\n'), output="a.html"
    )
    blank, blank_path = render(
        "doc", "b.md", text=document('title: "T"\npoints:\n'), output="b.html"
    )
    assert absent.returncode == 0, absent.stderr
    assert blank.returncode == 0, blank.stderr
    assert blank_path.read_text() == absent_path.read_text()


def test_the_badge_reaches_the_deck_title_slide(render_soup):
    soup = render_soup(
        "slide", "deck.md", text=document('title: "T"\npoints: 50\n')
    )
    assert badge(soup, ".deck-points") == "50 pts"
