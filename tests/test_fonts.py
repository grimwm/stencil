"""Every font weight the stylesheets ask for is a weight we actually ship.

A CSS rule naming a weight with no matching @font-face does not fail. The
browser picks the nearest face it has and the page looks almost right, which is
how `strong` shipped for several releases rendering SemiBold: Crimson Pro was
vendored at 400 and 600 only, Bootstrap's `bolder` resolved to 700, nothing
matched, and 600 won. Nothing in the build, the suite or a five-way CI matrix
had anything to say about it -- the only report was a person squinting at a
handout.

Measured advance width of "the quick brown fox" at 18px in Crimson Pro:

    400  142.6      600  149.1      700  152.8      800  156.8      900  156.8

600 is what bold used to render as, which is why 800 rather than 700: the step
from regular had to be worth seeing. 900 is identical to 800 because the family
has nothing heavier.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FONTS_CSS = ROOT / "stencil" / "assets" / "fonts.css"
VENDOR_SCRIPT = ROOT / "scripts" / "vendor_page_assets.py"
STYLE = ROOT / "stencil" / "templates" / "_page-style.css.j2"

# The body face. Everything the reader reads as prose lands on it, so it is the
# family where a missing weight is least visible and most damaging.
SERIF = "Crimson Pro"


def faces() -> set[tuple[str, str]]:
    """Every (weight, style) pair fonts.css actually defines, per family."""
    found = set()
    for block in re.finditer(r"@font-face\s*\{([^}]*)\}", FONTS_CSS.read_text()):
        body = block.group(1)
        family = re.search(r"font-family:\s*'([^']*)'", body)
        weight = re.search(r"font-weight:\s*([^;]*)", body)
        style = re.search(r"font-style:\s*([^;]*)", body)
        if not (family and weight):
            continue
        found.add(
            (
                family.group(1),
                weight.group(1).strip(),
                style.group(1).strip() if style else "normal",
            )
        )
    return found


def serif_faces() -> set[tuple[str, str]]:
    return {(w, s) for fam, w, s in faces() if fam == SERIF}


@pytest.mark.parametrize(
    "weight,style",
    [("400", "normal"), ("400", "italic"), ("800", "normal"), ("800", "italic")],
)
def test_the_body_serif_ships_the_weight_bold_asks_for(weight, style):
    """400 and 800, upright and italic: regular prose, **bold**, *italic* and
    ***both***. The italic pair is the one that went unnoticed longest -- with
    no italic above 400, bold italic rendered as plain italic."""
    assert (weight, style) in serif_faces(), (
        f"{SERIF} {weight} {style} is not vendored, so anything asking for it "
        f"silently renders as the nearest weight we do ship: "
        f"{sorted(serif_faces())}"
    )


def test_the_bold_rule_and_the_vendored_weights_agree():
    """The two halves of this fix live in different files and neither one works
    alone. A stylesheet asking for a weight the vendor script does not fetch is
    the original bug wearing different clothes."""
    rule = re.search(
        r"strong,\s*\n\s*b\s*\{[^}]*font-weight:\s*(\d+)", STYLE.read_text()
    )
    assert rule, "no strong/b weight rule in _page-style.css.j2"
    asked = rule.group(1)
    assert (asked, "normal") in serif_faces(), (
        f"the stylesheet sets strong to {asked}, which {SERIF} does not ship"
    )
    assert (asked, "italic") in serif_faces(), (
        f"bold italic would fall back: {SERIF} has no {asked} italic"
    )


def test_the_vendor_script_requests_what_it_is_meant_to():
    """Read from the URL rather than the output, so a stale committed fonts.css
    cannot make this pass on its own."""
    url = re.search(r'"\?family=Crimson\+Pro:([^"]*)"', VENDOR_SCRIPT.read_text())
    assert url, "the Crimson Pro request is not where this test expects it"
    spec = url.group(1)
    for wanted in ("0,400", "0,800", "1,400", "1,800"):
        assert wanted in spec, f"{wanted} missing from the Crimson Pro request: {spec}"
