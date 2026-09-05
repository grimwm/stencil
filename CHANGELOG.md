# Changelog

Notable changes to stencil, newest first.

This file starts at 0.3.0 and is not retroactive. The 170-odd commits before that
tag are the history of the tool arriving at this shape; git is the record of them,
and the closed epics in `.beads/issues.jsonl` are the readable index.

How the version gets bumped is written down in
[AGENTS.md](AGENTS.md#cutting-a-release), not here.

## 0.4.0

An undefined name in a template is now an error rather than the empty string,
which is a behaviour change for anyone whose config or templates were relying
on the old silence. No generated output changes: every package of six consumer
configs was generated before and after, 530 files, byte-identical.

- **A consumer's composition template is a checked interface.** Overriding
  `Makefile.j2` or `docker-compose.yml.j2` and including stencil's partials is
  the intended way to extend a build, and the keys those partials read are now
  recorded in `tests/test_template_contract.py`. Stencil's own suite fails when
  that set changes, and `StrictUndefined` makes a stale composition fail the
  consumer's `stencil gen` instead of emitting a Makefile with a recipe
  missing.
- **Custom keys can be declared.** A config-level `template_env` declares a key
  and supplies the value every package gets unless its own `template_env`
  overrides it. This is what lets one `templates_dir` serve several configs
  that each set a different subset of the flags their shared templates read.
- **Configuration mistakes are rejected instead of ignored.** A `when:` naming
  a key that is neither derived nor declared anywhere, and a `template_env` key
  whose name appears in no template, both now fail with the key named. Each was
  previously silent: the guarded template was skipped for every package, or the
  key sat there doing nothing.

Upgrading: if `stencil gen` now reports an undefined key, declare it in the
config-level `template_env` rather than setting it to `false` on every package
— a key some package sets stays undefined for the ones that do not, so
`{{ key | default('x') }}` keeps working, and a concrete `false` would break it.

Removed: `TEMPLATE-PACKS.md`, a design for packaging scaffolding as a
dependency. It recommended against building the thing it described, and the
reasoning is preserved in `stn-zza` and in git history.

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
