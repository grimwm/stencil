"""Loading a capability directory: manifests are read, problems are reported together."""

from pathlib import Path

import pytest

from stencil import generate
from stencil.capabilities import (
    Capability,
    builtin_capabilities,
    check_fields,
    load_capabilities,
    matching,
    render_context,
    validate_capability,
    when_matches,
)


def _capability(**overrides) -> Capability:
    values = dict(
        id="grading",
        directory=Path("grading"),
        when="grading",
        activates="grading",
        fields={},
        templates=[],
        fragments=[],
        optional=[],
        required_when={},
    )
    values.update(overrides)
    return Capability(**values)


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
        (tmp_path / name / "capability.yaml").write_text("id: same\nwhen: 'true'\n")
    with pytest.raises(ValueError, match="same"):
        load_capabilities([tmp_path])


def test_every_problem_is_listed_in_one_error(tmp_path):
    (tmp_path / "nowhen").mkdir()
    (tmp_path / "nowhen" / "capability.yaml").write_text("id: nowhen\n")
    (tmp_path / "noid").mkdir()
    (tmp_path / "noid" / "capability.yaml").write_text("when: 'true'\n")
    (tmp_path / "wrong").mkdir()
    (tmp_path / "wrong" / "capability.yaml").write_text("id: other\nwhen: 'true'\n")
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "capability.yaml").write_text("id: same\nwhen: 'true'\n")
    (tmp_path / "plain").mkdir()  # no manifest: ignored
    with pytest.raises(ValueError) as exc:
        load_capabilities([tmp_path])
    message = str(exc.value)
    assert "nowhen" in message and "missing required key 'when'" in message
    assert "noid" in message and "missing required key 'id'" in message
    assert "id other" in message
    assert "duplicate id same" in message
    assert "plain" not in message


def test_missing_name_in_when_is_false():
    assert when_matches("grading", {}) is False
    assert when_matches("grading.engine == 'playwright'", {}) is False


def test_missing_name_in_when_is_none():
    assert when_matches("grading is none", {}) is True
    assert when_matches("grading == none", {}) is True
    assert when_matches("grading > 1", {}) is False
    assert when_matches("'web' in services", {}) is False


def test_shape_expression_matches_services():
    assert when_matches("'web' in services", {"services": ["web"]}) is True
    assert when_matches("'web' in services", {"services": []}) is False


def test_when_syntax_error_names_the_expression():
    with pytest.raises(ValueError, match="when"):
        when_matches("{% if %}", {})


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


def test_explicit_when_must_mention_activates():
    cap = Capability(
        id="grading", directory=Path("grading"), when="true",
        activates="grading", fields={}, templates=[], fragments=[], optional=[],
    )
    problems = validate_capability(cap)
    assert any("grading" in problem for problem in problems)


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


def test_inactive_capability_is_skipped_not_an_error():
    docs = _capability(id="documents", when="docs", activates=None)
    vscode = _capability(id="vscode", when="vscode", activates="vscode")
    matched = matching([vscode, docs], {"docs": ["README.md"], "services": []})
    assert [c.id for c in matched] == ["documents"]


def test_optional_name_is_none_when_unset():
    web = _capability(id="web", when="'web' in services", activates=None, optional=["grading"])
    ctx = render_context({"services": ["web"]}, [web])
    assert ctx["grading"] is None


def test_omitted_activates_key_is_none():
    grading = _capability(id="grading", when="grading", activates="grading")
    ctx = render_context({}, [grading])
    assert ctx["grading"] is None


def test_documents_runs_for_docs_and_not_for_an_empty_package():
    docs = {c.id: c for c in builtin_capabilities()}["documents"]
    present = {
        "docs": ["a.md"],
        "slides": [],
        "package_type": "none",
        "package_sources": [],
    }
    empty = {
        "docs": [],
        "slides": [],
        "package_type": "none",
        "package_sources": [],
    }
    assert when_matches(docs.when, present)
    assert not when_matches(docs.when, empty)


def test_documents_destinations_are_under_dot_stencil():
    docs = {c.id: c for c in builtin_capabilities()}["documents"]
    dests = [item["dest"] for item in docs.templates + docs.fragments]
    assert dests
    assert all(dest.startswith(".stencil/") for dest in dests)


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


def test_help_prints_the_compose_command_make_runs(generate_package):
    package = generate_package(
        {"packages": {"demo": {"package_type": "doc", "docs": ["Notes.md"]}}}
    )
    text = (package / "Makefile").read_text()
    assert "-f .stencil/docker-compose.yml" in text
    lines = [line for line in text.splitlines() if line.startswith("STENCIL_COMPOSE ")]
    assert any("--project-directory .stencil" in line for line in lines)
    assert any("--project-directory" not in line for line in lines)
    assert text.count("$(STENCIL_COMPOSE)") >= 2


def test_compose_files_default_is_the_base_file_for_a_documents_only_package(
    generate_package,
):
    package = generate_package(
        {"packages": {"demo": {"package_type": "doc", "docs": ["Notes.md"]}}}
    )
    text = (package / "Makefile").read_text()
    assert "COMPOSE_FILES ?= .stencil/docker-compose.yml\n" in text
    assert "web.compose.yml" not in text


@pytest.mark.parametrize("kind", ["templates", "fragments"])
def test_compose_files_default_adds_each_matched_compose_fragment(
    tmp_path, kind
):
    cap = tmp_path / "caps" / "web"
    cap.mkdir(parents=True)
    (cap / "capability.yaml").write_text(
        "id: web\n"
        "when: \"'web' in services\"\n"
        f"{kind}:\n"
        "  - src: web.compose.yml.j2\n"
        "    dest: .stencil/web.compose.yml\n"
    )
    (tmp_path / "tpl").mkdir()
    (tmp_path / "tpl" / "web.compose.yml.j2").write_text("services: {}\n")

    config = {
        "capabilities_dir": ["caps"],
        "templates_dir": ["tpl"],
        "packages": {"demo": {"package_type": "none", "services": ["web"]}},
    }
    # make_package does not pass config_dir, and capabilities_dir resolves
    # from it, so drive generate_package directly.
    env = generate.build_environment(config, tmp_path)
    out = tmp_path / "out"
    generate.generate_package(env, config, out, "demo", config_dir=tmp_path)
    text = (out / "demo" / "Makefile").read_text()
    assert (
        "COMPOSE_FILES ?= .stencil/docker-compose.yml .stencil/web.compose.yml\n"
        in text
    )
    assert "include .stencil/web.compose.yml" not in text


def test_compose_files_default_lists_matched_fragments_in_sorted_order(tmp_path):
    for cap_id, when in (
        ("web", "'web' in services"),
        ("mysql", "'mysql' in services"),
    ):
        cap = tmp_path / "caps" / cap_id
        cap.mkdir(parents=True)
        (cap / "capability.yaml").write_text(
            f"id: {cap_id}\n"
            f"when: \"{when}\"\n"
            "fragments:\n"
            f"  - src: {cap_id}.compose.yml.j2\n"
            f"    dest: .stencil/{cap_id}.compose.yml\n"
        )
    (tmp_path / "tpl").mkdir()
    for cap_id in ("web", "mysql"):
        (tmp_path / "tpl" / f"{cap_id}.compose.yml.j2").write_text("services: {}\n")

    config = {
        "capabilities_dir": ["caps"],
        "templates_dir": ["tpl"],
        "packages": {
            "demo": {
                "package_type": "none",
                "docs": ["Notes.md"],
                "services": ["web", "mysql"],
            }
        },
    }
    env = generate.build_environment(config, tmp_path)
    out = tmp_path / "out"
    generate.generate_package(env, config, out, "demo", config_dir=tmp_path)
    text = (out / "demo" / "Makefile").read_text()
    lines = [ln for ln in text.splitlines() if ln.startswith("COMPOSE_FILES ?=")]
    assert lines == [
        "COMPOSE_FILES ?= .stencil/docker-compose.yml"
        " .stencil/mysql.compose.yml .stencil/web.compose.yml"
    ]
    assert "include .stencil/documents.mk" in text
    assert "include .stencil/mysql.compose.yml" not in text
    assert "include .stencil/web.compose.yml" not in text


def test_stored_boolean_strings_evaluate_as_booleans():
    # A YAML `when: true` is stored by the loader as the string "True".
    assert when_matches("True", {}) is True
    assert when_matches("False", {}) is False


def test_readme_reference_does_not_define_capabilities_with_consumer_names():
    text = Path("README.md").read_text()
    reference, _, rest = text.partition("## Use cases")
    assert "## Use cases" in text
    for word in ("grading", "vscode", "runner"):
        assert word not in reference.split("## Configuration")[-1]


# --- a package field turns a capability on; templates live beside it --------


def _extras_project(tmp_path, package_extra=None, config_extra=None):
    """A capability that activates ``extras`` with one template beside its manifest.

    Nothing here sets ``templates:`` or ``template_env``, and ``templates_dir``
    does not point at the capability directory.
    """
    cap = tmp_path / "caps" / "extras"
    cap.mkdir(parents=True, exist_ok=True)
    (cap / "capability.yaml").write_text(
        "id: extras\n"
        "activates: extras\n"
        "when: extras\n"
        "templates:\n"
        "  - src: extras.txt.j2\n"
        "    dest: .stencil/extras.txt\n"
    )
    (cap / "extras.txt.j2").write_text("extras for {{ package_id }}\n")
    package = {"package_type": "none", "docs": ["Notes.md"]}
    package.update(package_extra or {})
    config = {"capabilities_dir": ["caps"], "packages": {"demo": package}}
    config.update(config_extra or {})
    return config


def _generate(tmp_path, config):
    env = generate.build_environment(config, tmp_path)
    out = tmp_path / "out"
    generate.generate_package(env, config, out, "demo", config_dir=tmp_path)
    return out / "demo"


def test_a_package_field_turns_on_the_capability_and_its_template_renders(tmp_path):
    config = _extras_project(tmp_path, {"extras": True})
    package = _generate(tmp_path, config)
    assert (package / ".stencil" / "extras.txt").read_text() == "extras for demo\n"


def test_omitting_the_field_leaves_the_capability_off(tmp_path):
    config = _extras_project(tmp_path)
    package = _generate(tmp_path, config)
    assert (package / "Makefile").is_file()
    assert not (package / ".stencil" / "extras.txt").exists()


def test_the_context_holds_the_block_or_none(tmp_path):
    config = _extras_project(tmp_path, {"extras": {"mode": "fast"}})
    context = generate.get_template_context("demo", config, tmp_path)
    assert context["extras"] == {"mode": "fast"}

    config = _extras_project(tmp_path)
    context = generate.get_template_context("demo", config, tmp_path)
    assert context["extras"] is None


def test_a_capability_named_something_else_gets_the_same_treatment(tmp_path):
    # Nothing special-cases `extras`: any loaded activates key is copied.
    cap = tmp_path / "caps" / "reports"
    cap.mkdir(parents=True)
    (cap / "capability.yaml").write_text(
        "id: reports\nactivates: reports\nwhen: reports is not none\n"
    )
    config = {
        "capabilities_dir": ["caps"],
        "packages": {"demo": {"package_type": "none", "reports": {"kind": "pdf"}}},
    }
    context = generate.get_template_context("demo", config, tmp_path)
    assert context["reports"] == {"kind": "pdf"}


def test_a_package_field_may_not_replace_a_derived_key(tmp_path):
    cap = tmp_path / "caps" / "clash"
    cap.mkdir(parents=True)
    (cap / "capability.yaml").write_text(
        "id: clash\nactivates: pandoc_image\nwhen: pandoc_image\n"
    )
    config = {
        "capabilities_dir": ["caps"],
        "packages": {"demo": {"package_type": "none", "pandoc_image": "x/y:1"}},
    }
    with pytest.raises(ValueError, match="pandoc_image"):
        generate.get_template_context("demo", config, tmp_path)


def test_a_config_level_template_env_key_is_not_in_the_context(tmp_path):
    config = _extras_project(tmp_path, config_extra={"template_env": {"leaked": 1}})
    context = generate.get_template_context("demo", config, tmp_path)
    assert "leaked" not in context
    assert "leaked" not in context["template_env"]


def test_a_config_level_template_env_cannot_turn_a_capability_on(tmp_path):
    config = _extras_project(tmp_path, config_extra={"template_env": {"extras": True}})
    package = _generate(tmp_path, config)
    assert not (package / ".stencil" / "extras.txt").exists()


def test_the_capability_directory_is_searched_before_the_template_path(tmp_path):
    config = _extras_project(tmp_path, {"extras": True})
    (tmp_path / "tpl").mkdir()
    (tmp_path / "tpl" / "extras.txt.j2").write_text("from the template path\n")
    config["templates_dir"] = ["tpl"]
    package = _generate(tmp_path, config)
    assert (package / ".stencil" / "extras.txt").read_text() == "extras for demo\n"


def test_a_capability_template_not_beside_it_still_comes_from_the_template_path(
    tmp_path,
):
    config = _extras_project(tmp_path, {"extras": True})
    (tmp_path / "caps" / "extras" / "extras.txt.j2").unlink()
    (tmp_path / "tpl").mkdir()
    (tmp_path / "tpl" / "extras.txt.j2").write_text("shared\n")
    config["templates_dir"] = ["tpl"]
    package = _generate(tmp_path, config)
    assert (package / ".stencil" / "extras.txt").read_text() == "shared\n"


def test_a_package_template_env_key_read_by_the_capability_template_is_used(tmp_path):
    config = _extras_project(tmp_path, {"extras": True, "template_env": {"flavor": "x"}})
    (tmp_path / "caps" / "extras" / "extras.txt.j2").write_text(
        "{{ flavor }}\n"
    )
    generate.validate_config(config, generate.build_environment(config, tmp_path), tmp_path)
    package = _generate(tmp_path, config)
    assert (package / ".stencil" / "extras.txt").read_text() == "x\n"


def test_a_package_template_env_key_nothing_reads_is_still_rejected(tmp_path):
    config = _extras_project(tmp_path, {"extras": True, "template_env": {"unused": 1}})
    with pytest.raises(ValueError, match="unused"):
        generate.validate_config(
            config, generate.build_environment(config, tmp_path), tmp_path
        )


def test_the_cli_generates_from_capabilities_without_a_templates_list(tmp_path):
    import yaml

    from tests.test_cli import run_cli

    config = _extras_project(tmp_path, {"extras": True})
    config["output_dir"] = "out"
    (tmp_path / ".config.yaml").write_text(yaml.safe_dump(config))
    result = run_cli("gen", "demo", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "demo" / ".stencil" / "extras.txt").is_file()
    assert (tmp_path / "out" / "demo" / "Makefile").is_file()


# --- install and clean see what a capabilities_dir capability writes --------
#
# `capabilities_dir` is resolved against the config file's directory, so a
# caller that does not hand that directory down loads only the built-ins.
# `gen` passed it; `get_generated_files` did not, so every file a course's
# own capability wrote was missing from the managed .gitignore section and
# from the list `clean` removes -- generated on every `gen`, removed by
# nothing, and offered to the author as untracked forever. That is stn-633's
# failure mode reached by a different route, and it is why these tests drive
# the real commands rather than asserting on the loader.


def _extras_project_on_disk(tmp_path):
    """`_extras_project`, written out as a config file `gen` can be run against."""
    import yaml

    config = _extras_project(tmp_path, {"extras": True})
    config["output_dir"] = "out"
    (tmp_path / ".config.yaml").write_text(yaml.safe_dump(config))
    return config


def test_get_generated_files_names_a_capabilities_dir_template(tmp_path):
    config = _extras_project(tmp_path, {"extras": True})
    assert ".stencil/extras.txt" in {
        entry.removeprefix("demo/")
        for entry in generate.get_generated_files(config, tmp_path)
    }


def test_get_generated_files_without_a_config_dir_still_names_the_builtins(tmp_path):
    """The parameter is optional, and a caller that omits it keeps today's
    answer for everything stencil itself writes -- only the capability
    directory, which cannot be resolved without a root, drops out."""
    config = _extras_project(tmp_path, {"extras": True})
    entries = {entry.removeprefix("demo/") for entry in generate.get_generated_files(config)}
    assert "Makefile" in entries
    assert ".stencil/extras.txt" not in entries


def test_install_ignores_a_capabilities_dir_template(tmp_path):
    from tests.test_cli import run_cli

    _extras_project_on_disk(tmp_path)
    result = run_cli("install", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "out/demo/.stencil/extras.txt" in (tmp_path / ".gitignore").read_text()


def test_clean_removes_a_capabilities_dir_template_from_the_config(tmp_path):
    """The manifest `gen` leaves behind already names this file, so the gap
    only shows with the manifest gone -- which is exactly the state a
    consumer reaches by cleaning a tree generated before manifests, or by
    deleting one. Drive the config-derived fallback deliberately."""
    from tests.test_cli import run_cli

    _extras_project_on_disk(tmp_path)
    assert run_cli("gen", "demo", cwd=tmp_path).returncode == 0

    written = tmp_path / "out" / "demo" / ".stencil" / "extras.txt"
    assert written.is_file()
    (tmp_path / "out" / "demo" / generate.MANIFEST_NAME).unlink()

    result = run_cli("clean", "--all", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert not written.exists()


def test_clean_removes_a_capabilities_dir_template_from_the_manifest(tmp_path):
    """The other half: with the manifest present, `clean` drives from it.
    Both routes have to reach the same file, or which one runs decides
    whether the file survives."""
    from tests.test_cli import run_cli

    _extras_project_on_disk(tmp_path)
    assert run_cli("gen", "demo", cwd=tmp_path).returncode == 0

    written = tmp_path / "out" / "demo" / ".stencil" / "extras.txt"
    assert written.is_file()

    result = run_cli("clean", "--all", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert not written.exists()


def test_install_does_not_check_that_a_brand_image_exists(tmp_path):
    """Regression guard for the way this was nearly fixed. `config_dir` in
    `package_contexts` gates brand's file-existence check, which is
    deliberately gen-only (decision d-96da97d8) -- so threading the config
    directory in through THAT parameter would make `install` and `clean`
    fail on a missing logo. The capability root is a separate question and
    travels separately."""
    import yaml

    from tests.test_cli import run_cli

    config = _extras_project(tmp_path, {"extras": True})
    config["output_dir"] = "out"
    config["brand"] = "logo.svg"
    config["brand-alt"] = "SIU"
    (tmp_path / ".config.yaml").write_text(yaml.safe_dump(config))

    # logo.svg is not on disk anywhere.
    result = run_cli("install", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "not a file" not in result.stderr


def test_a_broken_capability_file_does_not_let_clean_trust_the_manifest(tmp_path):
    import json

    import yaml

    from tests.test_cli import run_cli

    config = _extras_project(tmp_path, {"extras": True})
    config["output_dir"] = "out"
    (tmp_path / ".config.yaml").write_text(yaml.safe_dump(config))
    generated = run_cli("gen", "demo", cwd=tmp_path)
    assert generated.returncode == 0, generated.stderr
    package = tmp_path / "out" / "demo"
    manifest_path = package / ".stencil-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["entries"].append("NOTES.md")
    manifest_path.write_text(json.dumps(manifest))
    (tmp_path / "caps" / "extras" / "capability.yaml").write_text("id: [")

    result = run_cli("clean", "--all", "--dry-run", cwd=tmp_path)
    combined = result.stdout + result.stderr
    assert "NOTES.md" not in combined or "does not derive" in combined
    assert "Would remove" not in combined or "NOTES.md" not in combined


def test_a_fragment_whose_when_is_false_is_not_included(tmp_path):
    config = _extras_project(tmp_path, {"extras": True})
    cap = tmp_path / "caps" / "extras" / "capability.yaml"
    text = cap.read_text()
    cap.write_text(
        text
        + "fragments:\n"
        + "  - src: extras.txt.j2\n"
        + "    dest: .stencil/extra.mk\n"
        + "    when: has_slides\n"
    )
    package = _generate(tmp_path, config)
    makefile = (package / "Makefile").read_text()
    assert "include .stencil/extra.mk" not in makefile
    assert not (package / ".stencil" / "extra.mk").exists()


def test_a_hyphenated_when_name_is_still_a_key(tmp_path):
    config = _extras_project(tmp_path, {"template_env": {"has-vscode": True}})
    config["templates"] = [
        {"src": "marker.txt.j2", "dest": "marker.txt", "when": "has-vscode"}
    ]
    (tmp_path / "marker.txt.j2").write_text("yes\n")
    config["templates_dir"] = ["."]
    package = _generate(tmp_path, config)
    assert (package / "marker.txt").read_text() == "yes\n"


def test_when_true_is_a_jinja_literal_not_a_missing_key():
    assert generate.when_holds({"when": "true"}, {}) is True
    assert generate.when_holds({"when": "True"}, {}) is True
    assert generate.when_holds({"when": "false"}, {}) is False


def test_a_scalar_field_list_is_refused():
    cap = _capability(fields={"engine": "chrome"})
    problems = validate_capability(cap)
    assert any("list" in problem for problem in problems)


def test_a_zip_package_still_has_format_md(generate_package):
    package = generate_package(
        {
            "packages": {
                "demo": {
                    "package_type": "zip",
                    "package_name": "demo.zip",
                    "package_sources": ["htdocs"],
                }
            }
        }
    )
    text = (package / "Makefile").read_text()
    assert "format-md:" in text
    assert ".PHONY:" in text
