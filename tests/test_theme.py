"""The theme token layer, and the containment that makes PDFs light.

The load-bearing assertion here is not that dark mode looks right -- it is that
no dark declaration exists outside `@media screen`. Print cannot match that
block, so the light `:root` values apply and nothing needs re-declaring. Break
the containment and every printed handout silently goes dark.

These read the template sources rather than a rendered page. The `css` fixture
in test_title_block.py slices from the first `/* Document title`, which is fine
for the questions asked there and wrong here: it would orphan the `:root` block
these tests are entirely about.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).parent.parent / "stencil" / "templates"
STYLESHEETS = ["_page-style.css.j2", "_slide-style.css.j2"]

COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


def source(template: str) -> str:
    return (TEMPLATES / template).read_text()


def strip_comments(css: str) -> str:
    """Comments discuss colours constantly; only declarations matter here."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def token_blocks(css: str) -> str:
    """Every `:root {...}` block -- the only place a colour may be written."""
    return "\n".join(
        m.group(0) for m in re.finditer(r":root[^{]*\{[^}]*\}", css, re.S)
    )


@pytest.mark.parametrize("template", STYLESHEETS)
def test_no_raw_colour_outside_the_token_blocks(template):
    """The drift guard, and the most valuable test in this file.

    Every colour is a token, so a hardcoded one reintroduced later is a value
    that silently escapes theming -- it would render correctly in light, wrong
    in dark, and nothing else in the suite would notice.
    """
    css = strip_comments(source(template))
    outside = css.replace(token_blocks(css), "")
    stray = sorted(set(COLOUR.findall(outside)))
    assert not stray, f"{template}: colours declared outside :root: {stray}"


def tokens(css: str, selector: str = ":root") -> dict[str, str]:
    """The custom properties declared by one selector's block.

    Values are returned raw; resolve() below chases `var()` indirection.
    """
    m = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", css, re.S)
    assert m, f"no {selector} block"
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", m.group(1)))


def resolve(value: str, table: dict[str, str], depth: int = 0) -> str:
    """A token value with any `var(--x)` indirection followed to a literal."""
    assert depth < 10, f"var() cycle resolving {value!r}"
    m = re.fullmatch(r"var\((--[a-z0-9-]+)\)", value.strip())
    if not m:
        return value.strip()
    return resolve(table[m.group(1)], table, depth + 1)


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of #rgb or #rrggbb.

    Both spellings are in use -- --text-subtle is #555 -- and a reader that
    only understood the long form would make this check pass by not looking.
    """
    digits = colour.strip().lstrip("#")
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    channels = (int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    a, b = relative_luminance(foreground), relative_luminance(background)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


TEXT_TOKENS = ["--text", "--text-subtle", "--text-muted", "--text-label",
               "--text-strong"]


@pytest.mark.parametrize("token", TEXT_TOKENS)
def test_light_palette_text_meets_aa(token):
    """Every text token against the surface it sits on, at AA's 4.5:1.

    Measured rather than eyeballed: --text-label shipped in 0.11.0 at #7a828f,
    which looks unremarkable and is 3.88:1. `make check-access` runs pa11y at
    WCAG 2.1 AA and would have caught it; nothing in the suite did.
    """
    table = tokens(strip_comments(source("_page-style.css.j2")))
    fg = resolve(table[token], table)
    bg = resolve(table["--surface"], table)
    ratio = contrast(fg, bg)
    assert ratio >= 4.5, f"{token} ({fg}) on {bg} is {ratio:.2f}:1, under 4.5:1"
