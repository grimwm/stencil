"""The title block's hierarchy: the title has to outrank the subtitle.

A document's subtitle carries the student's name; the title names the
assignment. The subtitle was set at 1.8rem/700 against a 2.2rem/700 title, so
on screen it was 18% smaller and equally bold -- barely a hierarchy at all.

In print it was worse than weak, it was inverted. The print block scales
.doc-title to 18pt but never mentioned .doc-subtitle, which stayed at its
screen 1.8rem. That is 21.6pt, so the name printed 20% larger than the
assignment it belonged to. The HTML was fine, which is why it lasted: the
inversion existed only in the PDF.

The deck templates had this right from the start -- .deck-title 2.9rem/700
over a .deck-subtitle at 1.5rem and no weight of its own -- so these tests
hold the document side to the relationship the decks already keep.
"""

from __future__ import annotations

import re

import pytest
from pypdf import PdfReader

CONFIG = {
    "templates": [{"src": "html-template.html.j2"}],
    "packages": {"demo": {"name": "Demo", "package_type": "none", "docs": ["a.md"]}},
}

SOURCE = """---
title: "Stencil Regression"
subtitle: "Subordinate Nameline"
---

# A heading

Body text.
"""


def rule(css: str, selector: str) -> str:
    """The declarations of the first rule whose selector is exactly this one.

    Anchored to the start of a line so `h6` does not match the tail of
    `h1, h2, h3, h4, h5, h6 {`, which carries the shared font-family and no
    size at all. `[^}]*` on purpose: the stylesheet ships with Bootstrap
    inlined, and a greedy pattern over that is a hang rather than a test.
    """
    match = re.search(
        r"(?m)^[ \t]*" + re.escape(selector) + r"\s*\{([^}]*)\}", css
    )
    assert match, f"no rule for {selector}"
    return match.group(1)


def size_rem(declarations: str) -> float:
    match = re.search(r"font-size:\s*([\d.]+)rem", declarations)
    assert match, f"no rem font-size in {declarations!r}"
    return float(match.group(1))


def size_pt(declarations: str) -> float:
    match = re.search(r"font-size:\s*([\d.]+)pt", declarations)
    assert match, f"no pt font-size in {declarations!r}"
    return float(match.group(1))


def weight(declarations: str) -> int:
    match = re.search(r"font-weight:\s*(\d+)", declarations)
    return int(match.group(1)) if match else 400


@pytest.fixture
def css(generate_package) -> str:
    """Stencil's own stylesheet, with Bootstrap cut off the front.

    Bootstrap is inlined ahead of it and defines h1..h6 itself, so a search
    that is not anchored past it reads Bootstrap's rules and concludes stencil
    sets sizes it does not.
    """
    text = (generate_package(CONFIG) / "html-template.html").read_text()
    return text[text.index("/* Document title") :]


@pytest.fixture
def print_block(css) -> str:
    """The whole @media print block, brace-matched.

    Slicing to the first closing brace stops inside @page and hides every rule
    after it, which makes the tests below pass for no reason.
    """
    # Anchored backwards from stencil's own rule: Bootstrap is inlined into
    # this stylesheet and ships an @media print block of its own, which comes
    # first and contains none of these selectors.
    start = css.rfind("@media print", 0, css.index(".doc-title { font-size:"))
    assert start != -1, "no @media print block around stencil's print rules"
    depth = 0
    for i in range(css.index("{", start), len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start : i + 1]
    raise AssertionError("unterminated @media print block")


# --- on screen -------------------------------------------------------------


# The decks set .deck-subtitle at 1.5rem under a 2.9rem .deck-title, a ratio of
# 0.52. The documents were at 0.82, which is smaller without being subordinate.
# 0.7 is the loosest ratio that still reads as a hierarchy rather than a pair.
SUBORDINATE = 0.7


def test_the_subtitle_is_clearly_subordinate_to_the_title(css):
    """Merely smaller is not enough. At 1.8rem under a 2.2rem title the two
    lines read as a pair, and the subtitle is usually the longer of them."""
    ratio = size_rem(rule(css, ".doc-subtitle")) / size_rem(rule(css, ".doc-title"))
    assert ratio <= SUBORDINATE, f"subtitle is {ratio:.0%} of the title"


def test_the_subtitle_does_not_match_the_title_for_weight(css):
    """Equal weight is most of why the eye lands on the longer line."""
    assert weight(rule(css, ".doc-subtitle")) < weight(rule(css, ".doc-title"))


# --- in print --------------------------------------------------------------


def test_the_print_block_scales_the_subtitle_too(print_block):
    """Left out, it keeps a rem value that outgrows the title's 18pt."""
    assert size_pt(rule(print_block, ".doc-subtitle")) < size_pt(
        rule(print_block, ".doc-title")
    )


def test_the_printed_subtitle_stays_above_the_byline(print_block):
    """Guards the over-correction: it is a name, not a footnote."""
    assert size_pt(rule(print_block, ".doc-subtitle")) > size_pt(
        rule(print_block, ".doc-meta")
    )


# --- what actually comes out of the printer --------------------------------


@pytest.mark.integration
def test_the_title_prints_larger_than_the_subtitle(to_pdf):
    """The assertion the CSS ones stand in for: measured off the real PDF."""
    result, pdf = to_pdf("doc", "titled.md", text=SOURCE, stem="titled")
    assert result.returncode == 0, result.stderr

    drawn: dict[str, float] = {}

    def visit(text, cm, tm, font_dict, font_size):
        word = text.strip()
        if word and font_size:
            drawn.setdefault(word, round(float(font_size), 1))

    PdfReader(pdf).pages[0].extract_text(visitor_text=visit)
    assert drawn.get("Stencil"), f"title not found in {sorted(drawn)[:20]}"
    assert drawn.get("Subordinate"), f"subtitle not found in {sorted(drawn)[:20]}"
    assert drawn["Stencil"] > drawn["Subordinate"], (
        f"title drew at {drawn['Stencil']}, subtitle at {drawn['Subordinate']}"
    )


# --- the title against the headings under it -------------------------------
#
# stencil set no screen font-size for h1..h6, so they fell through to
# Bootstrap's responsive scale -- calc(1.375rem + 1.5vw), which measured
# 39.4px against the title's 35.2px. A section heading outranked the name of
# the document and resized with the window while the title stayed put.
#
# Print had the same shape of hole one level further down. It set h1, h2 and h3
# in pt and left h4, h5 and h6 on Bootstrap's rem, which are absolute against
# the root font size rather than relative to the headings beside them. Measured
# off a real PDF: h1 21.3, h2 18.7, h3 16.0, then h4 22.3 -- bigger than h1 and
# just under the title -- and h5 20.0. The scale inverted at h4 in every
# document stencil has ever printed.

LEVELS = ("h1", "h2", "h3", "h4", "h5", "h6")


def assert_strictly_descending(labels, sizes) -> None:
    """Each step must be smaller than the one above, not merely no larger.

    Two adjacent levels at the same size is a flattened hierarchy, which is a
    real defect in its own right -- see stn-tum, where a deck's title prints at
    exactly slide-heading size. A sorted() check would call that fine.
    """
    scale = dict(zip(labels, sizes))
    for above, below in zip(labels, labels[1:]):
        assert scale[above] > scale[below], f"{above} is not above {below}: {scale}"


def test_a_section_heading_does_not_outrank_the_title(css):
    """Needs stencil to state a size for h1 at all, rather than inherit one."""
    assert size_rem(rule(css, "h1")) < size_rem(rule(css, ".doc-title"))


def test_every_screen_heading_level_steps_down(css):
    assert_strictly_descending(LEVELS, [size_rem(rule(css, t)) for t in LEVELS])


def test_every_printed_heading_level_steps_down(print_block):
    """h4 was the break: unset in print, so it kept a rem that outgrew h1."""
    assert_strictly_descending(
        LEVELS, [size_pt(rule(print_block, t)) for t in LEVELS]
    )


def test_the_printed_title_outranks_every_heading(print_block):
    title = size_pt(rule(print_block, ".doc-title"))
    for tag in LEVELS:
        assert size_pt(rule(print_block, tag)) < title, f"{tag} is not below the title"


HEADINGS_SOURCE = """---
title: "Probe Title"
subtitle: "Probe Subtitle"
---

# HeadingOne

## HeadingTwo

### HeadingThree

#### HeadingFour

##### HeadingFive

###### HeadingSix

Body paragraph.
"""


@pytest.mark.integration
def test_the_printed_heading_scale_never_inverts(to_pdf):
    """The assertion the CSS ones stand in for, measured off a real PDF."""
    result, pdf = to_pdf("doc", "headings.md", text=HEADINGS_SOURCE, stem="headings")
    assert result.returncode == 0, result.stderr

    drawn: dict[str, float] = {}

    def visit(text, cm, tm, font_dict, font_size):
        word = text.strip()
        if word and font_size:
            drawn.setdefault(word, round(float(font_size), 1))

    for page in PdfReader(pdf).pages:
        page.extract_text(visitor_text=visit)

    words = ["Probe", "HeadingOne", "HeadingTwo", "HeadingThree", "HeadingFour",
             "HeadingFive", "HeadingSix"]
    sizes = [drawn.get(w) for w in words]
    assert all(s is not None for s in sizes), dict(zip(words, sizes))
    assert_strictly_descending(words, sizes)
