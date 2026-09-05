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

THEME_CONFIG = {
    "templates": [{"src": "html-template.html.j2"}],
    "packages": {
        "demo": {"name": "Demo", "package_type": "none", "docs": ["a.md"]}
    },
}


def source(template: str) -> str:
    return (TEMPLATES / template).read_text()


def strip_comments(css: str) -> str:
    """Comments discuss colours constantly; only declarations matter here."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def token_blocks(css: str) -> list[str]:
    """Every `:root ... {...}` block -- light, and the dark one under @media
    screen. The only places a colour may be written."""
    return [m.group(0) for m in re.finditer(r":root[^{]*\{[^}]*\}", css, re.S)]


def without_token_blocks(css: str) -> str:
    """The stylesheet with every token block removed.

    Removed one at a time. Joining them and doing a single replace worked
    while there was one block and silently stopped matching the moment the
    dark one arrived -- the joined string is not a substring of anything.
    """
    for block in token_blocks(css):
        css = css.replace(block, "")
    return css


@pytest.mark.parametrize("template", STYLESHEETS)
def test_no_raw_colour_outside_the_token_blocks(template):
    """The drift guard, and the most valuable test in this file.

    Every colour is a token, so a hardcoded one reintroduced later is a value
    that silently escapes theming -- it would render correctly in light, wrong
    in dark, and nothing else in the suite would notice.
    """
    css = strip_comments(source(template))
    stray = sorted(set(COLOUR.findall(without_token_blocks(css))))
    assert not stray, f"{template}: colours declared outside :root: {stray}"


def tokens(css: str, selector: str = ":root") -> dict[str, str]:
    """The custom properties declared by one selector's block.

    Values are returned raw; resolve() below chases `var()` indirection.
    """
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css, re.S)
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


def test_every_token_used_is_a_token_defined():
    """A `var(--typo)` renders as nothing at all, silently.

    The two stylesheets share one :root -- a deck includes the document
    stylesheet -- so this checks them together. CSS has no error for an
    undefined custom property: the declaration is simply dropped, which looks
    identical to a rule that was never written.
    """
    page = strip_comments(source("_page-style.css.j2"))
    deck = strip_comments(source("_slide-style.css.j2"))
    defined = set(tokens(page)) | {
        t for t in re.findall(r"(--bs-[a-z0-9-]+)\s*:", page)
    }
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", page + deck))
    missing = sorted(used - defined)
    assert not missing, f"used but never defined: {missing}"


def screen_blocks(css: str) -> list[str]:
    """Every `@media screen` block, brace-matched.

    Slicing to the first `}` would stop inside the first rule and hide
    everything after it, which makes the containment test below pass for no
    reason at all.
    """
    blocks = []
    for m in re.finditer(r"@media screen[^{]*\{", css):
        depth, i = 1, m.end()
        while depth and i < len(css):
            depth += (css[i] == "{") - (css[i] == "}")
            i += 1
        blocks.append(css[m.start() : i])
    return blocks


def dark_block(css: str) -> str:
    m = re.search(r':root\[data-theme="dark"\][^{]*\{([^}]*)\}', css, re.S)
    assert m, "no dark token block"
    return m.group(1)


def test_every_dark_declaration_is_sealed_inside_media_screen():
    """The print guarantee, asserted directly rather than inferred.

    Print never matches @media screen, so a dark block living there is
    invisible to the print formatter and the light :root values apply. This is
    why no light value is re-declared for print anywhere: there is nothing to
    undo. A dark rule written outside that wrapper would reach the PDF, and
    every handout would print dark.
    """
    css = strip_comments(source("_page-style.css.j2"))
    outside = css
    for block in screen_blocks(css):
        outside = outside.replace(block, "")
    assert "data-theme" not in outside, (
        "a theme-conditional rule sits outside @media screen; print sees it"
    )


def test_the_dark_block_defines_exactly_the_light_token_names():
    """Parity. A token defined light but not dark keeps its light value in
    dark mode -- which is how a single unreadable element ships."""
    css = strip_comments(source("_page-style.css.j2"))
    light = {t for t in tokens(css) if not t.startswith("--font-")}
    dark = set(re.findall(r"(--[a-z0-9-]+)\s*:", dark_block(css)))
    dark = {t for t in dark if not t.startswith("--bs-")}
    # Print tokens are deliberately light-only: print never sees the dark
    # block, so overriding them there would be dead code.
    light = {t for t in light if not t.startswith("--print-")}
    assert dark == light, (
        f"light only: {sorted(light - dark)}; dark only: {sorted(dark - light)}"
    )


@pytest.mark.parametrize("token", TEXT_TOKENS)
def test_dark_palette_text_meets_aa(token):
    css = strip_comments(source("_page-style.css.j2"))
    table = dict(tokens(css))
    table.update(
        dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", dark_block(css)))
    )
    fg = resolve(table[token], table)
    bg = resolve(table["--surface"], table)
    ratio = contrast(fg, bg)
    assert ratio >= 4.5, f"{token} ({fg}) on {bg} is {ratio:.2f}:1, under 4.5:1"


def test_the_context_line_keeps_program_section_and_term_together():
    """program, section and term share one line, joined the way a reader
    parses them: the section belongs to its program, so it attaches with a
    full stop and no gap -- "CS 425.001" -- while the term is a separate fact
    behind a middot.

    Asserted against the stylesheet because the separators are ::before
    generated content. They are not in the DOM, so BeautifulSoup cannot see
    them and every structural test in this suite reads straight past them.
    Confirmed once in a real browser: computed content "." at margin 0 for the
    section, "\\00b7" at 6.4px either side for the term, and .doc-term
    computing to `inline`.
    """
    css = strip_comments(source("_page-style.css.j2"))

    assert not re.search(r"\.doc-term\s*\{[^}]*display:\s*block", css), (
        ".doc-term is display:block again; the term has left the program's line"
    )

    section = re.search(
        r"\.doc-context\s+\.doc-section::before\s*\{([^}]*)\}", css, re.S
    )
    assert section, "no section separator rule"
    assert 'content: "."' in section.group(1)
    assert re.search(r"margin:\s*0\b", section.group(1)), (
        "the section separator has a gap; CS 425 . 001 is not one identifier"
    )

    sibling = re.search(
        r"\.doc-context\s+span\s*\+\s*span::before\s*\{([^}]*)\}", css, re.S
    )
    assert sibling and "\\00b7" in sibling.group(1), (
        "the general context separator is no longer a middot"
    )


def test_the_byline_labels_every_field_it_names():
    """Author sits beside Issued and Due, so it is labelled like them. An
    unlabelled author next to two labelled dates reads as an oversight."""
    body = (TEMPLATES / "_doc-body.html.j2").read_text()
    for field in ["Author", "Issued", "Due", "Points"]:
        assert f'<span class="doc-label">{field}</span>' in body, field


HEAD = TEMPLATES / "_page-head.html.j2"


def theme_script() -> str:
    """The inline theme <script>, brace-matched to its own tags.

    Slicing a fixed character window around a marker was the first attempt and
    was wrong for the usual reason: it silently measures whatever happens to
    sit nearby rather than the thing being asserted about.
    """
    head = HEAD.read_text()
    start = head.rindex("<script>", 0, head.index("__stencilTheme"))
    end = head.index("</script>", start)
    return head[start:end]


def test_theme_resolution_runs_before_the_stylesheets():
    """Order is the whole point. A stylesheet parsed before the theme is
    resolved paints light first, which is a white flash for a dark reader on
    every single page load."""
    head = HEAD.read_text()
    script = head.index("__stencilTheme")
    first_style = head.index("<style>")
    assert script < first_style, (
        "the theme script runs after a stylesheet; dark readers get a flash"
    )


def test_every_storage_access_is_guarded():
    """Private windows and blocked site data throw on localStorage access,
    and html-to-pdf.js fails the build on an uncaught page error -- so an
    unguarded read turns a browser setting into a broken PDF."""
    block = theme_script()
    for access in ["localStorage.getItem", "localStorage.setItem"]:
        assert access in block, f"{access} not found"
    # Each access sits inside a try, and each try has a catch.
    assert block.count("try {") >= 2, "a storage access is unguarded"
    assert block.count("try {") == block.count("catch"), "a try without a catch"


def test_an_explicit_choice_outranks_the_operating_system():
    """Following an OS change while the reader has explicitly chosen would
    silently overturn their choice."""
    head = HEAD.read_text()
    m = re.search(r"query\.addEventListener\('change',[^}]*\}", head, re.S)
    assert m, "no matchMedia listener"
    assert "read() === 'system'" in m.group(0), (
        "the OS listener does not check the stored preference first"
    )


TOGGLE = TEMPLATES / "_theme-toggle.html.j2"


def test_the_control_is_a_radiogroup_with_three_named_options():
    """Three mutually exclusive states, so a radiogroup rather than three
    buttons -- a screen reader should say "2 of 3", not name three unrelated
    controls."""
    markup = TOGGLE.read_text()
    assert "role', 'radiogroup'" in markup or 'role\', \'radiogroup\'' in markup
    assert markup.count("'radio'") >= 1
    for value in ["'light'", "'dark'", "'system'"]:
        assert value in markup, f"no {value} option"
    assert "aria-label', 'Color theme'" in markup


def test_the_pressed_segment_tracks_the_preference_not_the_resolved_theme():
    """With System chosen on a dark OS, data-theme is "dark" while the
    pressed segment must still read System. Tracking the resolved value would
    have the control report a choice the reader never made."""
    markup = TOGGLE.read_text()
    m = re.search(r"subscribe\(function \(theme, pref\) \{(.*?)\n      \}\);",
                  markup, re.S)
    assert m, "no subscription"
    body = m.group(1)
    assert "=== pref" in body, "the pressed segment is not compared against pref"
    assert "=== theme" not in body, "the pressed segment follows the resolved theme"


def test_the_glyph_is_hidden_and_the_label_carries_the_name():
    """The glyph is decorative. If it were the accessible name, the control
    would announce as an unpronounceable character."""
    markup = TOGGLE.read_text()
    assert 'aria-hidden="true"' in markup
    assert "theme-toggle__label" in markup
    css = strip_comments(source("_page-style.css.j2"))
    label = re.search(r"\.theme-toggle__label\s*\{([^}]*)\}", css, re.S)
    assert label, "no visually-hidden rule for the label"
    assert "display: none" not in label.group(1), (
        "display:none would remove the accessible name, not just hide it"
    )


def test_the_control_is_hidden_in_print():
    css = strip_comments(source("_page-style.css.j2"))
    print_blocks = re.findall(r"@media print[^{]*\{(.*?)\n    \}", css, re.S)
    assert any("theme-toggle" in b and "display: none" in b for b in print_blocks), (
        "the theme control is not hidden in print"
    )


def test_every_byline_block_opts_out_of_the_title_scale():
    """.doc-title is 2.2rem/700 and these are its descendants.

    Naming the children is not enough: a flex container's own font-size still
    raises a strut, so .doc-facts inherited 2.2rem and padded a 27px row to
    60px while every child rendered at the right size. Measured in a browser,
    invisible in the markup.
    """
    css = strip_comments(source("_page-style.css.j2"))
    m = re.search(
        r"((?:\.doc-[a-z]+,\s*)+\.doc-[a-z]+)\s*\{[^}]*font-size:\s*1rem", css, re.S
    )
    assert m, "no byline font-size rule"
    named = set(re.findall(r"\.doc-[a-z]+", m.group(1)))
    assert {".doc-meta", ".doc-facts", ".doc-context"} <= named, (
        f"a byline block still inherits the title's 2.2rem: {named}"
    )


def test_the_dark_code_theme_is_sealed_in_media_screen(generate_package):
    """The vendored dark highlight theme is a dark declaration like any other
    and obeys the same containment: print must not see it, or a printed code
    listing comes out white-on-black."""
    page = (generate_package(THEME_CONFIG) / "html-template.html").read_text()
    assert ':root[data-theme="dark"] .hljs{' in page, "dark theme not emitted"
    sealed = "".join(screen_blocks(page))
    assert ':root[data-theme="dark"] .hljs{' in sealed, (
        "the dark code theme is outside @media screen; print would see it"
    )



SCRIPTS = TEMPLATES / "_page-scripts.html.j2"


def test_mermaid_stashes_its_source_before_the_first_render():
    """mermaid.run() replaces the element's contents with SVG, so a redraw has
    nothing to redraw from unless the source was kept."""
    js = SCRIPTS.read_text()
    assert "dataset.mermaidSource = text" in js
    assert js.index("dataset.mermaidSource = text") < js.index("await renderMermaid()")


def test_mermaid_ready_is_set_once_and_never_cleared():
    """html-to-pdf.js blocks on this flag. A redraw that reset it would hang
    the build until the two-minute timeout."""
    js = SCRIPTS.read_text()
    assert js.count("window.__mermaidReady = true") == 1
    assert "__mermaidReady = false" not in js
    assert "delete window.__mermaidReady" not in js
    # And the ready event fires once, so the tab code runs once.
    assert js.count("new Event('mermaid-ready')") == 1


def test_mermaid_redraw_skips_the_subscription_callback():
    """subscribe() calls back immediately with the current theme. Redrawing on
    that call would render every diagram twice on load, doubling the slowest
    part of the page."""
    js = SCRIPTS.read_text()
    m = re.search(r"__stencilTheme\.subscribe\(function \(\) \{(.*?)\n      \}\);", js, re.S)
    assert m, "no theme subscription"
    assert "firstCall" in m.group(1), "the immediate callback is not skipped"


def test_mermaid_reads_its_palette_from_the_tokens():
    """A hardcoded second palette drifts from the first. These are read off
    the live custom properties, so a token change moves the diagrams too."""
    js = SCRIPTS.read_text()
    assert "getPropertyValue" in js
    for token in ["--surface", "--text", "--accent"]:
        assert f"token('{token}')" in js, token
