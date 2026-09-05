"""The page assets are inlined, not fetched.

stn-r81. Before this, Bootstrap, highlight.js, Mermaid and the Google webfonts
were loaded from CDNs at page open, so `make pdf` was network-bound and the
suite retried around net::ERR_NETWORK_CHANGED. AUTHORING.md also promised
"one self-contained file", which was only true of the images.
"""

from __future__ import annotations

import re

import pytest

from stencil import assets

# Hosts the templates used to load from. A generated page that still names any
# of these is not self-contained, and a PDF build will reach the network again.
CDN_HOSTS = (
    "cdn.jsdelivr.net",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
)


@pytest.fixture
def page_html(doc_package):
    return (doc_package / "html-template.html").read_text()


def test_every_vendored_asset_is_present():
    loaded = assets.load()
    for key in assets._FILES:
        assert key in loaded
        assert loaded[key], f"{key} is empty"


def test_the_generated_page_does_not_fetch_from_a_cdn(page_html):
    for host in CDN_HOSTS:
        assert host not in page_html, (
            f"the generated page still reaches {host}; "
            f"make pdf is network-bound again"
        )


def test_the_libraries_are_present_inline(page_html):
    """Spot-check content that only exists inside the vendored files."""
    assert "bootstrap" in page_html.lower()
    assert "hljs" in page_html
    assert "mermaid" in page_html.lower()
    # The fonts CSS rewrites each face to a data URI; if the rewrite was
    # skipped, the template would still name fonts.gstatic.com (caught above)
    # or ship an empty <style> block.
    assert "data:font/woff2;base64," in page_html


def test_mermaid_uses_the_umd_global_not_an_esm_import(page_html):
    """The ESM build lazily imports diagram chunks, which reintroduces a fetch."""
    assert "mermaid.esm" not in page_html
    assert "import mermaid from" not in page_html
    assert "globalThis.mermaid" in page_html


def test_a_rendered_document_stays_offline(render_soup):
    """The pandoc output, not just the template: what make doc actually writes."""
    soup = render_soup("doc", "document.md")
    html = str(soup)
    for host in CDN_HOSTS:
        assert host not in html
    # Mermaid must still initialise from the inlined UMD build.
    assert soup.find("script", string=re.compile(r"globalThis\.mermaid"))


def test_scope_css_prefixes_every_selector_in_a_list():
    from stencil.assets import scope_css

    out = scope_css("pre code.hljs{display:block}.a,.b{color:red}", "#s")
    assert out == "#s pre code.hljs{display:block}#s .a, #s .b{color:red}"


def test_scope_css_drops_comments_and_keeps_every_rule():
    from stencil.assets import scope_css

    css = "/*! banner */.a{color:red}/* mid */.b{color:blue}"
    out = scope_css(css, "#s")
    assert "banner" not in out and "mid" not in out
    assert out.count("{") == 2, "a rule was lost"


def test_scope_css_refuses_a_stylesheet_it_cannot_scope():
    """The transform splits on `}`, which an at-rule block would break. It
    fails loudly rather than silently mangling one -- highlight.js themes are
    flat today and this is what notices if a bump changes that."""
    from stencil.assets import scope_css

    with pytest.raises(ValueError, match="at-rule"):
        scope_css("@media print{.a{color:red}}", "#s")


def test_the_vendored_dark_theme_is_scoped_and_intact():
    """Every rule in the real asset survives the transform.

    Rule counts compared before and after: a selector-prefixing bug that drops
    a rule leaves code highlighting subtly wrong in dark mode and nothing else
    would notice.
    """
    from stencil.assets import ASSETS_DIR, load, scope_css

    raw = (ASSETS_DIR / "highlight-github-dark.min.css").read_text()
    expected = re.sub(r"/\*.*?\*/", "", raw, flags=re.S).count("{")
    scoped = load()["highlight_css_dark"]
    assert scoped.count("{") == expected, "the transform lost or added a rule"
    assert ":root[data-theme=\"dark\"] .hljs{" in scoped
    assert not re.search(r"(?<![\]\w]) \.hljs\{", scoped), "an unscoped rule survived"
