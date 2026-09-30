# Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stencil 1.0.0 selects generated files by running capabilities, writes them under `.stencil/` beside a root `Makefile`, and documents that mechanism without turning the README into a course guide.

**Architecture:** A new `stencil/capabilities.py` loads `capability.yaml` files, evaluates each `when` expression, and returns the templates and fragments for one package. `generate.py` keeps writing files; it asks the capability module what to write. `documents` is the only built-in capability. A package `Makefile` stays at the package root and includes `.stencil/<id>.mk`. Compose runs with `--project-directory` set to that root.

**Tech Stack:** Python 3, Jinja2 (`StrictUndefined` for template bodies, a separate lenient environment for `when`), pytest from the stencil repo root via `.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-09-29-capabilities-design.md`

## Global Constraints

- Target version is **1.0.0**. Bump `stencil/__init__.py` and add a `CHANGELOG.md` entry.
- The loader does not special-case engine names, course ids, or course field values.
- Config-level `template_env` is removed. Package-level `template_env` still merges into the render context.
- Missing names inside a `when` expression are `None`. A template body still uses `StrictUndefined`, except names listed in that capability's `optional`, which are `None` when unset.
- Generated paths the documents capability owns go under `.stencil/`. The `Makefile` stays at the package root. A template `dest` is honored as written; the loader does not prefix it.
- `STENCIL_COMPOSE` is `$(DC) --project-directory . $(addprefix -f ,$(COMPOSE_FILES))`. `make help` prints that same command.
- README configuration reference does not use consumer names (`grading`, `vscode`, `runner`). Use cases are a later section.
- Run tests with `.venv/bin/python -m pytest` from the stencil repo root.
- Course adoption (cs234 and cs425 capability directories, deleting `test.sh`, generating Playwright configs) is **not this plan**. It is a follow-on plan after 1.0.0 loads capabilities.

______________________________________________________________________

## File structure

- Create `stencil/capabilities.py` — load, validate, match, and build the render context for capabilities. One responsibility: decide what runs.
- Create `stencil/templates/capabilities/documents/capability.yaml` — the built-in documents capability (templates, fragment, `when`).
- Modify `stencil/generate.py` — call the capability module from `generate_package`, `package_contexts`, and the Makefile/Compose writers. Do not grow new matching logic inline.
- Modify `stencil/pipeline.py` — document-file path constants gain the `.stencil/` prefix, matching the destinations in the documents capability.
- Modify `stencil/templates/Makefile-base.j2` — `--project-directory` and the help line.
- Modify `stencil/templates/Makefile-doc.j2` and `docker-compose-html.yml.j2` — paths those partials assume, once the files move under `.stencil/`.
- Create `tests/test_capabilities.py` — loader unit tests.
- Modify README, CHANGELOG, `__init__.py`.

Existing tests that assert a generated path at the package root (`html-template.html`, `frontmatter-filter.lua`, `format-package-lock.json`, `Dockerfile.browser`) must be updated in the task that moves that file, not left for a cleanup pass.

______________________________________________________________________

### Task 1: Load a capability directory

**Files:**

- Create: `stencil/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**

- Consumes: nothing

- Produces: `Capability` dataclass with `id: str`, `directory: Path`, `when: str`, `activates: str | None`, `fields: dict[str, list]`, `templates: list[dict]`, `fragments: list[dict]`, `optional: list[str]`. `load_capabilities(roots: list[Path]) -> list[Capability]`. Raises `ValueError` whose message lists every problem, not just the first.

- [ ] **Step 1: Write the failing test**

```python
def test_load_reads_id_when_and_templates(tmp_path):
    cap = tmp_path / "web"
    cap.mkdir()
    (cap / "capability.yaml").write_text(
        "id: web\n"
        "when: \"'web' in services\"\n"
        "templates:\n"
        "  - src: Dockerfile\n"
        "    dest: .stencil/Dockerfile\n"
    )
    loaded = load_capabilities([tmp_path])
    assert [c.id for c in loaded] == ["web"]
    assert loaded[0].templates[0]["dest"] == ".stencil/Dockerfile"


def test_id_must_match_the_directory(tmp_path):
    cap = tmp_path / "web"
    cap.mkdir()
    (cap / "capability.yaml").write_text("id: mysql\nwhen: 'true'\n")
    with pytest.raises(ValueError, match="id mysql"):
        load_capabilities([tmp_path])


def test_duplicate_ids_are_one_error(tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "capability.yaml").write_text(
            "id: same\nwhen: 'true'\n"
        )
    with pytest.raises(ValueError, match="same"):
        load_capabilities([tmp_path])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py -v`
Expected: FAIL with `ImportError` or `load_capabilities` missing.

- [ ] **Step 3: Write minimal implementation**

`load_capabilities` walks each root's immediate subdirectories for `capability.yaml`, parses YAML, and collects problems: missing `id` or `when`, `id` different from the directory name, duplicate `id`. One `ValueError` joined with newlines. A directory without `capability.yaml` is ignored.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stencil/capabilities.py tests/test_capabilities.py
git commit -m "feat: load capability manifests and refuse duplicate ids"
```

______________________________________________________________________

### Task 2: Evaluate `when`

**Files:**

- Modify: `stencil/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**

- Consumes: `Capability.when`

- Produces: `when_matches(expression: str, context: dict) -> bool`. A Jinja syntax error raises `ValueError` naming the expression. A missing context name is `None`, not an error.

- [ ] **Step 1: Write the failing test**

```python
def test_missing_name_in_when_is_false():
    assert when_matches("grading", {}) is False
    assert when_matches("grading.engine == 'playwright'", {}) is False


def test_shape_expression_matches_services():
    assert when_matches("'web' in services", {"services": ["web"]}) is True
    assert when_matches("'web' in services", {"services": []}) is False


def test_when_syntax_error_names_the_expression():
    with pytest.raises(ValueError, match="when"):
        when_matches("{% if %}", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py::test_missing_name_in_when_is_false tests/test_capabilities.py::test_shape_expression_matches_services tests/test_capabilities.py::test_when_syntax_error_names_the_expression -v`
Expected: FAIL with `when_matches` missing.

- [ ] **Step 3: Write minimal implementation**

Compile the expression with a Jinja `Environment` whose `undefined` is `ChainableUndefined` (missing names are falsy and attribute access does not raise). Render it as a boolean. `TemplateSyntaxError` becomes `ValueError`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stencil/capabilities.py tests/test_capabilities.py
git commit -m "feat: evaluate capability when expressions with missing names as false"
```

______________________________________________________________________

### Task 3: Shape rules versus explicit blocks

**Files:**

- Modify: `stencil/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**

- Consumes: `when_matches`, `Capability`

- Produces: `DERIVED_NAMES: frozenset[str]` (`services`, `docs`, `slides`, `package_sources`, `package_type`, and the `has_*` booleans `get_template_context` already sets). `validate_capability(capability: Capability) -> list[str]`. Shape capabilities (no `activates`) may only name `DERIVED_NAMES` inside `when`. Explicit capabilities must set `activates` to one identifier that is not in `DERIVED_NAMES`, and `when` must mention that identifier.

- [ ] **Step 1: Write the failing test**

```python
def test_shape_when_rejects_an_underived_name():
    cap = Capability(
        id="web", directory=Path("web"), when="vscode",
        activates=None, fields={}, templates=[], fragments=[], optional=[],
    )
    problems = validate_capability(cap)
    assert any("vscode" in problem for problem in problems)


def test_activates_cannot_reuse_a_derived_name():
    cap = Capability(
        id="docs2", directory=Path("docs2"), when="docs",
        activates="docs", fields={}, templates=[], fragments=[], optional=[],
    )
    problems = validate_capability(cap)
    assert any("docs" in problem for problem in problems)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py::test_shape_when_rejects_an_underived_name tests/test_capabilities.py::test_activates_cannot_reuse_a_derived_name -v`
Expected: FAIL with `validate_capability` missing.

- [ ] **Step 3: Write minimal implementation**

Parse `when` with Jinja and collect `meta.find_undeclared_variables`. Apply the two rules from the spec's Errors section. Return problem strings; `load_capabilities` folds them into its aggregated `ValueError`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stencil/capabilities.py tests/test_capabilities.py
git commit -m "feat: reject shape rules that name underived keys"
```

______________________________________________________________________

### Task 4: Field values

**Files:**

- Modify: `stencil/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**

- Consumes: `Capability.fields`
- Produces: `check_fields(capability: Capability, block: dict) -> list[str]`. A set value outside the declared list is a problem. An omitted key is not. A `required_when` mapping on the capability, `dict[str, str]`, is evaluated with `when_matches` against the block; when it is true and the key is absent, that is a problem.

`capability.yaml` spelling, so the spec's "required when engine is playwright" is data rather than a hardcoded engine:

```yaml
fields:
  engine: [playwright, mysql]
  runner: [problems, open]
required_when:
  runner: "engine == 'playwright'"
```

- [ ] **Step 1: Write the failing test**

```python
def test_unknown_field_value_is_rejected():
    cap = _capability(fields={"engine": ["playwright", "mysql"]})
    problems = check_fields(cap, {"engine": "sqlite"})
    assert any("sqlite" in problem for problem in problems)


def test_omitted_field_is_allowed():
    cap = _capability(fields={"engine": ["playwright", "mysql"]})
    assert check_fields(cap, {}) == []


def test_required_when_demands_the_key():
    cap = _capability(
        fields={"runner": ["problems", "open"]},
        required_when={"runner": "engine == 'playwright'"},
    )
    assert check_fields(cap, {"engine": "playwright"}) != []
    assert check_fields(cap, {"engine": "mysql"}) == []
```

Add `required_when: dict[str, str]` to `Capability`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py::test_unknown_field_value_is_rejected tests/test_capabilities.py::test_required_when_demands_the_key -v`
Expected: FAIL with `check_fields` missing.

- [ ] **Step 3: Write minimal implementation**

Compare each present value to its list. Evaluate each `required_when` expression against the block.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stencil/capabilities.py tests/test_capabilities.py
git commit -m "feat: reject capability field values outside the declared list"
```

______________________________________________________________________

### Task 5: Match a package and build its context

**Files:**

- Modify: `stencil/capabilities.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**

- Consumes: `when_matches`, `check_fields`, `Capability`

- Produces: `matching(capabilities: list[Capability], context: dict) -> list[Capability]`. Built-in `documents` first when it matches, then the rest sorted by `id`. A capability that matches no package is absent from the list and is not an error. `render_context(context: dict, matched: list[Capability]) -> dict` copies `context` and sets each matched capability's `optional` names to `None` when the key is absent. Explicit `activates` keys that the package omitted are also `None`.

- [ ] **Step 1: Write the failing test**

```python
def test_inactive_capability_is_skipped_not_an_error():
    docs = _capability(id="documents", when="docs")
    vscode = _capability(id="vscode", when="vscode", activates="vscode")
    matched = matching([vscode, docs], {"docs": ["README.md"], "services": []})
    assert [c.id for c in matched] == ["documents"]


def test_optional_name_is_none_when_unset():
    web = _capability(id="web", when="'web' in services", optional=["grading"])
    ctx = render_context({"services": ["web"]}, [web])
    assert ctx["grading"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py::test_inactive_capability_is_skipped_not_an_error tests/test_capabilities.py::test_optional_name_is_none_when_unset -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Filter with `when_matches`. Stable order: id `documents` first, then `sorted` by id. `render_context` only fills names the matched capabilities declared optional, plus omitted `activates` keys of capabilities that were loaded (so a shared capability the package did not turn on does not have to be declared on the config).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stencil/capabilities.py tests/test_capabilities.py
git commit -m "feat: match capabilities per package and fill optional names with none"
```

______________________________________________________________________

### Task 6: Built-in documents capability

**Files:**

- Create: `stencil/templates/capabilities/documents/capability.yaml`
- Modify: `stencil/capabilities.py` (`builtin_capabilities() -> list[Capability]`)
- Test: `tests/test_capabilities.py`

**Interfaces:**

- Consumes: `load_capabilities`

- Produces: `builtin_capabilities()`. The documents `when` is true when `docs` or `slides` is non-empty, or when `package_type == 'doc'` and `package_sources` is non-empty. Its template `dest` values are `.stencil/html-template.html`, `.stencil/frontmatter-filter.lua`, and the other files `injected_sources` emits today, each prefixed with `.stencil/`. Its fragment dest is `.stencil/documents.mk`. `src` names stay the current bundled template filenames so the existing templates are reused.

- [ ] **Step 1: Write the failing test**

```python
def test_documents_runs_for_docs_and_not_for_an_empty_package():
    docs = {c.id: c for c in builtin_capabilities()}["documents"]
    assert when_matches(docs.when, {"docs": ["a.md"], "slides": [], "package_type": "none", "package_sources": []})
    assert not when_matches(docs.when, {"docs": [], "slides": [], "package_type": "none", "package_sources": []})


def test_documents_destinations_are_under_dot_stencil():
    docs = {c.id: c for c in builtin_capabilities()}["documents"]
    dests = [item["dest"] for item in docs.templates + docs.fragments]
    assert dests
    assert all(dest.startswith(".stencil/") for dest in dests)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py::test_documents_runs_for_docs_and_not_for_an_empty_package tests/test_capabilities.py::test_documents_destinations_are_under_dot_stencil -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Author `capability.yaml` listing every file in `SHARED_PAGE_TEMPLATES`, `DOC_PAGE_TEMPLATES`, `SLIDE_PAGE_TEMPLATES`, and the documents fragment. Template-level `when` keeps slide-only and doc-only files gated the way `injected_sources` gates them today (`has_slides`, `has_docs or has_package_sources`). `builtin_capabilities` loads the directory next to the bundled templates.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stencil/capabilities.py stencil/templates/capabilities tests/test_capabilities.py
git commit -m "feat: ship documents as the built-in capability"
```

______________________________________________________________________

### Task 7: Write the package from the match

**Files:**

- Modify: `stencil/generate.py` (`generate_package`, `injected_templates`)
- Test: `tests/test_capabilities.py`

**Interfaces:**

- Consumes: `matching`, `render_context`, `builtin_capabilities`, `load_capabilities`

- Produces: generation reads `capabilities_dir` from the config (path or list, relative to the config file), loads those plus `builtin_capabilities()`, and renders each matched template and fragment. The base `Makefile` is still written at the package root and contains `include .stencil/<id>.mk` for each match, documents first, then by id. A format lockfile is still written for every package, at `.stencil/format-package-lock.json`.

- [ ] **Step 1: Write the failing test**

```python
def test_document_package_includes_only_documents(generate_package):
    package = generate_package(
        {
            "packages": {
                "demo": {
                    "package_type": "doc",
                    "docs": ["Notes.md"],
                }
            }
        }
    )
    makefile = (package / "Makefile").read_text()
    assert "include .stencil/documents.mk" in makefile
    assert "grading.mk" not in makefile
    assert (package / ".stencil" / "html-template.html").is_file()
    assert not (package / "html-template.html").exists()
```

The fixture is `generate_package(config) -> Path`, defined in `tests/conftest.py` as `make_package`. The package id is `demo`, and the returned path is the generated package directory.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py::test_document_package_includes_only_documents -v`
Expected: FAIL because `html-template.html` is still at the package root, or the include line is absent.

- [ ] **Step 3: Write minimal implementation**

Replace the per-package use of `injected_templates` with the matched capability template list. Keep `ALWAYS_TEMPLATES` only for the format lockfile, and set its dest to `.stencil/format-package-lock.json`. The base Makefile include lines come from the matched fragment dests. `capabilities_dir` default is empty: a config that does not set it still gets `documents`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py::test_document_package_includes_only_documents tests/test_template_contract.py -v`
Expected: the new test PASSes. Contract tests that still expect root paths fail here and are fixed in Task 8, not by putting the files back.

- [ ] **Step 5: Commit**

```bash
git add stencil/generate.py tests/test_capabilities.py
git commit -m "feat: render a package from the capabilities that match it"
```

______________________________________________________________________

### Task 8: Point the document pipeline at `.stencil`

**Files:**

- Modify: `stencil/pipeline.py` (`BROWSER_DOCKERFILE`, `BROWSER_LOCKFILE`, `FORMAT_LOCKFILE`, `_TEMPLATE`, the `--lua-filter=` strings)
- Modify: `stencil/templates/docker-compose-html.yml.j2` (the format-md existence check and any path that reads those files from the package mount)
- Modify: `stencil/templates/Makefile-doc.j2` where a recipe names one of those files
- Test: existing `tests/test_pipeline.py`, `tests/test_compose_format_md.py`, `tests/test_makefile.py`

**Interfaces:**

- Consumes: destinations from Task 6

- Produces: every pandoc `--template` and `--lua-filter` argument, and the browser `docker build -f` path, uses the `.stencil/` prefix. The format-md check looks for `.stencil/format-package-lock.json`.

- [ ] **Step 1: Write the failing test**

```python
def test_pandoc_filters_live_under_dot_stencil():
    argv = pandoc_argv("doc")
    assert "--template=.stencil/html-template.html" in argv
    assert "--lua-filter=.stencil/frontmatter-filter.lua" in argv
```

Add the assertion next to the existing argv test in `tests/test_pipeline.py` rather than a second copy of the argv builder.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py::test_pandoc_filters_live_under_dot_stencil -v`
Expected: FAIL because the argv still names `html-template.html` at the root.

- [ ] **Step 3: Write minimal implementation**

Prefix the constants. Update the compose file's `[ ! -f ... ]` check and the error text that says the lockfile is written beside the compose file so it names `.stencil/format-package-lock.json`. Search tests for the old filenames and update assertions to the new paths in this same change, so the suite matches the constants.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py tests/test_compose_format_md.py tests/test_makefile.py tests/test_template_contract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stencil/pipeline.py stencil/templates tests
git commit -m "feat: read document pipeline files from .stencil"
```

______________________________________________________________________

### Task 9: Compose project directory and the help line

**Files:**

- Modify: `stencil/templates/Makefile-base.j2`
- Test: `tests/test_makefile.py` (or `tests/test_capabilities.py` if the assertion renders a package)

**Interfaces:**

- Consumes: `COMPOSE_FILES`, `DC`, `STENCIL_COMPOSE`

- Produces: `STENCIL_COMPOSE` includes `--project-directory .` before `-f`. The default `COMPOSE_FILES` is `.stencil/docker-compose.yml` plus each matched compose fragment, documents first, then by id. The Makefile's first comment and `make help` both print `$(DC) --project-directory .` followed by `-f` for every entry in `COMPOSE_FILES`.

- [ ] **Step 1: Write the failing test**

```python
def test_help_prints_the_compose_command_make_runs(generate_package):
    package = generate_package(
        {"packages": {"demo": {"package_type": "doc", "docs": ["Notes.md"]}}}
    )
    text = (package / "Makefile").read_text()
    assert "--project-directory ." in text
    assert "-f .stencil/docker-compose.yml" in text
    # The help recipe and STENCIL_COMPOSE must share one spelling.
    assert text.count("--project-directory .") >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py::test_help_prints_the_compose_command_make_runs -v`
Expected: FAIL because `STENCIL_COMPOSE` has `-f` and no `--project-directory`.

- [ ] **Step 3: Write minimal implementation**

In `Makefile-base.j2`, change the `STENCIL_COMPOSE` definition to insert `--project-directory .` after `$(DC)`. Generate the opening comment from the same `COMPOSE_FILES` variable the recipes use, so a package with an extra fragment does not need a second hand-written string.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_makefile.py tests/test_capabilities.py::test_help_prints_the_compose_command_make_runs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stencil/templates/Makefile-base.j2 tests
git commit -m "feat: pin compose to the package root and print that command"
```

______________________________________________________________________

### Task 10: Keep `.stencil` out of a zip of `.`

**Files:**

- Modify: `stencil/templates/Makefile-pkg.j2`
- Test: `tests/test_package_sources.py`

**Interfaces:**

- Consumes: `package_sources` joined into `PKG_SOURCE_SPECS`. A zip package hands a directory to `zip -r` or `tar --format zip` whole, so hidden directories are included. A doc package's `pkg_walk` already drops paths matching `(^|/)\.` via `find -not -path '*/.*'`.

- Produces: when a zip package's `package_sources` includes `.`, the archive recipe excludes `.stencil`. Other listed sources are unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_zip_of_dot_excludes_dot_stencil(generate_package):
    package = generate_package(
        {
            "packages": {
                "demo": {
                    "package_type": "zip",
                    "package_name": "demo.zip",
                    "package_sources": ["."],
                }
            }
        }
    )
    text = (package / "Makefile").read_text()
    assert ".stencil" in text
    assert "-x" in text or "--exclude" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_package_sources.py::test_zip_of_dot_excludes_dot_stencil -v`
Expected: FAIL because the `zip -r` recipe has no exclude.

- [ ] **Step 3: Write minimal implementation**

In the zip arm of `Makefile-pkg.j2`, when `'.' in package_sources`, append `-x '.stencil/*'` to the `zip` recipe and `--exclude=.stencil` to the `tar` recipe. Do not change `pkg_walk`; doc packages already skip dot paths.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_package_sources.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stencil/templates/Makefile-pkg.j2 tests/test_package_sources.py
git commit -m "fix: keep .stencil out of a zip of the package directory"
```

______________________________________________________________________

### Task 11: README, version, changelog

**Files:**

- Modify: `README.md`
- Modify: `STENCIL.md` (one pointer to the README capability section)
- Modify: `stencil/__init__.py` (`__version__ = "1.0.0"`)
- Modify: `CHANGELOG.md`

**Interfaces:**

- Consumes: the behavior Tasks 1–10 implemented

- Produces: a configuration section that explains capabilities, `when`, explicit blocks, `.stencil/`, and the Compose help line, using a generic package. A separate "Use cases" section after it shows one small capability directory and the files `stencil gen` writes. That section may mention a course only as a labeled example, and the configuration section does not use `grading`, `vscode`, or `runner`.

- [ ] **Step 1: Write the failing test**

```python
def test_readme_reference_does_not_define_capabilities_with_consumer_names():
    text = Path("README.md").read_text()
    reference, _, rest = text.partition("## Use cases")
    assert "## Use cases" in text
    for word in ("grading", "vscode", "runner"):
        assert word not in reference.split("## Configuration")[-1]
```

Put this in `tests/test_capabilities.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py::test_readme_reference_does_not_define_capabilities_with_consumer_names -v`
Expected: FAIL because the section is missing.

- [ ] **Step 3: Write the README and the changelog**

Rewrite the configuration portion of `README.md` so a reader who has never seen a course can load a capability directory, turn one on from a package field, and find the output in `.stencil/`. Add `## Use cases` after that section with one complete generic example (`id: extras`, `activates: extras`, one template, one fragment). Changelog: 1.0.0, capabilities, `.stencil/`, config-level `template_env` removed, `COMPOSE_FILES` default path changed.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py tests/test_template_contract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md STENCIL.md CHANGELOG.md stencil/__init__.py tests/test_capabilities.py
git commit -m "docs: describe capabilities apart from consumer use cases"
```

______________________________________________________________________

## Spec coverage

| Spec                                                                                                    | Task                         |
| ------------------------------------------------------------------------------------------------------- | ---------------------------- |
| Load `capabilities_dir`, aggregated errors, duplicate ids                                               | 1                            |
| `when` is Jinja, missing names are false, syntax fails                                                  | 2                            |
| Shape versus explicit, derived-name rules                                                               | 3                            |
| `fields` lists, runner required when engine is playwright                                               | 4 (`required_when`)          |
| Inactive capability is not an error, optional names are `None`                                          | 5                            |
| `documents` built-in, dests under `.stencil/`                                                           | 6, 7                         |
| Fragment include order                                                                                  | 7                            |
| Pipeline paths and format lockfile                                                                      | 8                            |
| `--project-directory` and `make help`                                                                   | 9                            |
| `package_sources: [.]` skips `.stencil`                                                                 | 10                           |
| README split, version 1.0.0                                                                             | 11                           |
| Course directories, `test.sh` removal, Playwright `runner` templates, dropping cs234 `templates:` lists | Follow-on plan, not this one |

## Self-review notes

`required_when` is the data spelling of the spec sentence "runner is required when engine is playwright." The loader never mentions those words. Course adoption, when it is planned, puts that key on the grading capability and does not add a special case to stencil.
