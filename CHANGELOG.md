# Changelog

Notable changes to stencil, newest first.

This file starts at 0.3.0 and is not retroactive. The 170-odd commits before that
tag are the history of the tool arriving at this shape; git is the record of them,
and the closed epics in `.beads/issues.jsonl` are the readable index.

How the version gets bumped is written down in
[AGENTS.md](AGENTS.md#cutting-a-release), not here.

## 0.39.0

- **Every compose invocation the generated Makefile issues now names its compose file
  explicitly, so a `docker-compose.override.yml` or `.env` `COMPOSE_FILE` sitting in the
  package directory is no longer auto-discovered and merged by compose's own resolution**
  (`stn-qli`). `COMPOSE_FILES ?= docker-compose.yml` holds a bare list of files; `STENCIL_COMPOSE = $(DC) $(addprefix -f ,$(COMPOSE_FILES))` adds the `-f` at the point of use, and every
  compose invocation across the three Makefile partials — `ensure_image`'s pull, on both the
  POSIX and the Windows branch — goes through it instead of the bare `$(DC)`.

  **Measured**, 2026-09-12, against a real generated package (Docker Compose v5.3.1): before the
  fix, a planted `docker-compose.override.yml` rewrote `format-md`'s image and entrypoint and
  `make format-md` ran the planted one; a `.env` containing `COMPOSE_FILE=evil.yml` made compose
  load `evil.yml` alone. After the fix both are inert, and naming the override explicitly —
  `make doc COMPOSE_FILES="docker-compose.yml docker-compose.override.yml"` — still merges it. A
  relative `-f docker-compose.yml` from the package directory was checked against the unpinned
  baseline too, and leaves the compose project name and every resolved volume source identical
  to auto-discovery — the pin moves nothing else.

  Say precisely what this closes, and no more: compose no longer merges compose files it
  *discovered*, rather than compose files that were *named*. It does not mean a file merely
  appearing in the package directory can no longer change what `make` runs — GNU Make itself
  prefers a `GNUmakefile` over the generated `Makefile`, and reads a `MAKEFILES`-named file
  before either, and nothing in this Makefile's own contents can stop that. Measured
  separately, reproduced independently, and filed rather than folded in here because it needs
  its own decision: `stn-bux`, open.

  Two more measurements narrow the claim rather than widen it. `COMPOSE_PROJECT_NAME` in `.env`
  is *still* honoured under the pin (measured: `name: hijacked`) — low-impact, because every
  service already runs `--rm`, none declares a named volume, and the image each service builds
  is named explicitly rather than resolved by project name. `DOCKER_HOST`, `COMPOSE_ENV_FILES`
  and `DOCKER_DEFAULT_PLATFORM` are not honoured from `.env` under the pin; `COMPOSE_PROFILES`
  is moot because no generated service declares a profile. And the override never "restored
  root": no generated service sets `user:` at all — all six run as `user=None`, and `format-md`
  already ran as uid 0 before this change. What an override took, and what naming it back in
  still takes, is `stn-20h`'s `--no-config` hardening, the vendored lockfile installed with
  `npm ci`, and the digest pin above — not a user boundary that was never there.

  The guarantee is measured on Compose v2+ — `docker compose`, and `podman compose` where it
  delegates to the same provider. The separate Python `podman-compose` implementation was not
  measured; treat the guarantee there as inferred, not verified.

  Two parse-time guards protect the pin itself, both measured on GNU Make 3.81. `?=` alone tests
  whether `COMPOSE_FILES` is *defined*, not whether it is non-empty: `make COMPOSE_FILES=`,
  `COMPOSE_FILES= make`, and `MAKEFLAGS='COMPOSE_FILES=' make` all leave `?=` satisfied and would
  otherwise silently produce a working, unpinned `docker compose  run`; an empty value is now
  refused outright, with `COMPOSE_FILES must name at least one compose file, e.g. COMPOSE_FILES=docker-compose.yml`. An exported `COMPOSE_FILES` beats `?=` too, and would
  silently drop the pin for every package built in that shell; it is refused as well, with
  `COMPOSE_FILES from the environment is ignored; pass it on the make command line` — named
  honestly in the comment above it as honest-mistake protection rather than a boundary, since
  `MAKEFLAGS='COMPOSE_FILES=evil.yml' make` reports the variable's origin as "command line" and
  slips straight past it.

  The variable driving every call site is `STENCIL_COMPOSE`, not `COMPOSE` — a consuming
  composition defining its own `COMPOSE` would win by make's last-assignment-wins rule and
  silently unpin every call site, and no guard can catch a wrong-but-non-empty value the way the
  two above catch an empty one. `Makefile-doc.j2` and the `has_package_sources` arm of
  `Makefile-pkg.j2` each gained a parse-time guard naming `STENCIL_COMPOSE` explicitly, because
  AGENTS.md documents composing these partials a la carte: a composition that includes either
  without `Makefile-base.j2` first now fails with `Makefile-doc needs STENCIL_COMPOSE: include Makefile-base.j2 before this include, or define it yourself`, instead of the bare `make: run: No such file or directory` it produced before. stencil has no pinning consumers today, so this
  reaches every consumer on their next `stencil gen` with nothing in between.

  **`DC` may now name a compose implementation only, and a flag inside it is refused.**
  Found by the adversarial review of this change and reproduced: `STENCIL_COMPOSE` puts
  `$(DC)` *first*, so `DC="docker compose -f evil.yml"` expands to
  `docker compose -f evil.yml -f docker-compose.yml run …` — and compose *merges* the two
  rather than letting the pin override, so `config` still reports the pinned image while a
  `privileged: true` and a `/:/hostfs` mount out of `evil.yml` land intact. That is this
  same hole reopened through the sibling variable. `ifneq ($(filter -%,$(DC)),)` refuses any
  word beginning with a dash, which is deliberately broader than an `-f`/`--file` denylist:
  `--project-directory` moves the base every relative volume source resolves against and
  `--env-file` re-points the variable file, and neither is a compose file by name. All four
  spellings `pipeline.compose_command()` falls through — `docker compose`, `podman compose`,
  `docker-compose`, `podman-compose` — pass it, exported or on the command line, so
  `DC="podman compose"` is unaffected. Like the `COMPOSE_FILES` guards, this is
  honest-mistake protection rather than a boundary: `DC` names the command `make` runs, so
  anyone who can set it can already run anything. `CONTAINER` has the same env-settable
  shape but a different objective — what the image probe *executes* rather than which files
  compose *reads* — and is filed separately as `stn-3y8` with its reproduction.

- **pytest in a git worktree now tests that worktree** (`stn-2et`). A run
  started inside a worktree, using a venv whose `pip install -e .` points at
  another checkout, imported THAT checkout's `stencil` while running the
  worktree's tests — and said nothing. Measured: the console script imported
  `<main>/stencil/__init__.py` from a `<worktree>` whose own version was a
  minor ahead, and the same suite differed by two tests depending only on
  `PYTHONPATH`.

  `pythonpath = ["."]` in `[tool.pytest.ini_options]` fixes it by putting the
  rootdir on `sys.path` before `tests/conftest.py` is imported. Resolved
  relative to rootdir rather than the cwd, and it reaches xdist workers — both
  verified rather than assumed.

  Why it stayed hidden: `python -m pytest` was always right, because `-m`
  puts the cwd on `sys.path` first, so the obvious sanity check passed. And
  stn-12v's CLI guard passes either way — it derives `REPO_ROOT` from the
  imported module, so it pins the subprocess to whatever the in-process import
  chose. That is consistency, not correctness; under this bug both halves
  agreed on the wrong tree. Fixing the in-process import is what makes
  stn-12v's guarantee point somewhere useful.

  `tests/conftest.py` now refuses a run whose `stencil` belongs to a different
  checkout, naming both trees and the two ways out, because a setting can be
  deleted in a merge and every failure here is silent — the lesson this
  repository already paid for with a pre-push hook that failed open. It
  compares against the conftest's own checkout rather than `config.rootpath`,
  which would have refused the inner pytest runs `test_parallel_harness.py`
  and `test_tmp_footprint.py` legitimately make against a throwaway rootdir.
  AGENTS.md records what a contributor in a worktree should expect, including
  where the fix stops: `pythonpath` decides which `stencil/` is imported and
  nothing about what is installed, so a worktree that adds a dependency, a
  pytest plugin or an entry point still needs its own venv. That one fails as
  an honest `ModuleNotFoundError` rather than a silent wrong answer.

  The same bug existed one level down, and the guard is what found it: the
  inner pytest runs `test_parallel_harness.py` and `test_tmp_footprint.py`
  spawn get no ini file, so `pythonpath` never reached them and they resolved
  `stencil` through the interpreter's install — another checkout, under the
  borrowed venv the docs had just called safe. That surfaced as `UsageError`
  and exit 4 on two harness tests with nothing to do with this change, which
  was the refusal being right and the harness being wrong.
  `conftest.inner_pytest_env()` now appends this checkout to their
  `PYTHONPATH`. The regression test models the competing install as an
  appended `sys.meta_path` finder rather than a path entry, because that is
  what an editable install is — a first draft using `PYTHONPATH` made the
  competitor stronger than the real one and failed a correct fix, and a second
  draft forgot to remove the venv's own finder and passed against a build with
  the fix taken out.

  Two costs are accepted rather than left to be discovered. The guard has a
  loud door — `STENCIL_ALLOW_FOREIGN_STENCIL=1` proceeds and warns on every
  run, including under `-q`, since a guard with no way past it gets deleted
  whole and one that can be silenced is not a guard. And the repository root
  now precedes site-packages on `sys.path`, so a top-level `yaml.py` would
  become the one the suite imports; a new test fails if any git-tracked name
  at the root starts shadowing an installed module.

- **`format-md` installs only the lockfile stencil generated** (`stn-qge`). The
  service copied `format-package-lock.json` out of the mount — the consumer's
  own package directory — and ran `npm ci` from it. `npm ci` fetches whatever
  host each `resolved` names and checks `integrity` against a value in that same
  file, so a consumer-editable file decided which bytes became the prettier that
  then ran as uid 0 over that read-write mount. Neither `--ignore-scripts` nor
  0.39.0's own `--no-config` touches it: nothing has to run at install time,
  because the payload runs when prettier runs.

  **Measured**, on the pinned node image, with one `resolved` host changed in a
  generated package's lockfile and nothing else: npm requested that host. It now
  fails before npm asks, with a message saying the file is stencil's, that
  editing it has no supported effect, and that `stencil gen` restores it.

  The entrypoint carries the sha256 of the lockfile `stencil gen` wrote and
  checks the copy in `/tmp/fmt` — after the `cp`, before the install, so what
  was hashed is what npm reads rather than a file the host could still rewrite.
  The digest is derived from the vendored bytes at generation time, never
  written down, so re-vendoring moves the lockfile and its digest together and
  bumping a pin stays the same two steps it was.

  **What it proves, exactly.** A checksum is not a signature: it shows that two
  files in the package agree, and both are files whoever edited the lockfile
  could edit. It refuses every edit that touches only the lockfile — a script, a
  dependency bot, a bad merge, a half-finished hand edit — and it turns the
  collusive case into something visible: the digest in `docker-compose.yml`
  moves without stencil having been re-run.

  Nothing a consumer does legitimately changes that file, so no build that was
  working stops. If you had edited your package's copy — there was never a
  supported reason to — run `stencil gen` and the package goes back to the
  lockfile stencil ships.

- **A stray `npm install` in a package directory can no longer decide which
  puppeteer renders your handouts** (`stn-86y`). The generated
  `html-to-pdf.js` runs inside the browser image but lives in the mounted
  package directory, and it asked for its tools by bare specifier. Node
  resolves those starting from the requiring file, so
  `<package>/node_modules` outranked the image's pinned, lockfile-verified
  tree at `/opt/tools` -- which was reached only through `NODE_PATH`, Node's
  *last-resort* path.

  **Measured**, in the image built from the generated `Dockerfile.browser`,
  with a decoy planted at `<package>/node_modules`:

  ```
  puppeteer      /workspace/node_modules/puppeteer/index.js   0.0.0-decoy
  pdf-lib        /workspace/node_modules/pdf-lib/index.js     0.0.0-decoy
  ```

  Both, not just puppeteer -- and `pdf-lib` is the one that writes the PDF/UA
  role map and XMP metadata that `make check-pdf` exists to require, so a
  decoy there yields a finished-looking PDF missing exactly that, with
  nothing about the rendering looking wrong.

  `html-to-pdf.js` now resolves both through `module.createRequire` rooted at
  the image's install root, and refuses to run at all if resolution lands
  outside it. `make check-access` was never affected by this particular
  hijack: `pa11y` resolves its own dependencies from its own location inside
  the tools tree, not from the working directory.

  **What this does and does not close.** It closes an *incidental*
  `node_modules` -- someone, or some tool, once ran `npm install` in the
  package directory -- silently outranking the pinned tree. That is the real,
  common, traceless case. It does **not** close a hostile package directory,
  and cannot from inside one: `html-to-pdf.js` is itself in the mount, as are
  the `Makefile`, `docker-compose.yml` and the Lua filters.

  **If you have copied `html-to-pdf.js.j2` into your own `templates_dir`**,
  you keep the old resolution and get no warning -- `StrictUndefined` cannot
  tell you, because your copy reads none of these keys. Re-apply the change,
  or drop your override.

  **The fix reaches a package only when you run `stencil gen`.** `make pkg`
  has no `gen` prerequisite, so an existing package keeps running the
  `html-to-pdf.js` it was generated with.

- **The container tier now runs in parallel** (`stn-vda`). CI's integration job
  ran a single `pytest -v`; it now runs `pytest -v -n auto --dist loadfile`.
  `loadfile`, not xdist's default `load`: nine test files carry module- or
  file-scoped fixtures that each pay one container run to answer many
  assertions, and AGENTS.md now carries the full list and the reasoning.

  **Measured**, per container test on this branch against the pinned
  `pandoc/core:3.10.0.0` image: 0.30s docker-run container start and 0.56s
  pandoc, a 35%/65% split. `stencil gen` (`make_package`) is 0.031s;
  `install_fixtures` is 0.001s. The 0.56s decomposes by flag, cumulatively:
  pandoc's own process startup is 4ms; `--standalone` against pandoc's own
  template is 16ms; `--standalone --template=html-template.html` is 559ms;
  adding all six lua filters brings it to 565ms; adding `--citeproc` brings it
  to 579ms. The entire per-render cost is pandoc parsing the generated
  `html-template.html` — 5.3MB, almost all of it the inlined
  Bootstrap/highlight.js/Mermaid/webfont payload. The six lua filters and
  citeproc together cost about 20ms, roughly 2% of a render.

  That measurement refutes two of the ticket's three proposed fixes, plainly,
  so neither needs re-proposing from scratch. Widening fixture scope targets
  `stencil gen` at 31ms of an 870ms test — about 3.5% of the tier — while
  introducing shared mutable package directories across fifteen test files.
  Batching `test_dates.py`'s 102 builds into one container saves only the
  container starts (102 × 0.30s ≈ 31s), because each of the 102 still parses
  the same 5.3MB template, while turning a spreadable file into a serialized
  57s critical path.

  The single biggest cut available — generating test packages against a
  slimmed asset set, which is 65% of every container test — is not taken.
  `tests/test_assets.py`, `tests/test_fonts.py` and `tests/test_pins.py`
  assert on exactly that inlined payload; a lean variant would mean the
  container tier stops testing the artifact stencil actually ships.

  Locally, the container tier went from 689.63s to 191.00s — 989 tests passed
  either way, the same assertions against the same real containers — and the
  fast tier from 18.18s to 5.24s.

  On CI, which is the number that decides this: run 34678996838 on `8e17d65`
  measured the `pytest -v` step at 565s inside a 9m42s job; run 34683551394
  on this branch measured the same step at **352.95s** inside a **6m10s**
  job, 995 passed. That is **1.60x**, and it is worth saying plainly that the
  plan predicted 3.2-3.5x and was wrong.

  It was wrong about why the tier is slow, not about the arithmetic. The
  reasoning was that these tests are IO-bound on container startup, so four
  workers would overlap four waits. They are not: 65% of every container test
  is pandoc *parsing* the 5.3MB `html-template.html`, which is CPU and memory
  bandwidth. Measured on the CI run, the four workers were busy 348s, 327s,
  317s and 343s of a 351s wall clock — saturated, with idle tails of 0-31s,
  so the bin-packing `--dist loadfile` produced was close to ideal and is not
  where the missing speedup went. What the four workers spent was 1,335
  worker-seconds on work that takes 565s on one worker: the same unit of work
  costs **2.36x more** when four of them run at once on four vCPUs. Against a
  perfect-split floor of `565/4 ≈ 141s`, 353s is 2.50x over.

  So the honest statement is that parallelism recovers what contention
  leaves, and on this workload that is a little over half. 1.60x for a CI
  flag and two harness fixes is still worth having. It also sharpens the
  fourth cost this entry declines to take: the 5.3MB template is both the
  per-test cost *and* the reason four workers contend, so slimming it would
  pay twice. It is still not taken, for the reason above — `test_assets.py`,
  `test_fonts.py` and `test_pins.py` assert on exactly that payload.

  No assertion was deleted, weakened, skipped or merged, and the compose gate
  from #86 is untouched.

- **The shared-basetemp guard now actually fires** (`stn-6fs`). It never did.
  `pytest_configure` wrote `.pytest-run-owner` into the basetemp, and pytest's
  own `TempPathFactory.getbasetemp()` `rmtree()`s that directory on first use
  and recreates it — so a run deleted its own marker the moment any test
  asked for `tmp_path`, and the window in which the guard could fire was
  milliseconds. Dead, not racy: two concurrent runs on one `--basetemp` both
  passed.

  The test that was supposed to prove otherwise hand-writes the marker into a
  directory no pytest ever rotates, so it proved the marker is **read** while
  saying nothing about whether it is ever there to read.

  The fix claims the basetemp twice: the check stays in `pytest_configure`
  (refusing after the rotation would destroy the run being protected), and the
  write is repeated in `pytest_sessionstart`, after touching `getbasetemp()`
  to force the rotation while the run is still starting. Proven by two real
  pytest runs overlapping in time, the first already past a `tmp_path`.

  Two runs starting *simultaneously* are a second case, and the marker alone
  never covered it: checking for an owner and writing one are two steps, so
  both runs read an empty directory and both claimed it. Claiming is now
  atomic under a lock keyed on the resolved basetemp path, held only across
  that check-and-write and across the rotation — and living outside the
  basetemp, since a lock inside it would be deleted by the rotation it
  covers. Measured: four simultaneous starters leave exactly one survivor,
  15 runs out of 15; with the lock removed the same test fails 2 runs in 6.

  Forcing the rotation introduced a failure of its own, now closed.
  `getbasetemp()` does `rm_rf` and then `mkdir` with no `exist_ok`, so a
  directory recreated underneath it raised `FileExistsError` out of a session
  hook — an `INTERNALERROR` whose traceback never names a basetemp, which is
  worse than the corruption being guarded against. It retries once and then
  refuses in the guard's own words.

- **The three images the scaffolding pulls are pinned by manifest digest, not only by
  tag** (`stn-8vi`, closing the sibling gap `stn-5hv` left open). A registry tag is
  mutable — `docker.io/pandoc/core:3.10.0.0` can be repushed, and every rebuild after
  that silently gets different bytes under a name that says otherwise. `stn-5hv` fixed
  the layer above this — the npm tree the browser image installs is now fixed by a
  committed lockfile and verified by integrity hash — which left the images themselves
  as the remaining floating input.

  **Measured**, 2026-09-12: `node:24.20.0-alpine3.24` resolves to the OCI image index
  `sha256:e67514e5…`, covering `linux/amd64`, `linux/arm64/v8` and `linux/s390x`;
  `pandoc/core:3.10.0.0` resolves to the index `sha256:8d7467e8…`, covering
  `linux/amd64` and `linux/arm64`. `NODE_IMAGE` and `PANDOC_IMAGE` now carry
  `<tag>@sha256:<digest>`, keyed by the tag in the new
  `stencil/assets/image-digests.json` rather than written inline beside it — a
  registry resolves `name:tag@digest` **by the digest** and ignores the tag, so an
  inline pair that goes stale (tag bumped, digest not re-resolved) would silently keep
  building the old image under a name that no longer describes it. Keyed, that state
  cannot be expressed: `pipeline.pinned_image()` raises for a tag with no recorded
  digest, loudly and offline, the same way `npm ci` refuses a lockfile the manifest
  does not satisfy.

  `scripts/resolve_image_digests.py` writes the file, following
  `scripts/vendor_page_assets.py`'s shape — `urllib`, a maintainer runs it once with
  the network — but hardened well past
  that, because this script is the step that *establishes* trust rather than merely
  caching a CDN asset: what it writes is pinned permanently and rendered into a
  Makefile, a compose file and a `FROM` line. It refuses every redirect on the
  manifest and token requests (measured: Hub does not redirect either today, so this
  costs nothing), drops proxy inheritance, computes the digest locally rather than
  trusting the advertised `Docker-Content-Digest` header, and cross-checks the result
  against a real `docker pull`'s `RepoDigests` — by membership, not `[0]`, because
  podman records the arch-specific child digest there as well as the index digest and
  `[0]` compares against the wrong one under podman. All three tags resolve before one
  write, so a rate limit on the third cannot leave one fresh digest sitting beside two
  stale ones in a file that looks complete.

- **`verapdf/cli:v1.30.2` turned out not to be a manifest list at all.** The source
  ticket assumed all three images were multi-arch, the way node and pandoc are.
  Measured instead: veraPDF's tag resolves to a single
  `application/vnd.docker.distribution.manifest.v2+json`, `linux/amd64` only —
  `sha256:d5ee3296…` — with no index to resolve a per-architecture digest from. Its pin
  is an ordinary image digest, `image-digests.json` records the single-arch media type
  by name rather than treating "not an index" as an error, and a test checks for it
  explicitly so a future multi-arch veraPDF release is a deliberate edit rather than a
  silent pass. It already runs emulated on arm64 today; the digest makes that visible
  rather than causing it, and freezes it — a later multi-arch repush of the same tag
  would otherwise start running it native on arm64 with nothing in any diff to say so.

- **The generated `ensure_image` pull guard could not see a digest-pinned image**, so
  pinning the three above without this would have made every `make doc`, `make pdf`
  and `make format-md` pull on every build. Measured: `docker images -q <ref>` prints
  nothing for a reference that carries a digest, even when that exact image is present
  locally under it, while the bare-tag form of the same probe prints the id — the
  guard looked like it worked because some other tag happened to satisfy it, while
  compose pulled the pinned reference anyway. The guard now runs `docker image inspect <ref>`, which checks the reference actually named, digest included, and reports the
  image absent for a wrong digest — stricter than the old probe was ever able to be.
  Both the POSIX and the Windows branch changed; the old literal was one string shared
  by both, so a fix to one alone would have left every Windows consumer pulling on
  every build with a green suite.

  The runtime the probe names is derived rather than hardcoded now too: `CONTAINER = $(firstword $(subst -, ,$(DC)))` reads `docker` or `podman` out of whichever of the
  four `DC` spellings `pipeline.compose_command()` falls through to, because a
  podman-only host has always failed this probe outright — `docker images -q` doesn't
  merely miss the digest there, it fails to run at all.

- **The cost of pinning by digest is written down, not only accepted silently.** A
  digest pin gives a consumer three new ways to fail that a tag pin did not: registry
  garbage collection turns a repushed tag into `manifest unknown` instead of quietly
  different bytes; `docker save`/`load` loses `RepoDigests`, so an image that worked
  fine as a tag fails both `image inspect` and the compose pull after a save/load air
  gap; and `generate.py`'s `reject_derived` gives a consumer behind a mirror no
  supported override. The recovery path is the same for all three and is now in
  `AGENTS.md` next to the pin: a pull failing with `manifest unknown` means the digest
  was garbage-collected upstream — re-resolve with
  `python3 scripts/resolve_image_digests.py` and regenerate.

- **`format-md` no longer executes a consumer's prettier config** (`stn-20h`).
  The service installs prettier into `/tmp/fmt` precisely so npm resolves
  stencil's manifest and not the package's. That answered which *manifest*, and
  stopped one loader short: prettier's own config discovery was still rooted in
  the mount.

  **Measured**, running the generated service the way `make pkg` does, on the
  pinned node image with the pinned prettier: a `.prettierrc.cjs` in the package
  was evaluated as uid 0 and wrote to the read-write mount; and a
  `.prettierrc.json` — a file containing no JavaScript at all — named a
  `plugins` path that prettier then required out of the package's own
  `node_modules`, also as uid 0. The second is the one that matters, because
  "we only ship JSON" was never a defence. Both ran with the network up, on
  every build, and the build printed its usual success output afterwards.

  `--no-config` closes both. There is deliberately no allowlist of safe config
  formats: the dangerous file in the second case was the inert-looking one, and
  a `plugins` entry is available in every format prettier reads.

  **What this costs a consumer, and it is not nothing.** A package's own
  prettier settings stop applying — so do `.editorconfig`'s, including
  `end_of_line` and `indent_size`, which is the one most likely to surprise a
  Windows-authored repository whose markdown will come back LF. Plugins loaded
  through a config stop loading. Nothing is printed when this happens and
  `--write` means the reformat is already on disk, so run `make format-md` on a
  clean tree first and commit the result as its own commit, before anything
  else. `.prettierignore` and `.gitignore` still apply — they choose which files
  are formatted rather than what code runs — and they remain the way to keep the
  formatter away from a directory. If you also run prettier yourself, give it
  the same flags or exclude the package, or the two will take turns rewriting
  each other's output. [AUTHORING.md](AUTHORING.md#fenced-divs-and-prettier)
  says all of this to the person writing the markdown.

## 0.38.0

- **Two test runs at once no longer corrupt each other** (`stn-zim`). The
  neighbouring problem to the retention one, and it arrives through the same
  door: the advice for a full disk is "pass `--basetemp` somewhere with
  room", and doing that from two worktrees is what turned the first of these
  up.

  **Measured:** two suites given the same `--basetemp` delete each other's
  fixture trees, because pytest rotates that directory at startup. It read as
  `22 failed, 789 passed, 79 errors`, almost all `FileNotFoundError` under
  the shared path — a catastrophic-looking regression rather than two runs
  fighting, and it cost a re-run to tell the difference. A run now marks its
  basetemp and refuses to start on one a *live* pytest owns. A stale marker
  from a crashed run does not block, because a guard that refuses forever
  teaches people to delete the guard rather than the file.

  The browser image tag was the same hazard, reasoned from the code rather
  than observed: `pipeline`'s browser helpers built and ran one fixed tag, so
  a second run could rebuild the image out from under a first still using it.
  Each run gets its own tag now, overridable with
  `$STENCIL_BROWSER_IMAGE_TAG` for a CI job that wants to build once and
  reuse. Nothing in a generated package reads that tag — the compose file
  builds its own image — so this is a test-harness knob and cannot affect a
  consumer's build.

  A per-run tag could have meant rebuilding the image every run, which would
  have been a worse trade than the problem. Measured rather than assumed:
  **0.5s for the existing tag, 0.6s for a brand-new one** — podman's layer
  cache keys on the Dockerfile and context, not on the name, so the tag is
  free.

  The four helpers took the tag as a **default argument**, which bound it at
  import and made an environment override look like it worked while doing
  nothing. They resolve at call time now, and a test asserts the signatures
  stay that way — the same late-binding mistake was made and caught in
  `conftest.py`'s low-space threshold one ticket earlier.

- **The test tier no longer keeps every passing test's output**
  (`stn-0ot`, closing `stn-7im`). `tmp_path_retention_policy = "failed"`:
  measured, each generated package is ~10.75MB of inlined assets, the unit
  tier retained 823MB across 310 directories per run, and pytest kept three
  runs — which exhausted a 7.7GB tmpfs and produced `51 failed, 540 passed, 157 errors` that named the disk nowhere a reader would connect to the cause.
  A failing test still keeps its tree, which is the one you want to look at;
  `-o tmp_path_retention_policy=all` restores the old behaviour for a
  debugging session.

  Measured after the change, on the same full container-tier run: the
  basetemp tree went from **4.0GB to 117MB**, a 34x reduction, with 872
  tests passing either way.

- **A run says where its temp tree is and how much room it has**, in the
  header of every run, and a failing test is annotated when space is low — at
  the moment of failure, because with the new policy a run that exhausted the
  disk mid-way looks healthy by summary time.

  Nothing in a generated package changed; this is the suite only.

- **The CLI tests run the code under test** (`stn-12v`). `tests/test_cli.py`
  drives stencil as a subprocess, which imported whatever `pip install -e`
  put on the path — one checkout. Run from a git worktree, that meant the
  direct-import tests exercised the branch while every subprocess test
  exercised `main`, with nothing saying so. The dangerous direction is
  silent: a change that **breaks** the CLI passes in a worktree, because the
  subprocess never sees it — and AGENTS.md tells every agent to work in a
  worktree, so that is the default arrangement rather than an unusual one.
  It was found the friendly way round, by a correct fix that appeared not to
  work. `run_cli` sets `PYTHONPATH` to the tree under test, and a guard asks
  the subprocess where it imported stencil from.

- **A global option before the subcommand is honoured** (`stn-w4v`). `--config`
  and `--dry-run` are declared twice — once on the top-level parser and again
  on every subparser, because both spellings are documented — and the
  subparser's *default* overwrote the value the main parser had already
  stored. So the documented order silently read the wrong file:

  ```
  stencil --config other.yaml list   # other.yaml ignored, .config.yaml used
  stencil list --config other.yaml   # works
  ```

  and generate.py's own module docstring gives the broken one,
  `stencil [--config <path>] gen [--all] [pkg]`. The `--dry-run` half is the
  worse of the two: a preview that silently was not one. The subparser copies
  now default to `argparse.SUPPRESS`, so an absent option leaves the main
  parser's value alone.

- **A package that fails to generate now fails the command** (`stn-zfc`). Two
  routes survived `stn-k73`, which closed the config-error path only.
  `render_templates` prints its message and re-raises, and nothing caught it,
  so a `StrictUndefined` error — kept deliberately fatal so a renamed context
  key cannot render as the empty string — arrived as a raw traceback naming
  no package. And `generate_package` returns `None` for a package with no
  templates, which the `--all` loop discarded, so the loop carried on and the
  command exited **0**. Failures are collected across every package and
  reported together, the way config problems already are.

- **A damaged stencil install is no longer reported as a broken config**
  (`stn-hwo`). `pipeline.read_lockfile` raised `ValueError` for a vendored
  lockfile that lost its trailing-newline shape, and `ValueError` is the
  channel `package_contexts` collects *config* problems on — so a fault whose
  fix is `python3 scripts/vendor_npm_locks.py` was reported under a heading
  saying the config has a problem, with a trailer telling the reader to fix it
  and delete a directory by hand, in a file a consumer may not be able to edit
  at all. It raises `pipeline.VendoredAssetError` now, deliberately **not** a
  `ValueError` subclass, and `main` reports it as what it is:

  ```
  Error: browser-package-lock.json must end with exactly one newline ...
         Re-vendor it: python3 scripts/vendor_npm_locks.py

  This is stencil's own installation, not your config. Nothing was
  generated, removed or written.
  ```

- **A pathological config scalar cannot bury the report that names it**
  (`stn-oty`). The dangerous half of that ticket was already closed — measured:
  `_safe` renders an ANSI CSI sequence in a package id as a literal `\x1b`,
  and an embedded newline cannot forge an extra bullet in the aggregated list.
  What was missing was a bound, since `yaml.safe_load` returns a scalar of any
  size. Problem lines are capped at 400 characters and say how much they
  dropped; a real message runs to a couple of hundred, so nothing ordinary
  changes.

- **Every configured path now goes through the same check, and four of them
  did not before.** `check_config_path` already encoded what a configured
  path may look like — no shell metacharacter, no whitespace, no `~`, not
  absolute, no `..` — and `stn-k73` put it in front of `docs`, `slides`,
  `package_sources` and `pre_build`. These four were left:

  - **`dir`** (`stn-vhm`) prefixes every entry `get_generated_files` returns,
    which is every line of the managed `.gitignore` section *and* every path
    `clean` resolves and deletes. `dir: ../..` deleted outside the output
    base.
  - **A template's `dest`** (`stn-c25`) was joined onto the output directory
    verbatim, so `dest: ../../shared/Makefile` wrote outside the package on
    `gen` and `clean` **removed** that file. The escape was symmetric across
    `gen`, `clean` and the `.gitignore` section, which is what makes refusing
    it better than containing it on one side. A nested `dest` is still fine —
    `.vscode/settings.json` is in the config's own documentation.
  - **`brand`** (`stn-ttg`) was the only configured path that gets *opened*.
    `copy_brand_image` resolved it and handed it to `shutil.copyfile`, which
    follows a symlink — so a `logo.png` pointing at any readable file copied
    that file's **content** into a generated folder that AGENTS.md describes
    as routinely handed to someone as a project of their own. `file://` was
    stripped before any of it, so the scheme was not a defence, and an
    absolute value made `(config_dir / relative).resolve()` the absolute path.
  - **C0 control characters** (`stn-isr`) were in neither the metacharacter
    class nor the whitespace check. The rest of the C0 range and DEL are
    refused now; `\n` keeps its metacharacter message, and tab and carriage
    return keep the whitespace one, because "Make would split it into two
    arguments" says more than "that is a control character" does.

  None of these is a privilege boundary, and it is worth repeating why:
  `pre_build.run` is arbitrary execution by design, so whoever writes
  `.config.yaml` can already run anything. What they close is a filename
  quietly doing something other than naming a file. The `brand` one earns a
  little more than its severity suggests because `stencil gen --dry-run` over
  a pull request's config executes nothing today — so it was the one read
  available to a config nobody had run.

- **A symlinked `brand` is copied under the name the config gives it**
  (`stn-8wt`), not the name of the file the symlink resolves to.
  `copy_brand_image` named the destination from the resolved path while
  `get_generated_files` named it from the config string — one rule with two
  spellings, diverging on exactly the arrangement a course repository uses, a
  stable `logo.svg` pointing at a dated asset:

  ```
  ln -s img/siu-logo-2024.svg logo.svg
  stencil gen --all   ->  out/demo/siu-logo-2024.svg
  get_generated_files ->  ['demo/logo.svg']
  stencil clean --all ->  siu-logo-2024.svg SURVIVES
  ```

  So the copied logo sat outside the managed `.gitignore` section and outside
  what `clean` removes — git tracking a generated file, which is the failure
  `stn-k73` exists to prevent, arriving by a different route. The symlink is
  still followed for content; only the name changed.

- **`stn-2l6` is closed as already fixed** rather than reopened: brand
  validation reached the aggregating pre-flight with `stn-k73`, and
  `brand_problem` is called there before anything is written.

- **A link in front matter is legible on a deck's title slide, and still
  looks like a link** (`stn-c0b`). Two halves, because one alone would not
  have done it.

  The light palette never mapped Bootstrap's link token — the dark block has
  since it was written — so a light-theme link was the one colour on the page
  this project did not choose: Bootstrap's own `#0d6efd`, measuring **4.50:1**
  on white. That passes AA by rounding, on the easiest surface there is.
  `--accent` is 9.85:1 on the same surface and is what every other themed
  element already uses.

  Mapping it is not enough, though, and that is the interesting half: a
  themed light link **is** `--accent`, which is the same colour as
  `--deck-accent-from` — 1.00:1 against the fill it would sit on. Any link
  colour good on a pale prose surface is bad on a dark accent fill, exactly
  the bind `stn-7i8` found for inline code. So the title slide scopes it the
  same way, and the inherited ink measures 9.85:1 and 6.32:1 across the
  gradient against `#0d6efd`'s 2.19:1 and 1.40:1.

  The underline is not decoration. Inheriting the surrounding ink is precisely
  what removes the colour difference that marked the link as a link, and
  WCAG **1.4.1** is a separate criterion from 1.4.3 — fixing contrast by
  deleting the only cue would trade one failure for another.

- **A backticked front-matter title no longer puts literal markup in the
  browser tab** (`stn-myk`). Pandoc renders `$title$` as inline markdown,
  which is what makes a backticked title render as inline code on the page.
  The same variable is interpolated into `<title>`, where an element has
  nowhere to go — so pandoc escaped it and the tab, the bookmark and the
  PDF's document title read literally `The <code>foo</code> protocol`.
  `frontmatter-filter.lua` now stringifies `title`, `program`, `section` and
  `term` alongside the other derived keys, and the head partial reads the
  plain-text twin. Both templates share that partial, so it is one fix rather
  than two — asserted rather than assumed, since the ticket flagged the
  document side as needing checking.

- **Inline code on an accent fill takes the fill's own ink, so a backticked
  front-matter title is legible on a deck's title slide** (`stn-7i8`). Every
  field the title slide renders is parsed as inline markdown — measured, all
  seven of `title`, `subtitle`, `brand`, `program`, `section`, `term` and
  `author` — so a title like `` "Flow, Limits, and `WIP` Specifications" ``
  puts a `<code>` on the accent gradient. It took `--code-inline`, which is
  ink meant for pale prose surfaces: **1.47:1** against `--deck-accent-from`
  and **1.06:1** against `--deck-accent-to`, in both themes and in print.
  A scoped `.slide--title code { color: inherit }` hands it the `--on-accent`
  ink the slide already carries instead, which measures 9.85:1 and 6.32:1,
  and 9.85:1 on `--print-deck-bg`.

  No value of `--code-inline` could have fixed this, which is why it is a
  scoped rule rather than another palette change: the token has to work on
  pale prose surfaces *and* on a dark fill, and those pull in opposite
  directions. `stn-1y7` moved it from #c7254e to #b01f45 to fix a real
  failure on a table header, and in doing so took the title slide from
  1.78:1 to 1.47:1. `tests/test_theme.py` now asserts the token **fails**
  here, so the next person to try tuning the palette reds a test that
  explains why.

- **A matching rule for inline code in the tab strip**, which is defence in
  depth rather than a live fix, and the comment says so. The vendored
  Bootstrap carries `a>code{color:inherit}` at (0,0,2), which already beats
  its own `code{color:var(--bs-code-color)}` at (0,0,1), and
  `_page-scripts.html.j2` adds `.nav-link` to the author's own `<a>` rather
  than rebuilding it — so a `code` written as a direct child of a tab link is
  legible today. The new rule covers the nested case (`<a><strong><code>`)
  and stops the pairing resting on a reboot rule inside a vendored blob that
  a Bootstrap bump could drop.

- **The deck fixture now carries a backticked title and subtitle**, so a
  rendered page in the suite actually produces the pairing. What that
  measured is worth recording: **`make check-access` cannot see this defect.**
  With the rule reverted, pa11y passed all eight page/theme combinations over
  a deck proven to contain `<code>WIP</code>` in `.deck-title`. `.slide--title`
  is painted with `background: linear-gradient(...)`, whose shorthand resets
  `background-color` to transparent, so the checker finds no colour to
  composite against and skips the element rather than failing it — the same
  blind spot `_page-style.css.j2` already records for the deck toolbar. The
  unit measurement in `tests/test_theme.py` is therefore the whole guard.

- **A guard keeps developer home paths out of the published issue export**
  (`stn-zcw`). `.beads/issues.jsonl` is committed and this repository is
  public, and issue notes are written by agents that paste absolute paths — so
  the export had been accumulating
  `/Users/<user>/…/.claude/worktrees/<branch>/…` strings. Measured before
  fixing: three real ones, no credentials or tokens, and the only identity
  involved a username `git log` already carries. Hygiene rather than an
  incident, which is exactly why it wanted a guard: the volume grows with
  every note and nobody re-runs the check by hand.

  The three were redacted **in the database** and re-exported, never by
  editing the JSONL — that desyncs it from Dolt and trips the pre-push drift
  guard, which is the whole reason that guard exists. Two of them needed
  `bd import --allow-stale`, because their notes fields are ~300KB and a
  single command-line argument on Linux caps at 128KB.

  `tests/test_export_hygiene.py` also refuses credential-shaped strings, and
  carries its own false-positive case: the tracker records leak-*detection*
  snippets that build `'/Users/' + U`, so a plain search for `/Users/` reports
  every one of them as a leak.

## 0.37.0

- **A consumer's line after `{% include 'Makefile-pkg.j2' %}` is its own line
  again, so a composed `pkg` target runs its linters.** cs234's own
  `Makefile.j2` includes the partial and follows it with
  `pkg: fix lint lint-sql`, the rule that makes `make pkg` fix and lint the
  HTML, PHP and SQL before it archives. The partial ended in the `clean-pkg`
  recipe, whose last token was a `{% endfor %}` — and the Jinja environment's
  `trim_blocks` strips the newline after a block tag, so the include ended
  mid-line and the consumer's rule rendered as the tail of `rm -f ...`. All 15
  of the course's assignment Makefiles carried `... -*.pdf pkg: fix lint lint-sql` on the rm line; `make pkg` zipped without linting anything, and
  nothing said so because a `pkg:` rule with no prerequisites is a valid
  Makefile. The bundled `Makefile.j2` never showed it: it happens to leave a
  blank line after the include, which is the newline the partial was missing.

  The partial now gathers the products first and emits them with the one
  `{{ }}` expression on its last line, which keeps its newline. The bundled
  output changes by one trailing space. The guard is in
  `tests/test_template_contract.py`: every shared partial's rendered output
  must end with a newline, for a zip package and a doc package, plus the
  consumer's case end to end — a composition that writes `pkg: fix lint` after
  the include gets that line back as a rule. A partial ending with a newline is
  part of the interface, alongside the keys it reads; AGENTS.md says so now.

## 0.36.0

- **A zip package's `pkg` target archives with `tar` on Windows, so a hidden
  `.git` reaches the submission.** `Compress-Archive` cannot put one there.
  `git init` on Windows sets the real `FILE_ATTRIBUTE_HIDDEN` bit on `.git`
  — git's `core.hideDotFiles` defaults to `dotGitOnly`, so `.git` gets the
  attribute and no other dot-name does — and `Compress-Archive` expands a
  directory handed to `-Path` with `Get-ChildItem` and no `-Force`, which
  drops every hidden entry silently: not in the archive, and not mentioned
  under `-Verbose` either. No parameter turns it off; it is a long-standing
  limitation of `Microsoft.PowerShell.Archive` 1.x, which is what ships with
  both PS 5.1 and PS 7.x. So a student who ran `git init` inside the packaged
  directory submitted an archive with no repository in it, while the same
  layout on Linux or macOS submitted a complete one, because `zip -r` has no
  notion of hidden. For a course that grades the repository, the Windows half
  of the class was silently handing in nothing gradeable. Windows now runs
  `tar`, the bsdtar shipped as `tar.exe` since Windows 10 1803, which walks
  the tree as it is.

  The flag is `--format zip` rather than `-a`, and that is the load-bearing
  half. `-a` infers format and compression from the archive's suffix, and
  both of the ways it gets that wrong are quiet. `$(OS)` is still
  `Windows_NT` inside Git Bash and MSYS2, but `tar` there is GNU tar, whose
  `-a` does not know `.zip`: measured on GNU tar 1.35, `tar -a -cf out.zip dir` exits 0 and writes a POSIX tar archive under the `.zip` name.
  `--format zip` is `Invalid archive format` on that tar, exit 2, so make
  stops rather than shipping a mislabelled archive. And `package_name` is
  only required to *exist* for a zip package, never to end in `.zip` —
  bsdtar 3.8.3 given `-a --format zip -cf d.tar.gz` writes a GZIP-compressed
  zip, where the explicit format alone writes a plain zip whatever the
  archive is called. That is also what the `zip -r` branch already did, so
  the two platforms now agree on the name-independent contract as well as on
  the hidden files. `stn-m2h`.

  The `pkg_empty`/`pkg_space`/`pkg_comma` helpers left with the cmdlet that
  needed them: `Compress-Archive -Path` took a comma-separated list, and
  `tar` takes an ordinary argument list.

- **A zip package's `PKG` now has a default, so the bundled templates alone
  produce a working `pkg`.** Nothing in stencil's own templates defined it —
  only a consuming project's override of `Makefile.j2` did, which is why no
  course noticed — so a package generated from the bundled set had an empty
  `$(PKG)`: `clean-pkg` removed nothing, and the archiver was handed no name
  to write to. It is `PKG ?= <package_name>`, with `?=` rather than `=` so a
  composition that defines `PKG` before including the partial keeps its own
  value. Found while verifying the `tar` change end-to-end: an empty name is
  a refusal from `Compress-Archive`, which validates `-DestinationPath`
  against the empty string, but `tar -cf ""` writes the archive to stdout.

## 0.35.0

- **A config mistake now fails the command instead of quietly dropping the
  package it appears in.** `show_download_default` and a dozen other checks
  raise on a bad value, and two call sites — the `when:` key validator and the
  builder that feeds `stencil install` and `stencil clean` — caught that and
  moved on. So a quoted `show_download: "no"` did not fail anything: it
  removed that package from the managed `.gitignore` section, from what
  `clean` removed, and from a `gen --all` that still exited 0, and nothing
  said so. The section then went stale silently, which is the worst way for a
  generated file to be wrong. One aggregating pre-flight now reads every
  package before anything is written, and reports **every** problem it finds
  in one message rather than the first — fixing a config one error per run,
  when they were all visible on the first pass, is a bad trade for an author
  with a dozen packages.

  The fail-open was never specific to `show_download`; that key only widened
  the ways to trip it. Everything those paths could hit was swallowed too,
  including `check_config_path`'s refusals — the guard that stops a configured
  filename acting like a command in a generated Make recipe. Type mistakes
  (`docs: 7`, a `packages:` written as a list) were not swallowed but were not
  caught either, and reached the terminal as a Python traceback; they are
  reported now as well.

  Brand validation joins the same pre-flight, which is what removes the last
  raw traceback: a `brand` pointing at a file that does not exist used to
  raise *after* the package had been half-generated. Whether the logo file
  exists is checked by `gen` alone, because only `gen` copies it — the
  `.gitignore` entry and the clean list are both derived from the brand
  string, so a missing file cannot make either wrong, and checking for it
  everywhere would take `clean` away for no benefit. A missing `brand-alt`
  needs no filesystem and is reported by every command.

  Two consequences worth knowing before you meet them. A mistake anywhere
  fails every command, including package-scoped ones: `stencil gen hs1`
  refuses while `hs9` is broken, which is how `template_env` and `when:`
  mistakes have always behaved and is now the rule for the rest of the config
  rather than there being two kinds. And `clean` refuses too — most wanted
  exactly when the config has drifted and generated files are still on disk.
  Refusing is still right, because running a deletion pass from a config
  stencil cannot read is worse than not running one, so the message says the
  way out: fix the config, or remove the generated directory by hand.
  `stn-k73`.

- **Every generated page now carries a self-download button, on by default.** A
  document gets it beside the theme control; a deck gets it in the toolbar,
  between the theme group and `Present`. `show_download: false` in a
  document's front matter turns it off. The default is *true* rather than
  the polarity every other switch in this repository uses — `show_date`
  withholds by default — because the thing being defaulted on is different in
  kind: a generated page is already fully self-contained, assets inlined at
  `stencil gen` and images base64'd by `embed-images.lua`, so there is
  nothing to gain by hiding a control that just saves the page a reader
  already has. `hidden-filter.lua` deletes withheld content from the
  document before the HTML writer ever runs, so the button cannot expose
  anything a reader's own Ctrl+S could not already reach.

- **The default is configurable per package, and it ships here rather than
  waiting.** A package or a whole config can set `show_download: false` once
  in `.config.yaml`, exactly where `brand` and `lang` already resolve
  package-then-config-then-built-in, and a document's own front matter still
  overrides that setting in *either* direction. `stn-cqv`, filed to defer
  this half of the feature, is closed as superseded — the first course to
  ask deserved to find it already written down rather than filing the issue
  itself.

  The knob deliberately did **not** go into `docker-compose-html.yml.j2` as
  a `--metadata show_download=false`. Pandoc metadata supplied on the
  command line outranks a document's own front matter, which would have made
  the package default impossible to override per document — exactly
  backwards from what a course needs when one handout should behave
  differently from the rest. The default is baked into the generated Lua
  filter instead, as `CONFIG_SHOW_DOWNLOAD`, so `truthy()` sees it as just
  another fallback and front matter keeps the final word.

  Coercing it took more than reusing `show_date`'s table: pandoc's actual
  boolean resolution was measured directly against the pinned pandoc rather
  than assumed, and most spellings — `true`/`false`/`yes`/`no`/`on`/`off`, any
  case — already arrive as real booleans. Only `0` and `none` arrive as
  strings, and a blank value, `null` and `~` all collapse into the same
  indistinguishable empty string. That last case is the one a default-true
  key cannot get from the existing table unchanged: the false-ish table maps
  blank to "off", which is correct when absent already means off, and
  backwards when absent is supposed to mean on. `truthy()` gained an
  optional `default` argument so a blank value falls back to *that* rather
  than to `false`, and every existing call site that passes no default is
  unchanged.

- **The downloaded bytes are the parsed document, not a serialization of the
  live DOM.** By the time a reader can click the button, `highlight.js` has
  rewritten every code block into spans, Mermaid has replaced `<pre>` with
  rendered SVG, and the tab builder has moved content into panes it built —
  transformations a naive `outerHTML` capture cannot undo by removing nodes,
  only by never having recorded them. The button's partial is included
  between `_theme-toggle.html.j2` and `_page-scripts.html.j2` so it runs at
  parse time, before any of that happens, and captures what it needs then.

  `<head>` is deliberately **not** captured as markup. Holding a second copy
  of it would cost a measured 1.44 MB of inlined base64 fonts and CSS on every
  page, for the life of the tab. (The raw assets sum to about 2.4 MB; a
  rendered page's `<head>` holds less because 0.31.0's `merge_duplicate_faces`
  collapses the duplicated font blocks first.)

  What replaced it went through one wrong answer first, and the wrong answer
  is worth recording because it looked right. Capturing
  `document.head.childNodes.length` and trimming the clone's head back to that
  count reverts an *append* for the price of one integer — and every head
  mutation on this page was believed to be one. It is not: the vendored
  Mermaid bundle **prepends** its Cytoscape stylesheet,
  `a.insertBefore(h, a.children[0])`, ahead of even the charset meta. Trimming
  the tail therefore removes the wrong node. On these templates the node it
  removed was the whitespace before `</head>`, so the damage was a lost
  newline and a retained stylesheet — invisible, and the entire margin was one
  text node. On a consumer whose head ends `</style></head>` it would have
  been the page's own inlined stylesheet, and the downloaded file would open
  unstyled with no exception and no failing test.

  So both `<head>` and `<body>` are reverted by marking instead: every element
  child is stamped with `data-stencil-pristine` at parse time, the clone keeps
  the marked ones — plus `<script>`, because the page's own trailing scripts
  are not in the DOM yet when the stamping happens — and the markers are
  stripped before serializing. A marker does not care what order anything
  arrives in, and it does not care what a consumer calls their wrapper, which
  the previous body rule (a whitelist of *this* repository's class names) did.

  `.container`, by contrast, **is** captured as a string rather than a
  detached clone, and that half is the less obvious one: Chrome live-loads
  `<img>` elements even inside a detached tree, so cloning an image-heavy
  container would decode every base64 image on the page a second time for no
  reason. A string capture defers that work to the moment it is actually
  needed — assigned back into `outerHTML` on click — rather than paying it on
  every page load.

- **The documented reason `show_date` needs coercing was wrong, and is now
  measured rather than asserted** (`stn-38o`). `frontmatter-filter.lua`'s
  header comment, `AUTHORING.md` and two test docstrings all said pandoc reads
  YAML 1.2, where `true` and `false` are the only booleans, so `show_date: no`
  reaches the template as the *string* `"no"`. Measured against the pinned
  pandoc, it does not: `no` arrives as a real boolean. The obvious explanation
  — documentation that drifted when the pandoc pin moved under it — is not
  what happened: `PANDOC_IMAGE` was pinned to the image it still names the day
  *before* `frontmatter-filter.lua` was written, and has not moved since. The
  comment was wrong when it was written, and stayed wrong because nothing
  could fail.

  Nothing was broken and nothing changes. `truthy()` checks the boolean branch
  before consulting its table of false-ish words, so every spelling resolved
  correctly the whole time — which is exactly why the claim survived: no test
  could fail.

  The correction turned up something the ticket had not: **"any case" is
  wrong too.** Pandoc resolves only the boolean spellings YAML 1.1
  *enumerates* — lowercase, Titlecase and UPPERCASE — so `no`, `No` and `NO`
  are booleans while `nO` is the plain string `"nO"`. That makes the false-ish
  table load-bearing for a bigger reason than anyone had written down: it is
  consulted after lowercasing, and it is the only thing standing between
  `show_date: nO` and a date the author asked to withhold. Bare `y` and `n`
  are booleans; `1`, `0` and `none` are not; `null`, `~` and a blank value are
  indistinguishable from one another.

  The measurement now lives in `tests/test_yaml_resolution.py`, which drives a
  probe filter through the pinned image and pins every spelling. A pandoc bump
  that changes resolution fails there instead of silently re-truing a comment.
  That is the actual fix — the prose was a symptom, and each corrected site
  now scopes its claim to `PANDOC_IMAGE` and points at the test rather than
  asserting something timeless about "pandoc" again.

## 0.34.0

- **An exact version pinned three packages and left 44 floating.** The browser
  image ran `npm install --global --prefix /opt/tools pa11y@10.0.0 pdf-lib@1.17.1 puppeteer@25.10.0`, which fixes those three and the exact
  puppeteer-core puppeteer declares. Everything below that — 44 of the 47
  packages in the resolved tree — still resolved within a range at build time,
  so a rebuild months apart installed different code, and nothing checked the
  bytes of any of it. `stn-5hv`, raised by the adversarial review of `stn-ba9`.

  Both installs now go through a committed lockfile and `npm ci`:
  `stencil/assets/browser-package-lock.json` and
  `stencil/assets/format-package-lock.json`, resolved once by a maintainer
  running `python3 scripts/vendor_npm_locks.py` and shipped into every
  generated package. `stencil gen` still touches no network — this is the
  shape `stencil/assets.py` and `scripts/vendor_page_assets.py` already used
  for the page assets, applied to npm.

  The guarantee is a hash rather than a version number. Every one of the 47
  entries carries a sha512 `integrity`, and npm verifies each tarball against
  it: flipping one character of one hash fails the build with `npm error code EINTEGRITY`, measured rather than assumed.

- **`npm ci` has no `--global`, so /opt/tools stopped being a prefix.**
  `--global --prefix /opt/tools` put modules under `lib/node_modules` and
  binaries under `bin`. The install is now an ordinary local one rooted at
  `/opt/tools`, so `NODE_PATH` moves to `/opt/tools/node_modules` and `PATH` to
  the `node_modules/.bin` symlinks npm writes itself — no hand-made symlink for
  `pa11y` any more. Both paths are rendered from `stencil/pipeline.py`, and
  `tests/test_compose_check_access.py` runs the real service through
  `docker compose`, so a rewiring that leaves `pa11y` or `require("puppeteer")`
  unresolvable fails there rather than in somebody's handout.

- **A generated package gains two files**, `browser-package-lock.json` and
  `format-package-lock.json`. `stencil clean` removes them and the managed
  `.gitignore` section covers them. The `package.json` each install needs is
  *derived* from the pins and written inline by the Dockerfile and the
  format-md entrypoint, so there is still exactly one place a version is
  written down, and a consumer's package directory gains two files rather than
  four.

  The browser lockfile travels with `Dockerfile.browser`, for any package that
  renders markdown — the two must arrive together, because the Dockerfile
  `COPY`s it. The format-md one is emitted for **every** package, with no
  predicate, and that is the interesting half. Three predicates were tried and
  each left a case behind: `has_pages` missed a package whose config-level
  `templates:` produces a compose file anyway; the compose file's own name
  missed a renamed `dest:`; adding the conventional spellings still missed a
  consumer whose composition template has a name of its own and pulls the
  partial in by include — which nothing outside a template body can see. The
  file is 1.3 KB and `stencil clean` removes it, so shipping it to a package
  that does not use it costs a small unused file, against a `make format-md`
  that fails for a consumer who did nothing wrong. The service also checks for
  it now and says what to run, rather than dying on `cp: can't stat`.

- **Both `npm ci` invocations pass `--ignore-scripts`.** A lifecycle script
  runs as root at image build time, with network, before any test looks — and
  a transitive package that *gains* a `postinstall` arrives in a 48-entry JSON
  diff as one added boolean. It costs nothing here: measured on npm 11.19.0,
  `--ignore-scripts` still installs all 47 packages and still leaves `pa11y`
  and `puppeteer` in `node_modules/.bin`, because bin symlinks are the
  linker's work rather than a script's. The only install script in either tree
  is puppeteer's, whose job `PUPPETEER_SKIP_DOWNLOAD` already cancels, and
  `tests/test_pins.py` freezes that set so a new one is a failing test.

- **`stencil clean` and the managed `.gitignore` now read the same list
  `stencil gen` renders from.** They were two hand-maintained spellings of one
  list, and the comment recording what that cost — a `package_sources`-only
  doc package with five generated files nothing could remove — was sitting
  directly above the second one while this change added entries to both. The
  guard that outlives the refactor is a property test: generate a package,
  look at what is on disk, and require `get_generated_files` to name every
  file. It catches the next injected file, whoever adds it.

- **`tests/test_compose_format_md.py` runs the format-md service** rather than
  reading it, the way `tests/test_compose_check_access.py` does for
  check-access. Its entrypoint went from one `npm install` that needed nothing
  on disk to a `printf`, a `cp` and an `npm ci` whose behaviour depends on the
  mount, the working directory and the YAML block scalar — none of which a
  text assertion can see. One case formats a real file; the other plants a
  `package.json` in the workspace naming a dependency that does not exist, and
  proves `npm ci` never read it.

- **A bumped pin with a stale lockfile is now a loud failure.**
  `tests/test_pins.py` fails when the lockfile's root dependencies stop
  equalling the pin maps, when an entry carries no integrity hash, when one
  resolves from anywhere but registry.npmjs.org, and when the browser tree
  grows a second puppeteer — that last one used to require building an image
  and is now a fact about a file. `npm ci` refuses independently, with
  `npm error code EUSAGE`, `Invalid: lock file's pdf-lib@1.17.1 does not satisfy pdf-lib@1.17.0`.

  The container tier goes further: it compares *every* installed package
  against the lockfile entry for its path, in both directions. Three pinned
  names being right was never the claim in question.

- **Breaking for a consumer that copied `docker-compose-html.yml.j2` rather
  than including it.** The partial's context interface changed:
  `format_npm_specs` is gone, replaced by `format_manifest`,
  `format_lockfile_name` and `format_tools_dir`. A composition template that
  includes the partial gets the new service and needs no edit.

  **A copy is not a supported configuration and must be migrated.** It keeps
  rendering, and what it renders is a service that installs prettier by name —
  no lockfile, no integrity check, the whole tree below those two versions
  re-resolved on every run. That is precisely the state this release exists to
  end, and nothing in stencil can detect it, because a copy reads none of
  stencil's context keys and `StrictUndefined` therefore has nothing to
  complain about. Replace the copy with an include, or port the three keys and
  the `npm ci` invocation into it;
  `tests/test_template_contract.py` records the set a composition must
  provide.

- Filed rather than folded in: `stn-8vi`. `NODE_IMAGE`, `PANDOC_IMAGE` and
  `VERAPDF_IMAGE` are pinned by tag, and a registry tag is mutable. That is the
  same class of gap one layer further out, it affects all three images, and
  pinning one of them by digest while the other two float would make the
  convention inconsistent for whoever bumps the next one.

- Also filed rather than fixed: `stn-86y`. The image resolves its tools
  through `NODE_PATH`, which is Node's *last-resort* lookup — so a
  `node_modules` directory in the consumer's own package folder outranks it.
  Measured in the built image: a decoy resolves as `0.0.0-decoy` from
  `/workspace/node_modules`. That predates this release and is not made worse
  by it (`NODE_PATH` was already the mechanism; only its value moved), but it
  is the one path by which something other than the locked tree renders a
  handout, so it is written down with the measurement rather than left to be
  rediscovered.

## 0.33.0

Takes 0.33.0 rather than 0.32.0, which was in flight on another branch while
this was written and has since landed as the entry below. The run of versions
is contiguous; nothing is missing.

- **The other four painted gaps, measured rather than assumed.** 0.13.0 fixed
  the document and deck headers, where a whitespace-only text node between two
  inline boxes never reached the PDF text layer and "Author Ada Lovelace"
  printed as "AuthorAda Lovelace". Four more places paint a gap with nothing
  behind it — `.side-by-side`, deck `.columns`, the `header.doc-title` grid,
  and the facts line — and none of them had ever been looked at in a PDF.
  `stn-avj` asked for a measurement first, not a fix.

  All four are fine, and the reason is worth writing down because it is *not*
  the reason the header is fine. None of them has a character behind the gap:
  pandoc emits a whitespace-only text node between the boxes and flex and grid
  both discard it, which is exactly the 0.13.0 shape. What saves them is that
  these are **block** boxes. Chromium emits each side as its own text object
  with its own `Tm` origin, and an extractor recovers the boundary from the
  advance — the same way it does between any two paragraphs or table cells in
  any PDF.

- **The threshold that recovery depends on, since "the extractor handles it"
  is not a measurement.** Built by hand: two Helvetica runs on one baseline,
  no space glyph, only the second run's x varying. Both extractors jam at a
  zero gap, and both break the word above a fraction of an em that does not
  depend on point size — roughly **0.15 em** for pypdf and **0.12 em** for
  poppler's `pdftotext`, holding at 9, 11, 14 and 24pt.

  Against that, measured in the real print PDFs: `header.doc-title` 1.06 em
  (~7×), `.columns` 2.8 em (~18×), `.side-by-side` 3.5 em (~23×). The header
  is the narrow one, which is why it is the one that now has a guard that can
  fail rather than an assertion that cannot.

  In em rather than px on purpose. `@media print` rescales the root font to
  9.78pt, so a gap quoted in screen pixels — as an earlier draft of this entry
  did — describes a different document than the one being extracted.

- **The header guard could not have failed, and now can.** The assertion in
  `tests/test_pdf.py` since 0.13.0 said the identity and context columns must
  not run together, with a comment conceding it had never been checked whether
  an arrangement exists in which they could. There is one, and finding it
  needs three things at once: no byline, no subtitle, and a single-line title
  nearly filling the identity track. Anything else in the identity column is
  emitted between the two, and pypdf breaks on the y change before it ever
  compares x. The columns still extract apart in that arrangement.

- **The facts line's accessibility boundary is now enforced, not just
  described.** `.doc-facts` being a flex container blockifies its `<span>`
  children, and that — rather than any character — is what separates "Sep 05"
  from the next fact's label in the accessibility tree, since the separators
  are `aria-hidden` and contribute no whitespace. The stylesheet had said so
  in a comment since 0.11.0 and nothing checked it.

  The new test reads the computed display of the `.doc-fact` elements
  themselves rather than of `.doc-facts`'s children, so both ways of losing
  the boundary are caught: `display: block` on the container, which satisfies
  a stylesheet grep while leaving the spans inline, and grouping facts in a
  wrapper div, which would satisfy a check on the container's children. A
  second test pins the separators' `aria-hidden`, which is the premise the
  whole argument rests on. Both verified by mutation.

  Nothing else would have caught either: the PDF text layer stays correct
  throughout, because the separators carry real characters, and neither pa11y
  engine behind `check-access` has a rule for adjacent text with no separating
  whitespace.

  Not fixed, deliberately: putting a character inside each flex item would
  shift the `space-between` distribution, which is a rendered change to every
  handout's header in exchange for a boundary that already exists.

- **What the sweeps do and do not catch, established by mutation.** Setting
  `.columns { gap: 0 }` does not fail them — and that is evidence the mutation
  failed, not that the tests are insensitive. Zeroing the CSS gap leaves the
  left column's line-breaking slack in place, so the glyph-to-glyph distance
  never approaches zero. A narrowing control has to walk the last glyph to the
  track edge, which the fill sweeps do and a gap edit does not. Turning
  `.columns` into inline flow *does* fail, through the non-vacuity check that
  requires at least one variant to land on a single extracted line.

- **Two extractors, two models, and the stylesheets no longer overstate one.**
  pypdf follows the content stream and breaks on any y change; poppler does
  geometric column detection. On the deck columns poppler emits a paragraph
  break where pypdf emits a space — both correct, neither a jam. The header is
  where they genuinely disagree: poppler reorders it and puts the context after
  the body text. So the "far apart in the content stream" argument that stood
  in `_page-style.css.j2` was pypdf-specific, and the comment now says what
  both models actually rely on, which is the gap width.

## 0.32.0

- **Inline code in a table header failed WCAG AA, in the theme most handouts
  are printed from.** `--code-inline` was `#c7254e`, which measures 5.52:1 on
  white and 4.07:1 on `--surface-accent-on` `#d2def2` — the `thead` fill, the
  darkest surface in the light palette. A backticked column name in a markdown
  table was therefore below the 4.5:1 threshold while the identical colour
  passed everywhere else it appeared.

  Raised to `#b01f45`: 4.95:1 on the header fill, 6.71:1 on white, and
  indistinguishable from the old colour at reading size. The alternative was a
  scoped `th code {}` rule, and it was rejected — it makes the same inline code
  two different reds depending on which row it lands in, needs a dark-mode
  counterpart of its own, and leaves the *next* dark surface someone adds
  failing again. The palette had one colour that was too light; the fix is to
  stop it being too light, at the token.

  It also lifts printed table headers from 4.51:1 to 5.48:1. Half a hundredth
  above the threshold is not a margin, and print is the one output a reader
  cannot re-theme.

- **The dark pairing was measured and left alone.** `#ff9ab0` on `#2f4680` is
  4.55:1 — passing, on 0.05. Moving it to gain headroom would have been a
  change made without a failure to justify it, and the token's comment now
  records the number so the next person does not have to re-derive it.

- **Why four releases of `make check-access` never saw this.** pa11y measures
  the pairings a page actually renders, and no fixture had a table at all — so
  the checker was passing over a document that could not produce the failure.
  `tests/fixtures/document.md` now carries a table with backticks in both its
  header row and its caption, and `tests/test_fixtures.py` fails if it loses
  them.

  The stronger guard is cheaper: `tests/test_theme.py` now measures
  `--code-inline` against every fill prose can land on, in both themes and in
  print, and runs in `pytest -m 'not integration'`. Contrast is arithmetic on
  two hex values; it should never have needed a browser to find out.

- **A known trap came out of the stencil-tool plugin skill.** The
  docs-and-decks skill told authors not to put backticks in a table header
  row. That was a workaround for this bug, the constraint no longer holds, and
  standing advice that outlives its cause is worse than none.

- Not fixed here, and filed rather than glossed: `stn-7i8`. A deck's title
  slide is rendered from front matter, and pandoc renders `$title$` as inline
  markdown, so a backticked title puts inline code on the accent fill. It fails
  badly on the old colour and the new one alike — 1.78:1 then 1.47:1 against
  `--deck-accent-from`, and 1.06:1 against `--deck-accent-to`, which is very
  nearly no contrast at all. Raising the token cannot reach it, because the
  fill is dark and inline code is ink; it wants a scoped rule inheriting the
  on-accent colour, which is a different decision from the palette one.

## 0.31.0

- **Nothing a generated package installs was pinned.** `Dockerfile.browser`
  ran `npm install --global puppeteer pa11y pdf-lib` with no version
  constraint, `FROM node:lts-alpine` floated across Node majors and Alpine
  releases, and `format-md` installed prettier the same way. Three tools, none
  of them stated, all of them deciding what a handout looks like.

  The ticket that found it, `stn-s5b`, makes the argument better than a
  changelog can: 0.13.0 pins `tagged: true` on `page.pdf()` *precisely* because
  an accessibility property that is only a default is one a version bump can
  remove silently. The pin guards the option; the floating install guarded
  nothing about the runtime that honours it. Against a Puppeteer predating the
  option the pin is a silent no-op — and `test_the_pdf_is_tagged` still passes,
  because the default is tagged.

  Exact versions, from `stencil/pipeline.py`, where `PANDOC_IMAGE` and
  `VERAPDF_IMAGE` already live. Exact rather than `^`, because `tagged` is the
  kind of option a *minor* release adds or drops, and a caret satisfies "the
  same major" without satisfying the argument the pin was made for.

- **Measured before pinning, not after.** On a `--no-cache` rebuild of the
  image exactly as it shipped, 2026-09-07: node v24.20.0, Alpine 3.24.1,
  chromium 152.0.7977.82-r0, puppeteer 25.10.0, pa11y 10.0.0, pdf-lib 1.17.1.

  pa11y 10.0.0 had been released ten days earlier and had floated in
  unnoticed — a major version bump nobody chose. Worse, **no test in this
  repository had ever executed pa11y**: `make check-access` is a compose
  service, and the suite reads HTML and PDFs. Pinning it as found would have
  frozen a version that had never been run here, so it was run first, against
  both generated theme configs on a rendered page, and that run is now a test.

- **One Node image, named once.** Three files reached for it — the browser
  Dockerfile, the `format-md` service, and the `ensure_image` line that
  pre-pulls it. Three copies of a floating name is three chances for
  `make format-md` to pull one image and run another, which reads as a slow
  first build rather than as a defect.

- **Chromium is deliberately NOT pinned, and the reasoning is in the
  Dockerfile.** Both apk spellings were measured. Alpine holds one version of a
  package per branch and drops it when superseded, so a pin is a countdown
  rather than a pin: `chromium=151.0.7716.0-r0` — the version this image
  installed one release ago — already fails with `unable to select packages`,
  and `chromium=~152`, which does hold the major, resolves today and fails the
  same way the week Alpine moves to 153. Either converts
  silent drift into a hard build failure with no escape hatch: the Makefile and
  the compose file build the image with no build argument, and `make gen`
  rewrites both.

  A handout nobody can build is worse than one whose page breaks moved — and
  where the page breaks land is measured directly, on every pull request, by
  `tests/test_pdf.py` and the PDF/UA suite. What *is* pinnable is the Alpine
  branch Chromium comes from, which is the base image.

- **New guards**, in `tests/test_pins.py`. The rendered scaffolding assertions
  are a property rather than a list — every `npm install` in every generated
  file must name an exact version — so a fourth package added to some future
  template fails here without anyone remembering to add a case. One of them
  passed vacuously when first written: a deny-list of `{latest, lts}` does not
  match `lts-alpine`, so the check now requires a digit in the tag.

  The container tier asserts the three claims that are genuinely different: the
  image *holds* the versions the Dockerfile *names*; there is exactly **one**
  puppeteer in the tree, because pa11y depends on puppeteer and a pin outside
  its range silently gives `check-access` a different browser than `make pdf`;
  and pa11y runs.

- **`make check-access` is in CI, for the first time.** `tests/test_check_access.py`
  runs pa11y over a generated document *and* a generated deck, in both themes,
  through the same configs the compose service uses, and fails on any WCAG 2.1
  AA issue. This is the counterpart to `test_pdf_ua.py`: that file checks the
  PDF against PDF/UA-1, this one checks the HTML against WCAG 2.1 AA.

  Until now the WCAG result this project claims for its output was measured
  only by whoever last ran the target by hand — which is also why a pa11y major
  could float in unnoticed. Both themes are separate cases because checking one
  leaves the other's contrast unmeasured, and the deck is here because it
  renders through a different template, filter and stylesheet: 0.28.2 found
  exactly that omission in the PDF/UA suite, and the same one was sitting here.

  Measured against the six real cs425 handouts as well as the fixtures, on
  pa11y 10.0.0, both themes: no issues.

- **And running it found that `check-access` was broken.** For a package with an
  `output_dir` it could not pass at all: the service's loop searched `/out`
  while the URL it handed the browser was built from `/workspace`, so every page
  came back `net::ERR_FILE_NOT_FOUND at file:///workspace//out/document.html`.

  Shipped in 0.30.0, and invisible to every test here, because they all read the
  compose file's *text*: one asserted the loop line, another asserted the
  counting, and both were true of a script that could not work. Two individually
  plausible lines that only disagree when run.

  The script now lives in `stencil/pipeline.py` as `CHECK_ACCESS_SCRIPT`,
  exactly as `VERAPDF_SCRIPT` already did, so a test runs the same text the
  compose file ships. The directory arrives as `$1`, absolute in both layouts,
  and the `file://` URL is built from it — one path rather than two that have to
  agree. `pipeline.check_access()` runs it the way `pipeline.verapdf()` runs
  check-pdf's. Proven against its own breach: put the old URL back and
  `test_the_script_passes_over_an_output_directory` fails. `stn-8j4`.

  Still only asserted as text: the compose *service* around the script — its
  build stanza, mounts and argument wiring. That needs `docker compose` in the
  container tier, which is a larger decision than this fix.

- **The same font file was inlined into every page up to four times.** Google
  Fonts serves a *variable* font -- `fvar`, `gvar` and `avar` are present in
  every face stencil vendors -- and returns THE SAME FILE for every weight of a
  family. So `wght@0,400;0,600;0,800` fetches one file three times, and the
  stylesheet built from it carries that file three times, once per
  `font-weight` descriptor.

  Measured on the vendored `fonts.css`: **31 `@font-face` blocks holding 17
  distinct files, 1,576,444 raw woff2 bytes of which 706,644 are duplicates.**
  Crimson Pro's upright face is in there three times over and Inter's four.
  That is 947,218 bytes of base64 on every generated page, before any question
  of what the page uses.

  It also means 0.17.0's bold fix did not cost the 236 KB it is recorded as
  costing. Adding `0,800` and `1,800` to the request added no glyph and no
  file — it added a third and fourth copy of bytes the page already carried.
  The fix was free and nobody knew.

  `assets.merge_duplicate_faces` collapses them on the way into a page. The
  committed `fonts.css` is untouched, so it still says what was actually
  fetched, and re-vendoring does not rewrite two megabytes of base64 that did
  not change.

- **What the merged `font-weight: 400 800` changes, stated rather than
  glossed.** A `font-weight` descriptor on a variable face pins the `wght`
  axis — that is the only reason three identical files render as three
  different weights today — so one block declaring the span those weights cover
  renders each of *them* identically. Pinning the axis at 600 and instantiating
  600 out of a range are the same operation on the same file.

  What moves is the weights in *between*. With discrete faces at 400/600/800, a
  request for 700 finds no face and CSS font-matching rounds it up to 800;
  inside a `400 800` range, 700 is an exact match on a continuous axis.
  Measured across every family, the complete set of weights this moves is
  **Crimson Pro 500 and 700 upright, 500/600/700 italic** — precisely the ones
  no face ever declared. `tests/test_fonts.py` asserts that set as data, so
  widening it fails the suite.

  Exactly one of those is reachable from plain markdown: Bootstrap's reboot
  sets `dt { font-weight: 700 }`, so a definition term would have gone from
  the 800 face to a real 700. `_page-style.css.j2` now pins `dt` to 800, which
  is what it renders as today. Bootstrap's other 700 utilities — `.fw-bold`,
  `.badge`, `.alert-link`, `.nav-underline` — reach the body serif only through
  hand-written HTML, which is outside the dialect AUTHORING.md describes; on
  those, and only those, bold prose in the serif now renders at 700 rather
  than 800.

- **The syntax highlighter rides along only on documents that have code.**
  `highlight.min.js` and its four language packs are 141,445 bytes, and they
  were inlined into every page whether or not it held a listing. Same treatment
  as the mermaid bundle in 0.14.0: `code-bundle-filter.lua` sets `has-code`,
  and `_page-head.html.j2` and `_page-scripts.html.j2` inline the stylesheets,
  the bundle and the `hljs.highlightAll()` call together under it.

  Together, not separately. Gating the library and leaving the call behind is a
  `ReferenceError` in a page that otherwise looks finished — `make pdf` would
  fail, because `html-to-pdf.js` refuses to write on a page error, but
  `make doc` would ship it. `tests/test_code_bundle.py` converts a code-free
  document to PDF for that reason: it is the only check that runs the script
  rather than reading it.

- **A mermaid diagram is not a listing, and getting that wrong would have
  undone the change on the biggest decks.** `mermaid-figure-filter.lua` wraps
  its `CodeBlock` in a Figure rather than consuming it, so the block reaches
  the page as `<pre class="mermaid"><code class="language-mermaid">` — which is
  exactly what the mermaid driver looks for. A filter counting every
  `CodeBlock` would set `has-code` on every deck that draws a diagram. Measured
  on cs425: every code fence in `design-patterns.md` and `kanban-and-sdd.md` is
  a mermaid fence, so both decks would have carried the highlighter forever for
  markup the driver deletes from the DOM before a reader sees it.

  Inline `` `code` `` does not count either: `hljs.highlightAll()` highlights
  `pre code`, and pandoc writes inline code as a bare `<code>`.

- **Measured on real documents, before and after, through the same
  `generate_package` + pandoc path `make doc` uses.** The before column
  reproduces the committed cs425 HTML byte for byte.

  | document                                                | before    | after     | saved  |
  | ------------------------------------------------------- | --------- | --------- | ------ |
  | `classroom/job-search.md` (deck, no code, has emoji)    | 2,667,850 | 1,576,738 | −40.9% |
  | `classroom/cs425-syllabus.md` (document, no code)       | 2,661,804 | 1,570,692 | −41.0% |
  | `classroom/design-patterns.md` (deck, 8 mermaid fences) | 6,253,915 | 5,162,803 | −17.4% |

  `@font-face` blocks per page: 32 → 17. Font payload: 2,116,885 → 1,169,667.

- **Nothing was dropped to get there.** Every face stencil vendored before it
  still ships, latin-ext included, and every character any of it could draw it
  can still draw. `stn-uje` proposed subsetting the faces to the glyphs a page
  actually uses, which would take the font payload lower still; that is not in
  this release, and no coverage was removed in its place.

- **The generated compose SERVICE is now run, not read** (`stn-8j4`). The
  `check-access` fix above put the script in `stencil/pipeline.py` so a test
  could execute it, and `tests/test_check_access.py` does. That is a weaker
  claim than it sounds: the script is one line of a service definition, and
  the bug was in a different line — the argument saying which directory to
  search, disagreeing with the mount saying where the products are. A test
  that assembles the mounts itself in Python supplies the correct answer as an
  argument and then confirms the script uses it.

  `tests/test_compose_check_access.py` runs what `make check-access` runs —
  `compose build check-access`, then `compose run --rm check-access` — against
  a real generated package, in both layouts, with the page put in place by the
  compose `doc` service so the two services have to agree about `/out` rather
  than being told separately. Nothing in this repository had ever run compose.

  Proven against its own breach four ways, including the shipped error
  verbatim (`net::ERR_FILE_NOT_FOUND at file:///workspace//out/document.html`).
  Three of the four are also caught in the fast tier by a string assertion
  standing in for the behaviour. The fourth is not caught anywhere else:
  comparing the skip-list against `$f` instead of `$(basename "$f")` points
  pa11y at stencil's own pandoc templates, and the 298 fast-tier tests and all
  seven script-level cases stay green — because a skip-list is only wrong in a
  directory that has something to skip, and the script-level tests use a
  scrubbed one holding a single copied page.

  Generated packages are unchanged: this adds `pipeline.compose_command()`,
  `pipeline.compose()` and a test file, and rewrites no template.

- 0.29.0 through 0.30.2 shipped without entries here. Git is the record of them.

## 0.28.2

- **veraPDF now sees a deck.** `stn-l68`'s acceptance asked for a PDF/UA check
  against a generated handout *and a generated deck*. Every one of the twelve
  PDF/UA tests built a `doc`; **not one built a slide**, so the deck path had
  never been in front of the checker in CI.

  That is a whole template path unmeasured — `slide-template.html` rather than
  `html-template.html`, `slide-sections.lua` grouping blocks into cards, a
  generated title slide nobody wrote, the toolbar, present mode. The classroom
  decks pass when measured by hand, so this found no defect. It is here so the
  next change to any of those files cannot quietly stop being conformant.

  The same fixture-omission pattern cost four releases in a row — a missing
  hyperlink, a missing empty cell, a missing rule, a missing emoji. A missing
  whole *document kind* was the largest instance of it left.

- **The fixture is checked for being a deck at all.** If `slide` ever renders
  through the document template, the conformance assertion would still pass
  and stop meaning anything, so page geometry is asserted separately: a deck is
  landscape, a document is portrait.

  | breach                    | result                           |
  | ------------------------- | -------------------------------- |
  | drop the artifact marking | deck test fails                  |
  | drop the ToUnicode repair | **deck test passes** — see below |

  The second row is reported rather than hidden. The deck fixture carries no
  maths, so it cannot exercise the ToUnicode repair; the document fixture does,
  and that is where that guard lives. Padding the deck with a formula would
  duplicate coverage rather than add any, since the font machinery is shared.

- No behaviour change. Tests only.

## 0.28.1

- **The guard `stn-5ea` asked for, which 0.27.0 shipped without.** That ticket
  listed "`with=hidden` checks the -hidden files" among the guards the change
  needed, and the change went out with the other four and not this one.

  `make check-pdf with=hidden` must open the `-hidden` PDFs, not the plain
  ones. Both targets spell the name with `$(OUTPUT_SUFFIX)`, so they agree by
  construction — and "by construction" stops being true the moment someone
  edits one of the two lines. Checking the wrong six files would **pass**,
  which is the failure mode worth a test rather than an argument.

  The assertion is that the two lists are *equal*, not that either has a
  particular shape: whatever `pdf` writes is what `check-pdf` opens.

  | breach                                               | tests that fail |
  | ---------------------------------------------------- | --------------- |
  | drop `$(OUTPUT_SUFFIX)` from the check-pdf filenames | 2               |
  | check only the first document                        | 1               |

  No behaviour change. This is a closed ticket's unfinished half.

## 0.28.0

- **Emoji render instead of printing as empty boxes.** Six of them in cs425's
  job-search deck were `.notdef` rectangles in the handed-out PDF. The text
  faces carry no emoji, stencil inlines a closed set so a page is
  self-contained, and `make pdf` renders in a container with no system fonts —
  so there was nothing to fall back to. On screen it always looked right,
  because a browser falls back to the OS emoji font; only the PDF was wrong.

  Monochrome Noto Emoji is now inlined. **It costs 695 KB base64 on every
  page**, `fonts.css` 1,422,226 → 2,117,225, and it is now 32% of the font
  payload — as much as Inter. That is a poor ratio for six icons on one slide,
  and it is the operator's call: emoji are characters and should behave like
  characters. Colour emoji would be several megabytes and is not viable.

  Recorded because the first estimate was wrong: counting the chunks covering
  those six emoji gives three (+19%); counting the chunks covering the
  pictographic plane gives nine (+48%), because Google chunks by frequency
  rather than by block. Nine ships, so an emoji the author has not used yet
  still works.

- **A character no inlined font can draw now fails the build.** Anything still
  outside coverage — a CJK ideograph, a flag — stops `make pdf` and names the
  codepoint, instead of silently becoming a rectangle. The test measures a
  character's advance against a Private Use codepoint's, because that width
  *is* the `.notdef` width; `document.fonts.check()` answers "is this family
  available", which is a different question.

- **No ToUnicode entry maps to U+0000 any more.** PDF/UA-1 7.21.7 t2. Four
  entries in the embedded NotoSansMath subset did, in the queueing-theory
  deck. Traced with fontTools rather than guessed: gids 4851, 4859, 4861 and
  4985 have **no cmap entry at all**, are not MATH stretchy variants and are
  not reachable through GSUB, and their outlines are wide, short and entirely
  below the baseline. They are fraction bars and underbrace parts, drawn by
  glyph index because that is how a math renderer picks them. The font gives
  them no character because they are not characters, so Chromium's subsetter
  had nothing to write and wrote zero.

  They become U+0020. That is a repair of a producer defect, not a claim about
  meaning: a fraction bar separates a numerator from a denominator, and a space
  is what keeps them apart in extracted text. Deleting the entries instead
  would trade 7.21.7 t2 for 7.21.7 t1 — a used code with no mapping at all —
  so removal was never an option, only replacement.

- **Two guards were green in CI without exercising anything.** Every gate test
  asserts a non-zero exit, and "the container could not read the file" is also
  a non-zero exit. On CI the mounted `tmp_path` files were invisible and the
  tests passed anyway. Only the one asserting on the *count* caught it — which
  is exactly why that assertion was written that way. They now use a
  `pdf_workspace` subdirectory, the mechanism CI demonstrably accepts; the
  permissions theory for *why* tmp_path differs could not be reproduced
  locally, so it is not claimed as the cause.

## 0.27.0

- **A `Do` into a Form XObject that carries tagged content is no longer marked
  as an artifact.** This is a defect 0.21.0 introduced, not one it found.
  `markArtifacts` counts BDC/EMC in the **page** stream and treats `Do` as a
  painting operator; `Do` hands off to a stream one level of indirection away
  that can carry marked-content sequences of its own. Wrapping it put every
  MCID inside the XObject into an `/Artifact` — veraPDF 7.1 t2, *tagged content
  shall not be present inside content marked as Artifact*. `poster.pdf` failed
  1 check, `kanban-vs-scrum.pdf` 9.

- **The trigger is a horizontal rule**, which is why it stayed hidden for three
  releases. Bootstrap styles `<hr>` with `opacity: .25`; an opacity below 1
  makes Chromium emit a transparency group; a transparency group is a Form
  XObject. The six handouts contain no rule anywhere, so 0.21.0 through 0.24.0
  measured six of six and never saw it. That is the third release running where
  the fixture's omission *was* the bug — a missing hyperlink in 0.23.0, a
  missing empty cell in 0.24.0, a missing rule here.

- **Only `/Form` is scanned, and that nearly shipped wrong.** The first version
  checked every XObject for `BDC`, including `/Image`, whose data is pixels.
  Searching a photograph's bytes for a three-character token is a coin toss on
  binary that would eventually declare an image tagged and stop marking a
  genuinely decorative one as an artifact.

- **`check-pdf` names the files it checks instead of globbing the directory.**
  `cs425/classroom` carries an 11 MB third-party book, and `for f in *.pdf`
  failed the build on it. The report was true and unactionable, and an
  unactionable red is how a gate gets switched off.

  The target already knows exactly what `make pdf` just wrote, so the guard
  gets sharper rather than merely narrower: *found 0* becomes *expected 6,
  found 5*, naming the one that is missing. `$0` is spelled out in the compose
  entrypoint, because `sh -c SCRIPT` puts the first following argument there —
  without it the first filename would be swallowed and silently go unchecked.

- **A breach that did not hold, reported rather than omitted.** The ticket
  offered "treat `Do` as not-painting" as the cheap alternative. Applied, it
  fails **no test here**: no fixture produces a depth-0 `Do` into an *untagged*
  XObject, and every Form XObject in the classroom corpus carries marked
  content, so for these documents the two fixes are indistinguishable. The
  precise one is kept because it names the actual condition — a CSS
  background-image, or a future Chromium, would still be marked rather than
  silently reintroducing 7.1 t3.

  | breach                                           | tests that fail   |
  | ------------------------------------------------ | ----------------- |
  | ignore the tagged-XObject set (0.21.0 behaviour) | 1, naming `/X8`   |
  | drop `Do` from `PAINTING_OPERATORS`              | **0** — see above |
  | let the gate glob the directory again            | 1                 |
  | give the gate an empty file list                 | 1                 |

## 0.26.0

- **`data-cols` says how many columns a row has, and the block wraps.**
  `::: {.columns .cards data-cols=2}` with four children is a 2×2. Supported
  values are 2, 3 and 4.

  This is the other half of the 0.20.0 trade, not a reversal of it. Before that
  release `.columns` was `grid-template-columns: 1fr 1fr`, so four children
  **did** wrap to 2×2 — a layout people want — and three children left an empty
  cell, which nobody does. Replacing it with `grid-auto-flow: column` killed the
  empty cell and took the deliberate 2×2 with it. Both shapes are legitimate;
  the defect was that either one was implicit. Now the author says which.

- **Stacking two `::: columns` blocks was never a workaround, and the width is
  why.** Column widths are computed **per block** from that block's own child
  count, so five cards as 3 + 2 gives a row of thirds above a row of halves and
  nothing lines up — with no warning. Two blocks of two align only because the
  counts happen to match. Row heights are independent too, so a tall card in the
  first block does not stretch the second. One grid computes one set of tracks
  for every row; two grids cannot, at any child count.

- **A short last row stays short.** Five children at `data-cols=3` leaves a gap
  on the right, and nothing stretches the orphan to hide it. That is not the
  0.20.0 empty cell returning: there the count was imposed by the stylesheet and
  the hole was a surprise; here the author asked for three across and the
  remainder is arithmetic. A test pins it, because "fixing" it is the obvious
  wrong idea.

- **`data-cols` beats `wide-left`/`wide-right`, decided rather than left to
  chance.** The selectors have equal specificity, so source order decides it —
  and source order is a thing someone tidies. Saying how many columns there are
  is a more fundamental statement than how to bias two of them, and
  `data-cols=3` with `wide-left` has no meaning at all, so combining them gives
  equal columns and is documented as useless rather than made to work.

- **`data-cols`, not `cols`, and that spelling was measured.** Pandoc leaves an
  attribute name it recognises alone and prefixes one it does not with `data-`.
  So `foo=bar` becomes `data-foo="bar"` while `cols=2` passes through as
  `cols="2"` — invalid on a `<div>`, since `cols` is only real on `<textarea>`
  and `<colgroup>`. The shorter spelling is the one that produces invalid HTML,
  which is the opposite of what the auto-prefixing suggests, and this project
  runs pa11y over its own pages.

  | breach                                            | tests that fail                                 |
  | ------------------------------------------------- | ----------------------------------------------- |
  | remove the `data-cols` rules                      | 6                                               |
  | move them above `wide-left` so source order flips | 1 — the bias survives as `[622, 388, 622, 388]` |

- **AUTHORING.md gains the two silent syntax traps**, which is what sent this
  ticket in. `::: {columns}` does not error — it renders `class="{columns}"`,
  braces included, matching no CSS. And heading attributes go at the **end**
  (`## Title {.big}`); at the front they become visible text in the heading.
  Both render without complaint, which is the only reason they are worth a
  table.

## 0.25.0

- **A custom `template_env` key may no longer take a derived key's name, and
  says so instead of silently rewiring the build.** A package's `template_env`
  was merged with `context.update`, so it overwrote whatever it collided with:
  `pandoc_image`, `package_type`, `docs`, `assets`, and as of 0.22.0
  `verapdf_image` and `verapdf_script`. A typo that happened to match a derived
  name replaced the image every generated service runs.

  The comment three lines above that merge read *"setdefault throughout, so a
  config cannot shadow a derived key."* It was true of the two passes above it
  and false of the line directly beneath it, which is the kind of wrong that
  survives review: the code says what it should do, one line down.

- **Raising rather than dropping, and measured before deciding.** Silently
  ignoring a collision is the safe-looking option and it is worse — a config
  doing this today is asking for something and would stop getting it without
  being told. Across cs234 and cs425 there are **17 distinct `template_env`
  keys in use and none collides with a derived key**, so raising costs no
  existing config anything. Those 17 names are now a test: if a future derived
  key takes one of them, it fails here rather than in a course repository the
  next time someone runs `make gen`.

  This is the call `StrictUndefined` already makes elsewhere in the same file —
  fail at generation time, not in whatever the template rendered.

- **Both levels, or the rule is a suggestion.** Config-level `template_env`
  already could not shadow, because `setdefault` quietly dropped it; it now
  reports the collision instead of ignoring you. The error names every
  colliding key at once rather than the first, so a config with two mistakes
  needs one round trip.

- **The package-level merge stays an `update`, deliberately.** The obvious fix
  — make it a `setdefault` like the two passes above — is wrong and is guarded
  against: a package's own value must beat the config-wide default, which is
  the entire reason for declaring one. Applied as a breach, it fails four
  tests, including every one of the 17 real consumer keys.

  | breach                                                  | tests that fail |
  | ------------------------------------------------------- | --------------- |
  | revert the package merge to a bare `context.update`     | 2               |
  | drop only the config-level check                        | 1               |
  | "fix" it by making the package merge a `setdefault` too | 4               |

  Found by a review bot on the 0.22.0 PR, which proposed rejecting collisions
  for the two new `verapdf_*` keys specifically. That would have left every
  other derived key shadowable and encoded the inconsistency as deliberate.

## 0.24.0

- **An empty table cell is now tagged, so a table with a blank in it can
  pass.** PDF/UA-1 7.2 t43 requires every row of a table to span the same
  number of columns. Chromium gives an empty `<td>` a layout box and tags only
  cells that have content, so the cell vanishes from the structure tree and its
  row comes up short by exactly the number of blanks in it.

  The source table is not ragged; the tag tree is. Measured on
  `kanban-game.pdf`, table 0 came out `rows=[5, 5, 4, 4, 2]` from a table that
  is five cells wide in every row. A kanban column with two cards has three
  empty slots, and those slots are the figure — the markdown is not what needs
  fixing.

- **What this measured.** CS 425 corpus, veraPDF 1.30.2 `--flavour ua1`, six
  handouts:

  |                 | 0.22.0       | 0.23.0       | 0.24.0     |
  | --------------- | ------------ | ------------ | ---------- |
  | conformant      | 1 of 6       | 4 of 6       | **6 of 6** |
  | `kanban-game`   | FAIL 7.2 t43 | FAIL 7.2 t43 | PASS       |
  | `sprint-report` | FAIL 7.2 t43 | FAIL 7.2 t43 | PASS       |

- **The CSS answer was measured and does not work.** `td:empty::before` is the
  obvious one-liner and it was tried both ways before the post-process was
  written:

  |                    | tagged rows                 | text layer                 |
  | ------------------ | --------------------------- | -------------------------- |
  | `content: "\00a0"` | unchanged `[5, 5, 4, 4, 2]` | unchanged                  |
  | `content: "x"`     | unchanged `[5, 5, 4, 4, 2]` | an `x` in every blank cell |

  Chromium does not tag CSS-generated content in a cell at all. So it fails on
  the first column, not the second — and the second column is why no variant of
  it should be reached for either: generated content visible enough to be
  tagged is visible enough to be copied out of the table.

- **Which column a blank belongs to is the part that matters, and the tag tree
  cannot supply it.** Appending the missing cells to the end of a short row
  satisfies 7.2 t43 and puts the kanban board's blanks under the wrong
  headings. So the DOM's own row-and-cell layout is read before printing and
  the cells are inserted at their own index; the fixture has a table whose hole
  is in the *middle* column, which is the case appending gets wrong.

- **A row whose cells cannot be accounted for is left exactly as it was.** The
  two lists are the same tables counted two ways; every tagged cell has to be
  matched to a cell the page actually has, and a row that does not add up is
  skipped and reported on stderr rather than guessed at. A blank put in the
  wrong column is worse than a row that is short.

- **Measured asymmetries, now pinned by tests.** An empty `<th>` *is* tagged
  and an empty `<td>` is not; a wholly blank row still produces a `/TR`, with
  nothing under it; a cell holding only a picture has no text and is emphatically
  not blank. All three are in the fixture, and `tests/test_pdf_ua_gate.py`'s
  veraPDF document gained an empty cell so the checker itself sees the case.

## 0.23.0

- **Every link in a generated PDF now carries an alternate description.**
  PDF/UA-1 7.18.1 t2 and 7.18.5 t2 require one in the annotation's
  `/Contents`. Chromium writes the `/Annots` array, the `/Link` annotation and
  its URI action, and no `/Contents` — and no HTML makes it, because
  `/Contents` is a PDF-level description with no DOM equivalent, exactly as
  `/Alt` on a `<figure>` had none in 0.15.0. So it is added after printing,
  next to the role map, the XMP and the list bodies.

- **What this measured, which is the point of the release.** 0.22.0 shipped
  the gate; the first thing the gate did was report that 0.21.0's claim of
  full PDF/UA-1 conformance was true of `tests/test_pdf_ua.py`'s fixture and
  false of real documents. Against the CS 425 corpus, veraPDF 1.30.2
  `--flavour ua1`:

  |                        | before                    | after  |
  | ---------------------- | ------------------------- | ------ |
  | handouts conformant    | 1 of 6                    | 4 of 6 |
  | `final-presentation-1` | FAIL 7.18.1 t2, 7.18.5 t2 | PASS   |
  | `final-presentation-2` | FAIL 7.18.1 t2, 7.18.5 t2 | PASS   |
  | `resume-and-interview` | FAIL 7.18.1 t2, 7.18.5 t2 | PASS   |

  Nothing else about those three documents was wrong. They failed because
  they contain hyperlinks, and the fixture did not.

- **The description is the link's own words, and the URL is only the
  fallback.** A `/Contents` reading `https://example.com/a/b/c.html` satisfies
  both rules and tells a reader nothing they could not already see. Preference
  order: the anchor's `aria-label`, its text, the accessible name of a picture
  inside it, its `title`, and only then the URL.

  This is the `role="none"` lesson from 0.15.0 restated: clearing a rule is
  not the same as helping anyone, and the cheapest way to clear this one
  produces a document full of URLs read aloud character by character.

- **Annotations are matched to anchors through the structure tree, not by
  position or by URL.** One anchor becomes two annotations when the link wraps
  across a line, and two again for an image link — so counting annotations
  against anchors shifts every description after the first wrap. Two different
  links to one URL are a separate trap: matching on the URL gives them both
  the same words. Chromium already hangs an `/OBJR` under each `/Link`
  structure element for every annotation that anchor produced, so the grouping
  is read from the file rather than guessed. Both cases are in the fixture.

- **The text is read from the DOM before printing, not from the PDF
  afterwards.** Recovering it from the content stream would mean decoding
  subset-font glyph codes back through each `/ToUnicode` CMap. The browser
  still has the words; it is only the file that loses them.

- **`/Contents` is written as a hex string.** A literal string is
  PDFDocEncoded, and pdf-lib truncates each character to one byte — measured,
  a link labelled `Café — résumé` arrives as `Café \x14 résumé`, an em dash
  turned into a device-control character. The fixture uses that exact label.

- **The fixture gained the case that was missing.** `tests/test_pdf_ua.py`
  builds a document with a hyperlink, a wrapping link, two links to one URL, an
  image link, a decorative-image link with no words anywhere, and an accented
  label. `tests/test_pdf_ua_gate.py`'s veraPDF document gained a hyperlink too,
  so the checker itself — not only pypdf — sees the case. A fixture is a claim
  about the documents it resembles; this one had never seen a link.

## 0.22.0

- **`make check-pdf` runs veraPDF against the generated PDFs.** Every
  PDF/UA-1 number in this changelog up to now was produced by hand. Nothing in
  the repository ran the checker, so a regression in the PDF would have been
  invisible until someone thought to look — which, for the 13 unnamed figures
  in 0.12.0, was three releases later.

  It is a separate target from `check-access` because it measures a separate
  standard. pa11y reads the HTML at WCAG 2.1 AA; veraPDF reads the PDF at
  PDF/UA-1 (ISO 14289-1). A page can pass the first and produce a file that
  fails the second, and the `<figure>` wrapper is exactly that case.

- **The gate's real work is refusing to pass on nothing.** Measured against
  `verapdf/cli:v1.30.2`: invoked with no file arguments, veraPDF **exits 0 and
  prints nothing**. So the obvious implementation —
  `verapdf --flavour ua1 *.pdf` — reports a clean bill of health when it is
  run before `make pdf`, after a clean, or behind a glob that matched
  nothing. It is decoration that looks like a check.

  `check-pdf` counts the files it is about to open and fails loudly at zero,
  names each file on its own invocation rather than handing over a glob, and
  says how many it checked. `tests/test_pdf_ua_gate.py` points it at an empty
  directory and requires a non-zero exit, so removing the guard fails a test
  rather than quietly restoring the silent pass.

  This is the second no-op of its kind in two releases. 0.21.0 nearly shipped
  a content-stream rewrite that read compressed bytes, found no operators, and
  passed everything. Both had the same shape: a step that does nothing and
  reports success.

- **The image tag and the script are pinned and shared.** `VERAPDF_IMAGE` and
  `VERAPDF_SCRIPT` live in `stencil/pipeline.py`, the way the pandoc argv
  already does, so the compose file renders the same text a test runs rather
  than an approximation of it. Compose substitutes `$VAR` inside a service
  definition, so the rendered form doubles every `$` — an unescaped `$found`
  arrives as the empty string and `[ "" -eq 0 ]` is not a comparison that
  fails safely. That has its own test.

- **Proven against a real regression, not only against an empty directory.**
  Reverting 0.21.0's artifact marking makes the conformance test fail. A gate
  that only ever answers PASS is indistinguishable from one that has stopped
  working.

## 0.21.0

- **Generated PDFs are PDF/UA-1 conformant.** veraPDF 1.30.2 `--flavour ua1`,
  on a handout with headings, a list, a table and a figure:

  |               | at the start of this run | 0.19.0 | now   |
  | ------------- | ------------------------ | ------ | ----- |
  | rules failed  | 5                        | 1      | **0** |
  | checks failed | 47                       | 3      | **0** |

  106 rules and 1523 checks pass. Reaching zero was described in 0.19.0 as
  "not in this release"; this is that release.

- **Decoration Chromium paints untagged is now marked as an artifact.** What
  `7.1 t3` was reporting, measured on the content stream rather than reasoned
  about, was exactly three things: the white page background, a clip path, and
  the table header cells' shading. All three are decoration, which is what
  `/Artifact` exists to mark.

  **Marked, not removed.** Deleting them means turning `printBackground` off,
  which takes the table shading, the blockquote cards and the slide title bars
  with it.

  Only depth-0 runs are wrapped, and only runs that actually paint — a run of
  pure graphics state (colour, matrix, `gs`) is left alone, because marking it
  would claim content where there is none. Runs break on `q` and `Q`, so a
  wrapper can never straddle a save/restore pair it does not own.

- **Two things nearly shipped as successes**, and both are worth recording
  because each one passed every test it had.

  The first version read the stream with `getContentsString()`. That method
  exists and returns the stream's **compressed** bytes, so the transform found
  no operators, changed nothing, reported success, and the suite went green.
  The only thing that revealed it was the veraPDF failure count not moving.
  The working path is `decodePDFRawStream(...).decode()`, and the result must
  be written back as a **new** stream: a `PDFRawStream` owns its `/Length` and
  `/Filter`, so rewriting its bytes in place leaves a stream no reader can
  parse.

  A test asserted `count("re") > 5` and failed on a document with no table —
  it was measuring the fixture, not the code. It now checks that every
  `/Artifact` region contains a painting operator, which is the property that
  actually matters: the operators were bracketed rather than dropped.

  Three breaches were applied and measured, each failing only its own tests:
  no marking at all (2 tests), dropping the operators instead of bracketing
  them (1), and reverting to `getContentsString()` (2). That last row is the
  point — the silent no-op now fails loudly.

## 0.20.0

- **A columns block is as wide as its contents.** `.columns` was
  `grid-template-columns: 1fr 1fr` — two columns whatever you put in it. Three
  children left an **empty cell**; four stacked 2×2. Both shipped in a deck in
  use, and both read as mistakes rather than choices.

  `grid-auto-flow: column` takes the count from the content instead. Two stay
  two, three sit in a row of three, four in a row of four. `wide-left` and
  `wide-right` are unchanged — they name two explicit tracks, which is exactly
  what a biased two-column split is.

  Measured in a browser, not read off the stylesheet: a grid's *used* track
  count is not in the CSS, so the tests count distinct top offsets of the
  rendered boxes.

- **Columns can be cards.** `::: {.columns .cards}` gives each one a surface, a
  border, a radius and padding, built from the tokens `.takeaway` already uses
  so a card matches the boxed conclusion below it rather than introducing a
  second surface.

  **Opt-in, not default.** A card costs vertical space on a medium that has
  none spare, and every existing deck writes bare `::: columns`. Contrast was
  measured on the rendered card rather than assumed — body text is 13.76:1 in
  light and 11.6:1 in dark, the lead run 8.79:1 and 6.17:1.

- **A card can carry its own accent.** `--card-accent` defaults to the deck
  accent, and `::: {.column .accent-2}` overrides it for one card. The accent
  reaches both a left border — the same device `.lead-in` has always used — and
  the card's lead run, so `**S -- Situation**` is coloured the way the rest of
  the card is. `.lead-letter` on the block sets the first character larger,
  which is what a STAR-style slide wants.

  Page fit was checked rather than argued: a four-card slide and the same slide
  with plain columns both come out at the same page count, so the treatment
  adds no overflow of its own.

## 0.19.0

- **The document title is an `<h1>`.** It was bare text in a `<div>`, so a
  stencil document had **no h1 at all** and opened on whatever the author wrote
  first, normally an H2. That is wrong on its own terms — the title *is* the
  document's top heading, and the deck has always spelled it
  `<h1 class="deck-title">` — and PDF/UA rejects the skipped level. Styling is
  unchanged: `.doc-title` still sets the type and `.doc-name` only cancels the
  user-agent h1, so nothing moved.

- **Three things PDF/UA-1 needs that Chromium does not write** are now added to
  the finished PDF, after printing. None of them can be fixed from the HTML,
  because they are properties of the PDF rather than of the page.

  | added        | why                                                                                                                                                                                                   |
  | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `/RoleMap`   | Chromium tags `<strong>`/`<em>` as `/Strong`/`/Em`, which are not standard ISO 32000-1 structure types, and writes no map saying what they stand for. `<b>` tags identically, so no markup avoids it. |
  | XMP metadata | The catalog had no `/Metadata` stream at all. Now carries `pdfuaid:part=1` and the title.                                                                                                             |
  | `/LBody`     | Chromium tags a list item's content straight onto the `/LI`.                                                                                                                                          |

  The role map is **built from the document**, not hardcoded to Strong and Em.
  Chromium's tag vocabulary is not a published contract and the browser is
  unpinned, so a version that starts emitting one more non-standard type would
  otherwise reintroduce the same failure with nothing to catch it.

  The `/LBody` fix is the one worth recording, because the obvious explanation
  is wrong. It is **not** about tight versus loose markdown lists — measured,
  `<li>text</li>` and `<li><p>text</p></li>` both fail. A pandoc-side filter
  forcing loose lists was the first instinct and would have been wasted work.

- **Measured against veraPDF 1.30.2 `--flavour ua1`**, on a real handout:

  ```
  before   5 rules failed, 47 checks
  after    1 rule  failed,  3 checks
  ```

  What remains is `7.1 t3`, content Chromium paints without marking it an
  artifact: one per page plus roughly two per table. Bisected — dropping our
  own page background in print removes one of them, and the rest survive with
  every table background and border set to `transparent`, so they are the
  browser's own painting rather than anything this project declares. Reaching
  them means rewriting the page content stream, which is not in this release.

## 0.18.0

- **Changing slides no longer changes the theme.** Pick a theme while
  presenting and the next arrow key put it back; the palette then cycled
  light, dark, system as the talk went on. Reported by someone teaching from a
  deck, which was the only way it could be reported.

  Two keyboard handlers wanted the same key. The theme control is a
  radiogroup, and **selection follows focus** in a radiogroup -- its arrow
  handler does not merely move a highlight, it calls
  `__stencilTheme.set()`. The deck listens for the same arrows on `document`
  and guarded only `INPUT`, `TEXTAREA` and `contentEditable`. So once a reader
  had focused the theme control, one `ArrowRight` advanced the slide *and*
  stepped the theme, because the control's `preventDefault()` does not stop the
  event reaching the deck.

  That is why the behaviour looked so strange from outside: never touch the
  control and everything is fine, touch it once and every later slide change
  also repaints the deck.

  While `html.presenting` is set, the arrows now belong to the deck and the
  theme control ignores them. It is still clickable, and outside a
  presentation it keeps full keyboard control -- a document never sets that
  class, so nothing changes there.

  The obvious alternative fix is worse and is guarded against: silencing the
  *deck's* handler when focus sits in the toggle would make the theme stable
  and leave a presenter pressing a dead arrow key.

  Taking the arrows away needed a replacement, which a review caught before
  this shipped. Roving tabindex leaves only the *checked* option tabbable and
  the arrows are what normally reach the rest, so the guard on its own would
  have let a keyboard-only presenter focus the control and never change it --
  quieter than the bug being fixed, and worse. While presenting, every option
  is now its own tab stop: Tab walks them, Enter activates.

- **Space on a focused button no longer skips a slide as well.** The same
  double-action, one key over: Space activates a focused button and is also the
  deck's "next slide", so tabbing to the theme control and pressing Space
  changed the setting and lost a slide at once. Only the activation keys are
  surrendered -- the arrows stay with the deck wherever focus happens to be.

- **The deck's keyboard is now testable at all.** Every other test in this
  repository reads markup or a built PDF and none of them can press a key,
  which is why a collision between two handlers shipped. `pipeline.run_in_browser`
  runs a node script against the browser image the PDF build already uses, and
  `tests/test_present_mode.py` drives real key presses through it -- routed by
  focus, exactly as they would be for a person. Both failure modes above are
  covered, and both were confirmed to fail before the fix.

## 0.17.0

- **Bold is bold now.** `**like this**` in a handout rendered SemiBold rather
  than Bold, and in a serif at body size that is a difference you have to look
  for rather than one you see.

  The cause was not a CSS value, it was a missing file. Crimson Pro -- the body
  face -- was vendored at 400 and 600 only. Bootstrap's reboot says
  `b, strong { font-weight: bolder }`, which resolved to 700, no face at or
  above 700 existed, and the browser fell back to the nearest one it had: 600.
  A CSS rule naming a weight nobody ships does not fail. It renders as
  something close and looks almost right.

  Bold italic was worse. With no italic above 400, `***like this***` rendered
  as plain italic at regular weight -- no emphasis at all beyond the slant.

  The new weight is 800, and it was measured rather than picked. Advance width
  of "the quick brown fox" at 18px:

  | weight | width |                                                  |
  | ------ | ----- | ------------------------------------------------ |
  | 400    | 142.6 | regular                                          |
  | 600    | 149.1 | **what bold used to render as**                  |
  | 700    | 152.8 |                                                  |
  | 800    | 156.8 | what bold renders as now                         |
  | 900    | 156.8 | identical to 800; the family has nothing heavier |

  600 is the number to compare against, not 400, and against 600 a move to 700
  would have added 2.5% to a step that was already too small to read as
  emphasis. 800 roughly doubles it. 900 buys nothing at all.

  `strong, b` now names 800 explicitly rather than inheriting Bootstrap's
  relative `bolder`, which resolves against whatever the parent carries: inside
  `.doc-label` at 600 it asked for 700, and inside a heading at 700 it asked for
  900\. Emphasis should be one weight wherever it appears.

  Inter is untouched. It always shipped 700, which is why headings and the
  byline looked properly bold while the prose did not.

  `tests/test_fonts.py` now fails if the stylesheet and the vendor script ever
  disagree about which weights exist, in either direction. That is the check
  that was missing: nothing in the build, the suite, or a five-way CI matrix
  had anything to say about a rule asking for a face that was never fetched.

## 0.16.0

- **A figure can have a dark variant, and the PDF still prints the light one.**
  Since 0.12.0 mermaid diagrams redraw to follow the theme while static figures
  did not, so a deck carrying both went half-themed in dark mode: recoloured
  diagrams beside white-plate SVGs. That was true of a deck in active use.

  Put `images/foo-dark.svg` beside `images/foo.svg` and both are embedded, one
  classed `doc-img--light` and one `doc-img--dark`, with CSS showing one. The
  author writes `![alt](images/foo.svg)` exactly as before: no new syntax, no
  front matter, and a figure with no dark sibling is emitted unchanged.

  `<picture>` with `media="(prefers-color-scheme: dark)"` is the standard HTML
  answer and it is the wrong one here. This page resolves its theme in JS to
  `data-theme` so the dark palette stays one CSS block; `prefers-color-scheme`
  follows the operating system instead, so a `<picture>` would desync from the
  rest of the page the moment a reader picks a theme explicitly -- which is the
  case the theme control exists for.

  The containment has a new half. Every dark rule so far had to stay *inside*
  `@media screen`; the rule that **hides** the dark variant has to stay
  *outside* it, because print is what must match it. Tidying that rule in with
  the others would print both figures stacked on every handout. Both directions
  are now asserted.

  Verified against the artifact rather than the stylesheet: the fixtures are
  pure red and pure green, and the test reads the fill operator out of the
  built PDF's content stream. Chromium draws an SVG from an `<img>` as vector
  operators, so there is no image XObject to inspect -- measured, the PDF has
  none at all.

  Both variants embed, so a page using dark figures roughly doubles that part
  of its weight; a document with no dark siblings pays nothing.

## 0.15.0

- **Every figure in a generated PDF now carries an accessible name.** A PDF/UA
  checker reported 13 text-alternative failures on a 0.12.0 deck. All 13 were
  the `<figure>` elements: Chromium tags `<figure>` itself as `/Figure`, HTML
  has no `alt` attribute on `<figure>` for it to carry, and PDF/UA
  (ISO 14289-1) requires `/Alt` or `/ActualText` on every one. The `<img>`
  inside each wrapper was named correctly, which is why the count was 21
  `/Figure`, 8 named, 13 not.

  Nothing in the HTML was wrong, and that is the point. WCAG has no equivalent
  rule, so `make check-access` passed, the test suite passed, and a five-way CI
  matrix passed. The two standards disagree here, and only one of them was
  being checked.

  Four candidate fixes were measured in a built PDF's tag tree rather than
  argued about, because three of them look equally correct written down:

  |                                 | result                                  |
  | ------------------------------- | --------------------------------------- |
  | `<figcaption>` alone, no ARIA   | outer `/Figure` still unnamed — the bug |
  | `aria-label` on `<figure>`      | `/Alt` = the label                      |
  | `aria-labelledby` → the caption | `/Alt` = the caption's text             |
  | `role="none"` on `<figure>`     | wrapper not tagged **— do not use**     |

  `role="none"` is the trap. It removes the unnamed `/Figure`, so it clears the
  failure — and a mermaid figure's `<svg>` is not tagged either, so the whole
  diagram leaves the structure tree rather than being named. It reads as a fix
  and is a deletion.

  A new `figure-name-filter.lua` sets `aria-labelledby` on every figure,
  pointing at its own caption, so the name is derived from text already on the
  page rather than copied into an attribute that can drift from the caption
  beside it. It runs after `mermaid-figure-filter.lua`, which is what turns a
  mermaid block into a figure in the first place.

- **Mermaid diagrams are named in HTML too, not just in the PDF.** The drawn
  diagram is an `<svg>` full of shapes and loose label text with no name of its
  own; a screen reader walked into it and read the node labels as stray words.
  The container now takes `role="img"` and the caption as its `aria-label`.
  In the PDF this promotes the diagram to a named `/Figure` of its own, so the
  diagram is in the structure tree instead of absent from it.

- **A mermaid caption is one paragraph again, not one per word.**
  `pandoc.Caption` takes blocks first and was being handed inlines, so pandoc
  coerced the list and every `Str` and `Space` became a block: one
  `<figcaption>` holding four of them. Found while wiring the fix above, which
  reads the caption's structure.

- **A mermaid block with no caption, or an empty one, is now named too.** The
  default caption ("Diagram") was used but never written back to the block, so
  `data-caption` -- which the page script reads to name the drawn diagram --
  existed only when the author had written a caption. A captionless block got a
  named `<figure>` around an anonymous diagram.

  `caption=""` was worse: in Lua the empty string is truthy, so the existing
  `or 'Diagram'` default never fired for it, and the figure reached the page
  with an empty `<figcaption>` and no accessible name at all -- the exact
  PDF/UA failure this release removes, still reachable through the most natural
  way to ask for no caption. Both were found by review after the fix above was
  written, and both are now normalized in one place that every consumer reads.

- **No change for table headers, deliberately.** The ticket recorded all 20
  `/TH` on a real deck reaching the PDF with `/Scope: None`, and a Lua filter
  was planned to add `scope="col"`. Measured on that same PDF, every one of
  them already carries `/Scope /Column`: Chromium infers it from `<thead>`, and
  infers `/Row` for a row-header table too. So nothing was added. What was
  added is a test pinning it, because the inference depends on table shape and
  would be lost silently by a table with no `<thead>`.

## 0.14.0

- **A document that draws no diagram no longer carries the mermaid bundle.**
  Every generated page inlined `mermaid.min.js` unconditionally. It is 3.5 MB.
  A handout with no diagram in it weighed 5.19 MB, of which 3.57 MB was a
  library the page never called -- and none of the handouts in the course
  repository this tool was written for draw diagrams. They are all in that
  state. Dropping it takes such a page to 1.62 MB, a 69% cut.

  This is not a build-time fix and was not shipped as one. Measured both ways,
  alternating, on a real package:

  ```
  with the bundle     1776 ms / 1738 ms
  without             1756 ms / 1762 ms
  ```

  V8 does not charge for a script it never runs. What the weight cost was the
  point of the file: `embed-images.lua` and the asset inlining exist so a
  handout is one self-contained file somebody can email, and a 5 MB attachment
  that should be 1.6 MB is three times harder to send.

  `mermaid-figure-filter.lua` now sets a `has-mermaid` metadata flag and the
  template gates the bundle on it. The flag covers every route to a `.mermaid`
  element, not just the fenced code block the filter already rewrote -- the
  page script matches the class on any element, and four other things produce
  one. `<div class="mermaid">` and a fenced `::: {.mermaid}` both arrive as a
  `Div`; `<span class="mermaid">` arrives as a `Span`, because pandoc's
  `native_spans` and `native_divs` extensions parse those into the AST rather
  than leaving them raw. Only the tags pandoc has no native element for --
  `<pre class="mermaid">`, a custom element -- stay raw, as a `RawBlock` or a
  `RawInline`. Those two are matched by a plain substring search that
  deliberately over-matches: a false positive inlines a bundle the page does
  not need, which is what every page did before this release, while a false
  negative renders a diagram blank.

  The `Span` case is worth calling out because the markdown and the rendered
  HTML both say `<span`, so it reads like raw HTML from either end of the
  pipeline; only the AST in between disagrees. It was found by a review comment
  pointing at the right gap with the wrong mechanism, and settled by
  instrumenting the filter rather than by reasoning about it.

  The driver script is **not** gated, only the bundle. It no-ops on a page with
  no `.mermaid` element, and it is what sets `window.__mermaidReady` -- which
  `html-to-pdf.js` blocks on. Gating it too would not fail a PDF build, it
  would hang one for the full two-minute timeout first. `renderMermaid()` also
  now bails out with a console error rather than throwing if it somehow finds a
  diagram with no bundle loaded, for the same reason: an uncaught throw escapes
  the async function and `__mermaidReady` is never set.

  No action needed in a consuming repository. Re-run `stencil gen` and rebuild;
  pages with diagrams are byte-for-byte unchanged.

## 0.13.0

- **The header's PDF text layer no longer jams words together.** Extracted
  text read `IssuedSep 05`, `DueSep 12`, `Lovelace·Grace Hopper` and
  `001·Fall 2026` on a 0.12.0 build. Anyone copying from a PDF, or reading one
  with a screen reader, got run-on words in the header; the rendered page
  looked perfect, and so did the HTML, which has had real spaces there since
  0.11.0.

  The measured rule, which is narrower than it looks: **a whitespace-only text
  node sitting between two inline boxes never reaches the PDF text layer.**
  Chromium paints the advance and emits no glyph. Substituting a non-breaking
  space changes nothing -- a lone `&#160;` between two boxes is dropped exactly
  the same way. What survives is a space inside a text run that also carries a
  printing character, which is why `.doc-fact-sep`'s `&#160;&middot;&#160;`
  already came through and why `Points&#160;50 pts` did.

  So every gap moved inside its neighbour: `<span class="doc-label">Issued&#160;</span>`,
  and each author carries its own trailing space with the middot separator
  taking the other side. A `<wbr>` after each separator gives back the line
  break the non-breaking pair removes -- six names still wrap between names,
  on screen and in print, rather than overflowing.

- **The space between two authors is now in the accessibility tree as well.**
  The separator is `aria-hidden`, which removes its whole subtree, so a space
  parked inside it would reach the PDF and not a screen reader -- the same
  defect wearing the other coat. Chromium's accessibility tree for the 0.12.0
  markup read `Author`, `Ada Lovelace`, `Grace Hopper` as adjacent runs with
  no whitespace anywhere between them; it now reads `Author&nbsp;`,
  `Ada Lovelace&nbsp;`, `Grace Hopper&nbsp;`.

- **Something in this repository finally looks at a PDF.** `make check-access`
  is pa11y, an HTML checker, so until now "the PDFs pass WCAG" was unverified
  rather than verified-good. `tests/test_pdf.py` now asserts on a real
  generated PDF: that both headers extract as words, and that the document is
  tagged (`/StructTreeRoot`, `/MarkInfo /Marked true`), declares its language,
  and sets `/ViewerPreferences /DisplayDocTitle` so a reader announces the
  title rather than the file name. Those four held before this release only
  because they are Puppeteer's defaults; `html-to-pdf.js` now pins
  `tagged: true` explicitly, and turning it off fails the test.

- **What changed on the page.** The context separator's gap is held where it
  was -- 0.4rem of margin a side became 0.12em plus the non-breaking space now
  in the content, measured glyph-to-glyph at 14.17px before and 14.22px after
  at letter print width. The label gap is the one real change: it tightens by
  0.39-0.47px, because a non-breaking space rendered in the label's semibold
  face is slightly narrower than the regular-weight word space it replaced.
  That is 5% of the gap, and correcting it per font would over-fit to this
  one. Nothing else moves.

- **Copy-paste note.** The header's gaps are U+00A0, so text pasted out of a
  handout carries non-breaking spaces where it used to carry ordinary ones.
  Most tools treat them as spaces; some -- shells, diff tools, form
  validators -- do not. This was already true of `50&nbsp;pts` since 0.11.0
  and is now true of the labels and the author separators.

- **Knowingly left alone.** The rule above applies to any painted gap, and
  only the header was fixed. `.side-by-side` and `.columns` put two blocks of
  text on one baseline behind a `gap` with no character behind it, and the
  facts line's accessibility boundary comes from `.doc-facts` being
  `display: flex` -- its children blockify -- rather than from a character.
  Both are recorded rather than repaired here.

- `pypdf` is now bounded above. The suite asserts on extracted text, and
  pypdf's space and line-break insertion is a layout heuristic that has
  changed across majors.

## 0.12.0

- **A light/dark/system theme control on every HTML page**, top right on a
  document and in the toolbar on a deck. Three segments in a `radiogroup`, so
  all three states are visible and the pressed one says which is active. The
  choice persists per storage origin; over `file://` browsers disagree about
  what that means, so it may or may not follow you between handouts -- serve
  the folder over HTTP if that matters.

  The theme resolves in a synchronous inline script ahead of every stylesheet.
  Anything deferred paints light first, which is a white flash for a dark
  reader on every load. `data-theme` carries the resolved value the stylesheet
  reads and `data-theme-pref` the preference the control shows; they differ
  whenever the preference is "system".

- **PDFs are light by construction, not by configuration.** Every colour in
  both stylesheets is a token, and every dark value lives in one `@media screen` block. Print does not match `@media screen`, so the light `:root`
  values are the only ones a printer sees — no second copy of the palette to
  keep in sync, and it holds for a browser's own Ctrl+P as well as for
  Puppeteer. Measured with `data-theme="dark"` still set: screen paints
  `rgb(27,31,39)`, print paints `rgb(255,255,255)`.

  Bootstrap's `data-bs-theme` is deliberately unused. Its dark block sits
  inside the vendored CSS and is *not* wrapped, so that attribute would put a
  second dark mechanism in the page that print cannot switch off. The
  variables these pages surface are mapped from stencil's own tokens instead.

- **Diagrams follow the theme.** Mermaid bakes its colours into the SVG at
  draw time, so a theme change redraws them, with `themeVariables` read off
  the live tokens rather than a second palette that would drift.
  `__mermaidReady` is still set once and never cleared — `html-to-pdf.js`
  blocks on it, and a PDF build never changes theme.

- **`make check-access` runs pa11y in both themes**, clicking the real control
  rather than injecting state. It found three failures the first time it ran:
  `--accent` was serving as both ink and fill, so dark turned the deck's
  Present button white-on-pale at 2.35:1; `--text-faint` had been 2.60:1 for
  as long as it was `::before` content the checker could not see; and links
  stayed at Bootstrap's light-mode blue because 5.3 resolves them through
  `--bs-link-color-rgb`, not `--bs-link-color`.

- **The header reads as a header.** The points badge is gone from beside the
  title — it read as UI chrome on a document that has none — and Points joins
  Issued and Due on one line spread across the width. The byline is two
  stacked lines, authors above facts, so a team's `author:` list has room.
  Each name is its own element, so a list wraps between names and never
  mid-name. The subtitle is italic and sits tight to the title. `program`,
  `section` and `term` share one line, the section attaching to its program
  with a full stop — `CS 425.001 · Fall 2026`.

  The gaps in that header are real characters. A flex `gap` paints space and
  leaves nothing behind, so the header copied out of the page read
  "AuthorAda Lovelace". The same is not yet true of the PDF text layer
  (stn-40n).

## 0.11.0

- **`points` renders as a badge beside the title.** `points: 50` reads `50 pts`
  and `points: 1` reads `1 pt`; the plural is decided in the lua filter, which
  can compare a value where a pandoc template cannot. A non-numeric value
  renders verbatim, so `points: "extra credit"` works.

  This replaces a `subtitle: "Points: 50"` convention, which overloaded a
  presentation field with a data field so that nothing could query or validate
  it.

- **`due` is a new key, and `date` now renders as `Issued`.** Both take
  `yyyy-mm-dd` or `yyyy-mm-ddThh:mm` and render as `Due Sep 12 · 23:59`, with
  the time shown only when one was written. Two bare dates in one column cannot
  be told apart, which is why the build stamp grew a label it never needed while
  it was the only date there.

  Neither prints the year — a handout is read inside a term the reader already
  knows. Unprinted rather than lost: each date is wrapped in a `<time datetime>`
  carrying the full ISO string.

  `date` accepted any string before this and now accepts only the grammar above,
  a behaviour change with nothing in tree relying on the old latitude.
  Validation gates on shape, then field ranges, then the calendar, so the
  message names what actually broke rather than restating the grammar every
  time. The calendar gate is an `os.time` round-trip rather than a
  days-in-month table: `os.time` normalizes a day the month does not have, so
  comparing the fields back is what detects it, and the leap rules come from
  the platform `mktime` instead of a century rule here that nothing would
  exercise until 2100.

- **The document header is a grid, and its source order is its reading order.**
  The byline has always sat under the title on screen; in source order
  `.doc-context` came between the subtitle and the author, which is the order a
  screen reader announces and `pdftotext` extracts. Nothing moves visually. On a
  narrow viewport the header now stacks identity, byline, context.

## 0.10.3

- **Stopped naming a private consumer.** AGENTS.md, four comments in
  `generate.py` and three test files described what one course repository asks
  of stencil, by name: which of its configs set which custom keys, that its
  shared `Makefile.j2` composes stencil's partials, what its
  `front_controller` defaults to.

  A tool has no business carrying knowledge of a private consumer, and the
  copy it carried had already drifted -- it said three configs pointing at one
  templates directory, and there are four. That is what documentation in the
  wrong repository does: nobody working in either place is looking at it when
  the fact changes.

  Every reason survives, stated generally: a project *may* point several
  configs at one templates directory, a consumer's composition *may* include
  stencil's partials, a key defaulted to `False` *does* put the literal
  "False" where a filename belonged. The consuming repository now records its
  own arrangement in its own AGENTS.md.

## 0.10.2

- **Removed `copy_files` from STENCIL.md.** It described a per-package field
  for copying static files into a package, and no code has ever implemented
  it -- `grep -rn copy stencil/*.py` returns nothing. Setting it produced no
  files and no error, which is the exact failure the config validation exists
  to prevent for `template_env` keys and `when:` names: a key that reads and
  writes nothing, silently.

  Deleted rather than implemented. Nothing asked for it, and the one thing
  that wanted to copy a file -- a config-level `brand` logo, in 0.10.0 --
  needed to copy implicitly and register the copy in `.gitignore`, which a
  general opt-in list would not have given it. If a second caller ever turns
  up, that is the point to build the general thing, with two real uses to
  shape it (stn-4iv).

## 0.10.1

- **The two config-brand fallbacks were emitted on one line.** The Jinja
  environment sets `trim_blocks`, which eats the newline after a block tag, so
  the `{% endif %}` ending the first declaration pulled the second up beside
  it. With neither configured that produced `= nillocal CONFIG_BRAND_ALT`,
  where `nillocal` lexes as a single identifier -- so `CONFIG_BRAND` read an
  undefined global and `CONFIG_BRAND_ALT` became a global rather than a local.

  Both still evaluated to nil and every page still rendered, which is why the
  whole test suite passed over it. Inline conditionals now, which no block tag
  can trim, and a test asserts the two are separate `local` statements.

## 0.10.0

`brand` can be set once for a project instead of in every document.

- **`brand` and `brand-alt` in `.config.yaml`**, config-wide or per package,
  resolved the way `lang` is: package first, then config-wide, and front matter
  over both. The default only fills the key in when a document left it out,
  after which it is treated exactly as though the document had written it --
  which is what keeps a configured brand and a written one from drifting into
  two behaviours.

  A package that names its own brand does **not** inherit the config's
  `brand-alt`. "This logo, no alt yet" is what that means, and inheriting there
  would label one logo with another's name.

- **A configured logo is copied into every package that renders markdown**,
  without being asked to, and the copies are added to the managed `.gitignore`.
  The folders stencil generates are routinely handed to someone as a project of
  their own, separate from the repository that produced them -- so a logo
  living only next to `.config.yaml` would leave the recipient with a document
  referring to a file they were never given. A copy per package is the cost of
  each folder standing alone, and the filter names the copy rather than the
  source path so nothing looks outside the folder.

  Paths are resolved relative to the config file's directory. A config-level
  logo with no `brand-alt`, or one that is not a file, fails at `stencil gen`
  rather than at render -- a config mistake should reach whoever ran the
  generator, not whoever builds a document three packages away.

## 0.9.0

A document can carry the mark of what it belongs to.

- **`brand` front matter, a name or a logo.** It renders at the top of the
  header's right-hand column, above `program`, pushing the rest of the context
  and the byline down. On a deck it sits above the title slide's context line.

  The value decides which it is, rather than a second key: a `file://` prefix
  or an image extension makes it a picture, anything else is a name. So
  `St. Louis U.` stays text.

  A picture is inlined as a `data:` URI like any other image. `file://img/logo.svg`
  is rewritten to `img/logo.svg` before it is resolved, because
  `embed-images.lua` classes anything matching `scheme://` as remote and leaves
  it alone — a `file://` URI would have been the single spelling that never got
  bundled, leaving the page carrying a path and `make pdf` failing on an asset
  it could not fetch.

  A logo is capped on both axes and never distorted -- whichever cap binds
  first, the other dimension scales with it. Verified by measurement rather
  than by reading the spec: a 900x60 wordmark renders 223.95x14.93 and a
  50x600 crest renders 3.66x44, each at its exact natural ratio. A test guards
  the `width: auto` / `height: auto` pair that makes that true, because pinning
  either one turns the cap into a stretch silently, and only for whichever
  logo happens to be the wrong shape.

- **`brand-alt` is required when `brand` is a picture**, and the build fails
  naming the key when it is missing. Deliberately not defaulted to `alt=""`: a
  logo is frequently the only thing naming the institution on the page, so an
  empty alt drops that for a screen reader, and `make check-access` runs pa11y
  at WCAG 2.1 AA. A name needs none — it is already text.

## 0.8.0

Ordered task lists get the styling bullet ones have had.

- **`1. [ ]` no longer renders with the box jammed against the text.** Pandoc
  puts `class="task-list"` on a `<ul>` only; an ordered task list arrives as a
  bare `<ol type="1">` carrying the identical
  `<label><input type=checkbox>` items. Every rule from 0.4.0's task-list work
  was spelled `ul.task-list`, so an ordered list matched none of them — no gap
  beside the control, no accent tint, and a checked box printed empty because
  the print opt-out was scoped the same way.

  The two are still styled differently, on purpose. A bullet list's disc is
  decoration, so the checkbox replaces it and the text hangs off it. An ordered
  list's number is content the author asked for, so it stays and the box takes
  room beside it: `1. [x] done`, not `[x] done`.

  The gap is on both sides of the control. With only the reported side fixed,
  the box lands against the ordinal instead and the same complaint reads
  `1.[ ] text`.

## 0.7.0

A document says what it belongs to, and `show_date: no` means no.

- **`program`, `section` and `term` front matter.** The institutional context a
  handout carries: what it belongs to, which instance of that, and when. A
  document renders them top right, opposite the title; a deck puts them on one
  line above it. The names avoid `course`/`semester` deliberately — they read
  the same to an instructor and to anyone running a training program.

- **The document header is two columns.** Title and subtitle on the left of the
  first row with the context opposite them, author on the second with the date
  opposite. A file setting everything costs two lines instead of five. Below
  roughly 640px it collapses back to one left-aligned stack.

  The date moved out of `.doc-meta` and into `.doc-date`, so the middot that
  joined it to the author list is gone.

- **`show_date: yes` stamps the build date.** And `show_date: no` withholds it,
  which is worth stating because it did not used to. Pandoc reads YAML 1.2,
  where `true` and `false` are the only booleans, so `no` arrived at the
  template as the *string* `"no"` — as truthy as `"yes"`. The new
  `frontmatter-filter.lua` settles it before any template asks. An explicit
  `date:` still wins; the stamp is the build host's day, not the container's.

- **The package `name` is no longer injected as a document's `course`.** It was
  reaching pandoc as `--metadata course=`, which overrides front matter, so an
  author who set `course:` had it silently discarded — and a package name was
  never a course in the first place. `name` is a `stencil --list` label again.
  This is the one behavior change to look at when upgrading: a deck that showed
  its package name above the title now shows nothing there until it sets
  `program:`.

- **A deck's title no longer prints at slide-heading size** (stn-tum). The title
  slide's `.deck-title` is an `h1`, and `.slide > h1:first-child` outscores
  `.slide--title .deck-title` on specificity — so the title was sized as a
  section heading exactly when no context line preceded it. Adding a `course:`
  pushed a `<p>` in front and gave the title its size back, which is why it
  looked like a print-only bug. Both media now exclude the title slide.

- **`lang` and `dir` front matter, with a config-wide default.** Both templates
  hardcoded `<html lang="en">`, so a page written in Spanish asserted it was
  English — a screen reader pronounced it with English phonetics and sounded
  confidently wrong rather than obviously broken. Resolved narrowest first: a
  document's `lang:`, then a package's or the config's, then `en`. The
  attribute is never omitted, so nothing that renders today changes.

  `dir` is front matter only and emitted only when set. There is no
  config-level spelling for it because a package's `dir:` is already its output
  subdirectory, and giving one key two meanings is the mistake this release
  undoes elsewhere.

- **AUTHORING.md lists the keys stencil does *not* render.** `abstract`,
  `keywords`, `lang`, `toc` and the rest parse fine and do nothing, which reads
  a lot like working.

## 0.6.0

One type scale instead of two, and a way to ask which stencil you are running.

- **Print rescales the root rather than restating every size.** Sizes are in
  `rem` throughout, and a `rem` is the root font size, so `@media print` now
  sets `html { font-size: 9.78pt }` once and the whole scale follows. 9.78pt is
  what puts body text, at `1.125rem`, on the 11pt it has always printed at.

  This removes the defect class 0.5.0 fixed three instances of. There is no
  longer a second list of sizes that can fall out of step with the first, and a
  test now fails if print restates a size the shared scale already gives.

  Two visible consequences. Print inherits the screen proportions, so a
  document's title prints at 21.5pt rather than 18pt. And the root carries
  Bootstrap's `rem`-based padding and margins with it, so spacing tightens by
  about 18% and documents get shorter — a 12-page handout measured 11 pages.
  Everything else lands within ~2% of 0.5.0.

- **`stencil version`.** Prints the installed version, and answers before
  reading a config, because the question is usually asked when an install is
  suspect. The version now lives in `stencil/__init__.py` with `pyproject.toml`
  reading it, so the module and the installed distribution cannot disagree
  except by a stale install — which is the thing the command exists to reveal.
  Four course venvs were found running three different stencils while a
  template fix appeared not to work.

## 0.5.0

Typography fixes in the document stylesheet. Every generated document and deck
renders differently, on screen and on paper, so this is a minor bump.

Three instances of one mistake: a size stated in `pt` inside `@media print` for
some selectors, with the neighbouring ones left on `rem`. A `rem` is measured
against the root font size, not against the element beside it, so the two units
do not compare and the hierarchy inverts once the page is printed.

- **A document's subtitle printed larger than its title.** `.doc-title` scaled
  to 18pt while `.doc-subtitle` kept its screen `1.8rem` — 21.6pt. The student's
  name printed 20% larger than the assignment. Only the PDF was wrong, which is
  why it lasted (`stn-09g`).
- **The printed heading scale inverted at `h4`.** `h1`, `h2` and `h3` had `pt`
  sizes; `h4`, `h5` and `h6` did not, so they kept Bootstrap's `rem`. Measured
  off a real PDF: `h1` 21.3, `h2` 18.7, `h3` 16.0, then `h4` 22.3 — larger than
  `h1` and just under the title.
- **On screen a section heading outranked the document title.** stencil set no
  size for `h1`–`h6` at all, so they inherited Bootstrap's viewport-relative
  scale, `calc(1.375rem + 1.5vw)`. `h1` measured 39.4px against the title's
  35.2px, and resized with the window while the title stayed put.

The subtitle is also no longer bold: at `1.8rem`/700 under a `2.2rem`/700 title
it read as a second title rather than a subordinate line, and being the longer
of the two it took the eye. It is now `1.4rem`/400.

Headings now state their own sizes on screen and in print, stepping down
monotonically under the title in both, with the screen steps tracking the print
block's ratios against body text.

Known and not fixed: a deck's title prints at the same size as a slide heading,
and whether it does depends on an unrelated frontmatter key (`stn-tum`).

## 0.4.0

An undefined name in a template is now an error rather than the empty string,
which is a behaviour change for anyone whose config or templates were relying
on the old silence. No generated output changes: every package of six consumer
configs was generated before and after, 530 files, byte-identical.

- **A consumer's composition template is a checked interface.** Overriding
  `Makefile.j2` or `docker-compose.yml.j2` and including stencil's partials is
  the intended way to extend a build, and the keys those partials read are now
  recorded in `tests/test_template_contract.py`. Stencil's own suite fails when
  that set changes, and `StrictUndefined` makes a stale composition fail the
  consumer's `stencil gen` instead of emitting a Makefile with a recipe
  missing.
- **Custom keys can be declared.** A config-level `template_env` declares a key
  and supplies the value every package gets unless its own `template_env`
  overrides it. This is what lets one `templates_dir` serve several configs
  that each set a different subset of the flags their shared templates read.
- **Configuration mistakes are rejected instead of ignored.** A `when:` naming
  a key that is neither derived nor declared anywhere, and a `template_env` key
  whose name appears in no template, both now fail with the key named. Each was
  previously silent: the guarded template was skipped for every package, or the
  key sat there doing nothing.

Upgrading: if `stencil gen` now reports an undefined key, declare it in the
config-level `template_env` rather than setting it to `false` on every package
— a key some package sets stays undefined for the ones that do not, so
`{{ key | default('x') }}` keeps working, and a concrete `false` would break it.

Removed: `TEMPLATE-PACKS.md`, a design for packaging scaffolding as a
dependency. It recommended against building the thing it described, and the
reasoning is preserved in `stn-zza` and in git history.

## 0.3.0

The first tagged release. Not a set of changes so much as a name for what the
tool already does, so "the stencil that built this handout" is answerable.

What is in it:

- **Scaffolding generator.** Jinja2 templates rendered into per-package output
  directories from a YAML config, with a templates search path — every
  configured `templates_dir`, then the bundled set, first match wins — so a
  consuming project overrides one template without vendoring all of them.
- **Documents and decks.** Two pandoc HTML templates over shared partials.
  Decks are grouped by `slide-sections.lua` and CSS rather than by pandoc's
  slide-show support, which does not apply here.
- **A PDF stage.** `make pdf` prints the generated HTML through headless
  Chromium, with the print stylesheet as the page geometry rather than as
  decoration.
- **Self-contained output.** Bootstrap, highlight.js, Mermaid and the webfonts
  are vendored and inlined at `stencil gen` time, so a handout makes no network
  request and a build does not depend on a CDN being up.
- **Hidden variants.** `WITH=hidden` builds an answer key from the same source,
  with the filter order arranged so citeproc cannot leak a hidden citation's
  source into the visible build's reference list.
- **A pinned toolchain.** `docker.io/pandoc/core:3.10.0.0`, asserted by a test
  rather than trusted, so a pandoc release cannot change rendered output or
  break CI on a commit that changed nothing.
- **An authoring contract.** `AUTHORING.md` for the person writing markdown,
  `STENCIL.md` for the person configuring a package.
- **A test suite and CI.** Unit assertions on the rendered scaffolding, plus
  container-backed integration tests behind an `integration` marker that skip
  rather than fail when there is no runtime.

Fixed just before the tag:

- A doc package built only from `package_sources` generated five shared page
  files that `clean` and the managed `.gitignore` section could not see, and a
  `pkg` target naming a pandoc template that was never written (`stn-633`).
