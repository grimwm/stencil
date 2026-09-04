# Page assets

The CSS, JavaScript and webfonts every generated HTML file carries inline.
`stencil gen` reads these files and embeds them into the pandoc templates, so a
handout is one self-contained file and `make pdf` does not touch the network.

Refresh with:

```bash
python3 scripts/vendor_page_assets.py
```

Then commit the changed files with whatever template or version bump required
the refresh. Do not point the templates back at CDN URLs.
