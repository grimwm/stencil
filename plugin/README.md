# stencil-tool

A Cowork/Claude Code plugin for repositories scaffolded by
[stencil](https://github.com/grimwm/stencil).

Install it into any repository that stencil scaffolds so agent sessions know how to build, verify
and troubleshoot the document and deck pipeline without rediscovering it.

The skill describes only what stencil itself emits: `stencil gen`, and the targets of the generated
`Makefile`. A consuming repository's own wrappers and layout belong in that repository's skills.

## What it contains

| Skill            | Purpose                                                                                        |
| ---------------- | ---------------------------------------------------------------------------------------------- |
| `docs-and-decks` | Build/verify/troubleshoot the pandoc pipeline; work around a Cowork sandbox that has no Docker |

## Design rule

**This plugin does not restate the dialect.** `AUTHORING.md` and `STENCIL.md` in this repository are
the source of truth for markdown syntax, slide layouts and package configuration, and the skill links
to them rather than copying them. A copy one directory from the original drifts from it, and a
copy in a different repository drifts faster.

What the skill *does* carry is the operational knowledge that lives nowhere else: how to verify slide
splitting without Docker, how to produce a real PDF/UA file from a cloud sandbox, and which git
commands are unsafe on a Cowork mount.

## Updating

The skill is a plain `SKILL.md`. Edit it here, bump `version` in
`.claude-plugin/plugin.json`, and re-package:

```bash
cd plugin && zip -r /tmp/stencil-tool.plugin . -x "*.DS_Store"
```
