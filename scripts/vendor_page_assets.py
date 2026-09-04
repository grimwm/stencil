#!/usr/bin/env python3
"""Download the page assets stencil inlines into every generated HTML file.

Run from the repo root after deciding to bump a library version:

    python3 scripts/vendor_page_assets.py

The files land in stencil/assets/. Commit them with the template change that
consumes them -- a floating CDN URL is exactly what this replaces.
"""

from __future__ import annotations

import base64
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "stencil" / "assets"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Pin every URL to a release. A floating @11 for Mermaid is how we got here.
FILES = {
    "bootstrap.min.css": (
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
    ),
    "bootstrap.bundle.min.js": (
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"
    ),
    "highlight-github.min.css": (
        "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css"
    ),
    "highlight.min.js": (
        "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"
    ),
    "highlight-sql.min.js": (
        "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/sql.min.js"
    ),
    "highlight-python.min.js": (
        "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/python.min.js"
    ),
    "highlight-javascript.min.js": (
        "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0"
        "/languages/javascript.min.js"
    ),
    "highlight-bash.min.js": (
        "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/bash.min.js"
    ),
    # UMD build: no dynamic imports, exposes globalThis.mermaid. The ESM build
    # lazily fetches diagram chunks, which would reintroduce a network
    # dependency the whole point of vendoring is to remove.
    "mermaid.min.js": (
        "https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js"
    ),
}

FONT_CSS = (
    "https://fonts.googleapis.com/css2"
    "?family=Crimson+Pro:ital,wght@0,400;0,600;1,400"
    "&family=Inter:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500&display=swap"
)

# Only the subsets a Latin-script handout uses. Cyrillic/Greek/Vietnamese faces
# would roughly double the font payload for no gain on the course material.
KEEP_SUBSETS = {"latin", "latin-ext"}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    print(f"  {len(data):8d}  {url}")
    return data


def vendor_plain() -> None:
    for name, url in FILES.items():
        (OUT / name).write_bytes(fetch(url))


def vendor_fonts() -> None:
    css = fetch(FONT_CSS).decode("utf-8")
    parts = re.split(r"(/\* [^*]+ \*/)", css)
    kept: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        m = re.fullmatch(r"/\* ([^*]+) \*/", part.strip())
        if m and i + 1 < len(parts):
            subset = m.group(1).strip()
            block = parts[i + 1]
            if subset in KEEP_SUBSETS:
                kept.append(part)
                kept.append(block)
            i += 2
            continue
        if part.strip() and not part.strip().startswith("/*"):
            kept.append(part)
        i += 1

    css = "".join(kept)

    def replace_url(match: re.Match[str]) -> str:
        font = fetch(match.group(1))
        b64 = base64.b64encode(font).decode("ascii")
        return f"url(data:font/woff2;base64,{b64})"

    css = re.sub(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", replace_url, css)
    (OUT / "fonts.css").write_text(css)
    print(f"  fonts.css written ({(OUT / 'fonts.css').stat().st_size} bytes)")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Vendoring page assets into {OUT.relative_to(ROOT)}/")
    vendor_plain()
    vendor_fonts()
    print("done:")
    for path in sorted(OUT.iterdir()):
        if path.name.startswith("."):
            continue
        print(f"  {path.stat().st_size:8d}  {path.name}")


if __name__ == "__main__":
    main()
