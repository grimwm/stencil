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
from pathlib import Path

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
    # `css` starts past Bootstrap, which ships an @media print block of its
    # own, so the first one here is stencil's.
    start = css.index("@media print")
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
#
# Print does not restate the scale. Every size above is in rem, a rem is the
# root font size, so rescaling the root once carries the whole scale across --
# and there is no second list of sizes to fall out of step with the first.
# Restating them in pt is what put .doc-subtitle at 21.6pt under an 18pt title
# and inverted the heading scale at h4.

BODY_REM = 1.125  # the body rule at the top of the stylesheet
PRINTED_BODY_PT = 11.0  # what a document's body text has always printed at


def root_pt(print_block: str) -> float:
    return size_pt(rule(print_block, "html"))


def test_print_rescales_the_root(print_block):
    assert root_pt(print_block) > 0


def test_print_restates_no_size_the_scale_already_gives(print_block):
    """The guard that keeps the two media on one scale. Any of these coming
    back means a second scale, and a second scale drifts from the first."""
    restated = [
        selector
        for selector in (".doc-title", ".doc-subtitle", ".doc-meta", *LEVELS)
        if re.search(
            r"(?m)^[ \t]*" + re.escape(selector) + r"\s*\{[^}]*font-size", print_block
        )
    ]
    assert not restated, f"print restates a size for {restated}"


def test_the_root_keeps_body_text_where_it_has_always_printed(print_block):
    """Why 9.78pt and not a round number: it is what puts 1.125rem on 11pt.
    Change the root and this says what it costs."""
    printed = root_pt(print_block) * BODY_REM
    assert abs(printed - PRINTED_BODY_PT) < 0.05, f"body would print at {printed:.2f}pt"


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
    """Needs stencil to state a size for h1 at all, rather than inherit one.

    One scale serves both media, so this holds in print too -- and
    test_the_printed_heading_scale_never_inverts proves it off a real PDF
    rather than trusting that the root carries it across.
    """
    assert size_rem(rule(css, "h1")) < size_rem(rule(css, ".doc-title"))


def test_every_heading_level_steps_down(css):
    assert_strictly_descending(LEVELS, [size_rem(rule(css, t)) for t in LEVELS])


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


def test_the_points_badge_is_subordinate_to_the_title(css):
    """The badge sits inside .doc-title, so an omitted font-size would inherit
    that block's 2.2rem -- the same inversion .doc-subtitle exists to prevent,
    and the print block would carry it into the PDF just as it did there.

    Asserted against the stylesheet rather than a rendered PDF because the
    relationship is a stated one: measuring glyphs would test the renderer.
    """
    title = size_rem(rule(css, ".doc-title"))
    badge = size_rem(rule(css, ".doc-points"))
    assert badge < title, f"badge {badge}rem is not below title {title}rem"


def test_the_header_grid_rule_survives_the_parser(css):
    """A stray `*/` upstream silently deletes this rule.

    The prose above `header.doc-title` is a CSS comment. Close it twice --
    which is what happens when someone appends a paragraph after the existing
    `*/` -- and the orphaned text becomes part of this rule's selector
    prelude. The declaration block is then dropped whole, the header falls
    back to block layout, and the context stacks under the byline instead of
    sitting beside the title.

    Nothing else here notices: every other assertion in this suite reads the
    DOM or the stylesheet as text, and both are unchanged by a rule the parser
    threw away.
    """
    declarations = rule(css, "header.doc-title")
    assert "display: grid" in declarations
    assert "grid-template-columns" in declarations


@pytest.mark.parametrize(
    "template", ["_page-style.css.j2", "_slide-style.css.j2"]
)
def test_the_stylesheet_comments_are_balanced(template):
    """The general form of the bug above: more `*/` than `/*`.

    Reads the template rather than the `css` fixture, which slices from the
    first `/* Document title` and so orphans every terminator ahead of it --
    an imbalance of its own making, not one worth reporting.
    """
    source = (
        Path(__file__).parent.parent / "stencil" / "templates" / template
    ).read_text()

    # Scanned in order rather than counted. Equal totals are not enough:
    # `*/ ... /*` balances arithmetically and is still the bug -- an orphan
    # terminator followed by an unclosed opener.
    cursor = 0
    while True:
        opener = source.find("/*", cursor)
        closer = source.find("*/", cursor)
        assert closer == -1 or (opener != -1 and opener < closer), (
            f"{template}: comment terminator at {closer} with no opener before "
            "it; the text ahead of it becomes part of the next rule's selector"
        )
        if opener == -1:
            break
        closer = source.find("*/", opener + 2)
        assert closer != -1, (
            f"{template}: comment opened at {opener} is never closed"
        )
        cursor = closer + 2



def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of a #rgb or #rrggbb colour.

    Both spellings are in use here -- .doc-subtitle is #555 -- and a reader
    that only understands the long form silently skips the short one, which
    would make this check pass by not looking.
    """
    digits = hex_color.lstrip("#")
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    channels = (int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str = "#ffffff") -> float:
    a, b = relative_luminance(foreground), relative_luminance(background)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


@pytest.mark.parametrize(
    ("selector", "name"),
    [
        (".doc-date-label", "Issued/Due label"),
        (".doc-subtitle", "subtitle"),
        (".doc-meta,\n    .doc-date,\n    .doc-context", "byline and context"),
    ],
)
def test_header_text_meets_aa_contrast(css, selector, name):
    """Muted greys in the header are still text, and take the 4.5:1 threshold.

    The Issued/Due label shipped at #7a828f, which is 3.88:1 on white -- under
    AA, and `make check-access` runs pa11y at WCAG 2.1 AA. Nothing in this
    suite would have caught it: the label rendered, the DOM was right, and the
    stylesheet contained exactly the colour it was asked for.
    """
    declarations = rule(css, selector)
    match = re.search(r"color:\s*(#[0-9a-fA-F]{3,6})\b", declarations)
    assert match, f"no hex colour in {name}"
    ratio = contrast(match.group(1))
    assert ratio >= 4.5, (
        f"{name} ({match.group(1)}) is {ratio:.2f}:1 on white, under AA's 4.5:1"
    )
