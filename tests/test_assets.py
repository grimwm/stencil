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
