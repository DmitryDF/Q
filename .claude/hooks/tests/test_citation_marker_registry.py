"""Citation-marker registry + drift guard — research-source-adapters S2.

What these tests are FOR, so a later reader does not mistake their scope:

  * the registry is genuinely authoritative — the prompt is RENDERED from it, not
    restated beside it (C1, and A1's guard rail against recreating locus 3 inside
    one file);
  * the drift guard catches a divergence introduced in EACH mirror separately and
    NAMES the divergent marker (C3, C4, and A5's guard rail against a guard that
    under-reaches and still passes its own tests);
  * a retired marker leaves the offered set, reaches the prompt named with its
    retirement date, and stays readable (C8, C9, C10, C11).

What they deliberately do NOT prove: that the style CHECKER then reports a retired
marker. That is a model-layer behaviour with no production writer to intercept —
the plan classifies it AI in Gate 2 and names it as a residual. These tests prove
the vocabulary and the prompt; they cannot prove what a checker does with them.

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_citation_marker_registry.py -q
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import _factcheck_engine as E  # noqa: E402


ENGINE_PATH = HOOKS / "_factcheck_engine.py"

# The engine resolves its rules mirror from `_CONFIG_ROOT` (this module's own
# location, i.e. `<config>/hooks/../`) rather than from `$HOME/.claude` — so it is
# candidate-aware: a run against a rendered candidate tree checks that tree's own
# mirrors, and a live run resolves to `~/.claude` exactly as before. For a test that
# invokes the engine from THIS tree (via ENGINE_PATH below), that candidate-aware
# resolution and the env-var overrides set here agree — both name the same tree. The
# overrides are kept anyway, belt-and-braces rather than load-bearing: they pin the
# tree under test EXPLICITLY, so the test states its own subject rather than relying
# on (and silently inheriting) the engine's own resolution to get it right.
TREE_RULES_MIRROR = HOOKS.parent / "rules" / "research-scope-framing.md"
# Since research-entry-point-enforcement S2 the reference mirror ships with the
# harness beside the /research skill, so THIS tree's copy is the one to check —
# not a Projects checkout's. (Before S2: `E._citation_projects_root() / "Skills"`.)
TREE_SKILLS_DIR = Path(
    os.environ.get("CITATION_MARKER_SKILLS_DIR")
    or (HOOKS.parent / "skills" / "research"))


def _tree_env():
    env = dict(os.environ)
    env["CITATION_MARKER_RULES_FILE"] = str(TREE_RULES_MIRROR)
    env["CITATION_MARKER_SKILLS_DIR"] = str(TREE_SKILLS_DIR)
    env["FC_ENGINE"] = str(ENGINE_PATH)
    return env


# --------------------------------------------------------------------------- #
# Registry shape
# --------------------------------------------------------------------------- #

def test_registry_has_active_and_retired_entries():
    assert E.active_citation_markers(), "registry offers no markers at all"
    assert E.retired_citation_markers(), (
        "registry holds no retired marker — S2 retires the topic-CLAUDE pair, and a "
        "retirement recorded as an absence is exactly what C11 forbids")


def test_retired_pair_is_the_topic_claude_pair():
    assert set(E.RETIRED_CITATION_MARKERS) == {
        "[stated — topic-CLAUDE:<path>:<line>]",
        "[paraphrased — topic-CLAUDE:<path>:<line>]",
    }


def test_every_retired_marker_records_its_retirement_date():
    for m in E.retired_citation_markers():
        assert m.retired_on, f"{m.form} is retired but records no date"


def test_retired_markers_are_not_offered():
    """C8: a withdrawn marker leaves the set the vocabulary OFFERS."""
    offered = {m.form for m in E.active_citation_markers()}
    for form in E.RETIRED_CITATION_MARKERS:
        assert form not in offered


def test_retired_markers_are_still_recognised():
    """C9/C10: withdrawn is not deleted — the entry survives, so a consumer deriving
    a grammar still recognises the marker and an older file still parses."""
    known = {m["form"] for m in E.citation_marker_grammar()["markers"]}
    for form in E.RETIRED_CITATION_MARKERS:
        assert form in known, (
            f"{form} vanished from the grammar — a file citing it would stop parsing, "
            "which inverts forward-only retirement")


def test_registry_covers_both_web_and_internal_sources():
    """C6: one vocabulary, both halves, side by side."""
    classes = {m.source_class for m in E.active_citation_markers()}
    assert "web" in classes
    assert "internal" in classes


def test_grammar_exposes_kinds_and_locators_as_data():
    """A1's guard rail: shaped so S12 can DERIVE a grammar rather than string-match."""
    g = E.citation_marker_grammar()
    assert set(g["kinds"]) >= {"stated", "paraphrased", "inferred", "unverified"}
    assert set(g["locators"]) >= {"url", "local-file"}
    for entry in g["markers"]:
        assert set(entry) >= {"form", "kind", "locator", "status", "retired_on"}


# --------------------------------------------------------------------------- #
# The payload shape  (S12 / A1)
#
# `payload` is exempt from the mirror drift guard by construction — neither mirror
# publishes a `payload` column, and the guard compares only fields BOTH sides
# publish. These assertions are what stands in for that comparison: they bind each
# marker's payload to its own `form` WITHIN the registry, so the two cannot drift.
# --------------------------------------------------------------------------- #

def test_a_marker_carries_a_payload_iff_it_addresses_a_source():
    """`[inferred from …]`, `[My assessment: …]` and `[unverified — …]` name no
    source, so they carry no payload; every marker that declares a locator does."""
    for m in E.CITATION_MARKER_REGISTRY:
        assert (m.locator is None) == (m.payload is None), m.form


def test_every_payload_matches_its_own_form_placeholders():
    """The A1 guard: `form` (mirror-carried, display) and `payload.parts`
    (registry-only, extraction) are maintained separately and must agree."""
    for m in E.CITATION_MARKER_REGISTRY:
        if m.payload is None:
            continue
        assert E._form_placeholders(m.form) == m.payload.parts, m.form


def test_every_payload_pattern_captures_exactly_its_declared_parts():
    """A pattern that captured something else would extract a payload the registry
    does not describe — the drift this field exists to prevent, one level down."""
    for m in E.CITATION_MARKER_REGISTRY:
        if m.payload is None:
            continue
        rx = re.compile(m.payload.pattern)
        ordered = tuple(sorted(rx.groupindex, key=lambda g: rx.groupindex[g]))
        assert ordered == m.payload.parts, m.form


def test_every_payload_routes_only_parts_it_captures_to_its_locator():
    for m in E.CITATION_MARKER_REGISTRY:
        if m.payload is None:
            continue
        assert set(m.payload.locator_parts) <= set(m.payload.parts), m.form


def test_retired_markers_keep_a_payload():
    """Forward-only retirement (design-A27) is broken in a second dimension if a
    retired marker stays listed but stops being READABLE."""
    for m in E.retired_citation_markers():
        assert m.payload is not None, m.form


def test_grammar_exposes_the_payload_shape():
    """The consumer derives extraction from the vocabulary, not from a copy."""
    g = E.citation_marker_grammar()
    for entry in g["markers"]:
        assert "payload" in entry
    by_form = {e["form"]: e for e in g["markers"]}
    assert by_form["[stated — code:<repo>@<rev>:<path>:<lines>]"]["payload"].parts == (
        "repo", "rev", "path", "lines")


def test_validator_rejects_a_payload_that_has_drifted_from_its_form():
    """The negative case — without it, the assertions above would pass on a
    registry whose guard had been quietly removed."""
    drifted = E.CitationMarker(
        form="[stated — thing:<alpha>:<beta>]",
        kind="stated", locator="thing", source_class="internal", antipattern="exempt",
        meaning="fixture",
        payload=E.CitationPayload(
            prefix="thing:", parts=("alpha",), pattern=r"(?P<alpha>[^\]]+)",
            locator_kind=None, locator_parts=()),
    )
    original = E.CITATION_MARKER_REGISTRY
    try:
        E.CITATION_MARKER_REGISTRY = original + (drifted,)
        with pytest.raises(RuntimeError, match="drifted"):
            E._validate_citation_registry()
    finally:
        E.CITATION_MARKER_REGISTRY = original
    E._validate_citation_registry()  # the real registry is still valid


def test_validator_rejects_a_marker_declaring_a_locator_with_no_payload():
    half = E.CitationMarker(
        form="[stated — thing:<alpha>]",
        kind="stated", locator="thing", source_class="internal", antipattern="exempt",
        meaning="fixture",
    )
    original = E.CITATION_MARKER_REGISTRY
    try:
        E.CITATION_MARKER_REGISTRY = original + (half,)
        with pytest.raises(RuntimeError, match="addresses a source iff"):
            E._validate_citation_registry()
    finally:
        E.CITATION_MARKER_REGISTRY = original
    E._validate_citation_registry()


def test_neither_mirror_publishes_a_payload_column():
    """Why no mirror edit is owed for this field (S12 write-target reconciliation).
    If a mirror ever grew one, the drift guard would begin comparing it and this
    test says so before that surprises someone."""
    assert "payload" not in E._CITATION_COMPARED_FIELDS
    text = TREE_RULES_MIRROR.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("| Marker ") and "|" in line:
            assert "payload" not in line.lower(), line


def test_retired_symbol_name_does_not_collide_with_round_file_regex():
    """The module already binds _LEGACY_MARKER_RE to fact-check ROUND-FILE names.
    Reusing 'legacy' for the retired-citation set would overload one word with two
    unrelated meanings in one module."""
    assert E._LEGACY_MARKER_RE.match("R3.md"), "guard premise changed: _LEGACY_MARKER_RE no longer matches R<N>.md"
    assert not E._LEGACY_MARKER_RE.match("[stated — topic-CLAUDE:<path>:<line>]")


# --------------------------------------------------------------------------- #
# The prompt is RENDERED, not restated  (C1 / A1)
# --------------------------------------------------------------------------- #

def _style_prompt():
    return E.KIND_PROMPT_TEMPLATES["research_style"][0]


def test_prompt_names_every_active_marker():
    prompt = _style_prompt()
    for m in E.active_citation_markers():
        assert m.form in prompt, f"active marker {m.form} never reaches the checker prompt"


def test_prompt_names_every_retired_marker_with_its_date():
    """C8: named SPECIFICALLY — marker and retirement date — rather than passing as
    one more anonymous unrecognised marker."""
    prompt = _style_prompt()
    for m in E.retired_citation_markers():
        assert m.form in prompt
        assert m.retired_on in prompt


def test_prompt_states_that_retirement_is_forward_only():
    """C9/C10 reach the checker: a retired marker must not be read as a discrepancy."""
    prompt = _style_prompt().lower()
    assert "forward-only" in prompt
    assert "never treat its presence" in prompt


def test_prompt_exempts_every_verbatim_marker_not_just_the_web_one():
    """The antipattern SCOPE sentence must be rendered too.

    Regression: the first cut of this slice rendered the known-good list but left the
    scope sentence hard-coded to `[stated — URL]`. That was both a surviving literal
    (A1's guard rail) and substantively wrong — a verbatim quote from a local file is
    the source's own words and must be exempt, or the checker grades a source's prose
    as if the AI had written it."""
    prompt = _style_prompt()
    for m in E.CITATION_MARKER_REGISTRY:
        if m.antipattern == "exempt":
            assert m.form in E.render_markers_by_antipattern("exempt"), m.form
    assert "[stated — local-file:<path>:<line>]" in prompt
    exempt = E.render_markers_by_antipattern("exempt")
    assert "[stated — local-file:<path>:<line>]" in exempt, (
        "an internal verbatim quote is not exempt from the antipattern check")
    assert "[paraphrased — URL]" not in exempt, "a paraphrase must never be exempt"


def test_retired_markers_keep_their_antipattern_disposition():
    """Forward-only in the antipattern dimension: a retired verbatim marker stays
    exempt, so an older file does not start failing style checks on retirement day."""
    exempt = E.render_markers_by_antipattern("exempt")
    assert "[stated — topic-CLAUDE:<path>:<line>]" in exempt


def test_no_marker_literal_survives_inside_the_research_style_template():
    """THE load-bearing proof that rendering is the ONLY path by which marker names
    reach the prompt (A1's guard rail).

    It reads the module SOURCE, because the runtime-mutation test below cannot do this
    job and must not be mistaken for it: `KIND_PROMPT_TEMPLATES` is built once at
    import, so rebinding the registry afterwards cannot change it, and a hand-written
    list sitting inside the template would survive that test untouched. A literal did
    in fact survive the first cut of this slice — the antipattern scope sentence still
    said "Only verbatim `[stated — URL]` blocks are exempt", which was both a second
    copy and, once internal sources joined the vocabulary, wrong. This test is what
    stops that returning.
    """
    source = (HOOKS / "_factcheck_engine.py").read_text(encoding="utf-8")
    start = source.index('"research_style": [')
    end = source.index('"coverage_check": [', start)
    template_src = source[start:end]

    # Collapse implicit string concatenation before matching. Every prompt in this
    # module is written as adjacent literals wrapped near the same column the marker
    # forms occupy, so a form split across a wrap — `"… `[stated — "` / `"URL]` …"` —
    # renders byte-identically at runtime while defeating a naive substring search.
    # That is an ordinary editing accident here, not an adversarial case.
    joined = re.sub(r'"\s*\n\s*"', "", template_src)

    offenders = [m.form for m in E.CITATION_MARKER_REGISTRY
                 if m.form in template_src or m.form in joined]
    assert not offenders, (
        "marker form(s) hard-written inside the research_style template instead of "
        f"rendered from the registry: {offenders}. Rendering must be the only path "
        "by which marker names reach the prompt.")

    # And the rendering calls really are the ones supplying them.
    for call in ("render_known_good_markers()", "render_retired_markers()",
                 'render_markers_by_antipattern("exempt")',
                 'render_markers_by_antipattern("checked")'):
        assert call in template_src, f"template no longer renders via {call}"


def test_mutating_the_registry_changes_the_rendered_prompt():
    """Proves the RENDER FUNCTION derives from the registry rather than returning a
    constant. It deliberately does NOT prove the template is literal-free — the
    template is built at import, so this test cannot see into it. That job belongs to
    test_no_marker_literal_survives_inside_the_research_style_template above."""
    before = E.render_known_good_markers()
    original = E.CITATION_MARKER_REGISTRY
    try:
        E.CITATION_MARKER_REGISTRY = original + (
            E.CitationMarker(
                form="[synthetic — probe]", kind="synthetic-probe", locator=None,
                source_class="any", antipattern="checked", meaning="test probe",
            ),
        )
        after = E.render_known_good_markers()
    finally:
        E.CITATION_MARKER_REGISTRY = original
    assert after != before
    assert "[synthetic — probe]" in after
    assert "[synthetic — probe]" not in E.render_known_good_markers()


def test_registry_validation_rejects_a_retired_entry_with_no_date():
    original = E.CITATION_MARKER_REGISTRY
    try:
        E.CITATION_MARKER_REGISTRY = original + (
            E.CitationMarker(
                form="[bad — probe]", kind="bad-probe", locator=None,
                source_class="any", antipattern="checked", meaning="",
                status=E.CITATION_MARKER_RETIRED, retired_on=None,
            ),
        )
        with pytest.raises(RuntimeError, match="retirement date"):
            E._validate_citation_registry()
    finally:
        E.CITATION_MARKER_REGISTRY = original
    E._validate_citation_registry()          # the real registry is still valid


# --------------------------------------------------------------------------- #
# Drift guard — deliberate divergence in EACH mirror separately  (C3 / C4 / A5)
# --------------------------------------------------------------------------- #

def _write_mirror_pair(tmp_path, rules_text=None, reference_text=None):
    """Materialise a mirror pair, defaulting each side to a faithful rendering of
    the real registry so a test can diverge exactly one of them."""
    rules = tmp_path / "research-scope-framing.md"
    skills = tmp_path / "Skills"
    skills.mkdir(exist_ok=True)
    reference = skills / "research-sources.md"
    rules.write_text(rules_text if rules_text is not None else _faithful_table(), encoding="utf-8")
    reference.write_text(
        reference_text if reference_text is not None else _faithful_table(), encoding="utf-8")
    return rules, reference


def _faithful_table(skip=None, extra=None, columns=None):
    """Render the registry as a mirror table. `skip` omits one marker form (the
    forgotten-mirror case); `extra` appends a row absent from the registry (the
    hand-edited-document case); `columns` chooses which fields the table publishes
    and IN WHICH ORDER, so a test can exercise header resolution rather than assuming
    the shipped column layout.

    The default deliberately publishes every comparable field. An earlier version of
    this helper hard-coded four columns, which meant every drift test in this file
    would have passed unchanged under the old POSITIONAL parser — the antipattern and
    source-class comparisons had no coverage at all, and the repair that added them
    could have been reverted with a green suite.
    """
    columns = columns or ["Marker", "Kind", "Locator", "Status", "Source",
                          "Antipattern check"]

    def cell(m, col):
        if col == "Marker":
            return f"`{m.form}`"
        if col == "Kind":
            return m.kind
        if col == "Locator":
            return m.locator or "—"
        if col == "Status":
            return f"retired {m.retired_on}" if m.retired_on else "active"
        if col == "Source":
            return m.source_class
        if col == "Antipattern check":
            return "**Exempt** (the source's own words)" if m.antipattern == "exempt" \
                else "Checked (AI prose)"
        if col == "Meaning":
            return m.meaning
        raise AssertionError(f"unknown fixture column {col!r}")

    lines = ["| " + " | ".join(columns) + " |",
             "|" + "|".join("---" for _ in columns) + "|"]
    for m in E.CITATION_MARKER_REGISTRY:
        if skip and m.form == skip:
            continue
        lines.append("| " + " | ".join(cell(m, c) for c in columns) + " |")
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def test_guard_passes_when_both_mirrors_are_in_step(tmp_path):
    rules, reference = _write_mirror_pair(tmp_path)
    assert E.check_citation_marker_drift(rules, reference) == []


def test_guard_catches_a_marker_missing_from_the_rules_mirror(tmp_path):
    missing = E.CITATION_MARKER_REGISTRY[0].form
    rules, reference = _write_mirror_pair(tmp_path, rules_text=_faithful_table(skip=missing))
    divergences = E.check_citation_marker_drift(rules, reference)
    assert divergences, "a forgotten mirror edit produced no signal at all"
    assert any(missing in d for d in divergences), (
        f"the guard fired but never NAMED {missing} — C3 requires the divergent marker "
        f"be identified, got: {divergences}")


def test_guard_catches_a_marker_missing_from_the_reference(tmp_path):
    """The SECOND mirror, checked separately — A5's guard rail against a guard that
    reaches one document and passes."""
    missing = E.CITATION_MARKER_REGISTRY[2].form
    rules, reference = _write_mirror_pair(tmp_path, reference_text=_faithful_table(skip=missing))
    divergences = E.check_citation_marker_drift(rules, reference)
    assert any(missing in d for d in divergences), divergences


def test_guard_catches_a_marker_present_only_in_a_mirror(tmp_path):
    """The direction that detects a hand-edit to a document."""
    rules, reference = _write_mirror_pair(
        tmp_path, rules_text=_faithful_table(extra="| `[invented — probe]` | invented | — | active |"))
    divergences = E.check_citation_marker_drift(rules, reference)
    assert any("[invented — probe]" in d for d in divergences), divergences


def test_guard_catches_a_status_divergence(tmp_path):
    """The retirement half: a mirror still calling a retired marker active."""
    retired = E.retired_citation_markers()[0]
    rows = _faithful_table().replace(
        f"| `{retired.form}` | {retired.kind} | {retired.locator} | retired {retired.retired_on} |",
        f"| `{retired.form}` | {retired.kind} | {retired.locator} | active |")
    rules, reference = _write_mirror_pair(tmp_path, rules_text=rows)
    divergences = E.check_citation_marker_drift(rules, reference)
    assert any(retired.form in d and "status" in d for d in divergences), divergences


def test_guard_catches_an_antipattern_divergence(tmp_path):
    """The column that decides what the style checker EXEMPTS.

    Under the old positional parser this column was published by the reference and
    compared by nothing, so flipping a verbatim marker from Exempt to Checked passed
    silently — telling the checker to grade a source's own words as AI prose. This is
    the regression test for that repair; without it, deleting `antipattern` from
    `_CITATION_COMPARED_FIELDS` would revert the fix with a green suite."""
    marker = next(m for m in E.CITATION_MARKER_REGISTRY if m.antipattern == "exempt")
    flipped = _faithful_table().replace(
        f"| `{marker.form}` | {marker.kind} | {marker.locator or '—'} | "
        f"{'retired ' + marker.retired_on if marker.retired_on else 'active'} | "
        f"{marker.source_class} | **Exempt** (the source's own words) |",
        f"| `{marker.form}` | {marker.kind} | {marker.locator or '—'} | "
        f"{'retired ' + marker.retired_on if marker.retired_on else 'active'} | "
        f"{marker.source_class} | Checked (AI prose) |")
    assert flipped != _faithful_table(), "fixture flip did not apply"
    rules, reference = _write_mirror_pair(tmp_path, reference_text=flipped)
    divergences = E.check_citation_marker_drift(rules, reference)
    assert any(marker.form in d and "antipattern" in d for d in divergences), divergences


def test_guard_catches_a_source_class_divergence(tmp_path):
    marker = next(m for m in E.CITATION_MARKER_REGISTRY if m.source_class == "internal")
    table = _faithful_table().replace(
        f"| {marker.source_class} | ", "| web | ", 1)
    rules, reference = _write_mirror_pair(tmp_path, rules_text=table)
    divergences = E.check_citation_marker_drift(rules, reference)
    assert any("source_class" in d for d in divergences), divergences


def test_guard_resolves_columns_by_header_not_by_position(tmp_path):
    """A mirror may order its columns however it likes. Position-based parsing would
    read Kind out of the Locator slot here and report spurious divergence."""
    shuffled = _faithful_table(
        columns=["Marker", "Status", "Antipattern check", "Locator", "Kind"])
    rules, reference = _write_mirror_pair(tmp_path, rules_text=shuffled)
    assert E.check_citation_marker_drift(rules, reference) == []


def test_a_mirror_may_publish_a_subset_of_columns(tmp_path):
    """Omitting a column is legitimate — it is skipped, never guessed. The rules
    mirror ships without an Antipattern column and must still pass."""
    subset = _faithful_table(columns=["Marker", "Kind", "Locator", "Status"])
    rules, reference = _write_mirror_pair(tmp_path, rules_text=subset)
    assert E.check_citation_marker_drift(rules, reference) == []


def test_an_unrecognised_header_fails_loudly_rather_than_passing(tmp_path):
    """A header whose cells map to no field must not silently skip every row."""
    table = _faithful_table()
    lines = table.splitlines()
    lines[0] = "| Thing | Whatsit | Doodad | Gubbins | Widget | Sprocket |"
    rules, reference = _write_mirror_pair(tmp_path, rules_text="\n".join(lines) + "\n")
    divergences = E.check_citation_marker_drift(rules, reference)
    assert divergences, "a table with no recognisable header passed silently"
    # Either signal is loud enough: every marker reported missing, or the whole table
    # reported unfound. Which one fires depends on whether any row parsed at all.
    assert any("no marker table" in d or "missing from" in d for d in divergences), \
        divergences


def test_marker_rows_with_no_header_above_them_fail_loudly(tmp_path):
    table = "\n".join(_faithful_table().splitlines()[2:]) + "\n"
    rules, reference = _write_mirror_pair(tmp_path, rules_text=table)
    divergences = E.check_citation_marker_drift(rules, reference)
    assert divergences, "header-less marker rows passed silently"


def test_a_second_unrelated_table_in_the_same_file_is_ignored(tmp_path):
    """`research-sources.md` really does carry a second table (the locator reference).
    It must neither be parsed as markers nor disturb the marker table above it."""
    table = _faithful_table() + (
        "\nSome prose between the tables.\n\n"
        "| Locator | Form | Notes |\n|---|---|---|\n"
        "| `url` | the full URL | pins the page |\n"
        "| `local-file` | `<path>:<line>` | relative to the Projects root |\n")
    rules, reference = _write_mirror_pair(tmp_path, rules_text=table)
    assert E.check_citation_marker_drift(rules, reference) == []


def test_a_bolded_header_cell_still_resolves(tmp_path):
    """`| **Marker** |` is ordinary markdown; it must not blank the whole table."""
    table = _faithful_table()
    lines = table.splitlines()
    lines[0] = "| **Marker** | **Kind** | **Locator** | **Status** | **Source** | **Antipattern check** |"
    rules, reference = _write_mirror_pair(tmp_path, rules_text="\n".join(lines) + "\n")
    assert E.check_citation_marker_drift(rules, reference) == []


def test_guard_is_case_insensitive_on_identifiers(tmp_path):
    """A mirror must not fail merely for capitalising a locator differently."""
    rules, reference = _write_mirror_pair(tmp_path, rules_text=_faithful_table().lower())
    divergences = [d for d in E.check_citation_marker_drift(rules, reference)
                   if "locator" in d or "kind" in d]
    assert divergences == [], divergences


def test_missing_rules_mirror_is_drift(tmp_path):
    """The harness-side mirror lives in this repo — its absence is a real divergence."""
    _, reference = _write_mirror_pair(tmp_path)
    divergences = E.check_citation_marker_drift(tmp_path / "absent.md", reference)
    assert any("not found" in d for d in divergences), divergences


def test_missing_reference_is_drift_since_the_harness_move(tmp_path):
    """research-entry-point-enforcement S2 REVERSED A5's graceful skip. The skip
    existed because the reference lived in the Projects repo, which a harness
    promoted elsewhere might not have. The reference now ships with the harness
    beside `skills/research/SKILL.md`, so "not checked out" cannot describe it and a
    missing reference is what it looks like: a divergence, named."""
    rules, _ = _write_mirror_pair(tmp_path)
    absent = tmp_path / "Skills" / "absent.md"
    divergences = E.check_citation_marker_drift(rules, absent)
    assert divergences, "a missing reference mirror passed the guard"
    assert any("reference not found" in d and str(absent) in d for d in divergences), divergences


def test_a_mirror_with_no_marker_table_is_drift(tmp_path):
    """Deleting the table is not a way to pass the guard."""
    rules, reference = _write_mirror_pair(tmp_path, rules_text="# nothing here\n")
    divergences = E.check_citation_marker_drift(rules, reference)
    assert any("no marker table" in d for d in divergences), divergences


# --------------------------------------------------------------------------- #
# The shipped mirrors + the CLI surface
# --------------------------------------------------------------------------- #

def test_shipped_mirrors_are_in_step():
    """The real files this tree ships — the registry and both mirrors agree.

    Asserts the reference EXISTS first. That pins that the mirror named here is the
    one actually shipped, so the test cannot quietly change subject to comparing
    against nothing. A missing reference is drift (see
    test_missing_reference_is_drift_since_the_harness_move above) and would surface
    as a divergence in the list below, never as a silent skip."""
    reference = TREE_SKILLS_DIR / "research-sources.md"
    assert reference.exists(), (
        f"reference mirror not found at {reference} — this pins that the tree under "
        "test actually ships the mirror named here, so an absence is reported as an "
        "absence rather than surfacing through the next assert as content drift")
    assert E.check_citation_marker_drift(TREE_RULES_MIRROR, reference) == [], (
        "the mirrors this slice ships already diverge from the registry")


def test_cli_exits_zero_when_consistent():
    proc = subprocess.run(
        [sys.executable, str(ENGINE_PATH), "check-citation-marker-drift"],
        capture_output=True, text=True, env=_tree_env())
    assert proc.returncode == 0, proc.stderr
    assert "PASS" in proc.stdout
    # Non-vacuity: the PASS line must name both mirrors as compared, and must not be
    # reporting a skip.
    assert "research-sources.md" in proc.stdout, proc.stdout
    assert "research-scope-framing.md" in proc.stdout, proc.stdout
    assert "NOT COMPARED" not in proc.stdout, proc.stdout


def test_cli_fails_when_the_reference_mirror_is_absent(tmp_path):
    """Since S2 the reference is harness-side and REQUIRED: pointing the CLI at a
    directory with no reference is drift (exit 1, stderr names the missing mirror),
    never a PASS-with-a-skip. (Before S2 this test asserted the opposite — a PASS
    whose stdout declared the mirror NOT COMPARED — because the reference lived in
    an optional sibling repo.)"""
    env = _tree_env()
    env["CITATION_MARKER_SKILLS_DIR"] = str(tmp_path / "absent")
    proc = subprocess.run(
        [sys.executable, str(ENGINE_PATH), "check-citation-marker-drift"],
        capture_output=True, text=True, env=env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "reference not found" in proc.stderr, proc.stderr
    assert "PASS" not in proc.stdout, proc.stdout


def test_cli_pass_line_names_both_mirrors_it_compared():
    """The coverage clause on the PASS line must name BOTH mirrors, so a reader can
    see the guard reached the harness-side reference and not only the rules table."""
    proc = subprocess.run(
        [sys.executable, str(ENGINE_PATH), "check-citation-marker-drift"],
        capture_output=True, text=True, env=_tree_env())
    assert proc.returncode == 0, proc.stderr
    assert "checked: research-scope-framing.md, research-sources.md" in proc.stdout, proc.stdout
    assert "NOT COMPARED" not in proc.stdout, proc.stdout


def test_claude_verify_surfaces_an_uncompared_mirror_without_verbose():
    """The registered surface must show the skip with NO --verbose flag."""
    verify = HOOKS.parent / "bin" / "config-verify"
    text = verify.read_text(encoding="utf-8")
    block = text[text.index("4d."):]
    block = block[:block.index("\nfi\n") + 4]
    assert "NOT COMPARED:" in block, (
        "config-verify does not test for an uncompared mirror")
    assert "vlog" not in block.split("NOT COMPARED:")[1].split("else")[0], (
        "the uncompared-mirror notice is routed through vlog, which prints only under "
        "--verbose — the surface that runs this does not pass it")


def test_cli_exits_one_and_names_the_marker_on_drift(tmp_path):
    missing = E.CITATION_MARKER_REGISTRY[0].form
    rules = tmp_path / "research-scope-framing.md"
    rules.write_text(_faithful_table(skip=missing), encoding="utf-8")
    skills = tmp_path / "Skills"
    skills.mkdir(exist_ok=True)
    (skills / "research-sources.md").write_text(_faithful_table(), encoding="utf-8")

    env = dict(os.environ)
    env["CITATION_MARKER_RULES_FILE"] = str(rules)
    env["CITATION_MARKER_SKILLS_DIR"] = str(skills)
    proc = subprocess.run(
        [sys.executable, str(ENGINE_PATH), "check-citation-marker-drift"],
        capture_output=True, text=True, env=env)
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert missing in proc.stderr, proc.stderr


def test_wrapper_script_is_executable_and_passes():
    wrapper = HOOKS / "check-citation-marker-drift.sh"
    assert wrapper.exists(), "A5's wrapper was not shipped"
    assert os.access(wrapper, os.X_OK), "wrapper is not executable"
    proc = subprocess.run([str(wrapper)], capture_output=True, text=True, env=_tree_env())
    assert proc.returncode == 0, proc.stderr


def test_guard_is_registered_in_claude_verify():
    """'Registered, not merely present' — A5's headline guard rail. A guard nothing
    invokes is a guard that never runs, so this asserts the registration itself."""
    verify = HOOKS.parent / "bin" / "config-verify"
    text = verify.read_text(encoding="utf-8")
    assert "check-citation-marker-drift" in text, (
        "config-verify does not invoke the citation-marker drift check — the guard "
        "would be authored but never run")


# --------------------------------------------------------------------------- #
# Forward-only retirement: an older file still reads  (C9 / C10)
# --------------------------------------------------------------------------- #

def test_a_file_citing_a_retired_marker_still_reads_without_error(tmp_path):
    """C10: processing an older file that references a withdrawn marker does not
    produce an error. Read through the shipped consumer grammar, not a local one."""
    sys.path.insert(0, str(HOOKS))
    import _claim_harvest  # noqa: PLC0415

    doc = tmp_path / "legacy_RESEARCH.md"
    doc.write_text(
        "# Findings\n\n"
        "The pipeline runs nightly. [stated — topic-CLAUDE:Personal/foo/CLAUDE.md:12]\n"
        "It has done so since March. [paraphrased — topic-CLAUDE:Personal/foo/CLAUDE.md:14]\n"
        "The web page agrees. [stated — https://example.com/a]\n",
        encoding="utf-8")

    text = doc.read_text(encoding="utf-8")
    matches = list(_claim_harvest._MARKER_RE.finditer(text))
    assert matches, "the consumer grammar matched nothing at all on a legacy file"
    # The retired forms degrade to a null locator rather than raising — the recorded
    # S12 residual. What C10 requires is that reading does not ERROR, and it does not.
    kinds = {m.group("kind").lower() for m in matches}
    assert "stated" in kinds


def test_both_a_retired_and_an_active_marker_on_one_line_are_both_seen(tmp_path):
    """Design Review edge case 4: a retired marker must not poison its line."""
    sys.path.insert(0, str(HOOKS))
    import _claim_harvest  # noqa: PLC0415

    line = ("Claim. [stated — topic-CLAUDE:Personal/foo/CLAUDE.md:12] "
            "and also [stated — https://example.com/a]\n")
    matches = list(_claim_harvest._MARKER_RE.finditer(line))
    assert len(matches) == 2, (
        f"expected both markers on the line to be seen, got {len(matches)}")
