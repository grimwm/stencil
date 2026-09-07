"""The CSS, JavaScript and webfonts inlined into every generated page.

Fetched once by ``scripts/vendor_page_assets.py`` and committed under
``stencil/assets/``. ``load()`` is what ``stencil gen`` puts into the template
context, so a handout is one self-contained file and ``make pdf`` does not
reach the network.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"

# Filenames on disk, keyed by the names the templates use.
_FILES = {
    "bootstrap_css": "bootstrap.min.css",
    "bootstrap_js": "bootstrap.bundle.min.js",
    "highlight_css": "highlight-github.min.css",
    "highlight_css_dark": "highlight-github-dark.min.css",
    "highlight_js": "highlight.min.js",
    "highlight_sql": "highlight-sql.min.js",
    "highlight_python": "highlight-python.min.js",
    "highlight_javascript": "highlight-javascript.min.js",
    "highlight_bash": "highlight-bash.min.js",
    "mermaid_js": "mermaid.min.js",
    "fonts_css": "fonts.css",
}


_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def scope_css(css: str, scope: str) -> str:
    """Prefix every selector in a flat stylesheet with ``scope``.

    Used for the dark highlight.js theme, which has to apply only under
    ``:root[data-theme="dark"]`` and only inside ``@media screen`` -- the
    containment rule that keeps every dark declaration away from print.

    Done here rather than with CSS nesting on purpose. Nesting would express
    this in one line, but the theme opens with ``pre code.hljs``, a nested
    *type* selector, and that only parses under the relaxed nesting syntax
    (Chrome 120+, Firefox 117+, Safari 17.2+). A generated handout is read in
    whatever browser the reader has.

    Assumes a flat stylesheet: no at-rules carrying blocks, which would not
    survive the split on ``}``. highlight.js themes are machine-generated and
    uniform, and the assertion below fails loudly rather than silently
    mangling one if that ever stops being true.
    """
    css = _COMMENT.sub("", css)
    if "@" in css:
        raise ValueError(
            f"scope_css cannot scope a stylesheet containing an at-rule: "
            f"{css[css.index('@'):][:60]!r}"
        )
    scoped = []
    for rule in css.split("}"):
        if "{" not in rule:
            continue
        prelude, declarations = rule.split("{", 1)
        selectors = ", ".join(
            f"{scope} {part.strip()}"
            for part in prelude.split(",")
            if part.strip()
        )
        if selectors:
            scoped.append(f"{selectors}{{{declarations}}}")
    return "".join(scoped)


_FACE = re.compile(r"@font-face\s*\{[^}]*\}")
_WEIGHT = re.compile(r"(font-weight:\s*)([^;]*)(;)")


def _declaration(block: str, name: str) -> str | None:
    found = re.search(rf"{name}:\s*([^;]*);", block)
    return found.group(1).strip() if found else None


def merge_duplicate_faces(css: str) -> str:
    """Collapse ``@font-face`` blocks that inline the same file more than once.

    Google Fonts serves a VARIABLE font -- ``fvar``, ``gvar`` and ``avar`` are
    all present in every face we vendor -- and returns THE SAME FILE for every
    weight of a family. So ``wght@0,400;0,600;0,800`` fetches one file three
    times, and the stylesheet built from it inlines that file three times, once
    per ``font-weight`` descriptor.

    Measured on the vendored fonts.css: 31 ``@font-face`` blocks carrying 17
    distinct files, 1,576,444 raw woff2 bytes of which 706,644 are duplicates.
    Crimson Pro's upright face is in there three times over and Inter's four.
    That is 942 KB of base64 on every generated page, before any question of
    what the page uses.

    It also means 0.17.0's bold fix did not cost the 236 KB it is recorded as
    costing. Adding ``0,800`` and ``1,800`` to the request added no glyph and no
    file: it added a third and fourth copy of bytes the page already carried.

    WHY THE MERGED RANGE IS THE SAME FONT, AND WHERE IT IS NOT THE SAME PAGE.
    A ``font-weight`` descriptor on a variable face pins the ``wght`` axis, which
    is the only reason three identical files render as three different weights
    today. One block declaring the span those weights cover therefore renders
    each of THEM identically. What changes is the weights in BETWEEN: with
    discrete faces at 400/600/800 a request for 700 finds no face and CSS
    font-matching rounds it up to 800, while ``font-weight: 400 800`` makes 700
    an exact match and instantiates a real 700. Bootstrap asks for exactly that
    on ``dt`` and ``.fw-bold``, so _page-style.css.j2 pins both to 800 -- see the
    rule beside ``strong, b`` there. tests/test_fonts.py asserts the invariant
    directly: every weight any stylesheet on the page requests selects the same
    underlying file before and after this merge.

    The merged block is the first of its group with the ``font-weight``
    declaration rewritten and nothing else touched, so the payload is carried
    across verbatim rather than re-encoded.

    ON THE TWO-VALUE DESCRIPTOR AND OLD BROWSERS. ``font-weight: 400 800`` in
    an ``@font-face`` is CSS Fonts 4. A parser that does not know the form
    drops the whole descriptor, falls back to the initial value ``normal``, and
    the family ends up with one 400 face and synthesized bold -- which is the
    same class of quiet wrongness that ``scope_css`` below refuses to risk. The
    difference is where the bar sits: relaxed CSS nesting needs Chrome 120,
    Firefox 117 and Safari 17.2, while this needs Chrome 62, Firefox 62 and
    Safari 11, all shipped in 2017-2018. That is old enough not to be a
    consideration for a course handout; it is written down so the next person
    weighing a CSS Fonts 4 feature has the comparison rather than the guess.
    """
    groups: dict[tuple[str, ...], list[int]] = {}
    order: list[tuple[str, ...]] = []
    keys: list[tuple[str, ...]] = []
    for block in _FACE.findall(css):
        weight = _declaration(block, "font-weight")
        src = _declaration(block, "src")
        if weight is None or src is None:
            raise ValueError(
                f"@font-face without a font-weight or src cannot be merged: "
                f"{block[:120]!r}"
            )
        key = (
            _declaration(block, "font-family") or "",
            _declaration(block, "font-style") or "normal",
            # font-display is part of the identity, not an afterthought: two
            # faces sharing a payload but not a loading strategy are not
            # interchangeable, and a future vendor bump that changes one would
            # otherwise apply that change to its siblings silently.
            _declaration(block, "font-display") or "auto",
            _declaration(block, "unicode-range") or "",
            src,
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(int(weight))
        keys.append(key)

    emitted: set[tuple[str, ...]] = set()
    blocks = iter(keys)

    def replace(match: re.Match[str]) -> str:
        key = next(blocks)
        if key in emitted:
            return ""
        emitted.add(key)
        weights = groups[key]
        if len(weights) == 1:
            return match.group(0)
        span = f"{min(weights)} {max(weights)}"
        return _WEIGHT.sub(rf"\g<1>{span}\g<3>", match.group(0), count=1)

    merged = _FACE.sub(replace, css)
    # Two blank lines wherever a block was dropped, which is only cosmetic but
    # keeps the committed-versus-emitted diff readable when someone looks.
    return re.sub(r"\n{3,}", "\n\n", merged)


def _escape(text: str) -> str:
    # A literal </script> or </style> inside an asset would close the surrounding
    # tag early and dump the rest of the library into the page as text. None of
    # the current vendors contain one, but the escape is cheap insurance against
    # the next bump.
    text = (
        text.replace("</script>", "<\\/script>").replace("</style>", "<\\/style>")
    )
    # These strings are written into a pandoc template. Pandoc treats $var$ and
    # $if(...)$ as template syntax, and the minified libraries are full of `$`
    # (jQuery-style, regex, template literals). Double every one so pandoc emits
    # a literal dollar; $$ is pandoc's escape for $.
    return text.replace("$", "$$")


def _read(name: str) -> str:
    return _escape((ASSETS_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load() -> dict[str, str]:
    """The asset map the HTML templates expect under ``assets``."""
    missing = [f for f in _FILES.values() if not (ASSETS_DIR / f).is_file()]
    if missing:
        raise FileNotFoundError(
            f"page assets not vendored: {', '.join(missing)}; "
            f"run python3 scripts/vendor_page_assets.py"
        )
    loaded = {key: _read(name) for key, name in _FILES.items()}
    # Merged on the way out rather than in the committed file. Re-vendoring to
    # apply it would rewrite two megabytes of base64 that did not change, and
    # the committed fonts.css should keep saying what was actually fetched.
    #
    # Merged BEFORE the escape, not after: _escape doubles every `$` for
    # pandoc, and a regex reading the escaped text would be matching against
    # something fonts.css does not contain. There is no `$` in it today, which
    # is exactly why getting this order wrong would go unnoticed until the next
    # re-vendor.
    loaded["fonts_css"] = _escape(
        merge_duplicate_faces((ASSETS_DIR / _FILES["fonts_css"]).read_text("utf-8"))
    )
    # The dark code theme is scoped here rather than in the template, so the
    # template stays a single interpolation and the scoping is testable.
    loaded["highlight_css_dark"] = scope_css(
        loaded["highlight_css_dark"], ':root[data-theme="dark"]'
    )
    return loaded
