# `clean` reads a manifest instead of re-deriving from the config (stn-p9a)

Date: 2026-09-12 · Ticket: stn-p9a · Classification: architectural

## The problem

`clean_generated` derives the list of files to remove from `.config.yaml`, via
`get_generated_files`. So the one command someone reaches for *because* their
config broke is the one that cannot answer.

Before stn-445 a broken package silently dropped out of that list and `clean`
removed a shorter one without saying so. After stn-445 it refuses outright and
tells the reader to delete the directory by hand. Refusing is better than lying,
but it is still not a capability — and `AGENTS.md` and `STENCIL.md` both record
the refusal as a deliberate, uncomfortable trade rather than a settled one.

There is a second, quieter half. Even on a config that parses, the removal list
is derived from the config *as it reads today*, not from what `gen` actually
produced. Edit a package's `docs:` list and the files generated under the old
list are no longer named by anything; `clean` walks past them.

## What this adds

`stencil gen` writes a per-package manifest of what it produced.
`stencil clean` reads that manifest. It falls back to deriving from the config
only when no manifest is present.

### The artifact

`<package dir>/.stencil-manifest.json`, one per package, written at the end of
a successful `generate_package` and never under `--dry-run`.

```json
{
  "manifest_version": 1,
  "stencil_version": "0.39.0",
  "package": "hs1",
  "dir": "hs1",
  "entries": [
    "Guide*.html",
    "Guide*.pdf",
    "Makefile",
    "docker-compose.yml",
    "format-package-lock.json"
  ]
}
```

- `entries` are paths **relative to the package directory**, POSIX separators,
  sorted, and may contain `*` exactly where `get_generated_files` already emits
  a glob: the `.html` and `.pdf` a `make` run prints beside each `docs:` /
  `slides:` entry, and a `package_name` archive. Those are build outputs, not
  things `gen` writes, and `clean` has always removed them; the manifest records
  them as the patterns they are.
- `manifest_version` is an integer. A manifest whose version this stencil does
  not know is **refused**, naming the version — a newer stencil may record
  something an older one would mis-read, and guessing at a delete list is the
  failure this whole ticket is about.
- `stencil_version`, `package` and `dir` are diagnostic. Nothing resolves paths
  through them; a manifest is always read from, and its entries always resolved
  against, the directory it was found in.

### One derivation, two consumers

`get_generated_files`'s per-package body moves into a helper —
`package_entries(package_id, package, context, config_templates) -> set[str]`,
returning unprefixed entries. `get_generated_files` prefixes each with the
package dir as it does today; the manifest writer records them verbatim.

This is the point of the change rather than a tidy-up. The repository has
already been bitten twice by one rule spelled in two places — `injected_sources`
exists because the injected-template list and the clean list drifted, and
stn-ttg exists because `copy_brand_image` and `get_generated_files` named the
same file two ways. A manifest derived by a second, parallel walk of the config
would be a third instance of exactly that. There is one walk, and the manifest
is its output.

The manifest does **not** list itself. `get_generated_files` adds
`<pkg_dir>/.stencil-manifest.json` on top of `package_entries`, so the managed
`.gitignore` section covers it and a config-derived `clean` removes it; a
manifest-driven `clean` removes it last, explicitly (below).

### What `clean` does

For each package in scope, in this order:

1. **A manifest is present** — use its entries. The config is not consulted for
   this package at all, beyond the `dir` needed to find the manifest.
2. **No manifest, config readable** — derive from the config, exactly as today.
3. **No manifest, config not readable** — remove nothing for that package, say
   so by name, and make the command exit non-zero.

The manifest wins over the config whenever both exist. That is the second half
of the ticket: the manifest is what `gen` did, the config is what `gen` would do
now, and a removal pass should be driven by the first.

**Every entry is re-checked before anything is unlinked.** A manifest is a file
on disk that nothing validated, so its entries get the same treatment a
configured path gets, plus one the config path gets for free:

- `check_config_path` on each entry — no absolute path, no `..`, no `~`, no
  control character, no shell or Make metacharacter, no whitespace. (`*` is not
  in that set, so the build-artifact globs pass unchanged.)
- after resolution, and after glob expansion, the path must still be under
  `(output_base / pkg_dir).resolve()`. An entry that is not is refused by name
  and makes the command exit non-zero.

An entry that fails either check is reported and skipped; it does not abort the
rest of the package. A manifest that will not parse, or carries an unknown
`manifest_version`, or is not a JSON object with a list of strings under
`entries`, is refused **for that package as a whole** — named, with the
instruction to delete the manifest if the config-derived fallback is wanted.
It deliberately does not fall back on its own: a damaged manifest is not the
same statement as no manifest, and quietly re-deriving is the guessing this
ticket removes.

The manifest is unlinked **last** for its package, after every other entry. A
clean that fails halfway then still has a manifest on disk to resume from.

### Exit status

`clean` exits non-zero if any package in scope could not be fully cleaned: no
manifest with an unreadable config, a damaged manifest, or a refused entry. It
exits zero when everything asked for was removed — including when the config is
broken but every package in scope had a manifest. The config problem is still
printed in that case; it is a warning, not the outcome.

### What does not change

- `install` and the managed `.gitignore` section still fail closed on a broken
  config. They describe what the config *says*, so a config that cannot be read
  has nothing to say. Only `clean`, which describes what is on disk, gains a
  disk-backed source.
- `gen` still fails closed. A manifest is written from a context that was built,
  never from a config that failed.
- `clean_generated`'s direct API keeps its current contract: called without the
  new flag it still reads the config and still raises on a broken one.
- The stn-vhm / stn-c25 / stn-ttg path rules are not reopened. They gain a
  second enforcement point rather than an exemption.

## Non-goals, stated so they are not mistaken for oversights

- **A package deleted from the config is not cleaned.** The manifest is found
  under the package's configured `dir`; with the package gone there is no `dir`
  to look under. Scanning `output_dir` for stray manifests was considered and
  rejected: `dir` may name a nested path, so a bounded scan would be wrong and
  an unbounded one would wander through a tree stencil does not own, to delete
  files. A deliberately narrow blast radius is worth more here than the case.
- **The manifest is not a lockfile.** It records the names `gen` produced, not
  hashes or mtimes, and `clean` does not verify that what it removes is what
  was written. Verifying would make `clean` refuse to remove a generated file
  an author had edited, which is the opposite of what `clean` is for.
- **No migration.** A package generated before this exists has no manifest and
  takes the config-derived path — which is today's behaviour exactly, so
  nothing regresses and nothing needs a one-time command.

## Two defects found while probing, filed rather than absorbed

Both were reproduced against 0.38.0 at 8e17d65 and are not this ticket.

- **stn-40a** — a non-string `output_dir` reaches the terminal as a
  `TypeError` traceback, because `_main` computes `output_base` above the
  `package_contexts` pre-flight. It is a precondition of the degraded `clean`
  path here: without a usable `output_base` there is nowhere to look for a
  manifest.
- **stn-7t9** (P1) — a symlinked package directory lets `clean` delete files
  outside the output tree, because nothing re-checks a path after `.resolve()`
  follows the link. The containment check this design requires for manifest
  entries is the same check, in the same loop, and applying it to one source of
  entries but not the other would be both more code and less defensible.

Whether either fix lands in this branch is the operator's call at the boundary
gate, not an assumption made here.

## Testing strategy

A new `tests/test_manifest.py`, unit tier (no container runtime), written before
the implementation.

**The manifest says what gen did**

- `gen` writes a manifest whose entries name every file on disk under the
  package, `rglob`-recursive and manifest-exclusive — the same outcome assertion
  `test_package_sources.py` already makes against `get_generated_files`, so the
  next injected template is caught here too.
- a manifest's entries equal `get_generated_files`' slice for that package,
  minus the manifest itself — the no-drift guarantee, which is the reason the
  derivation was extracted.
- `gen --dry-run` writes no manifest and says it would.
- the manifest appears in `get_generated_files` and in the managed `.gitignore`
  section.

**The manifest is what clean uses**

- config edited after `gen` to drop a `docs:` entry: `clean` still removes that
  document's build artifacts, from the manifest.
- config edited after `gen` to add a template: `clean` does not remove a file
  `gen` never produced.
- `clean` removes the manifest, and removes it last.

**The degraded path**

- broken config + manifest present: `clean` removes the package's files, prints
  the config problem, exits 0.
- broken config + no manifest: still fails closed, non-zero, no traceback —
  the stn-445 guarantee, pinned.
- `clean --all` over one package with a manifest and one without, on a broken
  config: the first is cleaned, the second is named, exit non-zero.
- `clean <pkg>` for a package with a manifest succeeds while a sibling is
  broken. This narrows decision d-adf7c52b, which pinned the collective
  behaviour; the narrowing is deliberate and gets its own test saying so.

**The boundary holds**

- a manifest entry that is absolute, `..`-escaping, `~`-prefixed, or carries a
  control character or a shell metacharacter is refused by name, the file it
  points at survives, and the command exits non-zero.
- a manifest under a symlinked package directory cannot reach outside the
  resolved package directory.
- a glob entry whose expansion lands outside the package directory is refused.
- a damaged manifest, a manifest that is not an object, one whose `entries` is
  not a list of strings, and one with an unknown `manifest_version` are each
  refused by name with nothing removed for that package.

**Documentation**

- `STENCIL.md` gains a manifest section and its "When the config is wrong"
  section is rewritten: `clean` is no longer simply the awkward one. The
  existing `tests/test_export_hygiene.py` / markdown lint pass covers format.
