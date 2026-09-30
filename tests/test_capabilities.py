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
    assert "--project-directory ." in text
    assert "-f .stencil/docker-compose.yml" in text
    assert text.count("--project-directory .") >= 2


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


def test_stored_boolean_strings_evaluate_as_booleans():
    # A YAML `when: true` is stored by the loader as the string "True".
    assert when_matches("True", {}) is True
    assert when_matches("False", {}) is False
