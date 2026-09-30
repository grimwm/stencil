# Stencil

Generate project scaffolding from Jinja2 templates and YAML configuration.

Stencil renders Jinja2 templates into per-package output directories, driven by a YAML config file. A package gets the files of every _capability_ whose rule matches it, and none of the files of a capability whose rule does not.

Two companion guides: [STENCIL.md](STENCIL.md) describes the bundled templates and package
configuration; the [Authoring Guide](AUTHORING.md) covers writing the markdown itself, including
when to build a document and when to build a slide deck.

## Installation

Install directly from GitHub:

```bash
pip install git+https://github.com/grimwm/stencil.git
```

Or add to a `requirements.txt`:

```
stencil @ git+https://github.com/grimwm/stencil.git
```

## Usage

```bash
stencil [--config PATH] COMMAND [OPTIONS]
```

**Commands:**

| Command   | Description                                       |
| --------- | ------------------------------------------------- |
| `list`    | List available packages                           |
| `gen`     | Generate scaffolding (`--all` for all packages)   |
| `clean`   | Remove generated files (`--all` for all packages) |
| `install` | Update `.gitignore` with stencil-managed entries  |
| `help`    | Show help (optionally for a specific command)     |
| `version` | Print the installed stencil version               |

**Examples:**

```bash
stencil list                      # list packages (uses .config.yaml)
stencil gen mypackage             # generate one package
stencil gen --all                 # generate all packages
stencil gen mypackage --dry-run   # preview without writing
stencil install                   # update .gitignore
stencil --config other.yaml list  # use alternate config file
```

## Configuration

Stencil is driven by a YAML config file (default: `.config.yaml`). See [`config.example.yaml`](stencil/config.example.yaml) for a fully commented example.

### Top-level fields

| Field              | Description                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------ |
| `templates_dir`    | Path(s) to template directories, relative to config. String or list.                             |
| `capabilities_dir` | Path(s) to directories of capabilities, relative to config. String or list. See below.           |
| `output_dir`       | Base output directory, **relative to the working directory**, not to this file. Defaults to CWD. |
| `templates`        | Optional. Single files that belong to no capability, in the same shape a capability uses.        |
| `packages`         | Dictionary of package configurations keyed by package ID.                                        |

`output_dir` is resolved against the process's working directory rather than against the config
file that names it, which is worth stating plainly because it is the opposite of what every other
path in this file does — `templates_dir` and every `brand: file://` are relative to the config.
So `output_dir: build` puts output under `build/` **wherever you happened to run `stencil` from**,
and the same config generates into a different place depending on your shell's `cd`. Drive it from a
Makefile that runs in a fixed directory, or pass an absolute path.

### Capabilities

A capability is a directory holding a `capability.yaml`, the templates it renders, and a rule
saying which packages get them. A package does not list files. It sets fields, and the
capabilities whose rule matches those fields write theirs.

Point `capabilities_dir` at a directory whose immediate subdirectories are capabilities:

```text
capabilities/
  reports/
    capability.yaml
    summary.txt.j2
    reports.mk.j2
```

```yaml
# capabilities/reports/capability.yaml
id: reports # must equal the directory name
activates: reports # the one package field that turns this on
when: reports # a Jinja expression over the package
fields: # optional: values the block may hold
  format: [txt, html]
required_when: # optional: a key the block must set when an expression over the block holds
  destination: "format == 'html'"
optional: [footer] # names the templates may read that a package may leave unset
templates: # rendered files
  - src: summary.txt.j2
    dest: .stencil/summary.txt
fragments: # Makefile fragments, included by the generated Makefile
  - src: reports.mk.j2
    dest: .stencil/reports.mk
```

A capability whose rule does not match a package contributes nothing to it: no files, and no
names in its Makefile. A capability that matches no package in a config is not an error.

#### `when`

`when` is one Jinja expression, evaluated against the package. A name the package did not set is
`None`, so `reports is none` is true, `reports.format == 'html'` is false, and a comparison a
missing value cannot answer does not match. A syntax error in `when` fails the run and names the
expression. There are two kinds of rule, told apart by whether `activates` is set.

- **Shape rule** (no `activates`). `when` may name only values stencil derives from the package:
  `services`, `docs`, `slides`, `package_sources`, `package_type`, and the `has_*` booleans built
  from them. Setting the field is the switch. The built-in `documents` capability is a shape rule:
  it applies when `docs` or `slides` is non-empty, or a `doc` package has `package_sources`.
  Any other name in a shape rule is a validation error.
- **Explicit block** (`activates: NAME`). `NAME` is one package field stencil does not derive, and
  `when` must mention it. The field is optional on every package: set it and the capability
  turns on, omit it and the capability stays off. The field's value is the payload the
  capability's templates read.

```yaml
packages:
  weekly:
    package_type: none
    docs: [README.md]
    reports: # turns on the `reports` capability above
      format: html
  archive:
    package_type: none
    docs: [README.md] # no `reports`, so no reports files
```

When a capability lists `fields`, a block that sets a key to a value outside its list fails
validation, and `required_when` demands a key when its expression, read against the block's own keys, is true. Both are
checked before anything is written. `optional` names read as `None` when unset for the packages
where the capability is active. Every other undefined name still fails the render.

A template inside a matched capability may narrow further with its own `when`, the same field
`templates:` entries have. That expression runs only after the capability has matched, so the
block is a mapping by then.

Template lookup for a capability checks its own directory, then each `templates_dir`, then
stencil's bundled templates. The first match wins, so a project replaces one bundled file by
shipping its own under the same name.

#### Where the output goes

The generated `Makefile` stays at the package root, because `make` looks there. Everything else
stencil writes for a package goes under `.stencil/` beside it, so a plain `ls` of the package does
not list generated tooling. The Makefile includes each matched capability's fragment, `documents`
first, then the rest sorted by `id`:

```makefile
include .stencil/documents.mk
include .stencil/reports.mk
```

Compose files work the same way. Stencil always writes `.stencil/docker-compose.yml`, and each
matched capability that ships `.stencil/<id>.compose.yml` adds it, in the same order:

```makefile
COMPOSE_FILES ?= .stencil/docker-compose.yml .stencil/reports.compose.yml
STENCIL_COMPOSE = $(DC) --project-directory . $(addprefix -f ,$(COMPOSE_FILES))
```

`--project-directory .` makes every relative path in a Compose file mean the package root, not
`.stencil/`. `make help` prints the command it will run, so what you read is what runs:

```text
Compose, from this directory: docker compose --project-directory . -f .stencil/docker-compose.yml
```

A zip built from `package_sources` that walks `.` leaves `.stencil/` out of the archive. Files
that another program requires at a fixed path (a copied brand image, for one) are not moved into
`.stencil/`.

### Template definitions

Each entry in a capability's `templates`, `fragments`, and in the optional top-level `templates`:

| Field  | Description                                                                     |
| ------ | ------------------------------------------------------------------------------- |
| `src`  | Template filename to find in `templates_dir` (required).                        |
| `dest` | Output filename. Defaults to `src` with `.j2` suffix removed.                   |
| `when` | Context variable (or list) that must all be truthy for this template to render. |

### Package definitions

Each key under `packages` is a package ID passed to the CLI.

**Required:**

| Field          | Description                    |
| -------------- | ------------------------------ |
| `package_type` | `"zip"`, `"doc"`, or `"none"`. |

**Conditionally required:**

| Field          | When required       | Description                            |
| -------------- | ------------------- | -------------------------------------- |
| `package_name` | `package_type: zip` | Submission filename (e.g., `hs3.zip`). |

**Optional:**

| Field             | Default            | Description                                                                                                                                                                                                         |
| ----------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`            | package ID         | Display name shown by `list`.                                                                                                                                                                                       |
| `dir`             | package ID         | Output subdirectory under `output_dir`.                                                                                                                                                                             |
| `docs`            | `[]`               | Markdown files to convert to HTML documents.                                                                                                                                                                        |
| `slides`          | `[]`               | Markdown files to convert to HTML slide decks.                                                                                                                                                                      |
| `services`        | `[]`               | Docker Compose services (`web`, `mysql`).                                                                                                                                                                           |
| `package_sources` | `[htdocs]` for zip | What `pkg` puts into `package_name`: a glob expands sorted, a directory means every file under it, recursively, anything else is used as written.                                                                   |
| `sql_import`      |                    | SQL import config(s): `{target, database, file}` dict or list                                                                                                                                                       |
| `template_env`    | `{}`               | Custom variables merged into template context, for a key no capability has taken over. Package level only. May not reuse a context-variable name from the table below; a collision raises rather than shadowing it. |

### Package types

| Type   | Description                                                                                  |
| ------ | -------------------------------------------------------------------------------------------- |
| `zip`  | Generates `pkg` target to create submission archive. Requires `package_name`.                |
| `doc`  | Documentation only. Generates `doc`, `slide`, `pdf`, `check-access` and `check-pdf` targets. |
| `none` | Infrastructure only. No `pkg` target, no `package_name` required.                            |

### Context variables

Templates receive these variables, derived from the package config:

| Variable              | Description                                         |
| --------------------- | --------------------------------------------------- |
| `package_id`          | The package key                                     |
| `package_name`        | `package_name` field (may be `None`)                |
| `package_dir`         | `dir` field or package ID                           |
| `package_type`        | `package_type` field                                |
| `package_sources`     | `package_sources` list                              |
| `has_package_sources` | `true` for a `doc` package with `package_sources`   |
| `package_stem`        | `package_name` without `.pdf`, for `doc` packages   |
| `docs`                | `docs` list                                         |
| `has_docs`            | `true` if `docs` is non-empty                       |
| `slides`              | `slides` list                                       |
| `has_slides`          | `true` if `slides` is non-empty                     |
| `has_pages`           | `true` if either `docs` or `slides` is non-empty    |
| `services`            | `services` list                                     |
| `has_web`             | `true` if `"web"` in services                       |
| `has_mysql`           | `true` if `"mysql"` in services                     |
| `has_services`        | `true` if any services defined                      |
| `sql_imports`         | Normalized list of `sql_import` dicts               |
| `template_env`        | Custom variables dict (also merged to top level)    |
| _(custom)_            | All keys from `template_env` are available directly |

## Use cases

### Add one optional file set to a package

The smallest complete capability: a directory with one template and one Makefile fragment, turned
on by a package field.

```text
capabilities/
  extras/
    capability.yaml
    notes.txt.j2
    extras.mk.j2
```

```yaml
# capabilities/extras/capability.yaml
id: extras
activates: extras
when: extras
templates:
  - src: notes.txt.j2
    dest: .stencil/notes.txt
fragments:
  - src: extras.mk.j2
    dest: .stencil/extras.mk
```

```jinja
{# notes.txt.j2 #}
{{ extras.greeting }} from {{ package_id }}
```

```makefile
# extras.mk.j2
.PHONY: extras
extras: ## Print the extras note
	@cat .stencil/notes.txt
```

```yaml
# .config.yaml
capabilities_dir: capabilities

packages:
  demo:
    package_type: none
    docs: [README.md]
    extras:
      greeting: hello
```

`stencil gen demo` writes the Makefile at the package root and everything else under
`.stencil/`. The `documents` capability matches because `docs` is set; `extras` matches because
the package sets `extras`:

```text
demo/
  Makefile                       # includes .stencil/documents.mk and .stencil/extras.mk
  .stencil/
    docker-compose.yml
    documents.mk
    extras.mk
    notes.txt                    # hello from demo
    ...                          # the document pipeline's filters and templates
```

A package that omits `extras` gets none of those `extras` files, and its Makefile does not
mention them.

### Example: a course repository

A course repository is the case this was built for. Its generator keeps capabilities such as a
web server, a database, an editor configuration and an autograder as directories under one
`capabilities/`, and each assignment's `.config.yaml` only names its packages and sets the fields
those capabilities read. A document-only handout sets `docs` and gets `documents` alone; a
homework package that also sets `services` and an autograder block gets those too. Nothing in
stencil knows those names. They are the course's capabilities, written the way `extras` is above.

## Bundled templates

Stencil includes a minimal set of templates for document generation:

| Template                       | Description                                        |
| ------------------------------ | -------------------------------------------------- |
| `Makefile.j2`                  | Build targets (clean, format, doc, pdf, pkg)       |
| `Makefile-base.j2`             | Common Makefile variables and help target          |
| `Makefile-doc.j2`              | HTML generation via Pandoc, plus the `pdf` target  |
| `Makefile-pkg.j2`              | Submission packaging (zip)                         |
| `docker-compose.yml.j2`        | HTML generation service (Pandoc)                   |
| `docker-compose-html.yml.j2`   | Services for HTML, PDF, WCAG and PDF/UA checks     |
| `Dockerfile.browser.j2`        | Shared Chromium image for `pdf` and `check-access` |
| `browser-package-lock.json.j2` | The npm lockfile that image's `npm ci` installs    |
| `format-package-lock.json.j2`  | The npm lockfile behind `make format-md`           |
| `html-template.html.j2`        | Pandoc HTML template for flowing documents         |
| `slide-template.html.j2`       | Pandoc HTML template for slide decks               |
| `_page-*.html.j2`              | Head, styling and scripts shared by both           |
| `_doc-body.html.j2`            | Document body block                                |
| `_slide-*.j2`                  | Deck body, slide styling and present mode          |
| `_theme-toggle.html.j2`        | The three-way light/dark/system control            |
| `frontmatter-filter.lua.j2`    | Pandoc Lua filter normalizing title-block metadata |
| `hidden-filter.lua.j2`         | Pandoc Lua filter for hidden content sections      |
| `mermaid-figure-filter.lua.j2` | Pandoc Lua filter for Mermaid diagram captions     |
| `figure-name-filter.lua.j2`    | Pandoc Lua filter naming figures for PDF/UA        |
| `slide-sections.lua.j2`        | Pandoc Lua filter that groups a deck into slides   |
| `embed-images.lua.j2`          | Pandoc Lua filter inlining local images as base64  |
| `html-to-pdf.js.j2`            | Puppeteer driver the `pdf` service runs            |

Override these by providing your own `templates_dir`. Directories are searched in order (first match wins).

## Development

```bash
git clone git@github.com:grimwm/stencil.git
cd stencil
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pre-commit install
```

Pre-commit hooks run markdown formatting via [mdformat](https://github.com/hukkin/mdformat), over
this repository's own prose only. `tests/fixtures/` is excluded because mdformat corrupts markdown
written in stencil's dialect, and a project generated by stencil should exclude its course content
for the same reason — see
[Do not run mdformat over this markdown](AUTHORING.md#do-not-run-mdformat-over-this-markdown).

Delete `build/` before packaging if you have built before. setuptools copies from `build/lib` rather
than rebuilding it from scratch, so a template you deleted can still end up in the wheel.

## License

MIT
