# Template packs — a design

**Status: agreed design, not built.** The trigger to build it is at the bottom.
Tracked as `stn-zza`.

A pack is a named, versioned bundle of templates and the wiring that renders
them, which a course repo depends on instead of keeping in a directory of its
own. This document answers the four questions `stn-zza` raised, and it corrects
two premises in that ticket that turned out not to hold.

## What is actually true today

The ticket says the consumer "now maintains a generator of its own, duplicating
the walk-config-render loop stencil already implements". It does not.
`cs234/_generator/` contains exactly one thing — `templates/` — and cs234 renders
it by pointing stencil at it:

```yaml
templates_dir: ../_generator/templates
```

There is no second engine, and no rendering improvement has to be reimplemented
anywhere. The ticket also expects a blast radius across two course repos; cs425
has `templates_dir` commented out in all three of its configs and uses only the
bundled templates, so the radius today is one repo.

What is true, and is the thing worth designing for, is subtler and more
interesting. cs234 overrides stencil's two **composition** templates —
`Makefile.j2` and `docker-compose.yml.j2` — and its versions include stencil's
partials:

```jinja
{% include 'Makefile-base.j2' %}
...
{% include 'Makefile-doc.j2' %}
{% include 'Makefile-pkg.j2' %}
```

So the layering a pack would formalise is already in use and already works. The
search path — every configured `templates_dir`, then the bundled set, first match
wins — is the whole mechanism, and it is enough.

The hazard is that the layering has a contract nobody checks. cs234's
`Makefile.j2` is 200 lines composed against variables that `Makefile-base.j2`,
`Makefile-doc.j2` and `Makefile-pkg.j2` happen to define today. Rename one and
cs234's composition silently renders something subtly wrong; nothing in either
repository fails. That, not a duplicated engine, is the real cost of scaffolding
living outside the tool.

## The four questions

### Where does a pack live?

An ordinary Python distribution, installed the way stencil is, declaring an entry
point in the `stencil.packs` group that resolves to its template directory and
manifest. Not a git submodule, not a directory convention, not a fetch stencil
performs.

Reasons: it is the same shape stencil already uses to ship its own templates
(`[tool.setuptools.package-data]`), it gives the pack a name and a version
without stencil learning anything about git, and it means `pip install git+https://…/cs-grading-pack.git` is the entire installation story. A pack
becomes discoverable — `stencil packs` can list what is installed — which a
directory convention never gives you.

### How does a pack extend the config schema?

It already can, and the mechanism should not be replaced. `template_env` merges
arbitrary keys into the top-level context and `when:` tests them; cs234 drives
`has_vscode` and `has_install_scripts` through exactly this path today.

The one gap worth closing is that unknown keys are silent. `context.update( template_env)` accepts anything, and a `when:` naming a key nobody set reads as
`None`, so `has_vscde: true` renders nothing and reports nothing. A pack should
therefore ship a manifest that **declares** the keys it reads, with defaults and
one line of prose each, and stencil should reject a `template_env` key that no
installed pack declares.

That is the only schema work a pack needs. It is small, and it is worth doing on
its own even if packs are never built.

### What may a pack override?

**Templates, by filename, and nothing else.** Precedence runs: the config's own
`templates_dir` entries, then packs in the order the config names them, then the
bundled set. That ordering preserves today's escape hatch — a course repo can
still override one file locally without forking the pack.

What stays stencil's, and what a pack must not be able to reach:

- The derived context (`has_pages`, `has_docs`, `has_package_sources`,
  `package_stem`, `pandoc_argv_doc`, `pandoc_argv_slide`). These are computed
  from config, and a pack that could redefine them could make two packages
  disagree about what a package *is*.
- The pandoc invocation. The filter order is load-bearing in ways that are
  documented in `AGENTS.md` and enforced by tests — citeproc after
  `hidden-filter`, before `slide-sections` — and a pack reordering it would
  leak an answer key's sources into a handout with no error anywhere.

The line is: a pack decides what files a package gets and what is in them; it
does not decide what a package is or how markdown is rendered.

### Does the existing `templates:` list collapse into this?

Yes, and it is the main thing a pack buys. cs234's config carries 22 lines of
`src`/`dest`/`when` wiring for nine template entries — and that list, not the
templates, is what a second course repo would have to copy verbatim to get the
same scaffolding.

A pack's manifest ships that list. A config then says:

```yaml
packs: [cs-grading]
```

and the entries arrive with it. The config's own `templates:` list stays exactly
as it is, appended after the packs' entries, for one-off local additions. One
mechanism with two producers — the list is not removed, it gains a second source.

## Migration, without a flag day

Nothing here requires two repositories to move together.

1. **Stencil gains `packs:`.** With no `packs:` key the behaviour is
   byte-identical to today. cs234 and cs425 are untouched, and this step ships
   and merges on its own.
1. **Build the pack from what exists.** `cs234/_generator/templates` moves into
   the pack verbatim, along with a manifest holding cs234's `templates:` entries
   and declarations for `has_vscode` and `has_install_scripts`. The acceptance
   test is mechanical: generate every cs234 package through the pack and diff
   against what is on disk. A non-empty diff is a bug in the move.
1. **cs234 switches, in one revertible commit.** Delete `_generator/templates`,
   replace `templates_dir` and the `templates:` list with `packs: [cs-grading]`.
   The generated output does not change, which step 2 has already proved.
1. **cs425 adopts it if it ever wants grading**, by naming the pack. It needs no
   change otherwise, and it does not need to move when cs234 does.

## What this deliberately does not do

It does not introduce pinning. A pack is tracked the way course repos track
stencil — installed from the default branch, bumped by reinstalling — and
depending on a pack must not become a version contract through the back door.
`stn-aig` settled that question for stencil and the answer does not change
because the dependency has a different name.

It also does not make the composition contract safe. Formalising the layering
gives it a name and a version; it does not make a renamed variable in
`Makefile-base.j2` fail loudly in a pack that composes against it. That is a
separate problem, it is the one that actually bites, and a pack is not a fix for
it. Worth its own ticket either way.

## Should this be built now?

Not yet, and the reason is the corrected premise. With no duplicated engine and
with cs425 using only bundled templates, a pack today buys a name and a version
for one directory in one repository. That is real but small, and it is bought
with a new distribution to maintain, an entry-point mechanism, and a `packs:` key
in the config schema.

**The trigger to build it is a second repository needing those templates.** At
that moment the alternative is copying a directory and 22 lines of wiring, the
duplication stops being hypothetical, and every argument in this document starts
paying for itself. Until then the design is the deliverable.

Two pieces are worth doing before that trigger, because they stand alone:

- **Declared `template_env` keys**, so a typo fails instead of silently
  rendering nothing. Useful to cs234 today with no pack in sight.
- **A test that the composition contract holds** — that the variables cs234's
  `Makefile.j2` composes against are the ones stencil's partials define. This is
  the failure that can actually reach a handout.
