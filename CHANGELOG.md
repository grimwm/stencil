# Changelog

Notable changes to stencil, newest first.

This file starts at 0.3.0 and is not retroactive. The 170-odd commits before that
tag are the history of the tool arriving at this shape; git is the record of them,
and the closed epics in `.beads/issues.jsonl` are the readable index.

How the version gets bumped is written down in
[AGENTS.md](AGENTS.md#cutting-a-release), not here.

## 0.3.0

The first tagged release. Not a set of changes so much as a name for what the
tool already does, so "the stencil that built this handout" is answerable.

What is in it:

- **Scaffolding generator.** Jinja2 templates rendered into per-package output
  directories from a YAML config, with a templates search path — every
  configured `templates_dir`, then the bundled set, first match wins — so a
  consuming project overrides one template without vendoring all of them.
- **Documents and decks.** Two pandoc HTML templates over shared partials.
  Decks are grouped by `slide-sections.lua` and CSS rather than by pandoc's
  slide-show support, which does not apply here.
- **A PDF stage.** `make pdf` prints the generated HTML through headless
  Chromium, with the print stylesheet as the page geometry rather than as
  decoration.
- **Self-contained output.** Bootstrap, highlight.js, Mermaid and the webfonts
  are vendored and inlined at `stencil gen` time, so a handout makes no network
  request and a build does not depend on a CDN being up.
- **Hidden variants.** `WITH=hidden` builds an answer key from the same source,
  with the filter order arranged so citeproc cannot leak a hidden citation's
  source into the visible build's reference list.
- **A pinned toolchain.** `docker.io/pandoc/core:3.10.0.0`, asserted by a test
  rather than trusted, so a pandoc release cannot change rendered output or
  break CI on a commit that changed nothing.
- **An authoring contract.** `AUTHORING.md` for the person writing markdown,
  `STENCIL.md` for the person configuring a package.
- **A test suite and CI.** Unit assertions on the rendered scaffolding, plus
  container-backed integration tests behind an `integration` marker that skip
  rather than fail when there is no runtime.

Fixed just before the tag:

- A doc package built only from `package_sources` generated five shared page
  files that `clean` and the managed `.gitignore` section could not see, and a
  `pkg` target naming a pandoc template that was never written (`stn-633`).
