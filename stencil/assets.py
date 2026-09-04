"""The CSS, JavaScript and webfonts inlined into every generated page.

Fetched once by ``scripts/vendor_page_assets.py`` and committed under
``stencil/assets/``. ``load()`` is what ``stencil gen`` puts into the template
context, so a handout is one self-contained file and ``make pdf`` does not
reach the network.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"

# Filenames on disk, keyed by the names the templates use.
_FILES = {
    "bootstrap_css": "bootstrap.min.css",
    "bootstrap_js": "bootstrap.bundle.min.js",
    "highlight_css": "highlight-github.min.css",
    "highlight_js": "highlight.min.js",
    "highlight_sql": "highlight-sql.min.js",
    "highlight_python": "highlight-python.min.js",
    "highlight_javascript": "highlight-javascript.min.js",
    "highlight_bash": "highlight-bash.min.js",
    "mermaid_js": "mermaid.min.js",
    "fonts_css": "fonts.css",
}


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
    return {key: _read(name) for key, name in _FILES.items()}
