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


def _read(name: str) -> str:
    path = ASSETS_DIR / name
    text = path.read_text(encoding="utf-8")
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
    # The dark code theme is scoped here rather than in the template, so the
    # template stays a single interpolation and the scoping is testable.
    loaded["highlight_css_dark"] = scope_css(
        loaded["highlight_css_dark"], ':root[data-theme="dark"]'
    )
    return loaded
