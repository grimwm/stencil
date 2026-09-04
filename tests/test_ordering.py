"""The two filter-ordering constraints, proved against a real build.

test_pipeline.py asserts the argv is in the right order. These assert that the
right order actually produces the right document -- so if a future pandoc
changes what "citeproc after hidden-filter" means, this catches it and the argv
test would not.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def references(soup):
    """The reference list pandoc generated, as text."""
    refs = soup.select_one("#refs")
    assert refs is not None, "no reference list in the rendered page"
    return refs.get_text()


def test_a_presenter_only_citation_stays_out_of_the_handout(render_soup):
    """stn-rxn.3 -- the silent failure this whole epic was filed for.

    document.md cites little1961 in the body and reinertsen2009 only inside
    ::: {.hidden}. Built without WITH=hidden, hidden-filter removes that div
    before citeproc runs, so citeproc never sees the citation and never emits
    its entry.

    Run citeproc first and the hidden content is still correctly dropped from
    the page -- but its sources are already in the reference list. The handout
    looks right and quietly names the answer key's bibliography.
    """
    text = references(render_soup("doc", "document.md"))

    assert "Little" in text, "the visible citation should be listed"
    assert "Reinertsen" not in text, (
        "a work cited only inside ::: {.hidden} leaked into the handout's "
        "reference list -- citeproc is running before hidden-filter"
    )


def test_the_answer_key_lists_both_sources(render_soup):
    """The other half: with WITH=hidden, the hidden citation *should* appear.

    Without this, the test above would pass just as well if citeproc were
    broken outright and no reference list were ever produced.
    """
    text = references(
        render_soup("doc", "document.md", metadata={"include-hidden": "true"})
    )

    assert "Little" in text
    assert "Reinertsen" in text


def test_the_deck_reference_list_lands_inside_a_slide(render_soup):
    """stn-rxn.4 -- otherwise the list renders where present mode never shows it.

    deck.md has a citation and no explicit ::: {#refs}, so citeproc appends the
    list to the document's blocks. slide-sections has to run afterwards to sweep
    it into a slide. Reversed, it ends up at container.deck > div#refs --
    outside every card, invisible in present mode.

    Asserted on .slide rather than section.slide on purpose. slide-sections
    always builds a pandoc Div, but pandoc's HTML writer promotes one to
    <section> when its first block is a Header and leaves it a <div> otherwise
    -- and a generated reference list has no heading, so this particular card is
    always a div. The stylesheet selects .slide either way, so .slide is the
    real contract; matching on the element name would fail on correct output.
    """
    soup = render_soup("slide", "deck.md")
    refs = soup.select_one("#refs")
    assert refs is not None, "no reference list in the rendered deck"

    ancestors = [
        f"{p.name}.{'.'.join(p.get('class', []))}" if p.get("class") else p.name
        for p in refs.parents
        if p.name
    ]

    assert refs.find_parent(class_="slide") is not None, (
        "the reference list is outside every slide card, where present mode "
        f"never displays it -- ancestor chain was {ancestors}"
    )
    assert refs.parent.get("class") != ["container", "deck"], (
        "the reference list is a direct child of container.deck -- "
        "slide-sections ran before citeproc"
    )
