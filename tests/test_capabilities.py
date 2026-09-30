"""Loading a capability directory: manifests are read, problems are reported together."""

import pytest

from stencil.capabilities import load_capabilities, when_matches


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


def test_shape_expression_matches_services():
    assert when_matches("'web' in services", {"services": ["web"]}) is True
    assert when_matches("'web' in services", {"services": []}) is False


def test_when_syntax_error_names_the_expression():
    with pytest.raises(ValueError, match="when"):
        when_matches("{% if %}", {})


def test_stored_boolean_strings_evaluate_as_booleans():
    # A YAML `when: true` is stored by the loader as the string "True".
    assert when_matches("True", {}) is True
    assert when_matches("False", {}) is False
