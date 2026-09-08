---
name: docs-and-decks
description: Build, verify and troubleshoot stencil-generated documents and slide decks. Use when wiring a new deck or handout into a package, when `make doc`, `make pdf` or `make check-access` fails, or when working from a Cowork sandbox where Docker and the repository's venv are unavailable.
---

# Stencil documents and decks

Operational guidance for repositories scaffolded by
[stencil](https://github.com/grimwm/stencil). This skill deliberately does **not** restate the
markdown dialect or the package schema — those have canonical documents, and a second copy would
drift from them.

## Read the canonical docs first

For any question about syntax, slide layout, front matter, presenter notes, or package
configuration, read the source of truth rather than guessing:

- **[AUTHORING.md](https://github.com/grimwm/stencil/blob/main/AUTHORING.md)** — the markdown
  dialect: front matter, document vs deck, slide layouts, presenter-only content, fenced divs and
  prettier, math, citations, images and tables, printing a deck.
- **[STENCIL.md](https://github.com/grimwm/stencil/blob/main/STENCIL.md)** — bundled templates,
  `.config.yaml` package schema, Makefile targets, extending stencil, accessibility.

When working *inside* the stencil repository, read the local files instead of fetching them.

**Never hand-edit generated files.** `Makefile`, `docker-compose.yml`, the Lua filters, the pandoc
templates and `.gitignore` are all rewritten by `make gen`. Change the config or the template.

## Build loop

```bash
make gen T=<package>     # after any .config.yaml change
make doc                 # student build
make doc WITH=hidden     # presenter build -> <name>-hidden.html
make check-access        # pa11y, WCAG 2.1 AA
make pdf                 # PDF/UA-1: headless Chromium + pdf-lib repairs
```

Adding a `slides:` list beside `docs:` in a package is all that is needed to get a deck; the `slide`
pandoc service is emitted automatically under `has_slides`. A markdown file belongs to `docs:` or
`slides:`, never both.

**Never use the browser's print dialog for anything handed out.** The layout is identical but the
PDF is untagged — no structure tree, no role map, no accessible names. Only `make pdf` produces a
conformant file, and PDF/UA-1 is a different standard from the WCAG 2.1 AA that pa11y measures.

## Working from a Cowork sandbox

Three constraints usually apply, and none of them are stencil bugs:

1. **No Docker in the Cowork Linux VM**, so `make gen/doc/pdf/check-access` cannot run there.
2. **The repository's venv is often host-bound** — a macOS checkout has
   `#!/Users/…/python3.14` pointing into `/opt/homebrew`, which does not exist in the VM. The
   symptom is `make: …/stencil: No such file or directory` even though the file is present.
3. **The mount forbids `unlink` but permits `rename(2)`.**

So `make` targets run on the user's own machine. Two useful things can still be done agent-side.

### Verify slide structure without Docker

pandoc is present in the Cowork VM and in the cloud container. `slide-sections.lua.j2` is plain Lua
with no Jinja, so the template is byte-identical to what `make gen` writes.

```bash
pandoc --lua-filter=hidden-filter.lua --lua-filter=slide-sections.lua -t html deck.md -o stu.html
pandoc --metadata include-hidden=true --lua-filter=hidden-filter.lua \
       --lua-filter=slide-sections.lua -t html deck.md -o pres.html
grep -o 'class="slide"' stu.html | wc -l
```

Check three things: the slide count matches the headings-plus-rules the file should produce; no
slide is empty once the hidden filter has run (the hidden filter runs *before* the splitter, so a
slide whose only content is a hidden div becomes an empty frame); and a probe string taken from a
hidden div appears in `pres.html` but not in `stu.html`.

To confirm a document survives `--fail-if-warnings`, read the real argv out of the `doc` service in
the package's `docker-compose.yml` and run it directly. Drop `--citeproc` if the local pandoc
predates 2.11.

### Build PDFs in the cloud container

The cloud container has Chromium. Run the package's own `html-to-pdf.js` rather than any substitute
— same puppeteer, same pdf-lib repair passes, so the result is a genuinely tagged PDF instead of a
print-to-PDF.

```bash
mkdir -p /workspace && cd /workspace
# copy the BUILT html and html-to-pdf.js here; the script hardcodes file:///workspace/
export PUPPETEER_SKIP_DOWNLOAD=1
npm init -y >/dev/null && npm install --no-audit --no-fund puppeteer pdf-lib
export PUPPETEER_EXECUTABLE_PATH=/opt/pw-browsers/chromium-*/chrome-linux/chrome
node html-to-pdf.js Deck.html Deck.pdf
```

Success prints the repairs applied. Verify with pdf-lib that `StructTreeRoot` and `Metadata` are
present and the page geometry is right — letter landscape 792x612pt for decks, portrait for
documents. A deck PDF runs one page longer than its slide count; that is the cover page.

**Disclose this when you do it.** The container's Chromium differs from the one in
`Dockerfile.browser`, and Chromium's tag vocabulary is exactly what most of those repair passes
react to. Faithful for review; the user's own `make pdf` remains the authority for a shipped
artifact.

## Git on a Cowork mount

- **Never run `git status` or `git diff` there.** They refresh the index, then roll the refresh back
  by unlinking `.git/index.lock` — the one forbidden call. The lock survives and wedges every later
  `git add`, `commit` and `stash`, for the human in their own terminal as much as for the agent.
  Recovery is `rm .git/index.lock`, which the agent cannot perform.
- **Do not run git write commands there either.** `git commit` strands `.git/HEAD.lock`,
  `.git/objects/maintenance.lock` and `tmp_obj_*` files. A stranded `HEAD.lock` makes every later
  `git switch` and `git commit` fail with `fatal: unable to update HEAD`, and a switch that dies
  that way can rewrite the index while leaving HEAD behind — which surfaces as one file reported
  both staged and unstaged. The repair is `git restore --staged <file>`; HEAD and the working tree
  are fine, only the index is stale.
- Safe to read: `git log`, `git show`, `git rev-list`, `git cat-file`, `git ls-files`,
  `git hash-object`, `git rev-parse`.
- **Prefer a GitHub connector for anything that writes.** Creating branches, commits and pull
  requests through the API happens server-side, takes no local lock, and needs no SSH key in a
  sandbox. Never ask for a private key to be placed in one. Where no connector exists, prepare the
  files and let the human run git.
- Roll forward with `git revert` or a follow-up fix commit. Never `git reset --hard`, and never
  rewrite already-pushed history.

## Known trap: inline code in a table header

Inline `code` inside a `thead` cell fails WCAG AA in the light theme. `--code-inline` (`#c7254e`) on
`--surface-accent-on` (`#d2def2`) measures 4.07:1. That colour clears 4.5:1 on every other surface
in the theme — white 5.52, even rows 5.15, caption 4.73, code blocks 5.19 — so the header fill is
the only place it fails. The dark pairing (`#ff9ab0` on `#2f4680`) passes at 4.55:1, a 0.05 margin.

Until the palette changes, **do not put backticks in a markdown table header row**; put the literal
values in the body cells instead. The upstream fix is `--code-inline: #b01f45` (4.95:1 on the header,
6.71:1 on white, indistinguishable to the eye) or a scoped `th code {}` rule.

## Course content from an adopted textbook

When a course adopts a third-party textbook, use it to decide *what* to teach and in what order.
Write the prose and every code example fresh. Do not reproduce its sentences, figure captions,
example pages, or activity items into distributable slides and handouts.
