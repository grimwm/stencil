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


# ---------------------------------------------------------------------------
# stn-uje: the same file, inlined once per weight
#
# Google Fonts serves a variable font and returns THE SAME FILE for every
# weight of a family, so the vendored fonts.css carries 31 @font-face blocks
# holding 17 distinct files. assets.merge_duplicate_faces collapses them on the
# way into a page. These tests are about the two things that could go wrong:
# losing a face, and changing what a weight renders as.


def merged_faces() -> list[dict]:
    """Every @font-face in the CSS a generated page actually receives."""
    from stencil import assets

    return parse_faces(assets.merge_duplicate_faces(FONTS_CSS.read_text()))


def vendored_faces() -> list[dict]:
    """Every @font-face in the committed fonts.css, before the merge."""
    return parse_faces(FONTS_CSS.read_text())


def parse_faces(css: str) -> list[dict]:
    out = []
    for block in re.finditer(r"@font-face\s*\{([^}]*)\}", css):
        body = block.group(1)

        def decl(name: str) -> str:
            found = re.search(rf"{name}:\s*([^;]*);", body)
            return found.group(1).strip() if found else ""

        weights = [int(w) for w in decl("font-weight").split()]
        out.append(
            {
                "family": decl("font-family").strip("'\""),
                "style": decl("font-style") or "normal",
                "range": decl("unicode-range"),
                "src": decl("src"),
                "low": min(weights),
                "high": max(weights),
            }
        )
    return out


def test_the_merge_keeps_every_distinct_face():
    """A face is identified by everything except its weight. Lose one and a
    document silently drops to a fallback for whatever it covered."""
    before = {(f["family"], f["style"], f["range"], f["src"]) for f in vendored_faces()}
    after = {(f["family"], f["style"], f["range"], f["src"]) for f in merged_faces()}
    assert after == before, (
        f"the merge changed the set of distinct faces; "
        f"lost {sorted(str(x)[:60] for x in before - after)}, "
        f"gained {sorted(str(x)[:60] for x in after - before)}"
    )


def test_the_merge_carries_the_payload_across_verbatim():
    """No face is re-encoded. The merged block is the first of its group with
    only font-weight rewritten, so a subsetting or base64 bug cannot hide in
    here: the bytes are the bytes that were vendored."""
    vendored = {f["src"] for f in vendored_faces()}
    for face in merged_faces():
        assert face["src"] in vendored, (
            f"{face['family']} {face['style']} has a src that is not one of the "
            f"vendored payloads -- something rewrote the font rather than "
            f"moving it"
        )


def test_the_merge_actually_removes_the_duplicates():
    """The number this change exists for, stated as a number."""
    raw = FONTS_CSS.read_text()
    from stencil import assets

    merged = assets.merge_duplicate_faces(raw)
    assert len(merged) < len(raw) * 0.60, (
        f"fonts.css is {len(raw)} bytes and merges to {len(merged)}; the "
        f"duplicates are 45% of it, so anything above 60% means the merge "
        f"stopped finding them"
    )


def select(faces: list[dict], desired: int) -> tuple[str, int] | None:
    """Which face CSS font-matching picks for ``desired``, and at what weight.

    CSS Fonts 4 section 5.2, the font-weight step, over faces already narrowed
    to one family and style.

    RETURNING THE FILE ALONE MAKES THIS TEST VACUOUS, which is worth recording
    because the first version of it was. Every Crimson Pro upright face IS the
    same file -- that is the duplication being removed -- so "which file does
    700 select" has the same answer before and after and measures nothing. What
    the merge changes is the weight the variable axis is instantiated at, so
    that is what comes back with it.
    """
    def chosen(face: dict) -> tuple[str, int]:
        # A face is used at the desired weight when it covers it, and at its
        # nearest end when it does not.
        return face["src"], min(max(desired, face["low"]), face["high"])

    exact = [f for f in faces if f["low"] <= desired <= f["high"]]
    if exact:
        return chosen(exact[0])
    below = sorted((f for f in faces if f["high"] < desired), key=lambda f: -f["high"])
    above = sorted((f for f in faces if f["low"] > desired), key=lambda f: f["low"])
    if desired < 400:
        order = below + above
    elif desired > 500:
        order = above + below
    else:
        within = [f for f in above if f["low"] <= 500]
        order = within + below + [f for f in above if f["low"] > 500]
    return chosen(order[0]) if order else None


def by_family(faces: list[dict]) -> dict:
    grouped: dict = {}
    for face in faces:
        grouped.setdefault((face["family"], face["style"], face["range"]), []).append(face)
    return grouped


# Crimson Pro ships 400/600/800 upright and 400/800 italic, so 500 and 700 are
# the weights no face declared. Before the merge they rounded to a neighbour;
# after it they land on a real interpolated instance of the same variable font.
#
# This is the ONLY behaviour the merge changes, and it is written down as data
# rather than prose so that widening it fails here. Inter declares 400/500/600
# /700 and merges to `400 700`, so all four stay exact; JetBrains Mono declares
# 400/500 and merges to `400 500`, likewise. Every weight that had a face keeps
# rendering identically, because pinning the wght axis at 600 and instantiating
# 600 out of the range are the same operation on the same file.
KNOWN_WEIGHT_CHANGES = {
    ("Crimson Pro", "italic"): {500, 600, 700},
    ("Crimson Pro", "normal"): {500, 700},
}


def test_the_merge_changes_only_the_weights_no_face_ever_declared():
    before = by_family(vendored_faces())
    after = by_family(merged_faces())
    assert set(before) == set(after)
    changed: dict = {}
    for key, faces in before.items():
        for weight in range(100, 1000, 100):
            if select(faces, weight) != select(after[key], weight):
                changed.setdefault(key[:2], set()).add(weight)
    assert changed == KNOWN_WEIGHT_CHANGES, (
        f"the merge moved a weight that was not expected to move.\n"
        f"expected: {KNOWN_WEIGHT_CHANGES}\n"
        f"measured: {changed}\n"
        f"Every weight a face declared must render as the same file it does "
        f"today; only weights that were being rounded to a neighbour may move."
    )


def test_the_one_reachable_changed_weight_is_pinned_back():
    """700 on the body serif is reachable from plain markdown, through
    Bootstrap's `dt { font-weight: 700 }` and a definition list. The merge
    would render that at a real 700 instead of the 800 it rounds to today, so
    _page-style.css.j2 pins it. Without this rule the change is invisible until
    someone writes their first definition list."""
    # Comments stripped first. The rule below is explained by a comment that
    # quotes Bootstrap's `dt { font-weight: 700 }` verbatim, and a regex run
    # over the raw file finds the explanation before the rule -- which is how
    # the first version of this test reported the pin as 700.
    style = re.sub(r"/\*.*?\*/", "", STYLE.read_text(), flags=re.S)
    rule = re.search(r"\bdt\s*\{[^}]*font-weight:\s*(\d+)", style)
    assert rule, (
        "no dt font-weight rule in _page-style.css.j2; Bootstrap's dt asks for "
        "700, which the merged Crimson Pro range now resolves to literally"
    )
    assert (rule.group(1), "normal") in serif_faces(), (
        f"dt is pinned to {rule.group(1)}, which {SERIF} does not declare"
    )
