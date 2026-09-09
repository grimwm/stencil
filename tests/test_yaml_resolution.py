"""Pin pandoc's actual YAML boolean resolution -- a characterization test.

This module is NOT test-driven-development in the red/green sense: nothing
here drove an implementation, and it passes on the first run, because pandoc
already behaves this way. Its job is to fail loudly the day a PANDOC_IMAGE
bump changes that behaviour, instead of letting the change rot into the
templates and prose silently the way it did before this ticket.

Three prose sites in this repository said that "pandoc reads YAML
1.2, where true and false are the only booleans". MEASURED against whatever
pandoc PANDOC_IMAGE pins -- 3.10.0.0 when this was written, and deliberately
read from pipeline rather than hardcoded below -- with a Lua probe reporting
the exact values pandoc.utils.type() and pandoc.utils.stringify() hand back,
that claim is false. pandoc resolves exactly YAML 1.1's ENUMERATED boolean
forms -- three casings and no others:

    boolean       y Y yes Yes YES   n N no No NO
                  on On ON   off Off OFF
                  true True TRUE   false False FALSE
    string ""     null Null NULL, ~, and a blank value (all indistinguishable)
    nil           the key absent entirely
    Inlines       everything else, including mixed case (nO yEs yeS ofF oN
                  tRue falsE nUll), bare numbers and words (1 0 2 none), and
                  anything quoted ("no" "null")

"(any case)" -- a phrase this repository used to repeat -- is specifically
wrong: mixed case is a plain Inlines value, not a boolean. `nO` is not `no`.

Nothing here is a behaviour bug. truthy() in frontmatter-filter.lua.j2 checks
the real-boolean branch first and falls through to a lowercased FALSE-word
table, so every spelling above still resolves the way an author expects
regardless of which YAML revision a future pandoc image happens to pick. This
module exists so that if a future image ever does pick a different one, a
test names it instead of a paraphrase quietly going stale again.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from stencil import pipeline

pytestmark = pytest.mark.integration

PROBE_MARKER = "<<<YAML-RESOLUTION>>>"

# One entry per YAML spelling under test: the front-matter key, the literal
# text written after "key:" in the fixture (None means nothing follows the
# colon at all -- the blank-value case, which is not the same source text as
# an empty string), and the three values the probe is expected to measure.
#
# This table is the single source of truth: the fixture's front matter, the
# probe's explicit key list, and the assertions below are all derived from
# it, so a spelling cannot be added to the fixture without also being
# asserted on, and vice versa.
SPELLINGS: list[tuple[str, str | None, str, str | None, str | None]] = [
    # Enumerated booleans -- lowercase, Titlecase, ALL-UPPERCASE, and only
    # those three casings. This is the headline finding: pandoc resolves
    # YAML 1.1's enumerated forms, not "true/false in any case".
    ("k_y", "y", "boolean", "boolean", "true"),
    ("k_Y", "Y", "boolean", "boolean", "true"),
    ("k_yes", "yes", "boolean", "boolean", "true"),
    ("k_Yes", "Yes", "boolean", "boolean", "true"),
    ("k_YES", "YES", "boolean", "boolean", "true"),
    ("k_n", "n", "boolean", "boolean", "false"),
    ("k_N", "N", "boolean", "boolean", "false"),
    ("k_no", "no", "boolean", "boolean", "false"),
    ("k_No", "No", "boolean", "boolean", "false"),
    ("k_NO", "NO", "boolean", "boolean", "false"),
    ("k_on", "on", "boolean", "boolean", "true"),
    ("k_On", "On", "boolean", "boolean", "true"),
    ("k_ON", "ON", "boolean", "boolean", "true"),
    ("k_off", "off", "boolean", "boolean", "false"),
    ("k_Off", "Off", "boolean", "boolean", "false"),
    ("k_OFF", "OFF", "boolean", "boolean", "false"),
    ("k_true", "true", "boolean", "boolean", "true"),
    ("k_True", "True", "boolean", "boolean", "true"),
    ("k_TRUE", "TRUE", "boolean", "boolean", "true"),
    ("k_false", "false", "boolean", "boolean", "false"),
    ("k_False", "False", "boolean", "boolean", "false"),
    ("k_FALSE", "FALSE", "boolean", "boolean", "false"),
    # A genuine Lua string, stringifying to "", and indistinguishable from
    # one another -- null/Null/NULL, ~, and a blank value all collapse to
    # the same thing once they reach the filter.
    ("k_null", "null", "string", "string", ""),
    ("k_Null", "Null", "string", "string", ""),
    ("k_NULL", "NULL", "string", "string", ""),
    ("k_tilde", "~", "string", "string", ""),
    ("k_blank", None, "string", "string", ""),
    # Inlines: bare numbers and words that are not enumerated boolean forms.
    ("k_1", "1", "table", "Inlines", "1"),
    ("k_0", "0", "table", "Inlines", "0"),
    ("k_2", "2", "table", "Inlines", "2"),
    ("k_none", "none", "table", "Inlines", "none"),
    # Inlines: mixed-case spellings of the boolean words. THE ANTI-VACUITY
    # POINT this ticket exists to correct -- these are strings, not booleans,
    # despite differing from an enumerated form only in casing.
    ("k_nO", "nO", "table", "Inlines", "nO"),
    ("k_yEs", "yEs", "table", "Inlines", "yEs"),
    ("k_yeS", "yeS", "table", "Inlines", "yeS"),
    ("k_ofF", "ofF", "table", "Inlines", "ofF"),
    ("k_oN", "oN", "table", "Inlines", "oN"),
    ("k_tRue", "tRue", "table", "Inlines", "tRue"),
    ("k_falsE", "falsE", "table", "Inlines", "falsE"),
    ("k_nUll", "nUll", "table", "Inlines", "nUll"),
    # Inlines: a quoted value forces a string in the YAML sense, which is
    # NOT the same thing as pandoc's own "string" Lua type above -- a quoted
    # scalar still arrives as Inlines, because quoting only tells the YAML
    # parser not to enumerate it as a boolean.
    ("k_quoted_no", '"no"', "table", "Inlines", "no"),
    ("k_quoted_null", '"null"', "table", "Inlines", "null"),
    ("k_hello", "hello", "table", "Inlines", "hello"),
]

# The absent-key case is not a front-matter line at all, so it is kept out of
# SPELLINGS (which drives what gets written into the fixture) and given its
# own record for the assertions.
ABSENT_KEY = "k_absent"

EXPECTED: dict[str, dict[str, str | None]] = {
    key: {"lua_type": lua_type, "pandoc_type": pandoc_type, "stringify": stringify}
    for key, _, lua_type, pandoc_type, stringify in SPELLINGS
}
EXPECTED[ABSENT_KEY] = {"lua_type": "nil", "pandoc_type": None, "stringify": None}

# Every key the probe iterates, including the one that is never written to
# the fixture -- an explicit list because pairs() over Meta returned nothing
# when tried against this pandoc: Meta's table does not support that kind of
# iteration, only indexed lookups by name.
PROBE_KEYS = [key for key, *_ in SPELLINGS] + [ABSENT_KEY]

# The probe filter. Reports, per key in PROBE_KEYS, the plain Lua type(), the
# richer pandoc.utils.type(), and pandoc.utils.stringify() -- guarding the
# absent key explicitly, because pandoc.utils.stringify(nil) does not return
# an error Lua value, it makes the whole pandoc process exit 83 with "Cannot
# get Attr from TypeNil". pandoc.utils.type(nil) is safe and returns nil, so
# only the stringify call needs the guard.
#
# pandoc.json.encode is used rather than hand-building the JSON line, since
# it already handles quoting and escaping for every value reported here
# (Lua reserves that job for a documented one-line call rather than for a
# fragile string of concatenations).
#
# Interpolated with str.replace over __TOKENS__ rather than with %-formatting
# or .format(). Lua uses BOTH of the characters those reserve: `%` starts a
# pattern class and is string.format's own escape (frontmatter-filter.lua.j2
# has `string.format("%s %02d", ...)` a few lines from here), and `{` opens
# every table constructor. Either one would make the next person to extend
# this probe hit a TypeError or a KeyError raised from fixture setup, a long
# way from the line they actually edited.
PROBE_LUA = """
local KEYS = {__KEYS__}

function Meta(m)
  local report = {}
  for _, key in ipairs(KEYS) do
    local v = m[key]
    if v == nil then
      report[key] = {
        lua_type = "nil",
        pandoc_type = pandoc.json.null,
        stringify = pandoc.json.null,
      }
    else
      report[key] = {
        lua_type = type(v),
        pandoc_type = pandoc.utils.type(v),
        stringify = pandoc.utils.stringify(v),
      }
    end
  end
  io.write("__MARKER__" .. pandoc.json.encode(report) .. "\\n")
  return m
end
""".replace("__KEYS__", ", ".join(f'"{key}"' for key in PROBE_KEYS)).replace(
    "__MARKER__", PROBE_MARKER
)


def _front_matter_line(key: str, yaml_text: str | None) -> str:
    """One line of the fixture's front matter for a single key.

    ``yaml_text is None`` is the blank-value case: nothing at all follows the
    colon, which is a different source line from an empty string and is the
    spelling that collapses to the same MetaString "" as null/~ once pandoc
    resolves it.
    """
    if yaml_text is None:
        return f"{key}:\n"
    return f"{key}: {yaml_text}\n"


def _fixture_markdown() -> str:
    front_matter = "".join(
        _front_matter_line(key, yaml_text) for key, yaml_text, *_ in SPELLINGS
    )
    return f"---\n{front_matter}---\n\nbody\n"


@pytest.fixture(scope="module")
def yaml_resolution(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    """Run the probe once in the pinned pandoc image and return its payload.

    ONE container run for every spelling under test, not one run per
    spelling -- 30+ container starts for what a single conversion measures
    was explicitly rejected as wasteful. The probe filter and the fixture
    markdown are written to pytest's own tmp_path, never into the repository:
    this test owns a measurement, not a piece of the scaffolding stencil
    generates.

    Uses pipeline.container_runtime() and pipeline.PANDOC_IMAGE directly
    rather than pipeline.render(), whose argv is fixed to the full template
    filter chain -- there is no hook there for a one-off probe filter.
    pipeline.py is otherwise out of scope for this ticket, so the few lines
    of docker/podman argv below are duplicated by hand rather than justifying
    a change to the render harness for one caller.
    """
    runtime = pipeline.container_runtime()
    assert runtime is not None, "no container runtime found (docker, podman)"

    workdir = tmp_path_factory.mktemp("yaml-resolution")
    (workdir / "probe.lua").write_text(PROBE_LUA)
    (workdir / "fixture.md").write_text(_fixture_markdown())

    result = subprocess.run(
        [
            runtime,
            "run",
            "--rm",
            "-v",
            f"{workdir}:/workspace:z",
            "-w",
            "/workspace",
            pipeline.PANDOC_IMAGE,
            "--lua-filter=probe.lua",
            "fixture.md",
            "-o",
            "/dev/null",
        ],
        capture_output=True,
        text=True,
        # A timeout at all, because pipeline.render() has none of its own and
        # CI sets no timeout-minutes on this job: without one, a stuck
        # container hangs the run for hours instead of failing in seconds, and
        # letting subprocess.TimeoutExpired surface is exactly the loud
        # failure this module exists to produce.
        #
        # 120s rather than the ~4s this conversion actually takes, because the
        # window has to cover a COLD pull of a 340MB image on a runner that
        # has not cached it. The rest of the container suite allows 90s or
        # more for comparable work; being the one test with a tighter budget
        # would make this the first thing to flake for reasons having nothing
        # to do with what it measures.
        timeout=120,
        # Explicit, because a non-zero exit is HANDLED here rather than
        # tolerated: the assertions below turn it into a message carrying
        # pandoc's stderr, which is far more useful than the CalledProcessError
        # check=True would raise with the output swallowed.
        check=False,
    )

    line = next(
        (ln for ln in result.stdout.splitlines() if ln.startswith(PROBE_MARKER)),
        None,
    )
    assert line is not None, (
        f"the YAML resolution probe printed no result (exit {result.returncode})\n"
        f"stdout: {result.stdout[-3000:]}\nstderr: {result.stderr[-3000:]}"
    )
    assert result.returncode == 0, (
        f"pandoc exited {result.returncode} running the YAML resolution probe\n"
        f"stderr: {result.stderr[-3000:]}"
    )

    payload: dict[str, dict] = json.loads(line[len(PROBE_MARKER) :])

    # Key-set equality guards the probe against the EXPECTED table, not the
    # fixture against pandoc. The probe walks PROBE_KEYS and emits an entry
    # per key whatever pandoc parsed, so this assertion is blind to a fixture
    # that measured nothing -- it fires when the two lists drift apart, which
    # is the failure where a spelling gets added to one and forgotten in the
    # other and is then silently never asserted on.
    #
    # THE VACUITY TRAP IS CAUGHT BY THE PER-KEY ASSERTIONS BELOW, and it is a
    # real trap: an unclosed YAML fence makes pandoc exit 0 with every key
    # resolving to nil, which looks exactly like a healthy run. Verified by
    # breaking the fence deliberately -- 44 of the 46 tests here fail, the two
    # survivors being the two that assert the ABSENT key is nil, which is
    # still true when nothing parsed. That is why the per-key checks compare
    # the full measured shape rather than only asserting "not a boolean":
    # nil is not a boolean either.
    assert set(payload) == set(EXPECTED), (
        "the probe's key set does not match what the fixture wrote -- "
        f"missing: {set(EXPECTED) - set(payload)}, "
        f"unexpected: {set(payload) - set(EXPECTED)}"
    )

    return payload


# PROBE_KEYS rather than a second [key for key, *_ in SPELLINGS] + [ABSENT_KEY]:
# the probe and this parametrization must cover the same keys by construction,
# not because two derivations happen to agree today.
@pytest.mark.parametrize("key", PROBE_KEYS)
def test_spelling_matches_measured_type(
    yaml_resolution: dict[str, dict], key: str
) -> None:
    """Each spelling resolves to the exact type measured against pandoc 3.10.0.0.

    Compared by direct indexing into both dicts -- never through a
    ``.get(key, expected)`` fallback, which would make a spelling that the
    probe silently failed to report look like a pass instead of a KeyError.
    """
    expected = EXPECTED[key]
    measured = yaml_resolution[key]
    assert measured["lua_type"] == expected["lua_type"], (
        f"{key}: expected lua type {expected['lua_type']!r}, "
        f"measured {measured['lua_type']!r}"
    )
    assert measured["pandoc_type"] == expected["pandoc_type"], (
        f"{key}: expected pandoc.utils.type() {expected['pandoc_type']!r}, "
        f"measured {measured['pandoc_type']!r}"
    )
    assert measured["stringify"] == expected["stringify"], (
        f"{key}: expected stringify() {expected['stringify']!r}, "
        f"measured {measured['stringify']!r}"
    )


def test_mixed_case_is_not_boolean(yaml_resolution: dict[str, dict]) -> None:
    """Mixed-case spellings of a boolean word are a string, never a boolean.

    This is the specific claim this ticket exists to correct: the repository
    used to say booleans resolve "(any case)". They do not -- pandoc
    resolves exactly YAML 1.1's three enumerated casings (lower, Title,
    UPPER) and treats anything else, including a single letter out of case,
    as ordinary text.
    """
    mixed_case_keys = [
        "k_nO",
        "k_yEs",
        "k_yeS",
        "k_ofF",
        "k_oN",
        "k_tRue",
        "k_falsE",
        "k_nUll",
    ]
    for key in mixed_case_keys:
        pandoc_type = yaml_resolution[key]["pandoc_type"]
        assert pandoc_type != "boolean", (
            f"{key}: measured pandoc.utils.type() == 'boolean', but a mixed-case "
            "spelling must not resolve as one -- pandoc only enumerates "
            "lowercase, Titlecase and ALL-UPPERCASE boolean forms"
        )
        assert pandoc_type == "Inlines", (
            f"{key}: expected pandoc.utils.type() == 'Inlines' for a mixed-case "
            f"spelling, measured {pandoc_type!r}"
        )


def test_null_tilde_and_blank_are_indistinguishable(
    yaml_resolution: dict[str, dict],
) -> None:
    """null/Null/NULL, ~, and a blank value all collapse to the same thing.

    None of the five spellings below can be told apart once they reach the
    filter: each is a genuine Lua string, each has pandoc.utils.type() ==
    "string", and each stringifies to "". A filter that needs to treat a
    blank value differently from an explicit null cannot do it from this
    information alone -- it would need to inspect the YAML source itself.
    """
    equivalent_keys = ["k_null", "k_Null", "k_NULL", "k_tilde", "k_blank"]
    shape = {"lua_type": "string", "pandoc_type": "string", "stringify": ""}
    measured = {key: yaml_resolution[key] for key in equivalent_keys}

    # Keyed by spelling rather than carried in a list parallel to
    # equivalent_keys, so the failure message can name the spelling that broke
    # without the two sequences having to be kept in step.
    for key, entry in measured.items():
        assert entry == shape, (
            f"{key}: expected the null/~/blank shape {shape!r}, measured {entry!r}"
        )

    # The shape assertion above already implies this, since all five compare
    # equal to one literal. It is kept because it is the property the filter
    # actually relies on -- a filter cannot give `null` and a blank value
    # different meanings -- and a future edit that relaxes `shape` into
    # something per-spelling would silently drop that guarantee otherwise.
    distinct = {json.dumps(e, sort_keys=True) for e in measured.values()}
    assert len(distinct) == 1, (
        "null/Null/NULL/~/blank measured as distinguishable from one another: "
        f"{measured!r}"
    )


def test_absent_key_is_nil(yaml_resolution: dict[str, dict]) -> None:
    """A key that is never written to the front matter arrives as Lua nil."""
    entry = yaml_resolution[ABSENT_KEY]
    assert entry["lua_type"] == "nil", (
        f"expected an absent key's Lua type to be 'nil', measured "
        f"{entry['lua_type']!r}"
    )
