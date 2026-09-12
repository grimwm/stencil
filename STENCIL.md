# Stencil Template Specification

Stencil is a general-purpose scaffolding tool that generates project files from Jinja2 templates.
It can scaffold any type of project - documents, web applications, assignments, or custom workflows.
This document describes the bundled templates, features, and conventions.

## Document Generation

### Basic Usage

```bash
make doc              # Generate HTML documents from markdown files
make pdf              # Print the generated HTML to PDF
make format-md        # Format markdown files with prettier
```

### Features (WITH variable)

Use the `WITH` variable to include optional content sections in your documents:

```bash
make doc WITH=hidden              # Include hidden sections
make doc WITH=hidden,draft        # Include multiple features (comma-separated)
make doc with=hidden              # Lowercase also works
```

**Output filenames** reflect the features used:

- `make doc` → `Document.html`
- `make doc WITH=hidden` → `Document-hidden.html`
- `make doc WITH=hidden,draft` → `Document-hidden-draft.html`

### Available Features

| Feature  | Description                              | Markdown Syntax         |
| -------- | ---------------------------------------- | ----------------------- |
| `hidden` | Answer keys, solutions, instructor notes | `::: {.hidden} ... :::` |

### Markdown Syntax for Features

Wrap content in fenced divs to control visibility:

```markdown
## Practice Questions

1. What is 2 + 2?
2. Explain the concept of recursion.

::: {.hidden}

## Answers

1. 4
2. Recursion is when a function calls itself...
   :::
```

The content inside `::: {.hidden}` will only appear when `WITH=hidden` is specified.

### Blockquotes as Callout Cards

Markdown blockquotes (`>`) render as styled callout cards with borders and shadows. Use them for
notes, tips, or "Think about it" prompts:

```markdown
> **Think about it:** Why might this approach be inefficient for large datasets?
```

### Math Support

Math is rendered using MathML, which is natively supported by modern browsers:

```markdown
Inline math: $E = mc^2$

Block math:
$$\sum_{i=1}^{n} i = \frac{n(n+1)}{2}$$
```

### Citations

Pandoc's citation processor is enabled. Set `bibliography:` in the markdown front matter and cite with
`[@key]`; the reference list is generated where an empty `::: {#refs}` div appears, or appended at the
end. `--citeproc` runs after `hidden-filter.lua` so that a work cited only inside a `::: {.hidden}`
block stays out of the reference list of a build that drops it, and before `slide-sections.lua` so the
generated list is grouped into a slide.

### Build Failures

Pandoc runs with `--fail-if-warnings`, so a warning stops the build instead of shipping a damaged
page. In practice this catches two things: a citation key with no matching bibliography entry, which
would otherwise render as `(**key?**)`, and an unclosed fenced div. A missing image is reported by
`embed-images.lua` on stderr and does not fail the build, because the broken figure is visible in the
page itself.

### Code Syntax Highlighting

Code blocks are highlighted using highlight.js with the GitHub theme:

````markdown
```sql
SELECT * FROM users WHERE active = true;
```
````

### Mermaid Diagrams

Mermaid code blocks (```` ```mermaid ````) are rendered in the browser by Mermaid.js. The HTML template
replaces each block with a `div.mermaid`, runs Mermaid, then dispatches `mermaid-ready` so that the
nav-tabs script runs only after diagrams are rendered (avoiding layout errors from moving nodes
into hidden tabs mid-render).

### Printing and PDFs

There are two ways to reach a PDF, and they produce the same document because both render through
the same print stylesheet:

- `make pdf` prints every generated HTML file to a PDF beside it (`Document.html` →
  `Document.pdf`), using headless Chromium in a container. It depends on `doc`, so it always prints
  the HTML from the current build rather than whatever was left over from the last one. Feature
  variants pair up: `make pdf with=hidden` produces `Document-hidden.pdf` from
  `Document-hidden.html`.
- Your browser's print function (Ctrl+P / Cmd+P) on any built HTML file.

Either way you get:

- Proper page margins (1 inch for documents; decks print letter landscape, one slide per page)
- Print-safe fonts and sizes
- Code blocks and tables won't break across pages
- Colored backgrounds preserved for headers and callouts
- Tabbed sections expanded, so nothing is hidden behind a tab nobody can click on paper

`make pdf` fails instead of writing a file when an asset does not load or the page's client-side
rendering never finishes. Bootstrap, highlight.js, Mermaid and the webfonts are inlined into the
HTML at generate time, so a typical handout never makes a network request; the refusal still
catches a page that has been edited to fetch something that is not there.

## Project Structure

Stencil generates files based on your `.config.yaml` template list. For document packages, the
bundled templates produce:

| File                 | Purpose                                           |
| -------------------- | ------------------------------------------------- |
| `Makefile`           | Build targets (doc, pdf, format-md, clean, etc.)  |
| `docker-compose.yml` | Container definitions for doc and PDF generation  |
| `html-template.html` | HTML template with Bootstrap 5 styling            |
| `hidden-filter.lua`  | Pandoc filter for conditional content             |
| `html-to-pdf.js`     | Headless-Chromium driver behind `make pdf`        |
| `Dockerfile.browser` | Chromium image shared by `pdf` and `check-access` |

Two of them are npm lockfiles, and they are generated output like everything else
here — `stencil clean` removes them and the managed `.gitignore` section covers
them. Do not edit them by hand; `stencil gen` overwrites them from stencil's own
committed copies.

| File                        | Purpose                                                     |
| --------------------------- | ----------------------------------------------------------- |
| `browser-package-lock.json` | What `Dockerfile.browser`'s `npm ci` installs, hash by hash |
| `format-package-lock.json`  | The same for the `format-md` service's prettier             |

`browser-package-lock.json` arrives with the pandoc/Chromium templates, for any
package that renders markdown — `Dockerfile.browser` copies it into the image, so
the two always travel together. `format-package-lock.json` arrives for *every*
package, with no condition: `make format-md` formats the markdown a package
contains whether or not it renders any, and a compose file can reach stencil's
format-md service through a composition template stencil cannot inspect from the
outside. It is 1.3 KB, and `stencil clean` removes it either way.

### The Manifest

Every package `stencil gen` writes also gets `.stencil-manifest.json` in its own
directory: `manifest_version`, the `stencil_version` that wrote it, the package's
id and `dir`, and `entries` — every file and build-artifact glob that package
generates, relative to the package directory. It is generated output like
everything else on this page — the managed `.gitignore` section covers it,
`stencil clean` removes it, last, after the rest of the package — and it is
never hand-edited; `stencil gen` overwrites it in full on every run.

`stencil clean` reads a package's manifest in preference to re-deriving the
removal list from `.config.yaml`; see [When the config is
wrong](#when-the-config-is-wrong) for what that buys.

**What this does not protect.** The manifest is rewritten on every `gen`, so it
protects `edit config → clean → gen` but not `edit config → gen → clean`:
regenerating replaces the manifest with the new, shorter list, and the orphans
from the old config are then named by nothing — not the new manifest, not the
config as it reads now. Clean a package before you regenerate it, not after,
whenever you have just trimmed its config. Unioning the old manifest's entries
into the new one was considered and rejected: a stale name that an author later
re-creates by hand would then be deleted by a clean that believes it just
generated it. The manifest's authority is deliberately narrow — what `gen`
produced *this run*, not the union of everything it has ever produced.

Two more cases fall out of the same design and are not bugs:

- **A package whose `dir` was renamed after `gen`** is orphaned in full, the
  same as a package deleted from the config outright. `clean` looks for the
  manifest under the *current* `dir`; finding none, it derives from the config
  as it reads today, which no longer mentions the old location, so nothing
  under it is removed.

- **A package-level `output_dir`** can put build artifacts outside the package
  directory. `clean` only unlinks a path that resolves under the package's own
  directory — the same containment rule that keeps a symlinked package
  directory from reaching outside the output tree — so those artifacts are
  never in scope. That is a deliberate limit of what `clean` will touch, not a
  gap in the manifest.

  This is the supported way to build somewhere else, and the escape is
  deliberate — unlike the *top-level* `output_dir`, which must stay under the
  config file's directory (see [Configuration](#configuration) below). Note
  that the package-level key is not itself path-validated yet: `stn-1a4`
  tracks putting it through the same checks every other configured path
  already gets.

- **Packages sharing one `dir` share one manifest, and one blast radius.**
  There is a single `.stencil-manifest.json` in that directory and `gen`
  rewrites it, so it names whichever package generated last. `clean` unions it
  with the config-derived entries of every other package configured with that
  directory, so nothing is left behind — but the consequence is that
  `stencil clean alpha` removes what `beta` generated there too, because a
  manifest naming `beta` cannot be removed without removing what it names.
  Give two packages the same `dir` only when cleaning one should clean both.
  If the config *also* does not parse, the member the manifest does not name
  cannot be derived from anywhere: it is reported by name and `clean` exits
  non-zero, having removed only what the manifest listed.

You can create custom templates for any project type. Templates are Jinja2 files (`.j2` suffix)
that have access to the package context variables.

## Configuration

Projects configure stencil via `.config.yaml`:

```yaml
templates_dir: ../_generator/templates # Optional: custom templates (searched first)
output_dir: . # Where to generate packages (must stay under this file's directory)

templates: # Which templates to render
  - src: Makefile.j2
  - src: docker-compose.yml.j2
  - src: html-template.html.j2
  - src: hidden-filter.lua.j2
  - src: custom-script.sh.j2 # Any custom templates
    dest: setup.sh # Optional: rename output file
    when: has_mysql # Optional: conditional rendering

packages:
  lessons:
    name: "Classroom Lessons"
    dir: lessons
    package_type: doc
    docs:
      - RDCIS.md
      - Introduction.md
```

Templates are searched in order: `templates_dir` (if specified), then bundled stencil templates.
This allows projects to override or extend the default templates.

`output_dir` is resolved **relative to this file**, not to the working directory, and it must stay
under this file's directory. A value that escapes — `../build`, an absolute path, or a directory
that is itself a symlink pointing out — is refused with an error naming the key. That is a change
in behaviour: such a config generated at exit 0 before 0.39.0.

Two halves of that check reach different commands, which is worth knowing before you rely on it.
The **string** checks — not a string, `..`, absolute, `~`, whitespace, a metacharacter — run in the
config pre-flight, so every command applies them. The **containment** check, the only half that can
see an `output_dir` that is itself a *symlink* out of the tree, runs where the output base is
computed: `gen` and `clean` reach it, while `install` and `list` return above it. `install` never
writes through the output base, so nothing outside the tree is touched either way — the refusal is
deferred to the next `gen` or `clean`, not skipped.

If you were using the top-level key to build somewhere else, the **package-level** `output_dir` is
the supported way to do that and is untouched here — but read it as a different key rather than the
same one from another scope: it moves a package's *build products* relative to its package
directory, while this one decides where *scaffolding is generated*. If you were using it to keep
generated scaffolding out of the config directory, there is no equivalent key; move the config
file. Note also that the package-level key is **not itself path-validated yet** (`stn-1a4`), so it
is not a safer place to put an untrusted value.

The same containment rule now covers a package's `dir`, so a symlinked package directory can no
longer take `gen`'s writes out of the output tree. **That check covers the package directory
itself, and nothing below it.** A symlink at any path *inside* a real package directory — the
rendered file itself, a subdirectory a nested `dest` writes through, or a brand image's
destination — is still followed, and `gen` and `clean` do not even agree about which of those they
resolve, so such a package can generate at exit 0 and then be permanently un-cleanable. `stn-h5q`
tracks all of it with reproductions.

And `package_name` is now checked as what it is, a filename in the package directory: no separator,
no whitespace, no `..`, no shell or glob metacharacter. It is checked for every package type, where
before only `zip` and `doc` ever read it — so a `package_name` carrying a path, on a package type
that ignored it, is refused now where it was silently accepted before.

A handful of keys can also be set at this top level — `lang`, `brand`, `brand-alt`, and
`show_download` — to give every package in the config the same default without repeating it. Each
resolves package first, then config-wide, then a built-in default; see
[Package Configuration](#package-configuration) below for what each key does at the package level,
and [AUTHORING.md](AUTHORING.md) for how a document's own front matter overrides whatever is
configured here. `.config.yaml` is read as YAML 1.1, so `show_download: false` (unquoted) is already
a real boolean there — a *quoted* `"false"` is a string, and stencil refuses it with an error naming
the key rather than silently coercing it. That refusal stops the run: see
[When the config is wrong](#when-the-config-is-wrong).

### When the config is wrong

A config mistake stops the command. It does not skip the package it appears in.

That is worth stating because it used to be the other way round: a package with a bad value was
quietly dropped from the managed `.gitignore` section, from what `clean` removed, and from a
`stencil gen --all` that still exited 0. The section then went stale and nothing said so.

Three things follow, all deliberate:

- **Every problem is reported at once, not just the first.** Fixing a config one error per run,
  when the errors were all visible on the first pass, is a bad trade for an author with a dozen
  packages.
- **A mistake anywhere fails every command, including package-scoped ones.** `stencil gen hs1`
  refuses while `hs9` is broken. `stencil` already worked this way for `template_env` and `when:`
  mistakes, which have always been checked across the whole config; this extends the same rule to
  the rest of it rather than having two kinds of config error with two behaviours.
- **`clean` reads each package's own manifest before it reads the config, so it is no longer
  simply "the awkward one."** It works on a config that no longer parses, for every package that
  has a manifest — see [The Manifest](#the-manifest). The rule: exit **0** when every package in
  scope was cleaned, with the config problem printed as a warning; **non-zero**, naming the
  packages, when any package in scope had neither a manifest nor a readable config. A package
  generated before the manifest existed has none, and falls back to deriving from the config for
  that package — the old behaviour exactly.

The one check that is **not** applied everywhere is whether a `brand` logo's file exists. Only
`gen` looks, because only `gen` copies it: the `.gitignore` entry and the clean list are both
derived from the brand *string*, so a missing file cannot make either of them wrong, and checking
for it would take `clean` away for no benefit. A missing `brand-alt`, which needs no filesystem at
all, is reported by every command.

### Documents vs. Slide Decks

A package renders markdown two ways, and a file belongs to exactly one of them:

| Key      | Pandoc template       | Output                                              |
| -------- | --------------------- | --------------------------------------------------- |
| `docs`   | `html-template.html`  | One flowing document                                |
| `slides` | `slide-template.html` | A slide deck: one slide per `##`, plus present mode |

```yaml
packages:
  lessons:
    name: "Classroom Lessons"
    dir: lessons
    package_type: doc
    docs: [Introduction.md]
    slides: [kanban-deck.md]
```

`make doc` builds everything the package declares; `make slide` builds only the decks. Stencil
injects the pandoc template and Lua filters each kind needs, so the `templates:` list never has to
mention them.

Both kinds parse as the same dialect -- pandoc's `markdown`, inferred from the `.md` extension, with
its full default extension set; no `--from` is passed. Only the template and filter set differ.
Writing the markdown itself -- the dialect and its extensions, which constructs work in which kind,
slide breaks, layout fences, presenter-only content, present mode and printing -- is covered in the
[Authoring Guide](AUTHORING.md).

### Package Configuration

| Field             | Required | Description                                                                                                                                                                             |
| ----------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`            | No       | Display name (defaults to package ID)                                                                                                                                                   |
| `lang`            | No       | Language for this package's pages, overriding the config-wide `lang` (default `en`)                                                                                                     |
| `brand`           | No       | Brand for this package's documents, overriding the config-wide `brand`: a name, or a `file://` path to a logo resolved relative to this config file                                     |
| `brand-alt`       | No       | Alt text for a `brand` logo. Required when `brand` is an image; `gen`, `install` and `clean` all fail without it. Whether the logo *file* exists is checked by `gen` only               |
| `show_download`   | No       | Whether pages in this package carry the download button, overriding the config-wide `show_download` (default `true`); a document's own front matter overrides this, in either direction |
| `dir`             | No       | Output subdirectory (defaults to package ID)                                                                                                                                            |
| `package_type`    | Yes      | `doc` for HTML documents, `zip` for submissions                                                                                                                                         |
| `docs`            | No       | List of markdown files to convert to HTML docs                                                                                                                                          |
| `slides`          | No       | List of markdown files to convert to slide decks                                                                                                                                        |
| `package_name`    | zip only | Submission filename (a `doc` using `package_sources` needs one too, ending in `.pdf`)                                                                                                   |
| `package_sources` | No       | What `pkg` puts into `package_name` (zip default: `[htdocs]`); a glob expands sorted, a directory means every file under it, recursively, anything else is used as written              |
| `services`        | No       | Docker services: `web`, `mysql`                                                                                                                                                         |
| `deps_script`     | No       | Install scripts keyed by OS                                                                                                                                                             |

All package fields are available as template context variables. Custom fields can be added and
accessed in your templates.

## Makefile Targets

These are the targets stencil writes into a generated package's `Makefile`. `doc`, `slide`, `pdf`,
`check-access` and `check-pdf` exist only for a package with `docs:`, `slides:` or
`package_sources`, and `slide` only when there are `slides:`. The generator itself is
`stencil gen <package>`; the generated `Makefile` carries no target for it. A consuming
repository may wrap `stencil gen` in a target of its own, and that wrapper is the repository's
convention rather than stencil's.

| Target         | Description                                                                                                       |
| -------------- | ----------------------------------------------------------------------------------------------------------------- |
| `help`         | Show available targets                                                                                            |
| `doc`          | Generate all HTML (add `WITH=hidden` for extras)                                                                  |
| `slide`        | Generate HTML slide decks only                                                                                    |
| `pdf`          | Print the generated HTML to PDF/UA-1 files                                                                        |
| `check-access` | Check the generated HTML against WCAG 2.1 AA (pa11y)                                                              |
| `check-pdf`    | Check the generated PDFs for PDF/UA-1 conformance (veraPDF)                                                       |
| `format-md`    | Format markdown files with prettier                                                                               |
| `pkg`          | `zip` packages: the submission archive; `doc` packages with `package_sources`: the combined PDF. Absent otherwise |
| `clean`        | Remove generated files                                                                                            |
| `clean-pkg`    | Remove package-specific generated files                                                                           |

### Compose File Pinning

Every target above that drives compose does so through two Make variables: `DC ?= docker compose` names the implementation (override to `DC="podman compose"`, unchanged from before),
and `COMPOSE_FILES ?= docker-compose.yml` names the file(s) that implementation is pinned to.
Because of the pin, a `docker-compose.override.yml` sitting in the package directory next to the
markdown — or a `.env` setting `COMPOSE_FILE` — is **ignored**, not auto-discovered and merged
the way plain `docker compose` would. To merge one back in, name it explicitly:

```
make doc COMPOSE_FILES="docker-compose.yml docker-compose.override.yml"
```

- **`DC` names an implementation, and nothing else.** A flag inside it is refused —
  `DC="docker compose -f other.yml"` fails with *DC names a compose implementation only* —
  and so is an **empty** one: `DC= make doc` fails with *DC must name a compose
  implementation*. `?=` only asks whether `DC` is *defined*, so an empty value used to sail
  through and produce a build that exited 0 having written nothing, while running whatever
  `image` it found on your `PATH` (`stn-mbq`).
  This is not pedantry: `DC` is placed *before* the pin, so a compose file smuggled in there
  would be **merged** with `docker-compose.yml` rather than replaced by it, which is the exact
  behaviour the pin exists to stop. Compose files go in `COMPOSE_FILES`. All four spellings
  `stencil` itself probes for — `docker compose`, `podman compose`, `docker-compose`,
  `podman-compose` — are unaffected, whether exported or passed on the command line.

- **The pull guard's runtime follows `DC`, and is not a knob of its own.** Before each
  compose call the Makefile probes whether the pinned image is already present, so a build
  that needs no pull does none. That probe needs the *runtime* CLI rather than the compose one
  — there is no `compose image inspect` — so it derives one from `DC`: `docker compose` and
  `docker-compose` both probe with `docker`, both podman spellings with `podman`. It is
  written `override STENCIL_CONTAINER = ...`, which means an exported or command-line
  `CONTAINER` or `STENCIL_CONTAINER` is **ignored**. That is deliberate: the probe's result is
  run as a command, so a value exported once for something unrelated would otherwise change
  what every generated package executes (`stn-3y8`). The trade is that `make doc CONTAINER=podman` no longer does anything, and make issues no warning for an unused
  command-line variable, so there is no signal — set `DC` instead, and the probe follows it.

  If your `DC` is not a bare implementation name, the derivation reads its first word and gets
  this wrong: `sudo docker compose` probes with `sudo`, `env FOO=1 docker compose` with `env`,
  `/opt/my-tools/docker compose` with `/opt/my` (the `-` split reaches into the path too). The
  probe then always fails, which costs a pull on every build rather than breaking it — except
  for `sudo` on a host with no cached credential and a tty, where `sudo` opens `/dev/tty` for a
  password prompt that the probe's `>/dev/null 2>&1` does **not** suppress, so the build stalls
  on a prompt instead. `podman-remote compose` is quieter and worse: it probes the *local*
  image store while compose pulls to the remote.

  On any of those hosts, name the runtime in your own composition, after the include:

  ```make
  override STENCIL_CONTAINER = nerdctl
  ```

  The `override` is required — a plain assignment there loses to the Makefile's own.

- **`BUILD_DATE` may contain only digits, `-`, `T` and `:`.** It is how the build host's day
  reaches a document carrying `show_date:` — `make doc BUILD_DATE=2026-09-01` — and the
  accepted characters are exactly those of the date shapes the frontmatter filter honours,
  `yyyy-mm-dd` and `yyyy-mm-ddThh:mm`. It is a *character* class and not a date check:
  `2026-13-45` passes here and is refused later, by the filter, with a message about months.

  The check runs on the targets that actually stamp a date — `doc`, `slide`, `pdf`,
  `check-pdf` — and nowhere else, so an unrelated `BUILD_DATE` in your environment (the OCI
  `org.opencontainers.image.created` convention uses that name, and its `Z` is out of class)
  leaves `make help`, `make clean` and `make format-md` working. On a stamping target it is a
  hard error naming the variable; `make doc BUILD_DATE=` builds with the container's own date
  instead.

  **This costs you a vendored template, and that is the real price of the change.** `Makefile.j2`
  is four `{% include %}`s with no extension point, and `stencil gen` overwrites the generated
  `Makefile`, so "your own composition" means overriding `Makefile.j2` through `templates_dir`.
  If you have not done that, there is now **no** way to set the probe's runtime — `make doc STENCIL_CONTAINER=docker` is ignored by design. The old `CONTAINER ?=` was a working
  one-liner for exactly the `sudo docker compose` case above, and it is gone; that is the
  deliberate trade for a probe that cannot be repointed by a stray export. Do not reach for a
  `GNUmakefile` beside the generated `Makefile` as a cheaper workaround: that it silently
  replaces the generated `Makefile` at all is itself a filed defect (`stn-bux`), not a
  supported extension point.

- **Bare paths, not flags.** `COMPOSE_FILES` is a space-separated list of compose files, not a
  string of compose arguments — the Makefile adds each file's `-f` itself. Writing
  `COMPOSE_FILES="-f docker-compose.yml"` fails, and loudly: `-f` is a word like any other, so
  it gets a `-f` of its own and compose is handed `-f -f -f docker-compose.yml`, which ends in
  `open .../-f: no such file or directory`.

- **Order matters, and `docker-compose.yml` stays first.** With more than one `-f`, compose
  takes the *first* file's directory as the project directory — the value that becomes both the
  compose project name and the base every relative volume source in the file resolves against.
  Naming an override first would change both of those for a package whose own files never moved.

- **If you rename the compose template's `dest:`, rename `COMPOSE_FILES` to match.** A `.config.yaml`
  can render `docker-compose.yml.j2` under another name (see `dest:` under
  [Configuration](#configuration)) — say, `compose.yaml`, which works today purely because
  plain compose auto-discovers it. Under the pin it does not: compose looks for the literal
  name(s) in `COMPOSE_FILES` and refuses with `open .../docker-compose.yml: no such file or directory` if the rendered file isn't among them. That is the fix working as intended — a
  silently-broken build becomes a loud one. (`no configuration file provided` is the
  *auto-discovery* failure, printed only when compose is given no `-f` at all and finds
  nothing to fall back on; a pinned-but-missing file fails the other way. Measured on Docker
  Compose v5.3.1.)

- **No spaces in a `COMPOSE_FILES` entry on Windows.** The generated Makefile's Windows pull
  guard embeds the compose invocation inside a `powershell -Command "..."` string; a path
  containing a space is not quoted for that context and breaks it.

- **This is a pin, not a new configuration surface.** The reviewable, version-controlled way to
  customize what compose builds is still the template search path — overriding
  `docker-compose.yml.j2` (or `Makefile.j2`) in your own `templates_dir`, which stencil searches
  before its bundled templates. See [Extending Stencil](#extending-stencil) below.

## Extending Stencil

### Custom Templates

Create any `.j2` file and add it to your `.config.yaml` templates list. Templates have access to
all package configuration fields plus derived variables like `has_web`, `has_mysql`, `has_docs`.

#### `html-to-pdf.js.j2` and `Dockerfile.browser.j2` now travel together

Overriding `html-to-pdf.js.j2` still works, and still takes effect: the pdf service's image is
built from your package directory, so the copy `stencil gen` renders there is the one that gets
built in. What changed is that the script is **run from inside the image** rather than from the
mounted package directory — Node decides whether a `.js` file is CommonJS or an ES module from the
nearest `package.json` to the file, and from the mount that was yours.

Two consequences worth knowing before you override either file:

- If you also override `Dockerfile.browser.j2`, keep its `COPY html-to-pdf.js` line. Without it the
  pdf service starts with `Cannot find module`, and the script's own diagnostic — the one that
  explains a missing tools directory — cannot run, because the script is not there to run it.
- Keep its lockfile guard too — the `RUN … sha256sum -c …` between the `COPY` of
  `browser-package-lock.json` and the `npm ci` that installs from it. It refuses to build when that
  lockfile is not the one `stencil gen` wrote, which is what stops a changed `resolved` URL deciding
  which bytes become the puppeteer, pa11y and pdf-lib your build then runs as root. An override that drops it
  builds perfectly well and is simply no longer checked — nothing fails, which is the problem.
  `StrictUndefined` cannot warn you here: it catches a template that reads a key stencil stopped
  providing, not one that stopped reading a key stencil still provides. If you keep the guard, keep
  `{{ browser_lockfile_digest }}` with it; that value is derived from the vendored lockfile on every
  `stencil gen`, so it follows a re-vendor on its own.
- **Overriding `browser-package-lock.json.j2` is the one combination that does not work on its
  own.** That template resolves through the same search path, so you can replace it — but the digest
  in `Dockerfile.browser` comes from stencil's *vendored* lockfile, not from your rendered one, so
  the build refuses a package that is exactly what you asked for. Worse, the refusal tells you to run
  `stencil gen`, which regenerates your override and refuses again. If you override the lockfile,
  override `Dockerfile.browser.j2` in the same breath and put your own digest in its guard — or drop
  the lockfile override and pin what you need through `pipeline.py` instead. The refusal message says
  so too, so nobody has to find this page first.
- `docker compose run --rm pdf …` on its own now runs whichever script was baked the last time the
  image was built. `make pdf` runs `docker compose build pdf` first and is unaffected; if you
  invoke the service by hand while editing the script, build first. The `check-access` service does
  not have this property — its script is inlined into `docker-compose.yml`, so an edit there takes
  effect immediately.

### Custom Document Features

To add a new conditional feature (e.g., `draft`):

1. Create a Lua filter `draft-filter.lua.j2` in your templates directory
1. Add it to the pandoc entrypoint in `docker-compose.yml.j2`
1. Add template to your `.config.yaml`
1. Use `::: {.draft}` in markdown

The `WITH=` variable automatically passes `--metadata include-<feature>=true` to pandoc for any
feature name.

**A feature name may contain letters, digits, `-` and `_`, and the list is separated by commas.**
Anything else — a space, a slash, a shell metacharacter — is refused by name before the build
starts. The value reaches a `pandoc` command line *and* an output filename, and before this it
reached both unquoted, so `WITH='a;rm -rf /;b'` ran what it said (`stn-cb8`). The lowercase
`with=` spelling is checked the same way.

### Custom context keys

A package's `template_env` puts arbitrary keys into the context, and `when:` conditions test
them:

```yaml
template_env: # config level: declares a key, and gives every package its value
  has_playwright: false

packages:
  graded:
    template_env: # package level: overrides the config-level value
      has_playwright: true
```

Declare a key at config level whenever your templates read it but no package in *this* config
sets it — which happens as soon as one `templates_dir` serves more than one `.config.yaml`.
A key set by at least one package needs no declaration; the packages that do not set it see it
as undefined, so `{% if key %}` is false and `{{ key | default('x') }}` still gives `x`.

Stencil refuses a key that cannot do anything, in either direction:

- a `when:` naming a key that is neither derived nor declared anywhere, which would skip the
  template it guards for every package;
- a `template_env` key whose name appears in no `when:` and nowhere in any template.
  Appearing is all that is asked, because a key does not have to reach a template as a
  variable — `(template_env | default({})).get('docroot_subdir')` is a dict lookup — so the
  only sound complaint is that the name occurs nowhere at all, which is what a typo looks
  like.

Both used to be silent. An undefined name rendered as the empty string, so a mistyped
`has_vscde` produced no output and no error.

### Template Includes

Templates can include other templates using Jinja2's `{% include %}`. Stencil searches your
`templates_dir` first, then falls back to bundled templates, allowing selective overrides.

Overriding a *composition* template — `Makefile.j2`, `docker-compose.yml.j2` — while including
stencil's partials is the intended way to extend a build. Be aware that those partials read
context keys, and that set is an interface: `tests/test_template_contract.py` records it, and
stencil's own suite fails if it changes. Since an undefined name is now an error rather than
the empty string, a composition that has fallen out of step fails your `stencil gen` instead
of quietly emitting a Makefile with a recipe missing.

## Accessibility

HTML output is natively accessible:

- Semantic HTML structure
- Proper heading hierarchy
- MathML for screen reader-compatible math
- High contrast print styles
- No PDF/UA compliance headaches
