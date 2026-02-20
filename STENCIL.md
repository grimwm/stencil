# Stencil Template Specification

Stencil is a general-purpose scaffolding tool that generates project files from Jinja2 templates.
It can scaffold any type of project - documents, web applications, assignments, or custom workflows.
This document describes the bundled templates, features, and conventions.

## Document Generation

### Basic Usage

```bash
make doc              # Generate HTML documents from markdown files
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

### Printing

The HTML documents include print-optimized styles. Use your browser's print function (Ctrl+P / Cmd+P)
to generate a PDF that closely matches academic document formatting:

- Proper page margins (1 inch)
- Print-safe fonts and sizes
- Code blocks and tables won't break across pages
- Colored backgrounds preserved for headers and callouts

## Project Structure

Stencil generates files based on your `.config.yaml` template list. For document packages, the
bundled templates produce:

| File                 | Purpose                                     |
| -------------------- | ------------------------------------------- |
| `Makefile`           | Build targets (doc, format-md, clean, etc.) |
| `docker-compose.yml` | Container definitions for doc generation    |
| `html-template.html` | HTML template with Bootstrap 5 styling      |
| `hidden-filter.lua`  | Pandoc filter for conditional content       |

You can create custom templates for any project type. Templates are Jinja2 files (`.j2` suffix)
that have access to the package context variables.

## Configuration

Projects configure stencil via `.config.yaml`:

```yaml
templates_dir: ../_generator/templates  # Optional: custom templates (searched first)
output_dir: .                           # Where to generate packages

templates:                              # Which templates to render
  - src: Makefile.j2
  - src: docker-compose.yml.j2
  - src: html-template.html.j2
  - src: hidden-filter.lua.j2
  - src: custom-script.sh.j2            # Any custom templates
    dest: setup.sh                      # Optional: rename output file
    when: has_mysql                     # Optional: conditional rendering

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

### Package Configuration

| Field          | Required | Description                                      |
| -------------- | -------- | ------------------------------------------------ |
| `name`         | No       | Display name (defaults to package ID)            |
| `dir`          | No       | Output subdirectory (defaults to package ID)     |
| `package_type` | Yes      | `doc` for HTML documents, `zip` for submissions  |
| `docs`         | No       | List of markdown files to convert to HTML        |
| `package_name` | zip only | Filename for the submission zip                  |
| `services`     | No       | Docker services: `web`, `mysql`                  |
| `copy_files`   | No       | Static files/dirs to copy from a files directory |
| `deps_script`  | No       | Install scripts keyed by OS                      |

All package fields are available as template context variables. Custom fields can be added and
accessed in your templates.

## Makefile Targets

| Target       | Description                                       |
| ------------ | ------------------------------------------------- |
| `help`       | Show available targets                            |
| `install`    | Create/update virtual environment                 |
| `gen T=name` | Generate scaffolding for package `name`           |
| `doc`        | Generate HTML docs (add `WITH=hidden` for extras) |
| `format-md`  | Format markdown files with prettier               |
| `clean`      | Remove generated files                            |
| `clean-pkg`  | Remove package-specific generated files           |

## Extending Stencil

### Custom Templates

Create any `.j2` file and add it to your `.config.yaml` templates list. Templates have access to
all package configuration fields plus derived variables like `has_web`, `has_mysql`, `has_docs`.

### Custom Document Features

To add a new conditional feature (e.g., `draft`):

1. Create a Lua filter `draft-filter.lua.j2` in your templates directory
1. Add it to the pandoc entrypoint in `docker-compose.yml.j2`
1. Add template to your `.config.yaml`
1. Use `::: {.draft}` in markdown

The `WITH=` variable automatically passes `--metadata include-<feature>=true` to pandoc for any
feature name.

### Template Includes

Templates can include other templates using Jinja2's `{% include %}`. Stencil searches your
`templates_dir` first, then falls back to bundled templates, allowing selective overrides.

## Accessibility

HTML output is natively accessible:

- Semantic HTML structure
- Proper heading hierarchy
- MathML for screen reader-compatible math
- High contrast print styles
- No PDF/UA compliance headaches
