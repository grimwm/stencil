"""Task lists, ordered as well as unordered.

ceedb74 styled task-list checkboxes as the list marker, and scoped every rule
it added to `ul.task-list`. Pandoc puts that class on a bullet list only: an
ordered task list arrives as a bare `<ol type="1">` carrying the identical
`<label><input type=checkbox>` items. So `1. [ ]` matched none of the rules --
the box sat hard against the first letter, and a checked one printed empty.

The two are deliberately not styled the same. A bullet list's disc is
decoration, so the checkbox replaces it and the text hangs off it. An ordered
list's number is content -- the author asked for it -- so it stays, and the box
only gains room beside it.
"""

from __future__ import annotations

import re

import pytest

CONFIG = {
    "templates": [{"src": "html-template.html.j2"}],
    "packages": {"demo": {"name": "Demo", "package_type": "none", "docs": ["a.md"]}},
}

SOURCE = """---
title: "T"
---

- [ ] bullet open
- [x] bullet done

1. [ ] ordered open
1. [x] ordered done
"""


@pytest.fixture
def css(generate_package) -> str:
    """Stencil's own stylesheet, past the inlined Bootstrap."""
    text = (generate_package(CONFIG) / "html-template.html").read_text()
    return text[text.index("/* Document title") :]


def rule_for(css: str, selector: str) -> str | None:
    """The declarations of the first rule whose selector list contains this one.

    Comments are stripped first, and that is not tidiness. A CSS comment holds
    no braces, so a selector-matching pattern reads the comment above a rule as
    part of its selector list -- and this stylesheet comments nearly every
    rule, so the first selector after any comment would never match.

    `[^}]*` rather than a greedy match, for the reason test_title_block.py
    gives: Bootstrap is inlined ahead of this and a greedy pattern over it
    hangs rather than fails.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        selectors = [s.strip() for s in match.group(1).split(",")]
        if selector in selectors:
            return match.group(2)
    return None


# --- what pandoc gives us (guards, not preferences) ------------------------


@pytest.mark.integration
def test_pandoc_classes_a_bullet_task_list_and_not_an_ordered_one(render_soup):
    """The asymmetry this whole file exists for. If pandoc ever starts adding
    the class to <ol> too, the CSS below is redundant and this says so."""
    soup = render_soup("doc", "tl.md", text=SOURCE)
    assert soup.select_one("ul.task-list") is not None
    assert soup.select_one("ol.task-list") is None, (
        "pandoc now classes ordered task lists; the ol rules can be folded in"
    )


@pytest.mark.integration
def test_an_ordered_task_list_still_renders_checkboxes(render_soup):
    """Unclassed, but not uncheckboxed -- which is why it looked so odd."""
    boxes = [
        box
        for box in soup_boxes(render_soup("doc", "tl.md", text=SOURCE))
        if box.find_parent("ol")
    ]
    assert len(boxes) == 2
    assert [b.has_attr("checked") for b in boxes] == [False, True]


def soup_boxes(soup):
    return soup.select('input[type="checkbox"]')


@pytest.mark.integration
def test_the_ordinal_survives(render_soup):
    """An ordered task list keeps its <ol>. Dropping the number the way the
    bullet list drops its disc would discard something the author wrote."""
    soup = render_soup("doc", "tl.md", text=SOURCE)
    ol = soup.select_one("ol")
    assert ol is not None
    assert len(ol.select("li")) == 2


# --- what the stylesheet does about it -------------------------------------


def test_an_ordered_checkbox_is_given_room(css):
    """The reported symptom: no gap between the box and the first letter."""
    declarations = rule_for(css, 'ol > li > label > input[type="checkbox"]')
    assert declarations, "no rule reaches an ordered task list's checkbox"
    assert re.search(r"margin(-right)?:\s*[\d.]", declarations), (
        f"the box is given no room: {declarations!r}"
    )


def test_an_ordered_checkbox_is_given_room_on_the_ordinal_side_too(css):
    """Fixing only the right side moves the collision rather than removing it:
    the box then sits hard against the number, reading "1.[ ] text"."""
    declarations = rule_for(css, 'ol > li > label > input[type="checkbox"]')
    margin = re.search(r"(?<!-)margin:\s*([^;]+)", declarations or "")
    assert margin, f"no shorthand margin to carry a left gap: {declarations!r}"
    parts = margin.group(1).split()
    assert len(parts) == 4, f"expected all four sides, got {margin.group(1)!r}"
    assert parts[3] != "0", "no gap between the ordinal and the box"


def test_an_ordered_checkbox_is_tinted_like_a_bullet_one(css):
    """Same control, same document, so the same accent."""
    declarations = rule_for(css, 'ol > li > label > input[type="checkbox"]')
    assert "accent-color" in declarations


def test_an_ordered_list_keeps_its_marker(css):
    """The rule must not carry list-style: none, which is how the bullet list
    hands its column to the checkbox. Here the number wants that column."""
    for selector in ("ol", "ol.task-list", "ol > li"):
        declarations = rule_for(css, selector) or ""
        assert "list-style" not in declarations, (
            f"{selector} drops the ordinal: {declarations!r}"
        )


def test_print_keeps_a_checked_box_filled_in_either_list(css):
    """Browsers strip form-control fills from print. The opt-out was spelled
    `ul.task-list`, so an ordered list's checked boxes printed empty."""
    print_block = css[css.index("@media print") :]
    declarations = rule_for(print_block, 'li input[type="checkbox"]')
    assert declarations, (
        "the print opt-out does not reach an ordered task list's checkbox"
    )
    assert "print-color-adjust" in declarations
