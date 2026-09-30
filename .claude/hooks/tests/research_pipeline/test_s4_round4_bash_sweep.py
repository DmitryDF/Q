"""S4 round-4 fix — ITEM 0: a sweep test over every command the slice ships.

Three consecutive independent reviews found the SAME defect class: a command
S4's instruction surfaces ship that the live `sanitize-bash.sh` PreToolUse hook
refuses, so the model cannot actually run it. Each round fixed the instances
it found and missed others — round 3 fixed three templates, round 4 found two
more (MAJOR 1, MAJOR 2), one of them *created by the round-3 fixer's own
reasoning* that a template "was never broken". Fixing instances one at a time
is not convergence; this file turns "did we remember to check this command?"
into a gate that runs every time these two files change.

WHAT IT DOES
------------
Extracts every fenced block and every command-shaped inline span from S4's
two instruction surfaces — `~/.claude/rules/research-scope-framing.md` and
`~/.claude/skills/research/SKILL.md` — and drives every one CLASSIFIED AS
PROBE through the LIVE `${KIT_HOOKS_DIR}/sanitize-bash.sh`, asserting exit 0,
in two ways:

  1. **Verbatim, unsubstituted** — exactly as a model would see it if it
     pastes the block before resolving any placeholder. This is precisely how
     round 4's MAJOR 1 fired: the Autonomous template's own placeholder text
     contained "for" three times.
  2. **With realistic operator text substituted into each registered
     free-text placeholder.** Every placeholder left in a shipped command
     after this round's fixes is PATH-shaped (a scratch-file name, a cycle
     id, or the run's own `_RESEARCH.md` path) — no command embeds the
     operator's raw prose directly any more (that is the structural property
     MAJOR 1 / MAJOR 2 establish). The realistic fill for a path-shaped slot
     is therefore a SLUG, not a sentence, so the fixture set below renders
     each of the four required hazard categories as a slug:
       - a topic slug containing a trigger word, verbatim from the review
         (`case-for-heat-pumps`);
       - a slug an auto-slugified "for" ... "do" question would produce;
       - a slug an apostrophe-bearing question would produce;
       - a slug a "while" question would produce.
     Every registered free-text placeholder is probed against every fixture.

ROUND 6 — ITEM 0: THE SWEEP WAS AN ALLOWLIST; ITS OWN DOCSTRING PROMISED A
DENYLIST, AND THAT PROMISE WAS FALSE
--------------------------------------------------------------------------
A sixth independent review found a THIRD consecutive coverage hole in this
exact sweep, in a third shape: `_extract_blocks` kept a fenced block only when
its info string's first token was in a hard-coded bash/sh lang allowlist, so a
block with NO info string — a langless ` ``` ` fence — was invisible to BOTH
the probe and the drift guard, even though it shipped a real, runnable,
refusable command (MAJOR 1 below). Round 4 fixed a fenced-only blind spot;
round 5 fixed an inline-span blind spot; round 6 fixed a langless-fence blind
spot. Three different SHAPES of the same DESIGN error: discovery filtered by
lang before anything was classified, so whatever the filter didn't recognize
never reached the registry at all — a silent allowlist wearing the docstring
of a denylist ("Nothing is silently skipped by a filter... a HARD FAILURE
naming the drift" — true of the REGISTRY comparison, false of the FILTER
sitting in front of it).

The fix inverts the model rather than patching the third instance alone:

  1. **Discovery is now unconditional.** `_extract_blocks` returns EVERY
     fenced block in the file — any lang, no lang — via `_iter_fence_blocks`
     directly, with no lang-based filtering at this step. `_BASH_LANG_TOKENS`
     (the old allowlist) is retired; there is nothing left for it to gate.
  2. **Every discovered block is EXPLICITLY classified.** Each entry in
     `RULES_FILE_BLOCKS` / `SKILL_FILE_BLOCKS` is now PROBE (with its
     substitution points named) or EXCLUDE (with a stated one-line reason) —
     including every JSON payload example and the one LLM prompt template
     this round's enumeration surfaced, none of which were registered at all
     before (they were invisible to the old lang filter, not "excluded" —
     there is a real difference: an EXCLUDE entry is a decision on record; an
     invisible block is a decision nobody made).
  3. **The existing drift guard now enforces this over the FULL set.**
     `_check_registry_against_file`'s count-and-discriminator check already
     hard-fails on any mismatch between the registry and what the file
     contains; because discovery is unconditional, "what the file contains"
     is now every fenced block, so a newly-added block in ANY form — a fresh
     langless fence, a new lang tag, anything — fails the suite until a human
     classifies it. This is the property that ends the class: the coverage
     hole was never "the sweep doesn't understand form X", it was "discovery
     filters before classification exists" — remove the filter and the same
     drift guard that already worked for the forms it could see now works for
     all of them.

     **Scope of "all of them," stated precisely (round 7, MINOR 1).** This
     paragraph describes FENCED block discovery only. The INLINE-span arm
     (`_extract_inline_command_spans`, ROUND 5 below) was left standing as
     exactly the allowlist-sits-in-front-of-classification structure this
     item names as the defect — round 6 did not touch it, and round 7 widens
     it without making it unconditional. See ROUND 7 below for what changed
     there and, just as importantly, for the narrower guarantee that arm
     still does — and does not — make.
  4. Everything that already worked is unchanged: non-vacuity (the exemption
     test proving the live hook really refuses), honest `FIXTURE_EXEMPTIONS`,
     the per-spec discriminator guard, and the inventory report — see below.

MAJOR 1 (round 6) — the instance this closes. `SKILL.md`'s Closing step shipped
`python3 ${KIT_HOOKS_DIR}/output_security_record.py merge-report-section "<path
to the _RESEARCH.md>"` inside a langless fence. `SKILL.md:104` itself lists
this command among the ` ```bash ` blocks ("the merge-report step in
Closing") — the prose believed it was registered; the old filter meant it
never was. Fixed by giving the fence a `bash` info string (now discovered and
classified like any other block) and registering the same `FIXTURE_EXEMPTIONS`
residual the structurally-identical `<the _RESEARCH.md this run is writing>`
placeholder already carries (both are the run's own already-determined
`_RESEARCH.md` path, taken as a required positional/quoted argument — the
slug-bearing hazard is identical).

WHAT COUNTS AS A BLOCK, AND THE DRIFT GUARD
--------------------------------------------
Every fenced block extracted from each file — regardless of lang, including no
lang — is REGISTERED below with an explicit disposition: PROBE (with its
free-text substitution points named) or EXCLUDE (with a stated reason).
Nothing is silently skipped by a filter: a block this file does not know
about, or a placeholder a registered block no longer contains, is a HARD
FAILURE naming the drift — the registry must be updated deliberately, in the
same change that edits the block, rather than the sweep silently narrowing to
"whatever it still recognizes". As of round 6 this is no longer a claim the
extraction step could quietly violate: there is no filter left in
`_extract_blocks` for a new block to hide behind.

THE ONE RESIDUAL THIS SWEEP DOCUMENTS RATHER THAN SILENTLY PASSES
-------------------------------------------------------------------
Building this fixture set surfaced a genuine, unfixed-by-this-round hazard:
`sanitize-bash.sh` pattern 9's single-line form (`\bfor\b.*\bdo\b`) matches
"for" and "do" as a PAIR OF WHOLE WORDS anywhere on one line, with NO regard
for quoting. A slug that happens to contain both tokens (e.g. one mechanically
derived from a whole sentence, `vendor-search-for-what-to-do-next`) still
trips it — even fully resolved, even quoted, even behind `--payload-file`.
This round closed it where it COULD be closed (mandating a FIXED,
non-topic-derived name for every scratch file this skill writes — see the
prose beside each `Write a scratch ... file` instruction) and could not close
it for the placeholders that are not this round's to redesign: `cycle_id`
(a naming convention owned by the pipeline / `/work-decode`, including the
round-6-widened four-arm typed/caller enum — MINOR 5) and the two commands
whose target `_RESEARCH.md` path is a REQUIRED positional/quoted CLI argument,
not movable into a file without a CLI redesign — `declared_read.py`'s
`research_file` argument, and (new in round 6) `merge-report-section`'s own
positional argument. `FIXTURE_EXEMPTIONS` below names exactly these
combinations, each with its own reason, rather than silently dropping the
fixture or silently asserting a pass that is not true.

ROUND 5 — TWO EXTENSIONS TO THIS SWEEP, ONE FIX EACH FOUND
------------------------------------------------------------
A fifth independent review found MAJOR 2: a shipped command reproduced inside
a **single-backtick inline code span** (not a fenced block) was refused
verbatim by the live sanitizer, and this sweep could not see it — fenced-block
extraction is blind to inline spans by construction. Two things follow from
that, both implemented below:

  (a) **`_iter_fence_blocks`** recognizes a fenced block only when its
      delimiter (3+ backticks OR 3+ tildes) sits ALONE on its own line — the
      CommonMark rule. This is what makes inline-span detection possible at
      all: a naive `` ```.*?``` `` regex mis-pairs the moment a line of
      ordinary prose mentions a fence marker inline (this file's own SKILL.md
      carries exactly that line — "Every `` ` ```bash ` `` block below
      runs..." — which silently corrupted an early draft of the inline-span
      extractor below until this scanner replaced the naive one).
  (b) **`_extract_inline_command_spans`** finds every single-backtick span,
      OUTSIDE any real fenced block, that `_is_command_shaped` classifies as
      looking like a runnable instruction (see ROUND 7 below for exactly what
      that means, and what it deliberately still misses) and probes each one
      exactly as the fenced-block sweep does. `RULES_FILE_INLINE_SPANS` /
      `SKILL_FILE_INLINE_SPANS` below list every one found, in the same
      PROBE/EXCLUDE shape as the fenced-block registries, so "what did the
      detector match" is inspectable rather than asserted. **Unlike the
      fenced arm, this one is a heuristic FILTER, not unconditional
      discovery** — see ROUND 7's "narrowed claim" for what that costs.

ROUND 6 — MINOR 2 / MINOR 3: TWO MORE HONESTY GAPS IN THE SWEEP ITSELF
--------------------------------------------------------------------------
  (a) **MINOR 2** — `_odd_backtick_paragraphs` used to truncate to the first
      200 characters BEFORE checking for a registered keyword, so a keyword
      appearing past character 200 of an unbalanced-backtick paragraph was
      invisible to the very test whose job is to report that skip loudly. The
      live odd-backtick paragraph in `SKILL.md` is 205 characters. The keyword
      scan now runs over the FULL paragraph; truncation is applied only when
      RENDERING a paragraph for display (the inventory report, and a failure
      message), never before the check.
  (b) **MINOR 3** — `InlineSpanSpec` carried no `discriminator`, so the
      same-count swap/reorder hole MINOR 6 (round 5) closed for `BlockSpec`
      was still open for inline spans: a change removing one command-shaped
      span and adding a different one, same count, left the removed one
      unprobed with no failure. `InlineSpanSpec` now requires a non-empty
      `discriminator` (same `__post_init__` discipline as `BlockSpec`), and
      `_check_inline_registry_against_file` asserts it against the matched
      span at that position, mirroring `_check_registry_against_file`.

ROUND 7 — MINOR 1: THE INLINE ARM WAS STILL AN ALLOWLIST; WIDENED, AND THE
GUARANTEE NARROWED TO WHAT IS ACTUALLY TRUE
--------------------------------------------------------------------------
A seventh independent review found the SAME defect shape ROUND 6's ITEM 0
retired from the FENCED arm — a discovery filter sitting in front of
classification — still standing, untouched, in the INLINE arm:
`INLINE_COMMAND_KEYWORDS` was a three-substring allowlist (`python3 `,
`research_pipeline.py`, `declared_read`), so a command-shaped span matching
none of the three was invisible to both the probe and the drift guard. The
live instance: `SKILL.md`'s Closing step ships "...re-dispatch
`pre_plan_gates.py factcheck-research $SESSION_ID <path>`" as an inline span
— a genuine re-dispatch instruction a model could paste — matching none of
the three keywords, so neither probed nor registered.

Unlike ROUND 6's fenced-arm fix, this round does NOT invert the inline arm to
an unconditional "every span, classified by the registry" model. A bare
single-backtick span is ordinary markdown for an identifier, a flag name, a
path, or a prose fragment — `SKILL.md` alone carries 363 of them — and
treating every one as a probe-or-exclude candidate would turn the registry
into unmaintainable noise rather than a signal (`routes=ROUTES`,
`CLAUDECODE=1`, `user_approved_scope=true`, and their kin are exactly the
fragments this would sweep in for no reason). Inline discovery therefore
stays a DISCOVERY FILTER, but `_is_command_shaped` widens it from three
literal substrings to what a runnable instruction actually looks like:

  - the span's first whitespace-delimited token IS a recognized command verb
    (`python3`, `python`, `bash`, `sh`, `git`, `config-source`, `pytest` —
    `INLINE_COMMAND_VERBS`); or
  - some token in the span — after stripping trailing punctuation and an
    optional `:<line-range>` locator like `:39-49` — ends in `.py` or `.sh`
    (`_SCRIPT_EXT_RE`), covering both an invocation (`research_pipeline.py
    reset ...`) and a bare file-path mention (`sanitize-bash.sh`); or
  - the span contains one of the original three `INLINE_COMMAND_KEYWORDS`,
    kept verbatim so every span already registered against them stays
    matched (narrowing the old substrings away would have silently dropped
    live registry entries rather than only adding to them).

Widening the net surfaced 28 previously-invisible spans in
`research-scope-framing.md` and 22 in `SKILL.md`. Every one of them was
probed verbatim against the live sanitizer before being registered: all pass
(exit 0) — the coverage gap was real, but no shipped command was actually
unrunnable *because of* it. All are now registered PROBE, including the
`pre_plan_gates.py` span above, mirroring the standing precedent already set
for this registry: a fragment with no real command shape trivially passes
the sanitizer, and that trivial pass is itself the coverage evidence (see
"function/attribute-name fragments" below `RULES_FILE_INLINE_SPANS`),
not a reason to pre-filter it out before probing.

**The narrowed claim — what the inline arm actually guarantees, stated
plainly instead of implied.** Fenced-block discovery (ROUND 6, ITEM 0) is
unconditional: every fenced block, any lang or none, is found and MUST be
classified, so nothing can hide from it by taking an unrecognized shape —
`test_item0_extractor_is_unconditional_over_lang` pins exactly that. Inline-
span discovery is NOT unconditional, before this round or after it: it is,
and remains, a heuristic filter over what a runnable instruction typically
looks like. A command-shaped inline span written in a shape `_is_command_
shaped` does not recognize — no command verb as its first token, no token
ending in `.py`/`.sh`, and none of the three legacy substrings — is simply
invisible to this sweep: not probed, not excluded, not counted, and no
failure is raised. This is a real, currently open gap, not a hypothetical
one — there is no test here proving inline discovery is complete the way
there is for fenced discovery. Treat this arm as best-effort coverage of the
common case, not as the same guarantee the fenced arm makes.

**Latent gap, verified absent today, left open (not this round's to fix).**
`_FENCE_LINE_RE` accepts 0-3 leading spaces before a fence delimiter — the
CommonMark rule for a fence at the document's top level — so a fence nested
inside a list item at 4+ columns of indentation is invisible to
`_iter_fence_blocks`, and therefore to `_extract_blocks` and the fenced drift
guard, in exactly the same "filter sits in front of classification" shape
this round closed for the inline arm. Verified absent from both instruction
files today (neither currently embeds a 4+-space-indented fenced block).
Handling CommonMark's list-relative fence indentation correctly requires
tracking the enclosing list item's own content column, which this scanner
does not do; documented here rather than left for an eighth review to
re-find in a fourth shape.

THE NESTED-FENCE CLAIM THIS DOCSTRING NO LONGER MAKES (MINOR 3, round 5)
-------------------------------------------------------------------
An earlier version of this module claimed nothing is silently skipped and
implied a block could be probed even if its content itself contained a
fence-shaped line. That is not a property this scanner can have and still be
correct: CommonMark closes a fenced block on the FIRST line matching the
opening fence's character and length, with no concept of nesting — a bash
block whose example content shows another ` ```bash ` fence line closes
there for real, in a real renderer, exactly as it does here. This is
therefore accurate rather than narrowed: the scanner extracts precisely the
block a renderer would, never less. Neither instruction file currently
embeds a same-fence-shaped line inside a bash block's content (verified), so
this is a documented property, not a live truncation.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2]
SANITIZE_HOOK = HOOKS_DIR / "sanitize-bash.sh"

RULES_FILE = Path.home() / ".claude" / "rules" / "research-scope-framing.md"
SKILL_FILE = Path.home() / ".claude" / "skills" / "research" / "SKILL.md"

# --------------------------------------------------------------------------- #
# The fixture set (requirement 1) — four hazard categories, rendered as
# slugs because every registered substitution point is path-shaped (see the
# module docstring for why that is the realistic fill, not a sentence).
# --------------------------------------------------------------------------- #

FIXTURES: Dict[str, str] = {
    # Verbatim from the review's own reproduction — MAJOR 1's exact case.
    "topic-slug-with-trigger-word": "case-for-heat-pumps",
    # What a mechanical slugify() of "What should I look for and how do I
    # compare vendors?" produces — retains "for" and "do" as separate,
    # whole-word, hyphen-delimited tokens in that order.
    "topic-slug-with-for-and-do": "vendor-search-for-what-to-do-next",
    # What a slugify() of "what's the best option?" produces (apostrophes do
    # not survive slugification; this is the shape a real filename would
    # take, not the raw punctuation).
    "topic-slug-apostrophe-derived": "what-s-the-best-option",
    # Contains "while" as a whole-word token but not "do" — pattern 9's
    # single-line form needs the *pair*, so this checks "while" alone is
    # harmless, distinct from the for+do case above.
    "topic-slug-with-while": "market-shift-while-budgets-tight",
}

# (placeholder, fixture_name) -> reason. See the module docstring's "ONE
# RESIDUAL" section. Every entry here is `topic-slug-with-for-and-do`
# specifically — the other three fixtures pass everywhere (verified) and
# carry no exemption.
FIXTURE_EXEMPTIONS: Dict[Tuple[str, str], str] = {
    ("<path to the file just written>", "topic-slug-with-for-and-do"): (
        "This round mandates a FIXED, non-topic-derived name for every "
        "scratch file at this placeholder (see the prose beside each "
        "'Write a scratch ... file' instruction) — the realistic fill is "
        "the literal example name given, not a topic-derived slug. "
        "sanitize-bash.sh pattern 9's single-line form still refuses a "
        "for+do slug here if a model ignores that instruction (verified); "
        "documented as a residual of the sanitizer's own quote-blind "
        "matching, not a defect in this round's fix."
    ),
    ("<path to the scratch file holding the framed question>", "topic-slug-with-for-and-do"): (
        "Same reasoning and same fixed-filename mandate as "
        "'<path to the file just written>' above — this is the query-file "
        "scratch path, not the question's content."
    ),
    ("<this run's cycle_id>", "topic-slug-with-for-and-do"): (
        "cycle_id follows a naming convention owned elsewhere (`default` / "
        "`de` / `ru`, or `/work-decode`'s `wd-<brief>-r<i>`) — not a free "
        "topic-slug slot this file controls. A value containing both "
        "'for' and 'do' as tokens still trips pattern 9's single-line form "
        "here (verified); documented as a residual, not asserted as a pass."
    ),
    ("[default for EN | de for DE | ru for RU]", "topic-slug-with-for-and-do"): (
        "Same cycle_id reasoning as \"<this run's cycle_id>\" above — this "
        "is the per-language `--cycle-id` enum placeholder (typed / "
        "Autonomous routes), still a naming convention owned elsewhere, not "
        "a free topic-slug slot. Note the enum text ITSELF contains 'for' "
        "three times but never 'do' — the verbatim probe already confirms "
        "the unresolved placeholder passes; only a caller-supplied "
        "for+do-bearing VALUE placed there is exempt."
    ),
    ("[default | the --cycle-id the caller passed]", "topic-slug-with-for-and-do"): (
        "Same cycle_id reasoning as \"<this run's cycle_id>\" above — this "
        "is the predefined-scope route's `--cycle-id` placeholder "
        "(SKILL.md's Step -2 `r0_intake` block, the one entry point MINOR 5 "
        "(round 6) deliberately leaves untouched — see that fix's own "
        "reasoning for why this route never reaches the typed multi-"
        "language enum)."
    ),
    (
        "[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]",
        "topic-slug-with-for-and-do",
    ): (
        "Same cycle_id reasoning as \"<this run's cycle_id>\" above — this "
        "is MINOR 5's (round 6) widened four-arm `--cycle-id` enum, carried "
        "by every downstream checkpoint shared across the typed multi-"
        "language route and the predefined-scope/caller route (r1_scope, "
        "r2_research, r3_synthesis, r4_factcheck, r5_recommend). Still a "
        "naming convention owned elsewhere, not a free topic-slug slot; a "
        "value containing both 'for' and 'do' as tokens still trips "
        "pattern 9's single-line form here (verified)."
    ),
    ("<the _RESEARCH.md this run is writing>", "topic-slug-with-for-and-do"): (
        "This is the run's own already-determined `_RESEARCH.md` path — "
        "its name IS the topic slug, fixed upstream at topic creation "
        "(bookkeeping-model.md), and declared_read.py's `read` verb takes "
        "it as a REQUIRED positional CLI argument (not movable into a file "
        "without a CLI redesign, out of this round's scope). A topic slug "
        "containing both 'for' and 'do' as tokens still trips pattern 9's "
        "single-line form here (verified); documented as a residual."
    ),
    ("<path to the _RESEARCH.md>", "topic-slug-with-for-and-do"): (
        "ITEM 0 / MAJOR 1 (round 6): the run's own already-determined "
        "`_RESEARCH.md` path, passed as a required positional/quoted "
        "argument to `output_security_record.py merge-report-section` "
        "(the Closing-step command this round registers for the first "
        "time — it shipped inside a langless fence the old lang-filtered "
        "extractor never saw at all). Same reasoning, same residual, as "
        "`<the _RESEARCH.md this run is writing>` above — a different "
        "placeholder spelling because it is a different command, the "
        "identical already-determined path. A topic slug containing both "
        "'for' and 'do' as tokens still trips pattern 9's single-line form "
        "here (verified); documented as a residual."
    ),
}


@dataclass(frozen=True)
class BlockSpec:
    """One registered fenced block, in extraction order for its file. As of
    round 6 (ITEM 0) this covers EVERY fenced block regardless of lang — a
    JSON payload example and a prompt template are registered here exactly
    like a bash command, just as `probe=False` with a stated reason.

    `discriminator` (MINOR 6, round-5 fix) is a required substring that must
    appear in the block this spec is zipped against — closing the drift
    guard's blind spot: it previously compared block COUNT only, so an edit
    that removes one block and adds another (count unchanged) passed, and
    `zip` then silently paired each spec with whatever block now sat at that
    index. A `subs` placeholder catches this IF the swapped-in block lacks
    that placeholder — but several blocks share the SAME `--cycle-id`
    placeholder text and carry no other substitution point, so a swap or
    reorder among them was, and without a per-block discriminator still
    would be, undetected and mislabelled. Each checkpoint's own name
    (`r2_research`, `r3_synthesis`, ...) is a naturally unique substring of
    its own block and nothing else's — that is what `discriminator` pins."""

    label: str
    probe: bool
    discriminator: str = ""
    subs: Tuple[str, ...] = ()
    exclude_reason: str = ""

    def __post_init__(self):
        assert self.discriminator, (
            f"{self.label}: every spec needs a non-empty `discriminator` "
            f"(MINOR 6) — a substring required to appear in the block this "
            f"spec is paired with, so a swap/reorder is caught even when "
            f"`subs` is empty or shared across sibling blocks"
        )
        if self.probe:
            assert not self.exclude_reason, (
                f"{self.label}: a PROBE block must not carry an exclude_reason"
            )
        else:
            assert self.exclude_reason, (
                f"{self.label}: an EXCLUDED block must state why (requirement 4)"
            )


# --------------------------------------------------------------------------- #
# The registry — one entry per fenced block, IN THE ORDER `_extract_blocks`
# finds them in the file (round 6: EVERY fenced block, any lang, no lang — see
# the module docstring's ITEM 0 section). The length assertion in each test is
# the drift guard: add, remove, or reorder a block without updating this list
# and the sweep fails loudly rather than silently probing a shrunk set — and,
# since discovery no longer filters by lang, there is no way for a new block
# of any form to dodge that failure by not looking like bash.
# --------------------------------------------------------------------------- #

RULES_FILE_BLOCKS: List[BlockSpec] = [
    BlockSpec(
        label="research-scope-framing.md#1 — routing-prompt intake JSON example (Step 1)",
        probe=False,
        discriminator="routing_path",
        exclude_reason=(
            "JSON payload/example shape illustrating what the draft-scope "
            "adapter receives (`routing_path`, `user_query`, "
            "`last_user_messages`, ...) — not a shell command; this text is "
            "shown as data for illustration and is never passed to Bash. "
            "(ITEM 0, round 6: registered for the first time — the old "
            "lang-filtered extractor never saw a `json` fence at all, so "
            "this block was invisible rather than excluded.)"
        ),
    ),
    BlockSpec(
        label="research-scope-framing.md#2 — scope_draft_adapter_claude.py stdin redirect",
        probe=True,
        discriminator="scope_draft_adapter_claude.py",
        subs=("<path to the file just written>",),
    ),
    BlockSpec(
        label="research-scope-framing.md#3 — typed r0_intake payload JSON example (Step 4)",
        probe=False,
        discriminator="fresh-answer",
        exclude_reason=(
            "JSON payload example for the typed-route `r0_intake` call "
            "immediately below — not a shell command; the command that "
            "actually reaches Bash is the following ```bash block "
            "(`--payload-file` pointing at a file holding this shape), "
            "never this literal text. (ITEM 0, round 6: registered for the "
            "first time — same reasoning as #1 above.)"
        ),
    ),
    BlockSpec(
        label="research-scope-framing.md#4 — typed r0_intake --payload-file (Step 4)",
        probe=True,
        # #4 and #6 are byte-identical commands (verified) — both routes call
        # the exact same r0_intake template, so no CONTENT-based discriminator
        # can distinguish them from each other, and a swap between them is a
        # genuine no-op (same discriminator, same subs, same behaviour). The
        # discriminator here still does real work: it catches a swap against
        # any OTHER block in this registry.
        discriminator="advance $SESSION_ID r0_intake",
        subs=(
            "<path to the file just written>",
            "[default for EN | de for DE | ru for RU]",
        ),
    ),
    BlockSpec(
        label="research-scope-framing.md#5 — Autonomous r0_intake payload JSON example",
        probe=False,
        discriminator="autonomous_scope",
        exclude_reason=(
            "JSON payload example for the Autonomous route's `r0_intake` "
            "call immediately below — same reasoning as #1/#3 above: an "
            "illustrative payload shape, not a command. (ITEM 0, round 6: "
            "registered for the first time.)"
        ),
    ),
    BlockSpec(
        label="research-scope-framing.md#6 — Autonomous r0_intake --payload-file (MAJOR 1 fix, round 4)",
        probe=True,
        discriminator="advance $SESSION_ID r0_intake",
        subs=(
            "<path to the file just written>",
            "[default for EN | de for DE | ru for RU]",
        ),
    ),
    BlockSpec(
        label="research-scope-framing.md#7 — AdjacentPointsPort prompt template (S5)",
        probe=False,
        discriminator="You are helping a user broaden a research scope",
        exclude_reason=(
            "An LLM prompt template (the `AdjacentPointsPort` adapter's "
            "system prompt), quoted verbatim for documentation — not a "
            "shell command. It is sent as the `prompt` argument to a model "
            "call, never passed to Bash; probing it against "
            "sanitize-bash.sh would test a constraint nobody asked this "
            "text to satisfy. (ITEM 0, round 6: this is the langless fence "
            "whose EXISTENCE the old lang-filtered extractor could not see "
            "at all — the same invisibility MAJOR 1's merge-report-section "
            "command shipped with, just excluded here instead of fixed, "
            "because this block genuinely is not a shell command.)"
        ),
    ),
    BlockSpec(
        label="research-scope-framing.md#8 — S9 out-of-session smoke script",
        probe=False,
        discriminator="S9 out-of-session smoke for research-scope-framing-ui",
        exclude_reason=(
            "Not an instruction the model runs with its OWN Bash tool. The "
            "prose immediately above it is explicit: 'open a fresh terminal "
            "OUTSIDE the Claude Code session that produced this code ... "
            "never inline in the producer's process tree' (rule (a), "
            "verification-isolation contract). sanitize-bash.sh is a "
            "PreToolUse hook on THIS session's Bash tool; a script the "
            "operator is instructed to paste into a separate, unrelated "
            "shell never reaches it, so probing it here would test a "
            "constraint nobody asked the model to satisfy."
        ),
    ),
]

# Numeric/enum/boolean checkpoint payloads (`N`, `"engine_running"`,
# `true`) carry no operator English at all — the pipeline schema for each of
# these checkpoints (`RESEARCH_SCHEMAS` in research_pipeline.py) types the
# field as a count or a fixed literal, never free text. They are PROBED
# (they are real instructions a model runs). Each of these blocks also
# carries the shared `--cycle-id` placeholder (MINOR 5, round 6, widened to
# the four-arm typed/caller enum) — that is the only substitution point any
# of them has; there is still no free-text operator-prose vector here, and
# inventing one would test a shape a model would never actually produce.

SKILL_FILE_BLOCKS: List[BlockSpec] = [
    BlockSpec(
        label="SKILL.md#1 — skill marker write",
        probe=True,
        discriminator="skill_marker.py write research",
        subs=(),
    ),
    BlockSpec(
        label="SKILL.md#2 — cycle-scope-status",
        probe=True,
        discriminator="cycle-scope-status",
        subs=("<this run's cycle_id>",),
    ),
    BlockSpec(
        label="SKILL.md#3 — predefined-scope r0_intake payload JSON example (Step -2)",
        probe=False,
        discriminator="the calling skill passed via --caller",
        exclude_reason=(
            "JSON payload example for the predefined-scope `r0_intake` "
            "call immediately below — not a shell command; illustrative "
            "shape only. (ITEM 0, round 6: registered for the first time — "
            "the old lang-filtered extractor never saw a `json` fence at "
            "all.)"
        ),
    ),
    BlockSpec(
        label="SKILL.md#4 — predefined-scope r0_intake --payload-file (Step -2)",
        probe=True,
        discriminator="r0_intake",
        subs=(
            "<path to the file just written>",
            "[default | the --cycle-id the caller passed]",
        ),
    ),
    BlockSpec(
        label="SKILL.md#5 — r1_scope payload JSON example (Define Search Scope)",
        probe=False,
        discriminator="search_scope",
        exclude_reason=(
            "JSON payload example for the `r1_scope` call immediately "
            "below — not a shell command; illustrative shape only. "
            "(ITEM 0, round 6: registered for the first time.)"
        ),
    ),
    BlockSpec(
        label="SKILL.md#6 — r1_scope --payload-file (Define Search Scope, round-4 fix)",
        probe=True,
        discriminator="r1_scope",
        subs=(
            "<path to the file just written>",
            "[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]",
        ),
    ),
    BlockSpec(
        label="SKILL.md#7 — declared_read read --query-file (MAJOR 2 fix, round 4)",
        probe=True,
        discriminator="research.declared_read read",
        # `<Projects root>` is deliberately NOT a registered substitution
        # point: it is the fixed workspace root, not operator/topic content
        # — none of MAJOR 1's or MAJOR 2's diagnosed vectors touch it, and
        # substituting a hazard fixture there would test a shape this
        # placeholder never actually carries.
        subs=(
            "<the _RESEARCH.md this run is writing>",
            "<path to the scratch file holding the framed question>",
        ),
    ),
    # research-entry-point-enforcement S6: the finding recorder's payload
    # example and its one-line command, inserted in file order. Labelled #7a/#7b
    # rather than renumbering #8-#12, because the fixture-exemption registry
    # below keys on those labels and a renumber would silently re-point them.
    BlockSpec(
        label="SKILL.md#7a — record-finding payload JSON example (S6, §4 Recording each finding)",
        probe=False,
        discriminator="one self-contained sentence",
        exclude_reason=(
            "JSON payload example for the `record-finding` call immediately "
            "below — not a shell command; the command that reaches Bash is "
            "the following ```bash block (`--payload-file` pointing at a file "
            "holding this shape), never this literal text."
        ),
    ),
    BlockSpec(
        label="SKILL.md#7b — record-finding --payload-file (S6, §4 Recording each finding)",
        probe=True,
        discriminator="record-finding $SESSION_ID",
        subs=(
            "<path to the file just written>",
            "[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]",
        ),
    ),
    BlockSpec(
        label="SKILL.md#8 — r2_research advance (5.5 Record Research Complete, MAJOR 1 fix)",
        probe=True,
        # MINOR 6: r2_research..r5_recommend share the SAME `--cycle-id`
        # placeholder (the only `subs` entry each has) — a swap among these
        # four is invisible to the `placeholder not in raw` check alone.
        # Each checkpoint's own name is unique to its own block, closing it.
        discriminator="r2_research",
        subs=("[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]",),
    ),
    BlockSpec(
        label="SKILL.md#9 — r3_synthesis advance (7.5 Record Synthesis Complete, MAJOR 1 fix)",
        probe=True,
        discriminator="r3_synthesis",
        subs=("[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]",),
    ),
    BlockSpec(
        label="SKILL.md#10 — r4_factcheck advance (8.5 Convergence Fact-Check, MAJOR 1 fix)",
        probe=True,
        discriminator="r4_factcheck",
        subs=("[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]",),
    ),
    BlockSpec(
        label="SKILL.md#11 — r5_recommend advance (9. Recommend, close the pipeline, MAJOR 1 fix)",
        probe=True,
        discriminator="r5_recommend",
        subs=("[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]",),
    ),
    BlockSpec(
        label="SKILL.md#12 — merge-report-section (Closing, ITEM 0 / MAJOR 1 round-6 fix)",
        probe=True,
        discriminator="merge-report-section",
        subs=("<path to the _RESEARCH.md>",),
    ),
]


# --------------------------------------------------------------------------- #
# Extraction + probing machinery
# --------------------------------------------------------------------------- #

# A fence delimiter line: 0-3 leading spaces, then 3+ backticks OR 3+ tildes,
# then an optional info string, alone on its own line — the CommonMark rule.
# Matching on ITS OWN LINE (not "anywhere in the text") is what keeps a
# fence-shaped phrase inside ordinary prose (e.g. "the `` ` ```bash ` `` block
# below") from being mistaken for a real delimiter — the exact confusion a
# literal-string regex (`` ```bash\n(.*?)``` ``) cannot avoid, because it has
# no notion of "line" at all.
# Group 2 captures the WHOLE rest of the line (not just its first token) —
# an info string may carry more than one word ("```bash copy"), and `lang`
# below is derived from its first token in code, not by the regex.
_FENCE_LINE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
# A bare closing-fence line: same delimiter char, no info string.
_FENCE_CLOSE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*$")


def _iter_fence_blocks(text: str) -> List[Tuple[str, str]]:
    """Yield (lang, content) for every CommonMark-style fenced code block —
    backtick or tilde delimited, 3+ characters, alone on its own line, closed
    only by a same-character delimiter of >= length. `lang` is the info
    string's first token, lowercased (`""` if the fence carries none).

    A fence is NEVER "nested": the first line matching the closing shape
    ends the block, exactly as a real renderer would treat it (see the
    module docstring's "THE NESTED-FENCE CLAIM" section) — so this function
    does not, and cannot, "handle" a same-shaped line embedded as content.

    This function has never filtered by lang — round 6 (ITEM 0) removed the
    lang filter that used to sit in `_extract_blocks`, one call site above
    this one; this scanner's OWN contract (every fence, any lang) was already
    correct and unchanged."""
    lines = text.split("\n")
    blocks: List[Tuple[str, str]] = []
    i, n = 0, len(lines)
    while i < n:
        m = _FENCE_LINE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        delim, info = m.groups()
        fence_char, fence_len = delim[0], len(delim)
        lang = info.strip().split()[0].lower() if info.strip() else ""
        i += 1
        body: List[str] = []
        while i < n:
            cm = _FENCE_CLOSE_RE.match(lines[i])
            if cm and cm.group(1)[0] == fence_char and len(cm.group(1)) >= fence_len:
                i += 1  # consume the closing delimiter
                break
            body.append(lines[i])
            i += 1
        else:
            pass  # unclosed at EOF — content collected up to end of file
        blocks.append((lang, "\n".join(body)))
    return blocks


def _extract_blocks(path: Path) -> List[Tuple[str, str]]:
    """Every fenced block's (lang, content), in document order — EVERY block,
    regardless of lang, including a langless one (ITEM 0, round 6). This used
    to filter to a hard-coded bash/sh lang allowlist here; that filter is
    retired. Classification (PROBE vs EXCLUDE, and why) is now entirely the
    registry's job — see `RULES_FILE_BLOCKS` / `SKILL_FILE_BLOCKS` above and
    the module docstring's ITEM 0 section for why moving the decision here
    was the actual defect three review rounds kept re-finding in new shapes."""
    text = path.read_text(encoding="utf-8")
    return _iter_fence_blocks(text)


def _fenced_spans(text: str) -> List[Tuple[int, int]]:
    """Character-offset (start, end) spans covered by any fenced block
    (delimiter lines included), for `_extract_inline_command_spans` to skip —
    an inline single-backtick span is only real INLINE markup outside one of
    these, never inside (a lone backtick inside a fenced block's CONTENT is
    literal text, not inline code markup)."""
    lines = text.split("\n")
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1  # +1 for the '\n' split() consumed
    spans: List[Tuple[int, int]] = []
    i, n = 0, len(lines)
    while i < n:
        m = _FENCE_LINE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        delim, _info = m.groups()
        fence_char, fence_len = delim[0], len(delim)
        start = offsets[i]
        i += 1
        while i < n:
            cm = _FENCE_CLOSE_RE.match(lines[i])
            closes = (
                cm is not None
                and cm.group(1)[0] == fence_char
                and len(cm.group(1)) >= fence_len
            )
            if closes:
                end = offsets[i] + len(lines[i])
                i += 1
                break
            i += 1
        else:
            end = pos  # unclosed at EOF
        spans.append((start, end))
    return spans


def _run_sanitizer(command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        [str(SANITIZE_HOOK)], input=payload, capture_output=True, text=True
    )


def _first_line(command: str) -> str:
    return command.splitlines()[0] if command else "<empty>"


def _check_registry_against_file(path: Path, registry: List[BlockSpec]) -> List[str]:
    """Drift guard (requirement 4): the number of fenced blocks a file
    actually carries — ANY lang, including none — must match the registry
    exactly, AND (MINOR 6, round-5 fix) each spec's `discriminator` must
    actually appear in the block it is zipped against — this is what catches
    a same-count swap or reorder that the count check alone cannot see,
    independent of whether the swapped specs happen to share a `subs`
    placeholder. Returns a list of problems (empty = clean); never raises, so
    the caller controls how it fails."""
    problems = []
    blocks = _extract_blocks(path)
    if len(blocks) != len(registry):
        problems.append(
            f"{path}: found {len(blocks)} fenced block(s) (any lang) but the "
            f"registry declares {len(registry)}. A block was added, removed, "
            f"or reordered without updating RULES_FILE_BLOCKS / "
            f"SKILL_FILE_BLOCKS in this test — every fenced block in the "
            f"file must be classified PROBE or EXCLUDE, none may be "
            f"invisible to this count (ITEM 0)."
        )
        return problems  # count already wrong; pairing below would be noise
    for spec, (_lang, raw) in zip(registry, blocks):
        if spec.discriminator not in raw:
            problems.append(
                f"{path}: {spec.label} — discriminator {spec.discriminator!r} "
                f"not found in the block paired with it at this position. "
                f"Either the block changed, or a swap/reorder among the "
                f"registry entries has mislabelled it:\n    {raw!r}"
            )
    return problems


def _probe_registered_blocks(
    path: Path, registry: List[BlockSpec]
) -> Tuple[List[str], int, int, List[str]]:
    """Runs every PROBE block (verbatim + every fixture at every declared
    substitution point, minus any documented FIXTURE_EXEMPTIONS) through the
    live sanitizer.

    Returns (failures, probed_count, excluded_count, excluded_reasons_report).
    """
    failures: List[str] = []
    blocks = _extract_blocks(path)
    excluded_report: List[str] = []
    probed = 0
    excluded = 0

    for spec, (_lang, raw) in zip(registry, blocks):
        if not spec.probe:
            excluded += 1
            excluded_report.append(
                f"  EXCLUDED — {spec.label}\n    reason: {spec.exclude_reason}"
            )
            continue
        probed += 1

        # Verbatim, unsubstituted (requirement 2).
        proc = _run_sanitizer(raw)
        if proc.returncode != 0:
            failures.append(
                f"{spec.label} [verbatim]\n"
                f"    first line: {_first_line(raw)!r}\n"
                f"    hook stderr: {proc.stderr.strip()!r}"
            )

        # Every declared substitution point, against every fixture
        # (requirement 1) — unless explicitly exempted (documented above).
        for placeholder in spec.subs:
            if placeholder not in raw:
                failures.append(
                    f"{spec.label}: registered substitution point "
                    f"{placeholder!r} no longer appears in the block — the "
                    f"block changed without updating this test's registry."
                )
                continue
            for fixture_name, fixture_value in FIXTURES.items():
                exempt_reason = FIXTURE_EXEMPTIONS.get((placeholder, fixture_name))
                if exempt_reason:
                    continue
                filled = raw.replace(placeholder, fixture_value)
                proc = _run_sanitizer(filled)
                if proc.returncode != 0:
                    failures.append(
                        f"{spec.label} [{placeholder!r} <- fixture "
                        f"{fixture_name!r} = {fixture_value!r}]\n"
                        f"    first line: {_first_line(filled)!r}\n"
                        f"    hook stderr: {proc.stderr.strip()!r}"
                    )

    return failures, probed, excluded, excluded_report


# --------------------------------------------------------------------------- #
# MAJOR 2 (round-5) — inline command-shaped span sweep
#
# The coverage hole this closes: `_extract_blocks` above only ever sees
# FENCED blocks. A shipped command reproduced a second time as a
# single-backtick inline span — not a fenced block — was invisible to every
# round's sweep, and round 5's review found exactly one, refused verbatim by
# the live sanitizer even though the fenced original (in SKILL.md) passes.
#
# Detector: a single-backtick span, OUTSIDE any real fenced block (per
# `_fenced_spans` above, so a fence-shaped PHRASE inside ordinary prose —
# e.g. "the `` ` ```bash ` `` block below" — is correctly left alone, and so
# is a fence's own content), that `_is_command_shaped` classifies as looking
# like a runnable instruction.
#
# This detector is unchanged by round 6 (ITEM 0): ITEM 0's lang-filter defect
# lived entirely in FENCED-block discovery (`_extract_blocks`'s old
# `_BASH_LANG_TOKENS` check). Inline-span discovery was never lang-filtered —
# it is filtered by what LOOKS command-shaped, not by an info string a bare
# backtick span doesn't carry — so ITEM 0 does not subsume this detector;
# MINOR 3 (below) is a separate, narrower fix to the SAME discipline
# (discriminator-checked pairing) applied to this registry.
#
# ROUND 7 (MINOR 1) widened what "looks command-shaped" means — from three
# literal substrings to a command-verb-or-script-file heuristic — WITHOUT
# making discovery unconditional the way ITEM 0 made the fenced arm
# unconditional. See the module docstring's ROUND 7 section for the full
# reasoning and for the narrowed claim this arm actually supports.
# --------------------------------------------------------------------------- #

_INLINE_SPAN_RE = re.compile(r"`([^`]*?)`", re.DOTALL)
_PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n")

# The three substrings the pre-round-7 detector matched on. Kept verbatim
# (not folded away) so every span already registered against them — by
# content, not by which rule matched — stays matched; ROUND 7's docstring
# section explains why narrowing them away would have been a silent
# regression rather than a pure widening.
INLINE_COMMAND_KEYWORDS: Tuple[str, ...] = (
    "python3 ",
    "research_pipeline.py",
    "declared_read",
)

# ROUND 7 (MINOR 1) additions: a recognized command verb as the span's own
# first token, or a token (once trailing punctuation and an optional
# `:<line-range>` locator are stripped) ending in `.py` or `.sh`.
INLINE_COMMAND_VERBS: Tuple[str, ...] = (
    "python3",
    "python",
    "bash",
    "sh",
    "git",
    "config-source",
    "pytest",
)

# A trailing `:39-49`-style line/line-range locator, optional, then end of
# token — matches both a bare invocation (`research_pipeline.py`) and a
# file:line(-range) citation (`internal_kb.py:296-329`,
# `research-scope-gate.sh:39-49`).
_SCRIPT_EXT_RE = re.compile(r"\.(?:py|sh)(?::[\d-]+)?$")

# Punctuation this module strips from a token's tail before checking
# `_SCRIPT_EXT_RE` — closing brackets/quotes and sentence punctuation a
# script-file mention is routinely followed by in prose (a closing paren
# around a parenthetical, a comma, a period, a colon, a closing curly quote).
_TRAILING_PUNCT = ").,:;”’"


def _is_command_shaped(content: str) -> bool:
    """True if `content` — the text of a single-backtick inline span — looks
    like a runnable instruction. Three independent ways to match, ORed
    together (ROUND 7 / MINOR 1 docstring section has the full reasoning):

      1. contains one of the legacy `INLINE_COMMAND_KEYWORDS` substrings
         (kept for backward compatibility with spans already registered
         against them);
      2. the span's first whitespace-delimited token is exactly one of
         `INLINE_COMMAND_VERBS` (a real command verb, not merely a token
         that happens to start with one — "git-head" is a hyphenated
         identifier, not the `git` command, and must not match);
      3. some token in the span invokes or names a `.py`/`.sh` file
         (`_SCRIPT_EXT_RE`), after stripping `_TRAILING_PUNCT` from its tail.

    Deliberately NOT unconditional — see the module docstring's ROUND 7
    "narrowed claim" section for exactly what a span shaped to defeat all
    three checks would do here (nothing; it would not be found)."""
    if any(kw in content for kw in INLINE_COMMAND_KEYWORDS):
        return True
    stripped = content.strip()
    if not stripped:
        return False
    tokens = stripped.split()
    if tokens[0] in INLINE_COMMAND_VERBS:
        return True
    for tok in tokens:
        cleaned = tok.rstrip(_TRAILING_PUNCT)
        if _SCRIPT_EXT_RE.search(cleaned):
            return True
    return False


def _mask_fenced_spans(text: str, spans: List[Tuple[int, int]]) -> str:
    """Return `text` with every fenced span's BACKTICKS blanked out (replaced
    with a neutral character) while every other character — crucially every
    newline, which is what paragraph splitting keys on — is left in place.

    This is what makes paragraph-scoped backtick pairing safe: a fenced
    block's own content routinely contains backticks (nested examples) and
    blank lines (separating its own paragraphs of example text), and neither
    may be allowed to desync the backtick-counting or paragraph-splitting
    this module does over the SURROUNDING prose."""
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            if chars[i] == "`":
                chars[i] = chr(0)  # neutral placeholder, never a real char here
    return "".join(chars)


def _iter_paragraphs(text: str) -> List[Tuple[int, int, str]]:
    """Split `text` into (start, end, content) blank-line-delimited
    paragraphs, offsets into the ORIGINAL text. CommonMark inline code spans
    never cross a blank line, so scoping backtick pairing to one paragraph at
    a time bounds the blast radius of any single unbalanced backtick (a
    markdown authoring slip elsewhere in a long prose file) to that one
    paragraph, instead of desyncing pairing for the rest of the document —
    the failure mode a whole-document `` `(.*?)` `` regex has no defense
    against (see the module docstring's ROUND 5 section)."""
    paras = []
    pos = 0
    for chunk in _PARAGRAPH_SPLIT_RE.split(text):
        start = text.index(chunk, pos) if chunk else pos
        paras.append((start, start + len(chunk), chunk))
        pos = start + len(chunk)
    return paras


def _odd_backtick_paragraphs(path: Path) -> List[str]:
    """Paragraphs (outside any fenced block) whose backtick count is odd —
    a markdown authoring slip this sweep cannot pair correctly, reported
    rather than silently mis-paired or silently dropped.

    MINOR 2 (round 6): returns the FULL, untruncated paragraph content. This
    used to truncate to `content[:200]` HERE, before any keyword check ran —
    so a keyword appearing past character 200 of a long unbalanced-backtick
    paragraph was invisible to `test_no_odd_backtick_paragraph_hides_a_
    registered_keyword`, the very test whose job is to catch that. The one
    live odd-backtick paragraph (in `SKILL.md`) is 205 characters and
    contains no keyword even over its full length (verified — see that
    test); truncation now happens only where a paragraph is RENDERED for a
    human (the inventory report, a failure message), never before the check
    that decides whether it is safe to skip."""
    text = path.read_text(encoding="utf-8")
    masked = _mask_fenced_spans(text, _fenced_spans(text))
    return [
        content
        for _s, _e, content in _iter_paragraphs(masked)
        if content.count("`") % 2 != 0
    ]


def _extract_inline_command_spans(path: Path) -> List[str]:
    """Every single-backtick inline span, outside any fenced block, that
    `_is_command_shaped` classifies as looking like a runnable instruction —
    in document order. See that function's docstring, and the module
    docstring's ROUND 7 section, for exactly what is and is not matched;
    this is a heuristic filter, not unconditional discovery (unlike the
    fenced-block arm above).

    Pairing is PARAGRAPH-scoped (via `_iter_paragraphs` over fence-masked
    text), not whole-document: an earlier whole-document version of this
    function silently swallowed two genuinely refused commands (`python3
    ${KIT_HOOKS_DIR}/research_pipeline.py reset <session-id> [--cycle-id
    <id>]`, appearing twice in `SKILL.md`) into an unrelated, much larger
    mis-paired span the moment the document's TOTAL backtick count went odd
    anywhere before them — a single stray backtick far upstream desynced
    every pairing after it. Paragraph scoping was verified to recover both
    (see the round-5 fix notes)."""
    text = path.read_text(encoding="utf-8")
    fenced = _fenced_spans(text)
    masked = _mask_fenced_spans(text, fenced)

    def _inside_a_fence(start: int, end: int) -> bool:
        return any(fs <= start and end <= fe for fs, fe in fenced)

    matches: List[str] = []
    for pstart, _pend, para in _iter_paragraphs(masked):
        if para.count("`") % 2 != 0:
            continue  # reported separately by `_odd_backtick_paragraphs`
        for m in _INLINE_SPAN_RE.finditer(para):
            s, e = pstart + m.start(), pstart + m.end()
            if _inside_a_fence(s, e):
                continue
            content = text[s + 1:e - 1]  # re-slice the ORIGINAL (unmasked) text
            if _is_command_shaped(content):
                matches.append(content)
    return matches


@dataclass(frozen=True)
class InlineSpanSpec:
    """One registered inline command-shaped span, in extraction order for its
    file. Same PROBE/EXCLUDE discipline as `BlockSpec` — nothing the detector
    finds is silently skipped.

    `discriminator` (MINOR 3, round 6) is a required substring that must
    appear in the span this spec is zipped against — the same closure MINOR 6
    (round 5) already gave `BlockSpec`. Before this fix the inline-span
    registry compared COUNT only, so a change that removed one command-shaped
    span and added a different one (same count) left the removed one unprobed
    with no failure, and the inventory then mislabelled it."""

    label: str
    probe: bool = True
    discriminator: str = ""
    exclude_reason: str = ""

    def __post_init__(self):
        assert self.discriminator, (
            f"{self.label}: every spec needs a non-empty `discriminator` "
            f"(MINOR 3, round 6) — a substring required to appear in the "
            f"span this spec is paired with, so a swap/reorder is caught "
            f"even when spans share identical content elsewhere in the file"
        )
        if self.probe:
            assert not self.exclude_reason, (
                f"{self.label}: a PROBE span must not carry an exclude_reason"
            )
        else:
            assert self.exclude_reason, (
                f"{self.label}: an EXCLUDED span must state why"
            )


# One entry per span `_extract_inline_command_spans` finds, IN ORDER. These
# are function/attribute-name fragments (`declared_read._adapter_for`,
# `research_pipeline.py`) and one filename mention as well as genuine
# copy-pasteable commands — the detector is deliberately conservative (it
# matches on substring, not "is this runnable"), so both kinds are PROBED:
# a fragment with no command shape trivially passes the sanitizer, and that
# trivial pass is itself the coverage evidence, not a reason to pre-filter it
# out before probing. `discriminator` is the span's own exact content (or,
# where several byte-identical spans repeat, that shared content) — a swap
# between two byte-identical spans is a genuine no-op, same reasoning as the
# fenced registry's identical-command entries.
RULES_FILE_INLINE_SPANS: List[InlineSpanSpec] = [
    # --- round-7 (MINOR 1) additions below, up to the first pre-existing
    # entry — see the module docstring's ROUND 7 section for the widened
    # detector these newly-visible spans were found by.
    InlineSpanSpec(
        label="research-scope-framing.md:18 — check-localization-table.sh (structural validator description)",
        discriminator="check-localization-table.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:30 — check-localization-table.sh (Consumed-by list)",
        discriminator="check-localization-table.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:79 — scope_draft_adapter_claude.py (ScopeDraftPort adapter path)",
        discriminator="scope_draft_adapter_claude.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:87 — sanitize-bash.sh (typed r0_intake payload hazard note)",
        discriminator="sanitize-bash.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:210 — source_picker.py (Step 2.6 code pointer)",
        discriminator="source_picker.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:294 — declared_read._adapter_for (route-neutral reader note)",
        discriminator="declared_read._adapter_for",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:303 — declared_read._adapter_for (route-neutral reader note, 2nd mention)",
        discriminator="declared_read._adapter_for",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:322 — declared_read._adapter_for (Q26 reversal note)",
        discriminator="declared_read._adapter_for",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:387 — adapters/linear.py (kind_reachability driver-derivation note)",
        discriminator="adapters/linear.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:377 — declared_read._cmd_read (Linear route-boundary note)",
        discriminator="declared_read._cmd_read",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:534 — research-scope-gate.sh (post-approval dispatch PreToolUse hook)",
        discriminator="research-scope-gate.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:555 — sanitize-bash.sh (Autonomous payload hazard note)",
        discriminator="sanitize-bash.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:595 — sanitize-bash.sh (Autonomous pattern-9 trip note)",
        discriminator="sanitize-bash.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:644 — research-scope-gate.sh (exemption note, revoked-cycle remediation)",
        discriminator="research-scope-gate.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:649 — research-scope-gate.sh:39-49 (MINOR 2 round-3 fix note)",
        discriminator="research-scope-gate.sh:39-49",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:641 — research_pipeline.py reset <session-id> (revoked-cycle remediation, line-wrapped)",
        discriminator="research_pipeline.py reset\n<session-id> [--cycle-id <id>]",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:654 — research_pipeline.py (bare mention, 'not standalone authorization' note)",
        discriminator="research_pipeline.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:662 — research_pipeline.py (bare mention, 'Already completed' note)",
        discriminator="research_pipeline.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:683 — research-scope-gate.sh (session exemption lost note)",
        discriminator="research-scope-gate.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:671 — research_pipeline.py reset <session-id> (revoked-cycle remediation, 2nd mention, line-wrapped)",
        # Distinguishes this from spec #4 above (:641) — same command,
        # different line-wrap point, so the exact wrapped text differs.
        discriminator="research_pipeline.py reset <session-id> [--cycle-id\n<id>]",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:703 — research-scope-gate.sh (scope gate consults approval flag note)",
        discriminator="research-scope-gate.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:719 — declared_read.py (path mention, _resolve_scope_from_cycle note)",
        discriminator="~/.claude/skills/research/declared_read.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:760 — declared_read.py (same-reader note, bare mention)",
        discriminator="declared_read.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:783 — sanitize-bash.sh (MAJOR 2 round-5 inline-span refusal note)",
        discriminator="sanitize-bash.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:789 — internal_kb.py:296-329 (reader-delegation locator)",
        discriminator="internal_kb.py:296-329",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:829 — declared_read.py (path mention, admission-port note)",
        discriminator="~/.claude/skills/research/declared_read.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:855 — internal_kb.py (route-neutral reader move note)",
        discriminator="internal_kb.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:883 — factcheck-research-file.sh (Internal KB PostToolUse hook)",
        discriminator="factcheck-research-file.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:928 — _factcheck_engine.py (CITATION_MARKER_REGISTRY authoritative-list pointer)",
        discriminator="${KIT_HOOKS_DIR}/_factcheck_engine.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1106 — research-scope-gate.sh (Localization revoked-cycle remediation note)",
        discriminator="research-scope-gate.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1107 — check-research-pipeline-gate.sh (RESEARCH-VERDICT lines note)",
        discriminator="check-research-pipeline-gate.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1091 — research_pipeline.py (bare mention, code-identifiers note)",
        discriminator="research_pipeline.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1131 — check-verifier-isolation.sh (verification-isolation contract pre/post pair)",
        discriminator="${KIT_HOOKS_DIR}/check-verifier-isolation.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1143 — check-verifier-isolation.sh (smoke-block sub-test wrapper note)",
        discriminator="check-verifier-isolation.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1216 — test_s3_walking_skeleton.py (S9 sub-test i mapping table)",
        discriminator="test_s3_walking_skeleton.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1216 — research-scope-gate.sh (S9 sub-test i mapping table, 2nd mention same row)",
        discriminator="research-scope-gate.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1219 — test_s5_ultra_deep_and_production_adapter.py (S9 sub-test iv mapping table)",
        discriminator="test_s5_ultra_deep_and_production_adapter.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1220 — test_s6_internal_kb_and_locale_native.py (S9 sub-test v mapping table)",
        discriminator="test_s6_internal_kb_and_locale_native.py",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1202 — _factcheck_engine.py write-research-frontmatter <sid> (S9 smoke sub-test vi mapping table)",
        discriminator="_factcheck_engine.py write-research-frontmatter",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1229 — check-verifier-isolation.sh (bootstrap token is_dangerous() note)",
        discriminator="check-verifier-isolation.sh",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1231 — git -C ~/.claude status --porcelain (pre-snapshot command, quoted from the out-of-session S9 script's own body)",
        discriminator="git -C ~/.claude status --porcelain -- settings.json hooks",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1249 — research_pipeline.py reset SESSION_ID (Localization Table, EN error_revoked_cycle_remediation)",
        discriminator="research_pipeline.py reset SESSION_ID --cycle-id default",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1249 — research_pipeline.py reset SESSION_ID (Localization Table, DE column)",
        discriminator="research_pipeline.py reset SESSION_ID --cycle-id default",
    ),
    InlineSpanSpec(
        label="research-scope-framing.md:1249 — research_pipeline.py reset SESSION_ID (Localization Table, RU column)",
        discriminator="research_pipeline.py reset SESSION_ID --cycle-id default",
    ),
]

# Same span the review found refused (MAJOR 2's own instance) previously
# lived here as a spec #17 — round-5's fix REMOVES that reproduction (see
# `research-scope-framing.md`'s "READ, not only carried" paragraph, which now
# points at `SKILL.md`'s command instead of copying it), so it is not
# registered: the fix is the span no longer existing, not an exemption.

SKILL_FILE_INLINE_SPANS: List[InlineSpanSpec] = [
    # --- round-7 (MINOR 1) additions interleaved below — see the module
    # docstring's ROUND 7 section for the widened detector these
    # newly-visible spans were found by.
    InlineSpanSpec(
        label="SKILL.md:16 — skill_marker.py (unquoted-vs-quoted SESSION_ID failure-mode note)",
        discriminator="skill_marker.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:16 — check-skill-marker.sh (same paragraph, dispatch-time lookup mention)",
        discriminator="check-skill-marker.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:16 — skill_marker.py (2nd mention, quoted-empty case)",
        discriminator="skill_marker.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:18 — check-skill-marker.sh (shadow-map blocking note)",
        discriminator="check-skill-marker.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:31 — research-scope-gate.sh (per-cycle approval check, MAJOR 3 deviation note)",
        discriminator="research-scope-gate.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:39 — declared_read._resolve_scope_from_cycle (enforcement contrast note)",
        discriminator="declared_read._resolve_scope_from_cycle",
    ),
    InlineSpanSpec(
        label="SKILL.md:41 — _factcheck_engine.py (CITATION_MARKER_REFERENCE_PATH bundled-references note)",
        discriminator="${KIT_HOOKS_DIR}/_factcheck_engine.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:49 — declared_read._adapter_for (route-neutral reader note)",
        discriminator="declared_read._adapter_for",
    ),
    InlineSpanSpec(
        label="SKILL.md:49 — declared_read.py (path mention, 'is the reader that closes it')",
        discriminator="~/.claude/skills/research/declared_read.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:49 — scope_draft_adapter_claude.py (ScopeDraftPort adapter class path)",
        discriminator="~/.claude/skills/research/scope_draft_adapter_claude.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:49 — locale_native_subpass.py (S6 locale-native search-terms sub-pass)",
        discriminator="~/.claude/skills/research/locale_native_subpass.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:130 — research-scope-gate.sh (typed-run deadlock warning)",
        discriminator="research-scope-gate.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:130 — research-scope-gate.sh:39-49 (MINOR 2 round-3 fix note, 2nd mention)",
        discriminator="research-scope-gate.sh:39-49",
    ),
    InlineSpanSpec(
        label="SKILL.md:130 — python3 (bare verb, r1_scope-advance-not-blocked note)",
        discriminator="python3",
    ),
    InlineSpanSpec(
        label="SKILL.md:130 — research_pipeline.py (bare mention, 'raises Already completed')",
        discriminator="research_pipeline.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:132 — research_pipeline.py reset <session-id> (stranded-cycle recovery command)",
        discriminator="research_pipeline.py reset <session-id> [--cycle-id <id>]",
    ),
    InlineSpanSpec(
        label="SKILL.md:134 — sanitize-bash.sh (payload-through-file MAJOR 1 round-3 fix note)",
        discriminator="sanitize-bash.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:165 — research_pipeline.py (bare mention, 'raises Already completed', 2nd mention)",
        discriminator="research_pipeline.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:165 — research-scope-gate.sh (no-artifact-strands-the-cycle note)",
        discriminator="research-scope-gate.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:165 — research-scope-gate.sh:39-49 (2nd mention, MINOR 2 round-3 fix note)",
        discriminator="research-scope-gate.sh:39-49",
    ),
    InlineSpanSpec(
        label="SKILL.md:165 — research_pipeline.py reset <session-id> (stranded-cycle recovery command, 2nd mention)",
        discriminator="research_pipeline.py reset <session-id> [--cycle-id <id>]",
    ),
    InlineSpanSpec(
        label="SKILL.md:168 — research_pipeline.py (bare mention, PIPELINE_OVERRIDES / topic_slug gap note)",
        discriminator="research_pipeline.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:212 — sanitize-bash.sh (scope-description-through-file note)",
        discriminator="sanitize-bash.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:240 — sanitize-bash.sh (framed-question-through-file MAJOR 2 round-4 fix note)",
        discriminator="sanitize-bash.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:246 — python3 - <<'PY' heredoc (negative example, MAJOR 2 round-4 fix note)",
        probe=False,
        discriminator="python3 - <<'PY'",
        exclude_reason=(
            "This is the FORBIDDEN shape the surrounding sentence names: "
            "'Do not reach for a `python3 - <<\\'PY\\'` heredoc here ... "
            "sanitize-bash.sh refuses that shape outright (exit 2, "
            "\"python heredoc\")'. Probing it and asserting exit 0 would "
            "contradict the very prose it illustrates — it is REFUSED BY "
            "DESIGN, documented here as the reason a model must not improvise "
            "one, not as an instruction to run."
        ),
    ),
    InlineSpanSpec(
        label="SKILL.md:248 — hooks/sanitize-bash.sh (heredoc-refusal note, same sentence as the negative example)",
        discriminator="hooks/sanitize-bash.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:286 — sanitize-bash.sh (r2_research one-line-not-two round-5 sweep finding)",
        discriminator="sanitize-bash.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:337 — factcheck-research-file.sh (PostToolUse auto-fact-check note)",
        discriminator="factcheck-research-file.sh",
    ),
    InlineSpanSpec(
        label="SKILL.md:344 — pre_plan_gates.py factcheck-research $SESSION_ID <path> (re-dispatch command — MINOR 1's live-escape instance, the span that motivated this round's widening)",
        discriminator="pre_plan_gates.py factcheck-research $SESSION_ID <path>",
    ),
    InlineSpanSpec(
        label="SKILL.md:403 — _factcheck_engine.py (CITATION_MARKER_REGISTRY authoritative-list pointer)",
        discriminator="${KIT_HOOKS_DIR}/_factcheck_engine.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:480 — claims_registry.py (retired second-writer status-vocabulary note)",
        discriminator="claims_registry.py",
    ),
    InlineSpanSpec(
        label="SKILL.md:519 — research_pipeline.py (bare mention, Override Contract PIPELINE_OVERRIDES note)",
        discriminator="research_pipeline.py",
    ),
]


def _probe_inline_spans(path: Path, registry: List[InlineSpanSpec]) -> Tuple[List[str], int, int]:
    """Runs every PROBE span, verbatim, through the live sanitizer — spans
    carry no registered free-text substitution points (they are reproduced
    commands or identifier fragments, not templates), so this is a single
    verbatim probe per span, mirroring requirement 2 of the fenced sweep."""
    failures: List[str] = []
    spans = _extract_inline_command_spans(path)
    probed = excluded = 0
    for spec, raw in zip(registry, spans):
        if not spec.probe:
            excluded += 1
            continue
        probed += 1
        proc = _run_sanitizer(raw)
        if proc.returncode != 0:
            failures.append(
                f"{spec.label} [verbatim]\n"
                f"    content: {raw!r}\n"
                f"    hook stderr: {proc.stderr.strip()!r}"
            )
    return failures, probed, excluded


def _check_inline_registry_against_file(path: Path, registry: List[InlineSpanSpec]) -> List[str]:
    """Drift guard for the inline-span registry, same discipline as
    `_check_registry_against_file` for fenced blocks — including, since
    MINOR 3 (round 6), the per-span discriminator check that catches a
    same-count swap or reorder, not just a count mismatch."""
    problems: List[str] = []
    spans = _extract_inline_command_spans(path)
    if len(spans) != len(registry):
        matched_preview = "\n".join(f"    - {s!r}" for s in spans)
        return [
            f"{path}: the conservative inline-command detector found "
            f"{len(spans)} span(s) but the registry declares {len(registry)}. "
            f"A span was added, removed, or reordered without updating "
            f"RULES_FILE_INLINE_SPANS / SKILL_FILE_INLINE_SPANS in this test. "
            f"Spans found:\n{matched_preview}"
        ]
    for spec, raw in zip(registry, spans):
        if spec.discriminator not in raw:
            problems.append(
                f"{path}: {spec.label} — discriminator {spec.discriminator!r} "
                f"not found in the span paired with it at this position. "
                f"Either the span changed, or a swap/reorder among the "
                f"registry entries has mislabelled it:\n    {raw!r}"
            )
    return problems


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_registry_matches_the_blocks_each_file_actually_carries():
    """Drift guard: fails loudly if either file's fenced-block count (ANY
    lang, including none — ITEM 0, round 6) diverges from this test's
    registry, BEFORE any probing runs. This is what makes 'explicitly listed
    as excluded ... not silently skipped by a filter' (requirement 4) a real
    property rather than a comment — there is no filter left upstream of this
    check for a new block to hide behind."""
    problems = (
        _check_registry_against_file(RULES_FILE, RULES_FILE_BLOCKS)
        + _check_registry_against_file(SKILL_FILE, SKILL_FILE_BLOCKS)
    )
    assert not problems, "\n".join(problems)


def test_item0_extractor_is_unconditional_over_lang(tmp_path):
    """ITEM 0 (round 6): discovery must not filter by lang — a `json` fence
    (or any other non-bash lang, or NO lang at all) is found by
    `_extract_blocks` exactly like a `bash` one. Whether it is probed or
    excluded is the REGISTRY's job (PROBE/EXCLUDE with a stated reason),
    never the extractor's. This is what ends the "block invisible to both
    probe and drift guard" defect class three rounds each found in a
    different shape: nothing is filtered out before classification."""
    p = tmp_path / "sample.md"
    p.write_text(
        'prose\n\n```json\n{"a": 1}\n```\n\nmore\n\n```\nlangless content\n```\n',
        encoding="utf-8",
    )
    assert _extract_blocks(p) == [
        ("json", '{"a": 1}'),
        ("", "langless content"),
    ]


def test_minor3_extractor_recognizes_sh_lang_fence(tmp_path):
    """The scanner recognizes an `sh`-tagged fence as its own (lang, content)
    pair — a fence-form correctness property of `_iter_fence_blocks`,
    independent of ITEM 0's registry-classification split above."""
    p = tmp_path / "sample.md"
    p.write_text("prose\n\n```sh\necho hi\n```\n\nmore prose\n", encoding="utf-8")
    assert _extract_blocks(p) == [("sh", "echo hi")]


def test_minor3_extractor_recognizes_tilde_bash_fence(tmp_path):
    """A tilde-delimited fence (`~~~bash`) is CommonMark-legal and was
    invisible to the old backtick-only literal regex."""
    p = tmp_path / "sample.md"
    p.write_text("prose\n\n~~~bash\necho hi\n~~~\n\nmore prose\n", encoding="utf-8")
    assert _extract_blocks(p) == [("bash", "echo hi")]


def test_minor3_extractor_recognizes_bash_info_string_suffix(tmp_path):
    """An info string beyond the bare language tag (``` ```bash copy ```)
    still names `bash` as its FIRST token — `lang` is derived from that first
    token, not the literal string `bash\\n`."""
    p = tmp_path / "sample.md"
    p.write_text("prose\n\n```bash copy\necho hi\n```\n\nmore prose\n", encoding="utf-8")
    assert _extract_blocks(p) == [("bash", "echo hi")]


def test_minor3_fence_closes_on_first_matching_delimiter_never_nests(tmp_path):
    """Pins the corrected claim in the module docstring's 'THE NESTED-FENCE
    CLAIM' section: a same-shaped delimiter line INSIDE a bash block's own
    content closes the block right there — exactly as a real Markdown
    renderer would — rather than being 'handled' as nested content (which
    CommonMark fenced blocks cannot be). The stray closing-shaped line that
    follows starts a NEW (langless, unclosed-at-EOF) block — which, since
    ITEM 0 (round 6), `_extract_blocks` now RETURNS rather than silently
    drops (there is no bash/sh filter left to drop it behind)."""
    p = tmp_path / "sample.md"
    p.write_text(
        "prose\n\n```bash\necho before\n```\necho after (literal prose now)\n```\n\nmore\n",
        encoding="utf-8",
    )
    assert _extract_blocks(p) == [
        ("bash", "echo before"),
        ("", "\nmore\n"),
    ]


def test_minor6_discriminator_catches_a_same_count_swap(tmp_path):
    """Proves the MINOR 6 fix actually closes the gap it claims to, rather
    than asserting it by construction: two synthetic blocks share the same
    `subs` placeholder (mirroring `r2_research`/`r3_synthesis`'s shared
    `--cycle-id` text) but carry different discriminators. Registering them
    SWAPPED relative to the file's real order must be caught; registering
    them in the file's real order must not."""
    p = tmp_path / "sample.md"
    p.write_text(
        "```bash\n"
        "advance $SID r2_research '{}' --cycle-id \"PLACEHOLDER\"\n"
        "```\n\n"
        "```bash\n"
        "advance $SID r3_synthesis '{}' --cycle-id \"PLACEHOLDER\"\n"
        "```\n",
        encoding="utf-8",
    )
    correct = [
        BlockSpec(label="first", probe=True, discriminator="r2_research"),
        BlockSpec(label="second", probe=True, discriminator="r3_synthesis"),
    ]
    swapped = [
        BlockSpec(label="first", probe=True, discriminator="r3_synthesis"),
        BlockSpec(label="second", probe=True, discriminator="r2_research"),
    ]
    assert _check_registry_against_file(p, correct) == []
    problems = _check_registry_against_file(p, swapped)
    assert len(problems) == 2, (
        "a swap between two same-count, same-subs-shape blocks must be "
        f"caught for BOTH mispositioned specs; got: {problems}"
    )


def test_inline_span_registry_matches_the_spans_each_file_actually_carries():
    """MAJOR 2 (round-5) drift guard — the inline-span sibling of
    `test_registry_matches_the_blocks_each_file_actually_carries`: the
    conservative detector's match COUNT for each file must equal the
    registered `RULES_FILE_INLINE_SPANS` / `SKILL_FILE_INLINE_SPANS` length,
    checked BEFORE any probing runs."""
    problems = (
        _check_inline_registry_against_file(RULES_FILE, RULES_FILE_INLINE_SPANS)
        + _check_inline_registry_against_file(SKILL_FILE, SKILL_FILE_INLINE_SPANS)
    )
    assert not problems, "\n".join(problems)


def test_minor3_inline_discriminator_catches_a_same_count_swap(tmp_path):
    """MINOR 3 (round 6) — the inline-span sibling of
    `test_minor6_discriminator_catches_a_same_count_swap`: two synthetic
    single-backtick spans, same count, different content. Registering them
    SWAPPED relative to the file's real order must be caught by the
    discriminator check; registering them in the file's real order must not."""
    p = tmp_path / "sample.md"
    p.write_text(
        "See `research_pipeline.py` for the first, and "
        "`declared_read._adapter_for` for the second.\n",
        encoding="utf-8",
    )
    correct = [
        InlineSpanSpec(label="first", discriminator="research_pipeline.py"),
        InlineSpanSpec(label="second", discriminator="declared_read._adapter_for"),
    ]
    swapped = [
        InlineSpanSpec(label="first", discriminator="declared_read._adapter_for"),
        InlineSpanSpec(label="second", discriminator="research_pipeline.py"),
    ]
    assert _check_inline_registry_against_file(p, correct) == []
    problems = _check_inline_registry_against_file(p, swapped)
    assert len(problems) == 2, (
        "a swap between two different-content spans must be caught for "
        f"BOTH mispositioned specs; got: {problems}"
    )


def test_no_odd_backtick_paragraph_hides_a_registered_keyword():
    """A paragraph this sweep cannot pair (odd backtick count — a pre-existing
    markdown authoring slip, e.g. `SKILL.md`'s own literal ` ```bash ` example
    at the top of the Process section) is reported by
    `_odd_backtick_paragraphs` rather than silently mis-paired. MINOR 2
    (round 6): the check now runs over the FULL paragraph — the one live
    odd-backtick paragraph is 205 characters, past the old 200-character
    truncation this test's own scan used to be limited to. This test is what
    keeps that documented-and-skipped status honest: if any such paragraph
    ever comes to look command-shaped — by `_is_command_shaped`'s ROUND 7
    definition, not only the three legacy `INLINE_COMMAND_KEYWORDS`
    substrings — anywhere in its full length, this fails loudly instead of
    quietly missing a real command the way the round-5 review's MAJOR 2
    slipped past round-4's sweep. (Widened from a keyword-only scan to
    `_is_command_shaped` in round 7 alongside `_extract_inline_command_spans`
    itself — a narrower safety net here than at the main detector would have
    reopened exactly the gap this test exists to close.)"""
    problems = []
    for path in (RULES_FILE, SKILL_FILE):
        for para in _odd_backtick_paragraphs(path):
            if _is_command_shaped(para):
                problems.append(
                    f"{path}: an odd-backtick paragraph looks command-shaped "
                    f"but cannot be reliably paired: {para[:200]!r}"
                    + ("..." if len(para) > 200 else "")
                )
    assert not problems, "\n".join(problems)


def test_minor2_odd_backtick_scan_is_not_truncated_before_the_keyword_check(tmp_path):
    """Pins the MINOR 2 fix directly, independent of whether either target
    file happens to carry a long enough odd-backtick paragraph today: a
    synthetic paragraph with an odd backtick count and a registered keyword
    past character 200 must be CAUGHT, not silently passed the way the old
    `content[:200]` truncation (applied before the keyword check) would have
    missed it."""
    p = tmp_path / "sample.md"
    padding = "x" * 210  # pushes the keyword well past the old 200-char cutoff
    p.write_text(f"prose ` unbalanced {padding} research_pipeline.py\n", encoding="utf-8")
    odds = _odd_backtick_paragraphs(p)
    assert len(odds) == 1
    assert "research_pipeline.py" in odds[0]
    assert len(odds[0]) > 200


@pytest.mark.skipif(not SANITIZE_HOOK.exists(), reason="sanitize-bash.sh not present")
def test_every_fixture_exemption_still_actually_reproduces():
    """The exemptions above are not a way to quietly widen the pass set —
    each one must still be independently verifiable as a real, live
    sanitizer refusal (not a stale record of a combination that would pass
    fine today). Runs each exempted (placeholder, fixture) pair against a
    block that actually carries that placeholder and asserts it is STILL
    refused; a passing exemption here means the exemption should be
    deleted, not kept."""
    all_blocks = {
        RULES_FILE: (_extract_blocks(RULES_FILE), RULES_FILE_BLOCKS),
        SKILL_FILE: (_extract_blocks(SKILL_FILE), SKILL_FILE_BLOCKS),
    }
    checked = set()
    stale = []
    for path, (blocks, registry) in all_blocks.items():
        for spec, (_lang, raw) in zip(registry, blocks):
            if not spec.probe:
                continue
            for placeholder in spec.subs:
                for fixture_name in FIXTURES:
                    key = (placeholder, fixture_name)
                    if key not in FIXTURE_EXEMPTIONS or key in checked:
                        continue
                    checked.add(key)
                    filled = raw.replace(placeholder, FIXTURES[fixture_name])
                    proc = _run_sanitizer(filled)
                    if proc.returncode == 0:
                        stale.append(
                            f"{key} is recorded as exempt but PASSES today "
                            f"against {spec.label!r} — delete the exemption."
                        )
    missing = set(FIXTURE_EXEMPTIONS) - checked
    assert not missing, (
        f"FIXTURE_EXEMPTIONS declares {missing} but no registered block/"
        f"placeholder combination in either registry reaches it — a stale "
        f"exemption for a placeholder text that no longer exists."
    )
    assert not stale, "\n".join(stale)


@pytest.mark.skipif(not SANITIZE_HOOK.exists(), reason="sanitize-bash.sh not present")
def test_every_shipped_bash_block_passes_the_live_sanitizer():
    """The sweep. Every PROBE block, verbatim and under every non-exempt
    fixture at every declared substitution point, must exit 0 against the
    live `sanitize-bash.sh` — the hook that actually gates what a model can
    run, sitting ABOVE the permission layer where no allow rule can suppress
    it.

    A failure here means a model literally cannot execute an instruction
    this slice ships — the exact defect class three independent review
    rounds found piecemeal, most recently as a langless fence invisible to
    the lang-filtered extraction step ITEM 0 (round 6) retired. This test is
    what stops a fourth."""
    all_failures: List[str] = []
    total_probed = 0
    total_excluded = 0

    for path, registry in (
        (RULES_FILE, RULES_FILE_BLOCKS),
        (SKILL_FILE, SKILL_FILE_BLOCKS),
    ):
        failures, probed, excluded, _ = _probe_registered_blocks(path, registry)
        all_failures.extend(f"{path.name}: {f}" for f in failures)
        total_probed += probed
        total_excluded += excluded

    assert not all_failures, (
        f"{len(all_failures)} probe(s) failed against the live sanitizer "
        f"out of {total_probed} probed block(s) "
        f"({total_excluded} excluded, see the registry above):\n\n"
        + "\n\n".join(all_failures)
    )


@pytest.mark.skipif(not SANITIZE_HOOK.exists(), reason="sanitize-bash.sh not present")
def test_every_inline_command_span_passes_the_live_sanitizer():
    """MAJOR 2 (round-5) — the coverage hole itself. A shipped command
    reproduced a second time as a single-backtick INLINE span (never a
    fenced block) is invisible to `test_every_shipped_bash_block_passes_
    the_live_sanitizer`, because that test only ever extracts fenced blocks.
    This is the twin test over the conservative inline-command detector
    (`python3 `, `research_pipeline.py`, `declared_read`): every PROBE span
    from `RULES_FILE_INLINE_SPANS` / `SKILL_FILE_INLINE_SPANS`, verbatim,
    must exit 0 against the live sanitizer.

    Before the round-5 fix this failed on exactly one span — a
    `PYTHONPATH=...` command in `research-scope-framing.md`, reproduced
    inline with a markdown line-wrap landing right after the assignment,
    refused even though the fenced original in `SKILL.md` passes. The fix
    removed that reproduction (the paragraph now points at `SKILL.md`'s
    command instead of copying it), so this goes green immediately."""
    all_failures: List[str] = []
    total_probed = 0
    total_excluded = 0

    for path, registry in (
        (RULES_FILE, RULES_FILE_INLINE_SPANS),
        (SKILL_FILE, SKILL_FILE_INLINE_SPANS),
    ):
        failures, probed, excluded = _probe_inline_spans(path, registry)
        all_failures.extend(f"{path.name}: {f}" for f in failures)
        total_probed += probed
        total_excluded += excluded

    assert not all_failures, (
        f"{len(all_failures)} inline-span probe(s) failed against the live "
        f"sanitizer out of {total_probed} probed span(s) "
        f"({total_excluded} excluded, see the registry above):\n\n"
        + "\n\n".join(all_failures)
    )


def test_sweep_inventory_report(capsys):
    """Not a correctness assertion — a durable, always-printed inventory of
    what the sweep covers, so a reviewer can see the count/probe/exclude
    breakdown without reading this file's source. Run with `-s` to see it."""
    lines = ["", "=== ITEM 0 sweep inventory ==="]
    grand_probed = 0
    grand_excluded = 0
    grand_found = 0

    for path, registry in (
        (RULES_FILE, RULES_FILE_BLOCKS),
        (SKILL_FILE, SKILL_FILE_BLOCKS),
    ):
        blocks = _extract_blocks(path)
        probed = sum(1 for s in registry if s.probe)
        excluded = sum(1 for s in registry if not s.probe)
        grand_found += len(blocks)
        grand_probed += probed
        grand_excluded += excluded
        lines.append(f"\n{path}")
        lines.append(f"  fenced blocks found (any lang): {len(blocks)}")
        lines.append(f"  probed: {probed}   excluded: {excluded}")
        for spec, (lang, _raw) in zip(registry, blocks):
            if spec.probe:
                lines.append(f"    PROBE   [{lang or '<none>'}] {spec.label}  (subs={len(spec.subs)})")
            else:
                lines.append(f"    EXCLUDE [{lang or '<none>'}] {spec.label}")
                lines.append(f"            reason: {spec.exclude_reason}")

    lines.append(
        f"\nTOTAL: {grand_found} blocks found, {grand_probed} probed, "
        f"{grand_excluded} excluded"
    )
    lines.append(f"Fixture set ({len(FIXTURES)}): {FIXTURES}")
    lines.append(f"Documented fixture exemptions ({len(FIXTURE_EXEMPTIONS)}):")
    for (placeholder, fixture_name), reason in FIXTURE_EXEMPTIONS.items():
        lines.append(f"  {placeholder!r} x {fixture_name!r}: {reason}")

    # MAJOR 2 (round-5) — inline command-shaped span coverage.
    lines.append("\n=== ROUND 5 / MAJOR 2 inline-span sweep inventory ===")
    inline_probed = inline_excluded = inline_found = 0
    for path, registry in (
        (RULES_FILE, RULES_FILE_INLINE_SPANS),
        (SKILL_FILE, SKILL_FILE_INLINE_SPANS),
    ):
        spans = _extract_inline_command_spans(path)
        probed = sum(1 for s in registry if s.probe)
        excluded = sum(1 for s in registry if not s.probe)
        inline_found += len(spans)
        inline_probed += probed
        inline_excluded += excluded
        lines.append(f"\n{path}")
        lines.append(
            f"  inline command-shaped spans found "
            f"(legacy keywords={INLINE_COMMAND_KEYWORDS}, "
            f"+ verbs={INLINE_COMMAND_VERBS}, + any .py/.sh token): "
            f"{len(spans)}"
        )
        lines.append(f"  probed: {probed}   excluded: {excluded}")
        for spec, raw in zip(registry, spans):
            disp = "PROBE  " if spec.probe else "EXCLUDE"
            lines.append(f"    {disp} {spec.label}")
            lines.append(f"            matched: {raw!r}")
            if not spec.probe:
                lines.append(f"            reason: {spec.exclude_reason}")
        odd = _odd_backtick_paragraphs(path)
        if odd:
            lines.append(f"  odd-backtick paragraphs skipped ({len(odd)}, none contain a keyword — see test_no_odd_backtick_paragraph_hides_a_registered_keyword):")
            for o in odd:
                display = o[:200] + ("..." if len(o) > 200 else "")
                lines.append(f"    - {display!r}")
    lines.append(
        f"\nINLINE TOTAL: {inline_found} spans found, {inline_probed} probed, "
        f"{inline_excluded} excluded"
    )
    lines.append("=== end inventory ===\n")
    print("\n".join(lines))
    assert grand_found == grand_probed + grand_excluded
    assert inline_found == inline_probed + inline_excluded
