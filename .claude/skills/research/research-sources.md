---
name: research-sources
description: BUNDLED REFERENCE — not a standalone skill. Do NOT trigger this directly. It holds the citation Marker Contract for /research — how to cite a source so the claim can be found and re-checked later — covering both the public web and your own systems. Reached one hop from SKILL.md (the harness /research skill) / research-de.md / research-ru.md.
---

# Citing your sources — the Marker Contract

Every factual claim in a `_RESEARCH.md` file carries a **marker** naming where it came
from. The marker is not decoration: months later it is the only thing that lets someone
re-open the source and check the claim. It also tells the fact-check engine which text is
the source's own words (exempt from the antipattern check) and which is AI prose (checked).

This file is the whole contract, in one place, for **every** kind of source — the public
web and the systems you work in. Before research-source-adapters S2 the web forms lived
in the research skill body and the internal-source forms lived in a rules file, so a whole
class of source cited itself in a vocabulary the verification layer had never been told
about. One list, in one place, is the fix.

## Where the authoritative list lives

**This table is an asserted MIRROR, not the source of truth.** The authoritative list is
`CITATION_MARKER_REGISTRY` in `${KIT_HOOKS_DIR}/_factcheck_engine.py`. The fact-check
engine's checker prompt is *rendered* from that registry, and
`check_citation_marker_drift` compares the registry against this file and against
`~/.claude/rules/research-scope-framing.md`. Change any one without the others and
`config-verify` fails and names the marker that diverged.

**Adding a way to cite a new kind of source is one change touching all three:** the
registry, this reference, and the rules mirror. There is no order in which a partial
edit is safe — it either lands together or it fails loudly.

## The markers

| Marker | Kind | Locator | Source | Status | Antipattern check | Meaning |
|---|---|---|---|---|---|---|
| `[stated — URL]` | stated | url | web | active | **Exempt** (the source's own words) | Verbatim, word-for-word source text inside a quote block |
| `[paraphrased — URL]` | paraphrased | url | web | active | Checked (AI prose) | AI-authored summary of attributed source material |
| `[stated — local-file:<path>:<line>]` | stated | local-file | internal | active | **Exempt** (the source's own words) | Verbatim quote from a file on disk; path relative to the Projects root when the file resolves inside it, absolute when it resolves outside it (a cloned repo, a network share) |
| `[paraphrased — local-file:<path>:<line>]` | paraphrased | local-file | internal | active | Checked (AI prose) | AI-authored summary of a file on disk; path relative to the Projects root when the file resolves inside it, absolute when it resolves outside it (a cloned repo, a network share) |
| `[stated — code:<repo>@<rev>:<path>:<lines>]` | stated | code | internal | active | **Exempt** (the source's own words) | Verbatim quote from a file in a repository, pinned to the commit that was read; path repo-relative |
| `[paraphrased — code:<repo>@<rev>:<path>:<lines>]` | paraphrased | code | internal | active | Checked (AI prose) | AI-authored summary of a file in a repository, pinned to the commit that was read; path repo-relative |
| `[stated — linear:<workspace>@<version>:<issue>]` | stated | linear | internal | active | **Exempt** (the source's own words) | Verbatim quote from a Linear issue, pinned to the workspace and the issue version that was read; a comment id may follow the issue id when the quote came from a comment |
| `[paraphrased — linear:<workspace>@<version>:<issue>]` | paraphrased | linear | internal | active | Checked (AI prose) | AI-authored summary of a Linear issue, pinned to the workspace and the issue version that was read; a comment id may follow the issue id when the summary came from a comment |
| `[inferred from …]` | inferred | — | any | active | Checked | Reasoning that goes beyond what the source explicitly states |
| `[My assessment: …]` | my-assessment | — | any | active | Checked | The author's own judgment, not source-derived |
| `[unverified — …]` | unverified | — | any | active | Checked | Claim with no verifiable source |
| `[stated — topic-CLAUDE:<path>:<line>]` | stated | topic-CLAUDE | internal | retired 2026-08-19 | **Exempt** (the source's own words) | **Retired** — see *Retired markers* below |
| `[paraphrased — topic-CLAUDE:<path>:<line>]` | paraphrased | topic-CLAUDE | internal | retired 2026-08-19 | Checked (AI prose) | **Retired** — see *Retired markers* below |

A marker's identity is its **(kind, locator)** pair, not its wording. That is what lets a
tool recognise markers by rule rather than by matching display strings.

## Verbatim vs paraphrase

`[stated — …]` is for word-for-word quotation only. If you cannot reproduce the source's
exact phrasing — even when you are attributing honestly — use `[paraphrased — …]`. The two
are not interchangeable: only `stated` blocks are exempt from the antipattern check;
`paraphrased` blocks are AI prose and are checked alongside the surrounding analysis.

## Free-text slots

`[inferred from …]`, `[My assessment: …]` and `[unverified — …]` carry a free-text slot.
Fill it with something that tells a later reader what happened. For internal-knowledge-base
work the conventional fillings are:

- `[inferred from internal sources]` — inference combining several internal sources
- `[unverified — not found in internal knowledge base]` — the claim could not be backed
  from the declared internal sources

These are *fillings*, not separate markers, which is why the table lists the general form
once rather than listing every phrasing.

## Locators — what each one must carry

| Locator | Form | Notes |
|---|---|---|
| `url` | the full URL | Pins the page, not the site |
| `local-file` | `<path>:<line>` | **The form the source can bear.** A file that resolves **inside** the Projects root is cited **relative to it** (`Personal/foo/Docs/bar_RESEARCH.md:142`), never a bare filename and never absolute — that one shape is refused, because the relative form survives the workspace moving and the absolute form does not. A file that resolves **outside** the Projects root — a cloned repository, a folder on a network share — is cited **absolutely, by this rule**: it is the only expression such a source has, and rewriting it would produce a citation nobody can reopen. A line range (`:120-135`) or a Markdown section anchor (`#section-heading`) is also acceptable where a single line would misrepresent the quote; an anchor survives the file being reordered. |
| `code` | `<repo>@<rev>:<path>:<lines>` | `<repo>` is the repository identity and `<rev>` the commit **actually read**. `<path>` is **repo-relative**, never machine-absolute. `<lines>` is a single line or a range. A dirty working tree appends `+dirty` to `<rev>` — see below. |
| — | none | The `inferred` / `my-assessment` / `unverified` kinds address no specific location |

Per-source-kind locators for **trackers** arrive with the slices that add those source kinds.
Do not invent one here. **Document folders and knowledge libraries have theirs** — S7 shipped
them, and the section below says what they mean.

### The `local-file` locator — two source kinds, one citation vocabulary

Two source classes emit `local-file`: **your knowledge library** (the folders your project's
`research_library_folders:` key names, plus the research files belonging to the topic you are
working on) and **a folder of documents** you named yourself. A folder that happens to be a
mounted cloud drive is one of the latter and needs nothing extra — it is an ordinary folder,
read as one.

**They share the marker and not the locator kind, and the difference is deliberate.** The
citation a reader follows is identical for both — `[stated — local-file:<path>:<line>]`, the
form already shipped — but underneath, each source class registers its **own** locator kind
(`knowledge_library`, `document_folder`). The port looks up its per-kind rules under the
*locator* kind when it renders and under the *source* kind when it admits, so one shared
locator kind would make those two lookups disagree. The sharing is therefore stated
explicitly, as a named citation prefix both kinds carry, rather than left to a shared name.

**What the pin does not carry: a version.** As with `url`, the rendered marker is the shipped
form and is preserved exactly, so the version does not ride the citation — a marker carrying
one would be a different vocabulary. It is kept in the admission record instead, where the
re-open instruction names it. For a file on disk that version is the moment it was **read**,
not a revision a working tree can be clean at, so the re-open instruction says the file holds
these bytes *unless it has been edited since* — which is the honest thing to say about a file
nothing pins.

**An over-budget file is refused whole, never truncated.** A `<path>:<line>` locator addresses
a byte range, so a shortened excerpt would make the pin address more than was actually read.
(This is why `code` answers the same way and `url` answers the opposite way: a URL addresses a
whole page, so shortening what was read leaves the locator just as true.)

### The `url` locator — what it addresses, and what its pin does not carry

A `url` locator is a set of named parts like every other kind's, and it declares exactly
one required part: the URL itself (`~/.claude/skills/research/locator_grammar.py`). One part
is the right floor rather than an accident — it is what makes an empty locator *detectable*,
so the admission port can degrade an item and name the missing part instead of minting a pin
that quietly addresses less than it claims.

**It pins the page, not the site.** A query string and a fragment address a *view* of a page
rather than a page, so neither survives into the bound a declaration is checked against.

**What the web pin does not carry, stated rather than implied.** Every citation pin has three
parts — source identity, the version read, and the locator. For web the rendered marker is
just the URL, because `[stated — URL]` is the shipped form and it is preserved exactly; a
marker that also rendered the version would be a *different vocabulary*, and changing the
vocabulary is a single atomic edit across three loci under a drift guard. So the version a
web read was pinned to lives in the run's admission record instead of in the marker, next to
the re-open instruction that names it. It is not dropped — it is kept where the marker cannot
carry it.

**And the version is honest about its own strength.** A commit is an exact address; most web
pages have none. The record carries the strongest thing the response actually exposed — a
validator or a last-modified date where the server declared one, and otherwise the *instant
of the read*, said in those words. A timestamp is not allowed to imply the page was pinned.

### The `code` locator — what its parts mean

A code locator is not a string. It is a set of **named parts** declared by its kind
(`path`, `lines` — `~/.claude/skills/research/locator_grammar.py`), so a locator missing a
part its kind requires is *detectable* rather than merely shorter. That is why the
admission port can degrade an item and **name the missing part** instead of minting a pin
that quietly addresses less than it claims.

**`+dirty` — what it tells a reader.** The pin names a commit. When the working tree held
uncommitted changes at the moment of the read, that commit does **not** describe the bytes
that were read, so the suffix marks the commit as *context rather than an exact address*.
It is not a caution on its own: an item pinned `+dirty` has its **excerpt kept** in the
run's admission record, because the original cannot be re-opened at the state that was
read. An item with a clean `<rev>` carries a re-open instruction instead — the file on disk
at that path holds the bytes the claim rests on.

Either way a reader holding **no credentials** can reach what the claim rests on with a
file reader and the pin alone.

## Quote-block language tags

Quote blocks should carry an `[EN]`, `[DE]` or `[RU]` tag matching the language of the
quoted text. The fact-check engine uses the tag to pick the matching
`Skills/writing-coach-{lang}.md` antipattern table (a `Projects`-repository file; project-scoped). A quote block with no language tag
warns and does not block; the engine falls back to English.

## Unknown markers

A non-quote surface marker outside this list triggers a warning at fact-check time. The
warning does not by itself flip the verdict.

## Retired markers

A **retired** marker is no longer offered — do not write a new one — but it is still
recognised. Retirement here is **forward-only**, and the distinction is the point:

- A retired marker **still reads**. A file citing one parses normally and verifies
  normally. Nothing errors on an older file, and no file is rewritten on account of a
  retirement.
- A retired marker **stays listed**, with its retirement date. It is not deleted, because
  a marker that simply vanished would be indistinguishable from one dropped by mistake.
- The fact-check style checker **names** a retired marker and its retirement date, instead
  of letting it pass as one more anonymous unrecognised marker.

**What retirement does not do — stated plainly.** It is not enforced. No code path writes
these markers; they are written by the AI synthesising the research file, so there is
nothing to intercept and refuse. Retirement is advertised in the vocabulary and flagged by
the checker. That is weaker than a refusal, and it is named as weaker rather than dressed
up as one.

**`topic-CLAUDE` (retired 2026-08-19).** `CLAUDE.md` is a system topic file, not a research
artifact: it is read as a **pointer to where documents live**, never quoted as a source.
Cite the document the CLAUDE.md pointed you to. If the only support for a claim is a
CLAUDE.md, the claim is unsupported — mark it `[unverified — …]` and say so.

## See also

- `SKILL.md` — the research skill this reference is reached from.
- `~/.claude/rules/research-scope-framing.md` — the scope-framing flow, and the second
  mirror of this table.
- `${KIT_HOOKS_DIR}/_factcheck_engine.py` — `CITATION_MARKER_REGISTRY`, the authoritative
  list, and `check_citation_marker_drift`, the guard that keeps these copies in step.

*Moved to the harness 2026-09-22 (research-entry-point-enforcement S2) from `Projects/Skills/research-sources.md`, which is now a committed pointer stub. Bundled reference of `~/.claude/skills/research/SKILL.md`.*
