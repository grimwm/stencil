"""Capabilities: self-describing directories that say when they apply.

A capability is a directory holding a ``capability.yaml``. This module only
loads and validates those manifests; deciding whether one applies, and
generating from it, live elsewhere.

Nothing here knows what any particular capability is. The loader reads the
manifest's own words and holds every directory to the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jinja2 import ChainableUndefined, Environment, TemplateSyntaxError

MANIFEST = "capability.yaml"

# A ``when`` expression is a question about the context, and "no such name" is a
# legitimate answer to it: a missing name is falsy and attribute access on it
# does not raise. Template bodies keep StrictUndefined; this environment is only
# for ``when``.
_WHEN_ENV = Environment(undefined=ChainableUndefined)


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

            loaded.append(
                Capability(
                    id=cap_id,
                    directory=directory,
                    when=str(data["when"]),
                    activates=data.get("activates"),
                    fields=data.get("fields") or {},
                    templates=data.get("templates") or [],
                    fragments=data.get("fragments") or [],
                    optional=data.get("optional") or [],
                )
            )

    if problems:
        raise ValueError("\n".join(problems))
    return loaded


def when_matches(expression: str, context: dict) -> bool:
    """Evaluate a capability's ``when`` expression against ``context``.

    Missing names are ``None``, so ``grading.engine == 'x'`` is simply false
    when there is no ``grading``. A Jinja syntax error raises ``ValueError``
    naming the expression.
    """
    try:
        compiled = _WHEN_ENV.compile_expression(expression)
    except TemplateSyntaxError as e:
        raise ValueError(f"invalid when expression {expression!r}: {e}") from e
    return bool(compiled(**context))
