# Making the container tier parallel (stn-vda)

**Status:** design, pending approval
**Ticket:** stn-vda
**Baseline:** `8e17d65`, CI run 34678996838

## The problem, re-measured

`pytest (containers)` costs 9m42s of wall clock, 565s of which is the
`pytest -v` step. The ticket attributes that to "a container round-trip per
test" and proposes three fixes. Two of the three turn out to target costs that
are not there.

Everything below was measured on this branch's worktree against the pinned
`pandoc/core:3.10.0.0` image, not estimated.

### Where a container test's second actually goes

| component                      | cost   | share |
| ------------------------------ | ------ | ----- |
| `docker run` container start   | 0.30s  | 35%   |
| pandoc, full argv              | 0.56s  | 65%   |
| `stencil gen` (`make_package`) | 0.031s | 3.5%  |
| `install_fixtures`             | 0.001s | 0.1%  |

And pandoc's 0.56s decomposes further. Twenty invocations inside one warm
container, so container start is paid once:

| pandoc argv                                  | per invocation |
| -------------------------------------------- | -------------- |
| `--standalone` (pandoc's own template)       | 16ms           |
| `--standalone --template=html-template.html` | 559ms          |
| the above + all six lua filters              | 565ms          |
| the full argv, filters and `--citeproc`      | 579ms          |
| `pandoc --version` (process startup alone)   | 4ms            |

**The entire per-render cost is pandoc parsing the generated
`html-template.html`.** That file is 5.3MB, almost all of it the Bootstrap,
highlight.js, Mermaid and webfont payload inlined at `stencil gen` time. The
six lua filters and citeproc together cost about 20ms — 2% — and pandoc's own
process startup is 4ms.

### What that does to the ticket's three fixes

**Fix 2, widen the fixture scope, is refuted.** It targets `stencil gen`, which
is 31ms of an 870ms test. Collapsing every function-scoped `generate_package`
in the suite would recover about 3.5% of the container tier while introducing
shared mutable package directories across fifteen test files. Rejected on the
numbers.

**Fix 3, take date validation off the pandoc path, does not pay either.**
`tests/test_dates.py` is the largest single line item (112s, 19.8% of the CI
step). Batching its 102 builds into one container saves only the container
start — 102 × 0.30s ≈ 31s — because each of the 102 still has to parse the
5.3MB template, and a rejected shape costs the same as an accepted one (0.51s
vs 0.58s: pandoc compiles the template regardless of whether a filter later
aborts). Worse, the batch becomes one serialized 57s unit, which under the
parallel scheme below turns a spreadable file into a critical path. Rejected.

**Fix 1, pytest-xdist, is the whole win**, and it is large.

### The fourth cost, named but not taken

65% of every container test is pandoc re-parsing 5.3MB of base64 that most
tests do not assert on. Generating test packages against a slimmed asset set
would be the single biggest possible cut. It is rejected here because
`test_assets.py`, `test_fonts.py` and `test_pins.py` assert on exactly those
inlined payloads, so a lean variant means the container tier stops testing the
artifact it ships. Recorded so the measurement does not have to be taken again.

## Design

One change: run the container tier in parallel, and make the two pieces of
harness state that assume a single process correct under it.

### Distribution mode: `--dist loadfile`, not `load`

xdist's default `--dist load` spreads individual tests. This suite cannot use
it. Nine test files carry module- or file-scoped session fixtures that each pay
a container run to answer many assertions at once — `accessibility` in
`test_check_access.py` (a pa11y run over every page in every theme, 44s),
`installed` in `test_pins.py`, and the module-scoped fixtures in
`test_pdf_ua.py`, `test_pdf.py`, `test_columns.py`, `test_painted_gaps.py` and
`test_download.py`. Under `load`, a file's tests scatter across workers and
each worker rebuilds that file's expensive fixture — up to four times. That is
a regression dressed as parallelism.

`--dist loadfile` sends every test in a file to one worker, so every one of
those fixtures is built exactly once, precisely as today. The cost is coarser
bin-packing: with 565s of work, four workers and a largest file of 112s, the
floor is `565/4 ≈ 141s` rather than the largest file. `test_dates.py` at 112s
sits under that floor, so it does not bind, which is the second reason fix 3
is not worth doing.

### `pdf_workspace` builds the browser image once per run, not once per worker

`pdf_workspace` is session-scoped, and under xdist "session" means *per worker
process*. It calls `pipeline.build_browser_image`, which installs Chromium,
puppeteer and pa11y — minutes. Four workers would start four cold builds
simultaneously, with no layer cache to share because none has finished.

Two facts make a clean fix available, both verified on this branch:

- The image tag is already shared across workers. `pytest_configure` sets
  `$STENCIL_BROWSER_IMAGE_TAG` in the controller before workers spawn, and
  execnet passes `os.environ` down, so gw0 and gw1 observe the identical
  `run-<pid>-<epoch>` tag. This is load-bearing and currently untested.
- Worker basetemps are `<basetemp>/popen-gwN`, and `getbasetemp().parent` is
  therefore a run-scoped directory shared by every worker — both with an
  explicit `--basetemp` and with pytest's own default.

So: serialize the build on a lock file in that shared directory and record the
outcome in a sentinel beside it. The first worker to acquire the lock builds
and writes the outcome; the rest acquire it, see the sentinel, and skip
straight to using the tag. A failed build is recorded too, so all four workers
fail identically instead of three proceeding against an image that is not
there.

Each worker still gets its own *workspace directory* — that part is
`make_package` plus `install_fixtures`, 32ms, and it must stay per-worker
because `to_pdf` writes source files into it.

The shared directory is `getbasetemp().parent` in a worker and `getbasetemp()`
otherwise; in a serial run the parent is the system temp root, which is not
run-scoped and must not be used.

### The `--basetemp` ownership guard must exempt workers

`pytest_configure` refuses a `--basetemp` that a live pytest already owns
(stn-zim). Workers run `pytest_configure` too, with their controller's pid in
the marker and the controller very much alive, so the guard is a hazard the
moment xdist is switched on. Workers are exempted by `$PYTEST_XDIST_WORKER`.

Note that this guard does not currently fire at all, for an unrelated reason —
pytest rotates the basetemp after `pytest_configure`, deleting the marker
before any second run can read it. That is **stn-6fs**, filed separately and
deliberately not fixed here; the exemption added here is correct either way.

### Where `-n auto` is spelled

On the CI workflow's integration step, not in `addopts`. Putting it in
`addopts` would change what plain `pytest` does for every contributor, break
`-x` and `--pdb` workflows by default, and apply xdist to the fast tier where
there is nothing to win. `AGENTS.md` gains a line documenting
`pytest -n auto --dist loadfile` as the local fast path.

The risk of a CI-only flag is the one AGENTS.md names repeatedly: a code path
exercised only in CI is a code path that can silently stop working. That is
answered by testing the two harness behaviours directly in the fast tier
(below) rather than by trusting the integration job to notice.

### What does not change

The compose gate added by #86 is untouched and still fails the job when the
runner has no compose implementation. The image pull steps are untouched. No
assertion is deleted, weakened, skipped or merged: the same 983 tests run the
same checks against the same real containers.

## Testing

New fast-tier tests, in `tests/test_parallel_harness.py`, following the
inner-pytest-as-subprocess precedent `test_tmp_footprint.py` sets and the bare
`from conftest import ...` import style the suite already uses:

- Two xdist workers observe the same `$STENCIL_BROWSER_IMAGE_TAG`. Pins the
  execnet env inheritance the build lock depends on.
- `pytest_configure` does not raise in a worker process even when the basetemp
  carries a live owner marker.
- `pytest_configure` still refuses a live owner outside a worker — the stn-zim
  behaviour, unchanged.
- The build-once helper runs its build callable exactly once across concurrent
  callers, and re-raises the recorded failure to every caller when the build
  fails. Tested with an injected callable, so it needs no container runtime.

Plus the real verification: the full container tier run under `-n auto --dist loadfile` locally and in CI, compared against the 565s baseline, with
both numbers recorded on stn-vda.

## Dependencies

`pytest-xdist` (pulls `execnet`) and `filelock`, both to the `dev` extra only.
Neither reaches a generated package or a consumer. `filelock` is already
present transitively via `pre-commit` → `virtualenv`; it is declared explicitly
because depending on it by accident is how a transitive dependency becomes a
breakage. Neither is version-pinned here: they are test-harness tooling, not
part of the pinned supply chain `pipeline.py` governs, and `tests/test_pins.py`
concerns what a *generated package* installs.

## Files

- `tests/conftest.py` — worker exemption in `pytest_configure`; build-once lock
  in `pdf_workspace`; the helper it calls.
- `tests/test_parallel_harness.py` — new, the four tests above.
- `.github/workflows/ci.yml` — `-n auto --dist loadfile` on the integration step.
- `pyproject.toml` — `pytest-xdist`, `filelock` in the `dev` extra.
- `stencil/__init__.py`, `CHANGELOG.md` — 0.39.0.
- `AGENTS.md` — the local fast path, and why fixes 2 and 3 were not taken.
- Collateral: any test the parallel run exposes as order- or
  concurrency-dependent.

## Expected outcome

565s → roughly 150-180s on the `pytest -v` step; the job from 9m42s to about
3 minutes. The floor is `565/4 ≈ 141s` plus worker startup, and the four vCPUs
of an `ubuntu-latest` runner are saturated by four single-threaded pandoc
processes, so 3.2-3.5x is the honest expectation rather than 4x.
