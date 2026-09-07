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
    # The dark counterpart, pinned to the same release as the light one so a
    # bump cannot move only half the pair.
    "highlight-github-dark.min.css": (
        "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0"
        "/styles/github-dark.min.css"
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

# Crimson Pro carries 800 and 800 italic because it is the BODY face and
# <strong> lands on it. This asked for 400/600 only, so a browser resolving
# `bolder` to 700 found no face at or above it and fell back to 600 -- every
# **bold** word in a handout rendered SemiBold beside Regular, which in a serif
# at body size is a difference you have to look for rather than see. Bold
# italic was worse: with no italic above 400, `***x***` rendered as plain
# italic at regular weight.
#
# 800 rather than 700, measured rather than picked. Advance width of "the quick
# brown fox" at 18px: 400 -> 142.6, 600 -> 149.1, 700 -> 152.8, 800 -> 156.8.
# 600 is what bold used to render as, so 700 would have added 2.5% to a step
# that was already too small to read as emphasis. 900 measures identical to 800
# -- the family has nothing heavier -- so 800 is the end of the ladder, not a
# midpoint someone should try to push further.
#
# Inter is untouched: it always shipped 700, which is why headings and the
# byline looked properly bold while the prose did not.
FONT_CSS = (
    "https://fonts.googleapis.com/css2"
    "?family=Crimson+Pro:ital,wght@0,400;0,600;0,800;1,400;1,800"
    "&family=Inter:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500&display=swap"
)

# Only the subsets a Latin-script handout uses. Cyrillic/Greek/Vietnamese faces
# would roughly double the font payload for no gain on the course material.
KEEP_SUBSETS = {"latin", "latin-ext"}

# Noto Emoji, monochrome. The colour face is several megabytes and would land
# in every generated page; this one is served as ten unicode-range chunks and
# only the pictographic ones are kept.
#
# Measured, and the first measurement was wrong in a way worth recording.
# Counting the chunks that cover the six emoji in one real deck gives three,
# 212,740 bytes raw, +19%. Counting the chunks that cover the PICTOGRAPHIC
# PLANE gives nine, because Google's chunking is by frequency rather than by
# block and almost every chunk carries something in U+1F300-U+1FAFF. Nine is
# 493,000 raw, 2,117,225 in fonts.css against 1,422,226 before: +695 KB, +48%.
#
# Nine is what ships. Three would hold the payload down and turn every emoji
# an author has not used yet into a build failure, which is the opposite of
# what a character should do. The one chunk left out is flags and regional
# indicators -- a pair of letter-shaped codepoints that composes into a flag,
# which is a different feature from a pictograph and the one most likely to
# render as two letters rather than a box.
#
# The cost is real and lands on every page, including handouts with no emoji
# in them. stn-uje is the open ticket about page weight; if that gets solved
# by loading assets per-document rather than inlining all of them, this is one
# of the things that should follow.
#
# What is NOT covered still fails the build rather than printing a box:
# html-to-pdf.js refuses to write a PDF containing a character no inlined font
# can draw. So the gap this leaves is loud, which is the whole point -- before
# this, an emoji silently became an empty rectangle in a handed-out PDF.
EMOJI_CSS = (
    "https://fonts.googleapis.com/css2?family=Noto+Emoji:wght@400&display=swap"
)

# The planes worth carrying: Miscellaneous Symbols and Pictographs, Emoticons,
# Transport and Map, Supplemental Symbols and Pictographs, Symbols and
# Pictographs Extended-A.
EMOJI_RANGES = ((0x1F300, 0x1FAFF),)


def range_covers(declared: str, wanted: tuple[tuple[int, int], ...]) -> bool:
    """True when a @font-face's unicode-range overlaps a range we want."""
    for part in declared.split(","):
        part = part.strip().lstrip("uU+").replace("U+", "")
        if not part:
            continue
        if "-" in part:
            low, high = (int(x, 16) for x in part.split("-"))
        else:
            low = high = int(part, 16)
        if any(low <= b and a <= high for a, b in wanted):
            return True
    return False


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

    emoji = fetch(EMOJI_CSS).decode("utf-8")
    blocks = re.findall(r"@font-face\s*\{[^}]*\}", emoji)
    kept_emoji = []
    for block in blocks:
        declared = re.search(r"unicode-range:\s*([^;]+);", block)
        if declared and range_covers(declared.group(1), EMOJI_RANGES):
            kept_emoji.append(block)
    print(f"  Noto Emoji: keeping {len(kept_emoji)} of {len(blocks)} chunks")
    emoji_css = "\n".join(kept_emoji)
    emoji_css = re.sub(
        r"url\((https://fonts\.gstatic\.com/[^)]+)\)", replace_url, emoji_css
    )

    css = css + "\n" + emoji_css + "\n"
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
