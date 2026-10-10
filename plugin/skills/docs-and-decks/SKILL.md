---
name: docs-and-decks
description: Build, verify and troubleshoot stencil-generated documents and slide decks. Use when wiring a new deck or handout into a package, when `make doc`, `make pdf` or `make check-access` fails, or when working from a Cowork sandbox where Docker and a host-built venv are unavailable.
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
templates and `.gitignore` are all rewritten by `stencil gen`. Change the config or the template.

## Build loop

```bash
stencil gen <package>    # after any .config.yaml change; regenerates the package's Makefile
make doc                 # student build
make doc WITH=hidden     # presenter build -> <name>-hidden.html
make check-access        # pa11y, WCAG 2.1 AA
make pdf                 # PDF/UA-1: headless Chromium + pdf-lib repairs
make check-pdf           # veraPDF, PDF/UA-1 conformance
```

The `make` targets are the ones stencil writes into the generated `Makefile` (`STENCIL.md`,
"Makefile Targets"). A consuming repository may wrap `stencil gen` in a target of its own; if so,
that wrapper is the repository's convention, not stencil's — read its Makefile rather than assuming
a name.

Adding a `slides:` list beside `docs:` in a package is all that is needed to get a deck; the `slide`
pandoc service is emitted automatically under `has_slides`. A markdown file belongs to `docs:` or
`slides:`, never both.

**Never use the browser's print dialog for anything handed out.** The layout is identical but the
PDF is untagged — no structure tree, no role map, no accessible names. Only `make pdf` produces a
conformant file, and PDF/UA-1 is a different standard from the WCAG 2.1 AA that pa11y measures.

## Working from a Cowork sandbox

Three constraints usually apply, and none of them are stencil bugs:

1. **No Docker in the Cowork Linux VM**, so `make doc`, `make pdf` and `make check-access` cannot
   run there.
2. **A venv created on the host is host-bound.** Its console scripts carry the host interpreter's
   absolute path in their shebang — `#!/Users/…/python3.14`, say, pointing into `/opt/homebrew` —
   and that path does not exist in the VM. The symptom is `…/stencil: No such file or directory`
   even though the file is present. Where the checkout keeps its venv is the repository's business;
   the symptom is the same wherever it is.
3. **The mount forbids `unlink` but permits `rename(2)`.**

So `make` targets run on the user's own machine. Two useful things can still be done agent-side.

### Verify slide structure without Docker

pandoc is present in the Cowork VM and in the cloud container. `slide-sections.lua.j2` is plain Lua
with no Jinja, so the template is byte-identical to what `stencil gen` writes.

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
# copy the BUILT html, html-to-pdf.js AND browser-package-lock.json here;
# the script hardcodes file:///workspace/
export PUPPETEER_SKIP_DOWNLOAD=1
# Install the image's tree, not an approximation of it. The manifest is the
# single-quoted JSON on the `printf ... > /opt/tools/package.json` line in the
# package's Dockerfile.browser; the lockfile is generated beside it.
printf '%s\n' '<the JSON from Dockerfile.browser>' > package.json
cp -f browser-package-lock.json package-lock.json
npm ci --no-audit --no-fund --ignore-scripts
chrome=(/opt/pw-browsers/chromium-*/chrome-linux/chrome)   # a glob does not expand inside an assignment
test -x "${chrome[0]}"
export PUPPETEER_EXECUTABLE_PATH="${chrome[0]}"
node html-to-pdf.js Deck.html Deck.pdf
```

`--ignore-scripts` keeps a dependency's install hook from running against the files just copied
in; nothing here needs one, since Chromium is supplied rather than downloaded. Do not substitute
`npm install` and a list of names: `npm ci` against the package's own lockfile is what makes the
JavaScript identical to the image's rather than merely the same two top-level versions, and it
verifies every tarball against a sha512 on the way in. It installs pa11y as well, which this
build does not use — that is the cost of installing the image's tree rather than a subset of it.

What is left of the gap is Chromium, which stencil deliberately does not pin (see
`Dockerfile.browser`'s comment). This is a review build with the image's JavaScript and a
different browser, not a reproduction of the image.

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
  Recovery is for the human to remove the stale `.git/index.lock` from their own terminal, after
  confirming no git process is still running (`pgrep -x git` prints nothing); the agent cannot
  perform it, and removing a lock that a live git process holds can corrupt the index.
- **Do not run git write commands there either.** `git commit` strands `.git/HEAD.lock`,
  `.git/objects/maintenance.lock` and `tmp_obj_*` files. A stranded `HEAD.lock` makes every later
  `git switch` and `git commit` fail with `fatal: unable to update HEAD`, and a switch that dies
  that way can rewrite the index while leaving HEAD behind — which surfaces as one file reported
  both staged and unstaged. The repair is `git restore --staged <file>`, run from a normal
  checkout rather than the mount; HEAD and the working tree are fine, only the index is stale.
- Safe to read: `git log`, `git show`, `git rev-list`, `git cat-file`, `git ls-files`,
  `git hash-object`, `git rev-parse`.
- **Prefer a GitHub connector for anything that writes.** Creating branches, commits and pull
  requests through the API happens server-side, takes no local lock, and needs no SSH key in a
  sandbox. Never ask for a private key to be placed in one. Where no connector exists, prepare the
  files and let the human run git.
- Roll forward with `git revert` or a follow-up fix commit, again from a normal checkout or
  through the connector. Never `git reset --hard`, and never rewrite already-pushed history.

## Course content from an adopted textbook

When a course adopts a third-party textbook, use it to decide *what* to teach and in what order.
Write the prose and every code example fresh. Do not reproduce its sentences, figure captions,
example pages, or activity items into distributable slides and handouts.
