"""A long line inside a code block must reach the PDF whole, not clipped.

stn-t3r9. A handout's answer fence is a ```text block, and a student who
types a long answer on one line gets it cut off at the right edge of the box
in the PDF they submit. The HTML shows a horizontal scrollbar, so nothing
looks wrong on screen; Chromium's print path has no scrollbars, so the
overflow is simply gone. Only the PDF's text can prove the fix, so this is
container-tier.
"""

from __future__ import annotations

import re

import pytest
from pypdf import PdfReader

pytestmark = pytest.mark.integration

# Sixty distinct words on one line: several times the width of a letter page
# at the code font, so a box that does not wrap loses most of them.
SPACED_WORDS = [f"wrapword{i:02d}" for i in range(60)]
SPACED_LINE = " ".join(SPACED_WORDS)

# One token with no break opportunity at all -- a pasted URL is the everyday
# case. `white-space: pre-wrap` alone leaves this one clipped, because there is
# nowhere to wrap; it needs overflow-wrap as well.
UNBROKEN_TOKEN = "https://example.invalid/" + "segment" * 40 + "/ENDOFTOKEN"

FENCES = f"""
**`A1:`**

```text
{SPACED_LINE}
```

**`A2:`**

```text
{UNBROKEN_TOKEN}
```
"""

DOCUMENT = f"""---
title: Wrapping
---

# Long answers

{FENCES}
"""

DECK = f"""---
title: Wrapping
---

## Long answers

{FENCES}
"""


def squashed_text(path) -> str:
    """The PDF's text with every whitespace run removed.

    A wrapped line comes back from pypdf with newlines -- and, depending on
    where the break fell, sometimes a space -- between the pieces. Dropping
    whitespace lets one substring check ask the only question that matters:
    did every character make it onto the page.
    """
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return re.sub(r"\s+", "", text)


@pytest.fixture(scope="module")
def document_text(to_pdf):
    result, pdf = to_pdf("doc", "wrapping.md", text=DOCUMENT)
    assert result.returncode == 0, result.stderr
    return squashed_text(pdf)


@pytest.fixture(scope="module")
def deck_text(to_pdf):
    result, pdf = to_pdf("slide", "wrapping-deck.md", text=DECK)
    assert result.returncode == 0, result.stderr
    return squashed_text(pdf)


@pytest.mark.parametrize("kind", ["document", "deck"])
def test_a_long_spaced_line_in_a_code_block_is_printed_whole(kind, request):
    text = request.getfixturevalue(f"{kind}_text")

    missing = [word for word in SPACED_WORDS if word not in text]
    assert not missing, (
        f"the {kind} PDF clipped the code block: {len(missing)} of "
        f"{len(SPACED_WORDS)} words never reached the page, starting at "
        f"{missing[0]!r}"
    )


@pytest.mark.parametrize("kind", ["document", "deck"])
def test_an_unbroken_token_in_a_code_block_is_printed_whole(kind, request):
    text = request.getfixturevalue(f"{kind}_text")

    assert re.sub(r"\s+", "", UNBROKEN_TOKEN) in text, (
        f"the {kind} PDF clipped a token with no break opportunity; "
        "white-space alone does not wrap it, it needs overflow-wrap too"
    )
