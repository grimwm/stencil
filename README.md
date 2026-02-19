# Stencil

Generate project scaffolding from Jinja2 templates and YAML configuration.

Stencil renders a set of Jinja2 templates into per-package output directories, driven entirely by a YAML config file. It also copies static files and OS-specific dependency scripts. Conditional rendering lets you include or skip templates based on package features.

## Installation

Install directly from GitHub:

```bash
pip install git+https://github.com/grimwm/stencil.git
```

Or add to a `requirements.txt`:

```
stencil @ git+https://github.com/grimwm/stencil.git
```

## Development setup

Clone the repo and set up a virtual environment with dev dependencies:

```bash
git clone git@github.com:grimwm/stencil.git
cd stencil
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pre-commit install
```

This installs stencil in editable mode and enables the pre-commit hooks (markdown formatting via [mdformat](https://github.com/hukkin/mdformat)). The hooks run automatically on `git commit`, or you can run them manually:

```bash
pre-commit run --all-files
```

## Usage

```bash
stencil --config config.yaml --list         # list available packages
stencil --config config.yaml mypackage      # generate scaffolding for mypackage
stencil --config config.yaml mypackage --dry-run  # preview without writing files
```

## Configuration

Stencil is driven by a single YAML config file. See [`config.example.yaml`](stencil/config.example.yaml) for a fully commented example.

### Top-level fields

| Field           | Description                                                                                                                           |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `templates_dir` | Path(s) to Jinja2 template directories, relative to the config file. Can be a string or list. If omitted, uses the bundled templates. |
| `output_dir`    | Base output directory, relative to CWD. Defaults to CWD if omitted.                                                                   |
| `templates`     | List of template definitions to render (see below).                                                                                   |
| `packages`      | Dictionary of package configurations keyed by package ID.                                                                             |

### Template definitions

Each entry in `templates` has:

| Field  | Description                                                                                   |
| ------ | --------------------------------------------------------------------------------------------- |
| `src`  | Template filename to look up in `templates_dir` (required).                                   |
| `dest` | Output filename. Defaults to `src` with `.j2` suffix removed.                                 |
| `when` | Context variable name (or list of names) that must all be truthy for this template to render. |

### Package definitions

Each key under `packages` is a package ID passed as a positional argument to the CLI.

**Required:**

| Field          | Description                                                    |
| -------------- | -------------------------------------------------------------- |
| `package_type` | `"zip"` or `"pdf"`.                                            |
| `package_name` | Submission filename (required for `zip` type, e.g. `hs3.zip`). |

**Optional:**

| Field            | Default    | Description                                                                                     |
| ---------------- | ---------- | ----------------------------------------------------------------------------------------------- |
| `name`           | package ID | Display name shown by `--list`.                                                                 |
| `dir`            | package ID | Output subdirectory under `output_dir`.                                                         |
| `pdfs`           | `[]`       | Markdown files to convert to PDF.                                                               |
| `services`       | `[]`       | Docker Compose services (`web`, `mysql`).                                                       |
| `package_folder` | `htdocs`   | Folder to include in the submission archive.                                                    |
| `copy_files`     | `[]`       | Static files/directories to copy from bundled `files/`. Accepts strings or `{src, dest}` dicts. |
| `deps_script`    |            | OS-keyed dependency install scripts from bundled `scripts/`.                                    |
| `sql_import`     |            | SQL import config: one dict or list of dicts with `target`, `database`, `file`.                 |

### Context variables

Templates receive these context variables, derived from the package config:

| Variable         | Source                                                      |
| ---------------- | ----------------------------------------------------------- |
| `package_id`     | The package key                                             |
| `package_name`   | `package_name` field                                        |
| `package_dir`    | `dir` field (defaults to package ID)                        |
| `package_type`   | `package_type` field                                        |
| `package_folder` | `package_folder` field                                      |
| `pdfs`           | `pdfs` list                                                 |
| `has_pdfs`       | `true` if `pdfs` is non-empty                               |
| `services`       | `services` list                                             |
| `has_web`        | `true` if `"web"` in services                               |
| `has_mysql`      | `true` if `"mysql"` in services                             |
| `has_services`   | `true` if any services defined                              |
| `sql_imports`    | List of `sql_import` dicts (normalized from single or list) |
| `deps_script`    | `deps_script` dict                                          |
| `copy_files`     | `copy_files` list                                           |

## Bundled templates

Stencil ships with templates for a Docker-based PHP/MySQL development environment:

- `Makefile.j2` -- build targets for Docker, linting, formatting, packaging, PDF generation
- `docker-compose.yml.j2` -- PHP-FPM, nginx, MySQL, linters, formatters, PDF service
- `Dockerfile.j2` / `Dockerfile.pdf.j2` -- PHP and Pandoc container images
- `nginx.conf.j2` -- reverse proxy config
- `pandoc-header.tex.j2` / `pandoc-template.latex.j2` -- LaTeX/PDF generation with PDF/UA-2 tagging
- `table-style.lua.j2` -- Pandoc Lua filter for styled tables
- `.sqlfluff.j2` -- SQL linter config
- `pdfa-metadata.xmp.j2` -- PDF/A metadata

Override or extend these by pointing `templates_dir` in your config to your own template directories. Directories are searched in order (first match wins).

## License

MIT
