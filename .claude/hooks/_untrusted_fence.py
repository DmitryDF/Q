"""Shared untrusted-text containment fence — the single locus for escape + fence.

These three primitives were previously private to ``assessment_engine.py``. They
are relocated here so that every consumer that needs to place untrusted text into
a model context reaches the SAME behaviour by a one-line import, rather than
importing from an unrelated engine or copying the functions. A copied escaping
rule is exactly the drift that turns a "single locus" claim into a false one, so
an escaping-rule change must land here and only here.

**Honesty (locked constraint — do not weaken).** Escaping-and-fencing is a
*code-hard* mechanism: it structurally prevents the contained text from
terminating its own container, and that much is a real guarantee for that one
vector. It does **not** make the contained text harmless. A model reading a fenced
block can still be persuaded by instructions written inside it; the fence only
guarantees the block's boundary, never the reader's obedience. Containment is a
strong reduction of injection risk, **not** an absolute guarantee, and a residual
injection risk remains after it is applied.

Callers (as of slice S1 of ``research-output-security-20260804213834``):

* ``assessment_engine.py`` — re-exports all three names so its existing callers
  and tests keep working unchanged; it fences the assessed input + reference
  before handing them to a judge (assessment-engine design Arch #16).
* ``output_security.py`` — uses them for the write-side containment envelope
  around a produced research claim.

Standalone and vendor-neutral: pure standard library, no harness imports, no I/O.
"""

from __future__ import annotations

import html

# Default fence tags. A caller may pass its own tag; the tag is always chosen by
# the CALLING CODE and never derived from the text being fenced.
DEFAULT_INPUT_TAG = "untrusted_input"
DEFAULT_REFERENCE_TAG = "untrusted_reference"


def escape_untrusted(text: str) -> str:
    """Escape an untrusted string so it cannot break out of an XML fence.

    ``&`` → ``&amp;``, ``<`` → ``&lt;``, ``>`` → ``&gt;`` (``quote=False`` — only the
    three markup-significant characters, matching the design). An injected
    ``</untrusted_input>`` close-tag therefore becomes inert escaped text.
    """
    return html.escape(text, quote=False)


def fence_untrusted(text: str, tag: str = DEFAULT_INPUT_TAG) -> str:
    """Wrap escaped untrusted text in an XML fence the reader is told to treat as data."""
    return f"<{tag}>\n{escape_untrusted(text)}\n</{tag}>"


def build_untrusted_payload(input_text: str, reference_text: "str | None") -> str:
    """Fence BOTH the assessed input and the reference as untrusted data (Arch #16)."""
    parts = [fence_untrusted(input_text or "", DEFAULT_INPUT_TAG)]
    if reference_text is not None:
        parts.append(fence_untrusted(reference_text, DEFAULT_REFERENCE_TAG))
    return "\n".join(parts)


__all__ = [
    "DEFAULT_INPUT_TAG",
    "DEFAULT_REFERENCE_TAG",
    "escape_untrusted",
    "fence_untrusted",
    "build_untrusted_payload",
]
