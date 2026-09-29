"""The durable record of what one admission run produced — both outcomes.

research-source-adapters S3 / plan action A3 (design-A6, design-A7, design-A16).

Named for its whole responsibility, not half of it. The design register called
this ``evidence_store.py``, which was accurate while it held only evidence and
stopped being accurate the moment it also took the degradation record. It holds
**both** admission outcomes because both must survive the run (C4) and neither
has another home:

* :class:`EvidenceEntry` — keyed on the **rendered pin string**. Carries which
  evidence path was taken: ``original`` (the source can be re-opened at the
  state that was read, so the entry carries a re-open instruction a plain file
  read can follow) or ``captured`` (it cannot, so the excerpt itself is kept).
* :class:`DegradationRecord` — keyed on **``item_id``**, because a degraded item
  has **no pin by construction** (E3, C3). That is the pair's whole point: a
  pin-keyed store cannot hold the rejection half, so keying them differently is
  what makes both durable. A single store with one key space would have left
  C4's durability claim with no writer behind it.

Why this is a durable file at all (design-A16): across this topic's own research
files every checker reported the ``<quarantined_source_content>`` zone absent
from its prompt, so no cited source was verified (recorded as O1). Repairing the
engine is out of scope, and the constraint that follows is that the evidence a
credential-less checker needs must come from **this design's own store** and be
readable with a plain file read. Nothing here assumes the engine hands anything
over.

**Layout: one file per RUN, not one per entry.** A run is the unit S11 and S13
read, and per-entry files would make :meth:`list_degradations` a directory scan.
The file is ``.md`` with the JSON in a fenced block, because every pattern in the
bookkeeping slug grammar is ``.md``-suffixed; ``ADMISSION`` is a new advisory
TYPE registered in ``bookkeeping_invariant.ADVISORY_USER_TYPES`` and mirrored in
``rules/bookkeeping-model.md`` §4 Bucket 3.

Dependency direction: **this module imports nothing from ``source_port``.** The
port imports the store, never the reverse — which is why A3 can be built before
A4 despite the port being its only caller.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

SCHEMA_VERSION = 1

# The two evidence paths. Which one applies is the PORT's decision, taken from
# the adapter's declared capability — the adapter never chooses (design-A6).
EVIDENCE_ORIGINAL = "original"
EVIDENCE_CAPTURED = "captured"
EVIDENCE_PATHS = (EVIDENCE_ORIGINAL, EVIDENCE_CAPTURED)

# The advisory TYPE this store writes under (bookkeeping-model.md §4 Bucket 3).
ADVISORY_TYPE = "ADMISSION"

# Cold-session fallback, mirroring the assessment engine's: when no topic
# advisory bucket resolves, records still land somewhere durable rather than
# being dropped.
DEFAULT_STATE_ROOT = Path.home() / ".claude" / "state" / "research_admission"

_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
_RUN_ID_RE = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")


class AdmissionRecordError(ValueError):
    """Raised on a malformed record or an unreadable store file."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# The two record kinds.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class EvidenceEntry:
    """How one admitted item was evidenced.

    `run_id` is on BOTH record kinds so the two halves scope alike and a later
    reader can reconstruct one run rather than the whole store.

    `pin_str` is the RENDERED pin — a string, deliberately, so this module takes
    no dependency on ``source_port``'s ``SourcePin`` type.

    `declaration_bound` and `matched_selector` record HOW NARROWLY the source was
    declared. They arrived at S10/A1 because this entry is the only thing that
    outlives the run keyed on what a citation actually says — a reason that has
    nothing to do with grading and survives it. Both are plain strings for the
    same reason ``pin_str`` is: this module takes no dependency on
    ``scope_record``'s types either.

    *(These were described as "the FIRST of design-A13's three grounds" until
    2026-09-01. A13 is withdrawn and the per-claim grading layer deleted by slice
    D5 — but THESE TWO FIELDS ARE RETAINED, and the old phrasing is precisely
    what could mislead a later reader into removing them with the grader. They
    are data the port records, deliberately never a grading rule; design-A8 kept
    the rule out of the port, which is what let the rule go while these stayed.
    Five retained production modules and six retained suites consume them.)*

    * ``declaration_bound`` — the mode of the declaration that admitted the item
      (``"unscoped"`` / ``"enumerated"`` in ``scope_record``'s vocabulary).
      ``None`` means the entry does not say, which is what every record written
      before A1 reports — deliberately distinct from ``"unscoped"``, because
      "nobody recorded a bound" and "the person declared no bound" are different
      facts and grading them alike would put words in a person's mouth.
    * ``matched_selector`` — WHICH declared selector contained the item, when one
      did. ``None`` on an unscoped admission, where no selector was consulted.

    Nothing here judges — and since 2026-09-01 nothing downstream judges either.
    The rule that once decided which combination of grounds withheld a
    confirmation was descoped and deleted with slice D5; what remains is the
    record of the bound itself, which is what a later reader needs to tell
    whether a source has moved since it was read.
    """

    run_id: str
    pin_str: str
    evidence_path: str
    reopen_instruction: Optional[str] = None
    excerpt: Optional[str] = None
    at: str = field(default_factory=_utc_now)
    declaration_bound: Optional[str] = None
    matched_selector: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.run_id:
            raise AdmissionRecordError("an evidence entry needs a run_id")
        if not self.pin_str:
            raise AdmissionRecordError("an evidence entry needs a rendered pin")
        if self.evidence_path not in EVIDENCE_PATHS:
            raise AdmissionRecordError(
                f"evidence_path must be one of {list(EVIDENCE_PATHS)}, "
                f"got {self.evidence_path!r}")
        if self.evidence_path == EVIDENCE_ORIGINAL and not self.reopen_instruction:
            raise AdmissionRecordError(
                "an 'original' entry must carry a re-open instruction a plain "
                "file read can follow (C14); without one the branch claims a "
                "re-openable source and supplies no way to reach it")
        if self.evidence_path == EVIDENCE_CAPTURED and self.excerpt is None:
            raise AdmissionRecordError(
                "a 'captured' entry must keep the excerpt itself (C15); the "
                "branch exists precisely because the original cannot be "
                "re-opened at the state that was read")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pin_str": self.pin_str,
            "evidence_path": self.evidence_path,
            "reopen_instruction": self.reopen_instruction,
            "excerpt": self.excerpt,
            "at": self.at,
            "declaration_bound": self.declaration_bound,
            "matched_selector": self.matched_selector,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceEntry":
        """Rebuild an entry, including one written before A1 existed.

        The two ground fields default to ``None`` rather than to a mode, so a
        pre-A1 record loads and reports honestly that it does not say — instead
        of loading as though someone had declared something.
        """
        return cls(
            run_id=data.get("run_id", ""),
            pin_str=data.get("pin_str", ""),
            evidence_path=data.get("evidence_path", ""),
            reopen_instruction=data.get("reopen_instruction"),
            excerpt=data.get("excerpt"),
            at=data.get("at") or _utc_now(),
            declaration_bound=data.get("declaration_bound"),
            matched_selector=data.get("matched_selector"),
        )


@dataclass(frozen=True)
class DegradationRecord:
    """One item that could not be admitted, and the obligation it failed.

    Keyed on `item_id` — the resolved path, or the declared-path identity when
    enumeration itself failed. NOT on a pin: a degraded item has none.
    """

    run_id: str
    item_id: str
    obligation: str
    reason: str
    at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        for name in ("run_id", "item_id", "obligation", "reason"):
            if not getattr(self, name):
                raise AdmissionRecordError(
                    f"a degradation record needs a non-empty {name}; a rejection "
                    "that does not name the item and the obligation it failed is "
                    "the invisible failure C2 forbids")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "item_id": self.item_id,
            "obligation": self.obligation,
            "reason": self.reason,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DegradationRecord":
        return cls(
            run_id=data.get("run_id", ""),
            item_id=data.get("item_id", ""),
            obligation=data.get("obligation", ""),
            reason=data.get("reason", ""),
            at=data.get("at") or _utc_now(),
        )


# --------------------------------------------------------------------------- #
# The store.
# --------------------------------------------------------------------------- #

class AdmissionRecordStore:
    """One directory holding one ``.md`` file per admission run.

    Every write is atomic (``O_EXCL`` temp + ``os.replace``) so a crash mid-run
    leaves the previous state rather than a half-written file, and so a record
    written before process exit is readable after it.
    """

    def __init__(self,
                 directory: Union[str, Path, None] = None,
                 *,
                 slug: str = "research-admission",
                 ts: str = "",
                 scope_segment: str = "") -> None:
        self.directory = Path(directory) if directory else DEFAULT_STATE_ROOT
        self.slug = slug
        self.ts = ts
        # The Bucket-2 <SCOPE> of the research file this store belongs to, or ""
        # for a name that carries none. Keyword-only and defaulted so every
        # existing construction site is unaffected — the thirteen records
        # already on disk were written by a caller that passes no scope, and
        # `_stem` below is byte-identical for them.
        #
        # Named `scope_segment` rather than `scope` deliberately: `scope` means
        # a `ScopeRecord` at both production sites that construct this store,
        # so the shorter name would collide with a different domain concept in
        # the two functions most likely to be read together.
        self.scope_segment = scope_segment

    # -- paths -------------------------------------------------------------- #

    def _stem(self) -> str:
        """``<slug>[-<ts>][_<SCOPE>]`` — the one place the stem is composed.

        `run_path` and `run_ids` each built this expression verbatim, so a
        third component added to two copies is how the two drift apart. With
        no scope the result is byte-identical to what those copies produced,
        which is what keeps records written before the scope existed readable.
        """
        stem = f"{self.slug}-{self.ts}" if self.ts else self.slug
        return f"{stem}_{self.scope_segment}" if self.scope_segment else stem

    def run_path(self, run_id: str) -> Path:
        """``<slug>[-<ts>][_<SCOPE>]_ADMISSION_<run_id>.md`` — the slug-grammar advisory form."""
        if not _RUN_ID_RE.match(run_id or ""):
            raise AdmissionRecordError(
                f"run_id {run_id!r} must be 1-64 chars of [A-Za-z0-9._-] so it can "
                "be a filename segment")
        return self.directory / f"{self._stem()}_{ADVISORY_TYPE}_{run_id}.md"

    def run_ids(self) -> Tuple[str, ...]:
        """Every run this store holds, in filename order.

        Filename order, not chronological order: the sort below is
        lexicographic over the directory listing and coincides with write
        order only because run ids have so far been written in a shape where
        the two agree. An earlier version of this line promised "oldest
        filename first", which reads as a chronological guarantee this makes
        no attempt to provide.
        """
        prefix = f"{self._stem()}_{ADVISORY_TYPE}_"
        if not self.directory.is_dir():
            return ()
        found = [p.name[len(prefix):-3] for p in sorted(self.directory.iterdir())
                 if p.name.startswith(prefix) and p.name.endswith(".md")]
        return tuple(found)

    # -- raw document ------------------------------------------------------- #

    def _empty_doc(self, run_id: str) -> Dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "run_id": run_id,
                "evidence": {}, "degradations": []}

    def _read_doc(self, run_id: str) -> Dict[str, Any]:
        path = self.run_path(run_id)
        if not path.exists():
            return self._empty_doc(run_id)
        text = path.read_text(encoding="utf-8")
        m = _JSON_FENCE_RE.search(text)
        if not m:
            raise AdmissionRecordError(
                f"{path} holds no ```json fenced block; the store's own layout "
                "is a fenced JSON payload inside a .md advisory artifact")
        try:
            doc = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise AdmissionRecordError(f"{path}: payload is not valid JSON: {e}") from None
        if doc.get("schema_version") != SCHEMA_VERSION:
            raise AdmissionRecordError(
                f"{path}: unsupported schema_version {doc.get('schema_version')!r}; "
                f"this build reads {SCHEMA_VERSION} only")
        doc.setdefault("evidence", {})
        doc.setdefault("degradations", [])
        return doc

    def _write_doc(self, run_id: str, doc: Mapping[str, Any]) -> Path:
        path = self.run_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"# Admission record — run `{run_id}`\n\n"
            "Written by the research admission port "
            "(`~/.claude/skills/research/source_port.py`). Two key spaces: evidence "
            "keyed on the rendered pin, degradations keyed on `item_id` because a "
            "degraded item has no pin. Readable with a plain file read — no "
            "credential and no engine round-trip is needed to reach what a claim "
            "rests on.\n\n"
            "```json\n"
            + json.dumps(doc, indent=2, sort_keys=False)
            + "\n```\n"
        )
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".admission-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path

    # -- evidence ----------------------------------------------------------- #

    def put_evidence(self, entry: EvidenceEntry) -> Path:
        """Persist one evidence entry, keyed on its rendered pin within its run."""
        doc = self._read_doc(entry.run_id)
        doc["evidence"][entry.pin_str] = entry.to_dict()
        return self._write_doc(entry.run_id, doc)

    def get_evidence(self, pin_str: str,
                     run_id: Optional[str] = None) -> Optional[EvidenceEntry]:
        """Retrieve an entry **by its rendered pin alone**.

        `run_id` narrows the lookup to one run file when the caller knows it;
        without it the store searches its runs, because the pin is the address a
        later reader holds — they have the citation, not the run identity.
        """
        candidates = (run_id,) if run_id else self.run_ids()
        for rid in candidates:
            try:
                doc = self._read_doc(rid)
            except AdmissionRecordError:
                continue
            raw = doc["evidence"].get(pin_str)
            if raw is not None:
                return EvidenceEntry.from_dict(raw)
        return None

    def list_evidence(self, run_id: str) -> Tuple[EvidenceEntry, ...]:
        """Exactly this run's evidence entries, and none from any other run."""
        doc = self._read_doc(run_id)
        return tuple(EvidenceEntry.from_dict(v) for v in doc["evidence"].values())

    # -- degradations ------------------------------------------------------- #

    def put_degradation(self, record: DegradationRecord) -> Path:
        """Append one degradation. A LIST, because the same item can fail more
        than one obligation and none of those failures may overwrite another."""
        doc = self._read_doc(record.run_id)
        doc["degradations"].append(record.to_dict())
        return self._write_doc(record.run_id, doc)

    def list_degradations(self, run_id: str) -> Tuple[DegradationRecord, ...]:
        """Exactly this run's degradations — the accessor S13's INCOMPLETE
        roll-up reads. Returns nothing from any other run in the same store."""
        doc = self._read_doc(run_id)
        return tuple(DegradationRecord.from_dict(v) for v in doc["degradations"])


# --------------------------------------------------------------------------- #
# The side-effect-free store (research-source-adapters S6 / plan target 12).
# --------------------------------------------------------------------------- #

class InMemoryAdmissionRecordStore:
    """A store that keeps a run's outcomes in memory and writes nothing.

    **Why this is a second implementation rather than a parameter.**
    :class:`AdmissionRecordStore` is directory-backed by construction — it
    resolves every path against ``self.directory``, and its writer ``mkdir``s and
    ``os.replace``s — so "write nowhere" is not a directory it can be handed. Even
    a temp directory would still write.

    **Why it exists at all.** S6 makes the fact-check engine's web ingest drive
    the port, and two shipped suites call that ingest DIRECTLY with an injected
    fetch. Without a store that writes nothing, those suites would quietly begin
    creating real files under the state root while still reporting green — a worse
    failure than a red test, because nothing would say it was happening.

    **Why here.** A store that writes nothing is a second implementation of an
    existing abstraction, not a source-class-shaped branch, so it belongs beside
    the only other store. Defining it next to its caller in the hooks tree would
    make a hooks-side module define a type the skills-side port consumes — the
    dependency inversion this codebase refuses in writing elsewhere.

    It implements the two methods the port actually calls (:meth:`put_evidence`
    and :meth:`put_degradation`) plus the two readers, so a caller can still
    inspect a run; the records simply do not outlive the process.
    """

    def __init__(self) -> None:
        self._runs: Dict[str, Dict[str, Any]] = {}

    def _doc(self, run_id: str) -> Dict[str, Any]:
        return self._runs.setdefault(run_id, {"evidence": {}, "degradations": []})

    def put_evidence(self, entry: EvidenceEntry) -> None:
        self._doc(entry.run_id)["evidence"][entry.pin_str] = entry.to_dict()

    def put_degradation(self, record: DegradationRecord) -> None:
        self._doc(record.run_id)["degradations"].append(record.to_dict())

    def get_evidence(self, pin_str: str,
                     run_id: Optional[str] = None) -> Optional[EvidenceEntry]:
        """Same signature as the directory-backed store's, deliberately.

        Pin FIRST, run id optional — because the pin is the address a later reader
        holds; they have the citation, not the run identity. An earlier draft of
        this class took the two the other way round, which is the defect a second
        implementation of an abstraction exists to avoid: code written against one
        store would break silently on the other, and both signatures would look
        reasonable in isolation.
        """
        run_ids = (run_id,) if run_id else tuple(self._runs)
        for rid in run_ids:
            raw = self._runs.get(rid, {}).get("evidence", {}).get(pin_str)
            if raw:
                return EvidenceEntry.from_dict(raw)
        return None

    def list_evidence(self, run_id: str) -> Tuple[EvidenceEntry, ...]:
        """Exactly this run's evidence entries — the directory store's signature.

        Added S8 Session 3. The class docstring above already claimed parity on
        "the two readers", and the directory-backed store has had this one since
        S3; its absence here meant a caller could list a run's evidence against a
        real store and crash against the double — the asymmetry a test double
        exists to avoid. Found by A12's own gate, which needed it.
        """
        return tuple(EvidenceEntry.from_dict(v)
                     for v in self._doc(run_id)["evidence"].values())

    def list_degradations(self, run_id: str) -> Tuple[DegradationRecord, ...]:
        return tuple(DegradationRecord.from_dict(v)
                     for v in self._doc(run_id)["degradations"])
