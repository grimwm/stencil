"""Capabilities: self-describing directories that say when they apply.

A capability is a directory holding a ``capability.yaml``. This module only
loads and validates those manifests; deciding whether one applies, and
generating from it, live elsewhere.

Nothing here knows what any particular capability is. The loader reads the
manifest's own words and holds every directory to the same rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jinja2 import Environment, TemplateSyntaxError, meta

MANIFEST = "capability.yaml"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Names a shape ``when`` may use. Anything else belongs on an explicit block
# via ``activates``. These are the fields ``get_template_context`` derives,
# not keys a package author types as a capability switch.
DERIVED_NAMES = frozenset({
    "services",
    "docs",
    "slides",
    "package_sources",
    "package_type",
    "has_package_sources",
    "has_docs",
    "has_slides",
    "has_pages",
    "has_web",
    "has_mysql",
    "has_services",
    "has_package_output_dir",
    "has_pre_build",
})

# A ``when`` expression is a question about the context. A name the package did
# not set is ``None``, so ``grading is none`` is true and a comparison the
# value cannot answer does not match. Template bodies keep StrictUndefined;
# this environment is only for ``when``.
_WHEN_ENV = Environment()


@dataclass
class Capability:
    id: str
    directory: Path
    when: str
    activates: str | None = None
    fields: dict[str, list] = field(default_factory=dict)
    templates: list[dict] = field(default_factory=list)
    fragments: list[dict] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    required_when: dict[str, str] = field(default_factory=dict)


def load_capabilities(roots: list[Path]) -> list[Capability]:
    """Load every capability found one level below each root.

    Subdirectories without a ``capability.yaml`` are ignored. Every problem
    found across all roots is collected into a single ``ValueError``, so one
    run shows the whole list rather than the first item on it.
    """
    problems: list[str] = []
    loaded: list[Capability] = []
    seen: dict[str, Path] = {}

    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            manifest = directory / MANIFEST
            if not manifest.is_file():
                continue

            try:
                data = yaml.safe_load(manifest.read_text())
            except yaml.YAMLError as e:
                problems.append(f"{manifest}: not valid YAML: {e}")
                continue
            if not isinstance(data, dict):
                problems.append(f"{manifest}: must be a mapping of keys to values")
                continue

            missing = [k for k in ("id", "when") if data.get(k) in (None, "")]
            for key in missing:
                problems.append(f"{manifest}: missing required key '{key}'")

            cap_id = data.get("id")
            if "id" not in missing:
                cap_id = str(cap_id)
                if cap_id != directory.name:
                    problems.append(
                        f"{manifest}: id {cap_id} does not match "
                        f"directory name {directory.name}"
                    )
                if cap_id in seen:
                    problems.append(
                        f"{manifest}: duplicate id {cap_id}, already defined "
                        f"by {seen[cap_id]}"
                    )
                else:
                    seen[cap_id] = manifest

            if missing:
                continue

            capability = Capability(
                id=cap_id,
                directory=directory,
                when=str(data["when"]),
                activates=data.get("activates"),
                fields=data.get("fields") or {},
                templates=data.get("templates") or [],
                fragments=data.get("fragments") or [],
                optional=data.get("optional") or [],
                required_when=data.get("required_when") or {},
            )
            for problem in validate_capability(capability):
                problems.append(f"{manifest}: {problem}")
            loaded.append(capability)

    if problems:
        raise ValueError("\n".join(problems))
    return loaded


def validate_capability(capability: Capability) -> list[str]:
    """Return the shape and explicit-block problems for one capability.

    A shape rule (no ``activates``) may name only ``DERIVED_NAMES``. An
    explicit block must activate one identifier stencil does not already
    derive, and its ``when`` must mention that identifier. An empty list
    means the rule is well formed. Callers collect these strings; this
    function does not raise.
    """
    try:
        names = meta.find_undeclared_variables(
            _WHEN_ENV.parse("{{ " + capability.when + " }}")
        )
    except TemplateSyntaxError as e:
        return [f"{capability.id}: invalid when expression {capability.when!r}: {e}"]

    if capability.activates is None:
        return [
            f"{capability.id}: shape when names {name}, which stencil does not derive"
            for name in sorted(names)
            if name not in DERIVED_NAMES
        ]

    activates = capability.activates
    problems: list[str] = []
    if not isinstance(activates, str) or not _IDENTIFIER.fullmatch(activates):
        problems.append(f"{capability.id}: activates must be a single identifier")
        return problems
    if activates in DERIVED_NAMES:
        problems.append(
            f"{capability.id}: activates {activates}, which stencil already derives"
        )
    if activates not in names:
        problems.append(
            f"{capability.id}: when does not mention its activates key {activates}"
        )
    return problems


def check_fields(capability: Capability, block: dict) -> list[str]:
    """Return problems for a package block this capability activates.

    A key the block omits is allowed. A key it sets must be one of the
    values the capability listed. A ``required_when`` entry names a key
    that must be present when its expression is true of the block.
    """
    problems: list[str] = []
    for key, allowed in capability.fields.items():
        if key not in block:
            continue
        value = block[key]
        if value not in allowed:
            problems.append(
                f"{capability.id}: {key} is {value!r}, not one of {list(allowed)}"
            )
    for key, expression in capability.required_when.items():
        if key not in block and when_matches(expression, block):
            problems.append(
                f"{capability.id}: {key} is required when {expression}"
            )
    return problems


def matching(capabilities: list[Capability], context: dict) -> list[Capability]:
    """Return the capabilities whose ``when`` is true for ``context``.

    ``documents`` comes first when it matches. Every other match follows,
    sorted by id. A capability that does not match is absent, and that
    absence is not an error.
    """
    matched = [cap for cap in capabilities if when_matches(cap.when, context)]
    documents = [cap for cap in matched if cap.id == "documents"]
    rest = sorted(
        (cap for cap in matched if cap.id != "documents"),
        key=lambda cap: cap.id,
    )
    return documents + rest


def render_context(context: dict, capabilities: list[Capability]) -> dict:
    """Copy ``context`` and fill the names those capabilities may read unset.

    Optional names are filled only for capabilities that matched, because
    only those templates render. An omitted ``activates`` key is ``None``
    for every capability in the list, matched or not, so a shared template
    can mention a block this package never turned on.
    """
    filled = dict(context)
    matched_ids = {cap.id for cap in matching(capabilities, context)}
    for cap in capabilities:
        if cap.activates:
            filled.setdefault(cap.activates, None)
        if cap.id not in matched_ids:
            continue
        for name in cap.optional:
            filled.setdefault(name, None)
    return filled


def when_matches(expression: str, context: dict) -> bool:
    """Evaluate a capability's ``when`` expression against ``context``.

    Missing names are ``None``, so ``grading is none`` is true and
    ``grading.engine == 'x'`` is false when there is no ``grading``. A
    comparison ``None`` cannot answer does not match. A Jinja syntax error
    raises ``ValueError`` naming the expression.
    """
    try:
        ast = _WHEN_ENV.parse("{{ " + expression + " }}")
        compiled = _WHEN_ENV.compile_expression(expression)
    except TemplateSyntaxError as e:
        raise ValueError(f"invalid when expression {expression!r}: {e}") from e
    bound = dict(context)
    for name in meta.find_undeclared_variables(ast):
        bound.setdefault(name, None)
    try:
        return bool(compiled(**bound))
    except TypeError:
        return False
