# Stencil Template Specification

Stencil generates project scaffolding for PDF and web-based assignments. This document describes the
templates, features, and conventions used by stencil-generated projects.

## PDF Generation

### Basic Usage

```bash
make pdf              # Generate PDFs from markdown files
make format-md        # Format markdown files with prettier
```

### Features (WITH variable)

Use the `WITH` variable to include optional content sections in your PDFs:

```bash
make pdf WITH=hidden              # Include hidden sections
make pdf WITH=hidden,draft        # Include multiple features (comma-separated)
make pdf with=hidden              # Lowercase also works
```

**Output filenames** reflect the features used:

- `make pdf` → `Document.pdf`
- `make pdf WITH=hidden` → `Document-hidden.pdf`
- `make pdf WITH=hidden,draft` → `Document-hidden-draft.pdf`

### Available Features

| Feature  | Description                                    | Markdown Syntax           |
| -------- | ---------------------------------------------- | ------------------------- |
| `hidden` | Answer keys, solutions, instructor notes       | `::: {.hidden} ... :::`   |

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

### Code Blocks and tagpdf

The PDF generation uses tagpdf for accessibility. A known limitation: having 3+ consecutive code
blocks under one heading can cause LaTeX errors. Solutions:

1. Add subheadings between groups of code blocks
2. For sections with many code blocks (like answer keys), wrap in `::: {.hidden}` which disables
   tagpdf tagging automatically

## Project Structure

Stencil generates the following files for PDF-enabled packages:

| File                   | Purpose                                      |
| ---------------------- | -------------------------------------------- |
| `Makefile`             | Build targets (pdf, format-md, clean, etc.)  |
| `docker-compose.yml`   | Container definitions for PDF generation     |
| `Dockerfile.pdf`       | LaTeX/Pandoc container image                 |
| `pandoc-header.tex`    | LaTeX preamble (styling, packages)           |
| `pandoc-template.latex`| Document template                            |
| `table-style.lua`      | Pandoc filter for table formatting           |
| `hidden-filter.lua`    | Pandoc filter for conditional content        |

## Configuration

Projects configure stencil via `.config.yaml`:

```yaml
templates_dir: ../_generator/templates  # Optional: custom templates
output_dir: .                           # Where to generate packages

templates:                              # Which templates to use
  - src: Makefile.j2
  - src: docker-compose.yml.j2
  - src: pandoc-header.tex.j2
  # ... etc

packages:
  lessons:
    name: "Classroom Lessons"
    dir: lessons
    package_type: pdf
    pdfs:
      - RDCIS.md
      - Introduction.md
```

## Makefile Targets

| Target       | Description                                         |
| ------------ | --------------------------------------------------- |
| `help`       | Show available targets                              |
| `install`    | Create/update virtual environment                   |
| `gen T=name` | Generate scaffolding for package `name`             |
| `pdf`        | Generate PDFs (add `WITH=hidden` for extra content) |
| `format-md`  | Format markdown files with prettier                 |
| `clean`      | Remove generated files                              |
| `clean-pkg`  | Remove package-specific generated files             |

## Extending with Custom Features

To add a new feature (e.g., `draft`):

1. Create a Lua filter `draft-filter.lua.j2` in stencil templates
2. Add it to `docker-compose-pdf.yml.j2` entrypoint
3. Add template to your `.config.yaml`
4. Use `::: {.draft}` in markdown

The `WITH=` variable automatically passes `--metadata include-<feature>=true` to pandoc for any
feature name.
