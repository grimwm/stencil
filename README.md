# Stencil

Generate project scaffolding from Jinja2 templates and YAML configuration.

Stencil renders Jinja2 templates into per-package output directories, driven by a YAML config file. Conditional rendering lets you include or skip templates based on package features.

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

| Field           | Description                                                                                      |
| --------------- | ------------------------------------------------------------------------------------------------ |
| `templates_dir` | Path(s) to template directories, relative to config. String or list.                             |
| `output_dir`    | Base output directory, **relative to the working directory**, not to this file. Defaults to CWD. |
| `templates`     | List of template definitions to render.                                                          |
| `packages`      | Dictionary of package configurations keyed by package ID.                                        |

`output_dir` is resolved against the process's working directory rather than against the config
file that names it, which is worth stating plainly because it is the opposite of what every other
path in this file does — `templates_dir` and every `brand: file://` are relative to the config.
So `output_dir: build` puts output under `build/` **wherever you happened to run `stencil` from**,
and the same config generates into a different place depending on your shell's `cd`. Drive it from a
Makefile that runs in a fixed directory, or pass an absolute path.

### Template definitions

Each entry in `templates`:

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

| Field             | Default            | Description                                                                                                                                             |
| ----------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`            | package ID         | Display name shown by `list`.                                                                                                                           |
| `dir`             | package ID         | Output subdirectory under `output_dir`.                                                                                                                 |
| `docs`            | `[]`               | Markdown files to convert to HTML documents.                                                                                                            |
| `slides`          | `[]`               | Markdown files to convert to HTML slide decks.                                                                                                          |
| `services`        | `[]`               | Docker Compose services (`web`, `mysql`).                                                                                                               |
| `package_sources` | `[htdocs]` for zip | What `pkg` puts into `package_name`: a glob expands sorted, a directory means every file under it, recursively, anything else is used as written.       |
| `sql_import`      |                    | SQL import config(s): `{target, database, file}` dict or list                                                                                           |
| `template_env`    | `{}`               | Custom variables merged into template context. May not reuse a context-variable name from the table below; a collision raises rather than shadowing it. |

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
