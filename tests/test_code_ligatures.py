"""Code shows the characters an author typed, not ligatures drawn over them.

The code face, JetBrains Mono, ships programming ligatures on by default: `!=`
draws as a slashed equals sign, `<=` and `>=` as the math symbols, and `===`,
`&&`, `||`, `->` and `=>` as joined glyphs. The characters underneath are
unchanged, so copy and paste still works, but a handout teaching a student to
type `!=` shows them a key that does not exist. Math is MathML in its own face,
so turning the ligatures off for code costs it nothing.
"""

from __future__ import annotations

import pytest

from test_task_lists import rule_for

CONFIG = {
    "templates": [{"src": "html-template.html.j2"}],
    "packages": {"demo": {"name": "Demo", "package_type": "none", "docs": ["a.md"]}},
}


@pytest.fixture
def css(generate_package) -> str:
    """Stencil's own stylesheet, past the inlined Bootstrap."""
    text = (generate_package(CONFIG) / ".stencil" / "html-template.html").read_text()
    return text[text.index("/* Document title") :]


@pytest.mark.parametrize("selector", ["code", "pre"])
def test_code_turns_ligatures_off(css, selector):
    rule = rule_for(css, selector)
    assert rule is not None and "var(--font-mono)" in rule, (
        f"no rule gives {selector} the code face; this test is looking in the wrong place"
    )
    assert "font-variant-ligatures: none" in rule, (
        f"{selector} uses the code face with its ligatures on, so != prints as a slashed equals"
    )
