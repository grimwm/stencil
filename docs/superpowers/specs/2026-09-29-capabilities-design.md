# Capabilities

Date: 2026-09-29
Status: approved

Stencil 1.0.0 generates a package by running capabilities. A capability is a
directory of templates plus a `capability.yaml`. It renders its templates and
its Makefile and Compose fragments only for packages whose rule matches. A
capability that does not match contributes nothing: no files, and no names in
the Makefile.

This replaces the config-level `templates:` shopping list as the way a course
selects files. `cs234` repeats that list across three configs, and its shared
`Makefile.j2` reads flags (`has_playwright`, `deps_script`) that only some of
those configs set, so the others declare the flags `false` to satisfy
`StrictUndefined`. `cs425` already lists only `Makefile.j2` and
`docker-compose.yml.j2`, which is the document capability written out by hand.

The course repos adopt the mechanism in the same effort. Stencil ships the
loader and one built-in capability, `documents`. Course behavior (nginx,
MySQL, VS Code, install scripts, grading) stays in the course repo.

______________________________________________________________________

## The config a course writes

`capabilities_dir` is a path, or a list of paths, relative to the config file.
Each immediate subdirectory that contains a `capability.yaml` is one
capability. Stencil always loads `documents` as well.

```yaml
capabilities_dir: ../_generator/capabilities

packages:
  hs4:
    name: "HTML: Forms"
    dir: hs4-HTML-Forms
    package_type: zip
    package_name: hs4.zip
    docs: [README.md]
    services: [web, mysql]
    package_sources: [htdocs]
    vscode: true
    sql_import:
      target: import-seed
      database: seed
      file: db/seed.sql
```

A config names packages. It does not list the templates those packages need.
`templates:` remains an escape hatch for a single file that belongs to no
capability, with the same `src`, `dest`, and `when` fields as a capability
template. A config that only uses capabilities omits the list.

Package-level `template_env` remains a bag of extra keys merged into the
render context, for a key no capability has taken over. Config-level
`template_env` goes away. Defaults for keys a shared template might mention
live on the capability, once, not on every config that shares the template.

## Turned on by the package

A capability has one `when` expression. It is a Jinja expression evaluated
against the package context. Missing names in a `when` expression are `None`,
so a test of an unset block is false. A syntax error in `when` fails
validation.

Two kinds of rule, told apart by whether the manifest sets `activates`.

### Shape

No `activates` key. `when` may name only values stencil already derives from
the package (`services`, `docs`, `slides`, `package_sources`, `package_type`,
and the `has_*` booleans derived from those). Any other name is a validation
error. Setting the field is the switch. The package does not also name the
capability.

| Field                                                                     | Capability  |
| ------------------------------------------------------------------------- | ----------- |
| `docs` or `slides` is non-empty, or a `doc` package has `package_sources` | `documents` |
| `services` contains `web`                                                 | `web`       |
| `services` contains `mysql`                                               | `mysql`     |

`sql_import` is data the `mysql` capability reads. It is not a switch. A
package with `services: [mysql]` and an `sql_import` block gets the import
targets. A package with neither gets no MySQL files and no MySQL targets.

### Explicit block

`activates` names the one package key that turns the capability on. `when`
is that key, or a test of fields inside it. The key is optional on every
package. In the render context it is the block the package wrote, or `None`
when the package omitted it. Omitting it leaves the capability off.

A capability may not `activates` a name stencil derives.

An explicit capability may declare the values it accepts for a field of
that block:

```yaml
id: grading
activates: grading
when: grading
fields:
  engine: [playwright, mysql]
  runner: [problems, open]
```

A package whose block sets `engine` or `runner` to anything outside that
list fails validation. The list is the course capability's; stencil does
not know the names. Omitting `fields` means any truthy block matches, and
template-level `when` expressions do the narrowing. `grading` declares
`engine` so an unknown engine cannot render an empty file set and exit 0. `runner` is required when `engine` is `playwright`. `engine: mysql`
does not set `runner`.

`vscode` has no payload beyond being set. Assignment packages that want
`.vscode/extensions.json` and `.vscode/settings.json` set `vscode: true`.
Grading packages do not use those files. `answers/.config.yaml` leaves
`vscode` unset, and stencil does not write them.

`grading` carries the payload its templates read. The assignment `hs4` and
the grading `hs4` both have web and MySQL. Only the grading package sets the
block:

```yaml
hs4:
  package_type: none
  dir: hs4-HTML-Forms
  docs: [README.md]
  services: [web, mysql]
  sql_import:
    target: import-seed
    database: seed
    file: db/seed.sql
  grading:
    engine: playwright
    runner: problems
    assignment_id: hs4
    assignment_title: HS4
    assignment_total_points: 20
    problems:
      - { name: p1, label: P1 Text Box }
```

A SQL grader uses the same block with `engine: mysql` and the fields that
grader reads (`assignment_name`, `base_database`, `problems`). `install`
carries the setup scripts for the one package that has them:

```yaml
install:
  Windows_NT: [install-tools.ps1]
  default: [install-tools.sh]
```

A template inside an active capability can narrow further with its own
`when`. The Playwright sources require `grading.engine == 'playwright'`. The
SQL grader sources require `grading.engine == 'mysql'`. Those expressions run
only after the capability has matched, so `grading` is a mapping.

A document-only package sets `docs` or `slides` and nothing else. The only
capability that matches is `documents`.

A capability that matches no package in this config is fine. `classroom`
loads the shared `vscode` capability and never sets the key. That is an
inactive capability, not an error.

## What a capability directory holds

```text
capabilities/vscode/
  capability.yaml
  extensions.json
  settings.json

capabilities/web/
  capability.yaml
  Dockerfile
  nginx.conf.j2
  make.mk.j2
  compose.yml.j2

capabilities/grading/
  capability.yaml
  playwright/...
  db/...
  make.mk.j2
```

```yaml
id: web
when: "'web' in services"
templates:
  - src: Dockerfile
    dest: .stencil/Dockerfile
  - src: nginx.conf.j2
    dest: .stencil/nginx.conf
fragments:
  - src: make.mk.j2
    dest: .stencil/web.mk
  - src: compose.yml.j2
    dest: .stencil/web.compose.yml
optional: [front_controller, docroot_subdir, grading]
```

`id` matches the directory name. `src` is a file in that directory. `dest`
defaults to `src` with a `.j2` suffix removed, and is the path written into
the package.

Template lookup for a capability checks that capability's directory, then
each `templates_dir` in order, then stencil's bundled templates. The first
match wins. A course replaces one bundled file by shipping its own under the
same name. A course capability id that collides with `documents`, or with
another directory's id, is a validation error.

`optional` lists names this capability's templates may read that the package
might not have set. Each is `None` when unset, for packages where this
capability is active. A template tests them with a guard:

```jinja
{% if grading and grading.engine == 'playwright' %}
{% include 'nginx-view-source.conf' %}
{% endif %}
```

`front_controller` and `docroot_subdir` are package fields the semester
project sets and every other web package leaves unset. The view-source panel
is the `grading` test above. `view-source.js` is a grading template, rendered
with the Playwright sources, not a web template.

`StrictUndefined` still applies to every other name. A typo of `docs` or
`services` fails the render. An `optional` name is the only absence a
template can see, and the capability declares it.

## Where generated files go

The generated `Makefile` stays at the package root, because `make` looks
there. Everything else stencil writes goes in `.stencil/` beside that
Makefile, so a plain `ls` of the package does not list it. `include` paths
and Compose file paths are `.stencil/...`.

These generated files stay outside `.stencil` because some other program
already decided their path:

- `.vscode/extensions.json` and `.vscode/settings.json`. The editor reads
  `.vscode` at the workspace root.
- `htdocs/view-source.js`. Nginx serves that URL.
- A brand image copied for a page to display. The HTML refers to it at the
  package root.

`Makefile.local` is hand-written and stays at the package root. The
generated Makefile ends with `-include Makefile.local`.

`db/test.sh.j2` is not carried forward. SQL grading has no `test.sh` at
the package root and none inside `.stencil`. The grading capability's
`grading.mk` holds a `test` target for the SQL grader, separate from the
Playwright `test` target in that same fragment, and it runs the Python
grader directly.

Playwright's `package.json` and `playwright.config.ts` are generated into
`.stencil/` too. They are not hand-written. Two templates, selected by
`grading.runner`:

- `problems` is the homework runner. The ten homework configs are one file
  today, and the ten `package.json` files differ only by name. The config
  imports `discoverStudents`, sets `testDir` to `./e2e`, and sets workers
  from the student count. Each student has the same pages, so the tests run
  in parallel. `package.json` names the package from `assignment_id` and
  depends on `@playwright/test`.
- `open` is the semester-project runner. Students design their own site
  within the requirements in the assignment README, and the grader loads one
  student at a time into a single nginx docroot (`docroot_subdir`). The
  config forces `workers: 1` and `fullyParallel: false`, matches
  `*-grading.spec.ts`, and keeps traces and screenshots on failure.
  `package.json` adds `mysql2`, `tsx`, and TypeScript, plus a `discover`
  script. That runner stays its own template. Folding it into `problems`
  would assume every submission is the same assignment.

`make test` installs with `npm --prefix .stencil` and runs Playwright with
`--config .stencil/playwright.config.ts`. Relative paths inside those two
files (`./e2e`, `e2e/write-reports.js`) are relative to `.stencil/`, where
the generated tests live.

A package whose `package_sources` walks `.` skips `.stencil` when it builds
the archive.

Pandoc filters, HTML templates, the browser Dockerfile, and the format
lockfile live in `.stencil/`. `pipeline.py` and the document Compose
service name those paths with the `.stencil/` prefix. The format-md service
still mounts the package directory as its working directory, and its
existence check looks for `.stencil/format-package-lock.json`.

## Fragments

Stencil always writes a base `Makefile` and `.stencil/docker-compose.yml`.
They are the driver. They are not a capability the course lists.

The base Makefile is today's `Makefile-base.j2`, the fragment includes,
and `clean: clean-pkg`. `documents.mk` includes `Makefile-doc.j2` and
`Makefile-pkg.j2`.

The base Makefile includes one fragment per capability that matched, the
built-in first, then the rest sorted by `id`:

```makefile
include .stencil/documents.mk
include .stencil/mysql.mk
include .stencil/web.mk
```

A grading package also has `include .stencil/grading.mk`. A syllabus
package has `include .stencil/documents.mk` only. An inactive capability's
fragment is not written, and the base Makefile does not name it.
Playwright targets live in `grading.mk`, so a document Makefile never
mentions them.

The base Compose file holds the services every package has (the `format-md`
service and its lockfile, which today are `ALWAYS_TEMPLATES` because a
predicate kept getting the include graph wrong). Each matching capability
that has a compose fragment adds `.stencil/<id>.compose.yml`.

`STENCIL_COMPOSE` pins both the file list and the project directory:

```makefile
COMPOSE_FILES ?= .stencil/docker-compose.yml .stencil/web.compose.yml
STENCIL_COMPOSE = $(DC) --project-directory . $(addprefix -f ,$(COMPOSE_FILES))
```

`--project-directory .` is required. Relative bind paths in a Compose file
are resolved from the first file's directory, and without the flag a
`./htdocs` mount written in `.stencil/docker-compose.yml` would look for
`.stencil/htdocs`. The flag makes every relative path the package root,
which is the directory `make` runs in. `DC` may be `docker compose` or
`podman compose`, as it is today; both take `--project-directory` before
the subcommand.

The generated Makefile opens with a comment, and `make help` prints one
line, both built from `DC` and `COMPOSE_FILES` so they cannot drift from
`STENCIL_COMPOSE`:

```text
Compose, from this directory: docker compose --project-directory . -f .stencil/docker-compose.yml -f .stencil/web.compose.yml
```

Typing `docker compose up` in the package directory does not see those
files. The comment is the way to run Compose without `make`.

The shared partials (`Makefile-base.j2`, `Makefile-doc.j2`, `Makefile-pkg.j2`,
`docker-compose-html.yml.j2`) stay the interface of the `documents`
capability. `tests/test_template_contract.py` still records the keys they
read. Course targets (nginx, SQL lint, Playwright) live in course fragments,
so a new key on a stencil partial does not have to be threaded through those
targets.

## The documents capability

Ships with stencil. Runs when `docs` or `slides` is non-empty, and when a
`doc` package builds a PDF from `package_sources`. Writes the Pandoc HTML
templates, the Lua filters, the PDF driver, the browser image and its
lockfile, and the `doc`, `slide`, `pdf`, `check-access`, and `check-pdf`
fragment. A config does not list those files. The browser image and its
lockfile are written only when `documents` runs. The format lockfile is
written to `.stencil/` for every package, because the base Compose file's
`format-md` service copies it from there.

## Errors

Validation stays fail-closed and aggregated, one report for the config, in
the style of `package_contexts`. The config is refused when:

- a `capability.yaml` is missing `id` or `when`, or `id` differs from the
  directory name
- two capabilities share an `id`
- a shape `when` names anything stencil does not derive
- an explicit capability's `activates` is missing, is not a single
  identifier, or reuses a derived name
- an explicit `when` does not mention its `activates` key
- a package sets a `fields` entry to a value outside that capability's
  list
- a `when` expression does not parse
- two different sources write one package path (the existing collision
  rule; a template listed as well as injected by `documents` is still one
  source)
- a rendered template reads a name that is not derived, not a package key
  the context carries, and not in that capability's `optional` list

A capability matching zero packages is not an error. A package that sets a
`fields` value outside that capability's list fails validation. The loader
does not special-case engine names.

## README

`README.md` has two layers, and the first one does not borrow vocabulary
from the second.

The configuration section explains the mechanism in the terms stencil
already uses for packages: a directory with a `capability.yaml`, a `when`
rule that matches a field stencil derives, an explicit block the package
sets, fragments included from the base Makefile, and generated files in
`.stencil/` with the `Makefile` left at the package root. The Compose
section shows `STENCIL_COMPOSE` and the line `make help` prints. `documents`
is the built-in capability. `templates:` is the one-file escape hatch.
That section does not name a consumer's capabilities, blocks, or field
values.

A later section, set off from the configuration reference, shows use cases:
a small capability directory, a package that turns it on, and the files
`stencil gen` writes. A use case may be taken from a real consumer,
including a course, when that is the clearest way to show a setup. It
stays in that section. The configuration reference does not start using
that consumer's names (`grading`, `vscode`, `runner`) as if they were part
of stencil.

`STENCIL.md` keeps the bundled-template inventory and points capability
authors at the README.

## Course adoption

After the loader exists:

- `cs234` splits `_generator/templates/Makefile.j2` and
  `docker-compose.yml.j2` into `web`, `mysql`, `vscode`, `grading`, and
  `install` capability directories. The document composition goes back to
  including stencil's partials from the `documents` fragment only.
- `assignments` sets `vscode: true` on the packages that have it today.
  `answers` does not set `vscode`. `install` is set on `hs1` only.
  `grading` replaces `has_playwright`, `has_db_grading`, and the grading
  keys those templates read from `template_env`.
- `db/test.sh.j2` is deleted. SQL grading's `test` target in `grading.mk`
  runs the Python grader. No package gets a `test.sh`.
- Homework grading packages set `grading.runner: problems`. The semester
  project sets `engine: playwright` and `grading.runner: open`. Both get
  `.stencil/package.json` and `.stencil/playwright.config.ts` from the
  matching template. The hand-written copies at the package root go away.
- All three cs234 configs drop their `templates:` lists and their
  config-level `template_env` falses (`has_playwright`, `deps_script`,
  `needs_world_database`). `needs_world_database` becomes a field of
  `grading` for the SQL grader, defaulting to off inside that capability.
- The three cs425 configs drop their `templates:` lists. They set no
  `capabilities_dir`. `documents` is the whole generation.

## Tests

Loader tests, beside the existing generate tests:

- shape: `services: [web, mysql]` plus `docs` activates `web`, `mysql`, and
  `documents`, and does not activate `grading` or `vscode`
- explicit omission: a package with web and MySQL and no `vscode` key has no
  `.vscode/` entries in its manifest; the same package with `vscode: true`
  does
- engine narrowing: a course fixture whose `fields.engine` lists
  `playwright` and `mysql` writes the Playwright sources for
  `playwright` and the SQL runner for `mysql`; any other value fails
  validation before render
- inactive shared capability: a config whose packages never set `vscode`
  still loads a `vscode` capability and generates cleanly
- fragments: the base Makefile for a document package contains
  `include .stencil/documents.mk` and does not contain `grading.mk`; a
  grading package contains both; the grading fragment file is absent from
  the document package
- Compose: `make help` prints `docker compose --project-directory . -f`
  for each compose file the package generated, and that command is the
  same one `STENCIL_COMPOSE` runs
- a SQL grading package's manifest has no `test.sh`
- optional absence: nginx for a web package with no `grading` block renders
  without the view-source include; nginx for `engine: playwright` contains
  it
- shape `when` that names an underived key fails validation; duplicate
  capability ids fail; a `when` that does not parse fails
- a config that lists `templates:` and sets no `capabilities_dir` still
  renders those entries, and a document package with no `templates:` list
  gets the base Makefile plus the `.stencil/` document files
- `tests/test_template_contract.py` still fails when a `documents` partial
  reads a key the context does not provide

`cs234` and `cs425` adoption is checked by `stencil gen` for one document
package, one assignment package (`vscode`, web, mysql), and one grading
package (Playwright, no `.vscode/`), each compared against the files that
package's config produces today, minus the VS Code files on grading
packages.
