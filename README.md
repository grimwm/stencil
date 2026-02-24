# Stencil

Generate project scaffolding from Jinja2 templates and YAML configuration.

Stencil renders Jinja2 templates into per-package output directories, driven by a YAML config file. It also copies static files and OS-specific scripts. Conditional rendering lets you include or skip templates based on package features.

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
| `install` | Generate all packages and update `.gitignore`     |
| `help`    | Show help (optionally for a specific command)     |

**Examples:**

```bash
stencil list                      # list packages (uses .config.yaml)
stencil gen mypackage             # generate one package
stencil gen --all                 # generate all packages
stencil gen mypackage --dry-run   # preview without writing
stencil install                   # generate all + update .gitignore
stencil --config other.yaml list  # use alternate config file
```

## Configuration

Stencil is driven by a YAML config file (default: `.config.yaml`). See [`config.example.yaml`](stencil/config.example.yaml) for a fully commented example.

### Top-level fields

| Field           | Description                                                           |
| --------------- | --------------------------------------------------------------------- |
| `templates_dir` | Path(s) to template directories, relative to config. String or list.  |
| `output_dir`    | Base output directory, relative to CWD. Defaults to CWD.              |
| `files_dir`     | Path to static files directory, relative to config. For `copy_files`. |
| `scripts_dir`   | Path to scripts directory, relative to config. For `deps_script`.     |
| `templates`     | List of template definitions to render.                               |
| `packages`      | Dictionary of package configurations keyed by package ID.             |

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

| Field            | Default    | Description                                                      |
| ---------------- | ---------- | ---------------------------------------------------------------- |
| `name`           | package ID | Display name shown by `list`.                                    |
| `dir`            | package ID | Output subdirectory under `output_dir`.                          |
| `docs`           | `[]`       | Markdown files to convert to HTML.                               |
| `services`       | `[]`       | Docker Compose services (`web`, `mysql`).                        |
| `package_folder` | `htdocs`   | Folder to include in the submission archive.                     |
| `copy_files`     | `[]`       | Static files/dirs to copy. Strings or `{src, dest}` dicts.       |
| `deps_script`    |            | OS-keyed install scripts: `{Windows_NT: [...], default: [...]}`. |
| `sql_import`     |            | SQL import config(s): `{target, database, file}` dict or list.   |
| `template_env`   | `{}`       | Custom variables merged into template context.                   |

### Package types

| Type   | Description                                                                   |
| ------ | ----------------------------------------------------------------------------- |
| `zip`  | Generates `pkg` target to create submission archive. Requires `package_name`. |
| `doc`  | Documentation only. Generates `doc` target for HTML from markdown.            |
| `none` | Infrastructure only. No `pkg` target, no `package_name` required.             |

### Context variables

Templates receive these variables, derived from the package config:

| Variable         | Description                                         |
| ---------------- | --------------------------------------------------- |
| `package_id`     | The package key                                     |
| `package_name`   | `package_name` field (may be `None`)                |
| `package_dir`    | `dir` field or package ID                           |
| `package_type`   | `package_type` field                                |
| `package_folder` | `package_folder` field                              |
| `docs`           | `docs` list                                         |
| `has_docs`       | `true` if `docs` is non-empty                       |
| `services`       | `services` list                                     |
| `has_web`        | `true` if `"web"` in services                       |
| `has_mysql`      | `true` if `"mysql"` in services                     |
| `has_services`   | `true` if any services defined                      |
| `sql_imports`    | Normalized list of `sql_import` dicts               |
| `deps_script`    | `deps_script` dict                                  |
| `copy_files`     | `copy_files` list                                   |
| `template_env`   | Custom variables dict (also merged to top level)    |
| *(custom)*       | All keys from `template_env` are available directly |

## Bundled templates

Stencil includes a minimal set of templates for document generation:

| Template                       | Description                                    |
| ------------------------------ | ---------------------------------------------- |
| `Makefile.j2`                  | Build targets (clean, format, doc, pkg)        |
| `Makefile-base.j2`             | Common Makefile variables and help target      |
| `Makefile-doc.j2`              | HTML documentation generation via Pandoc       |
| `Makefile-pkg.j2`              | Submission packaging (zip)                     |
| `docker-compose.yml.j2`        | HTML generation service (Pandoc)               |
| `docker-compose-html.yml.j2`   | HTML generation service definition             |
| `html-template.html.j2`        | Pandoc HTML template with Bootstrap styling    |
| `hidden-filter.lua.j2`         | Pandoc Lua filter for hidden content sections  |
| `mermaid-figure-filter.lua.j2` | Pandoc Lua filter for Mermaid diagram captions |

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

Pre-commit hooks run markdown formatting via [mdformat](https://github.com/hukkin/mdformat).

## License

MIT
