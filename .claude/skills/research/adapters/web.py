"""The web adapter — the highest-traffic source class, and deliberately as thin
as the first one.

research-source-adapters S6 / plan action A4 (design-A29, design-A7, design-A20,
design-A24's read-only half).

Three things and no more, exactly like `code_base.py`:

* ``enumerate_within`` — a **plain generator** that yields candidates lazily,
  applies **no bound** and holds **no bound constant**. It yields the URLs the
  run was asked to open; the port consumes them and stops at its own limits.
* ``read_with_pin`` — fetches one URL and returns the facts the pin needs (the
  source identity, the version read, a ``url`` locator) plus the ``truncated``
  flag.
* ``can_reopen_without_credentials`` — a **declared capability**, not a decision.

Why this adapter does not own the fetch policy
----------------------------------------------
The fetch itself is **injected**. The engine's ingest loop already owns the
fair-share per-URL budget, the global wall-clock cap and the aggregate ceiling,
and those cannot move here: the fair share is computed from a citation count this
adapter never sees, and an adapter that held a bound could widen one. So the
caller hands in the same ``fetch(url, timeout_s, max_bytes)`` callable it would
otherwise have called directly — which is also what keeps the two shipped suites
that inject a fake fetch working untouched.

**Read-only** (design-A24). No write path: no cookie jar, no browser profile, no
credential, no state. The adapter cannot write to the web and does not write to
disk either; persistence is the port's, through the record store.

What "version read" means for a page — stated, not implied
----------------------------------------------------------
A commit is an exact address; a web page mostly has none. This adapter reports
the strongest thing the response actually exposed, in order: an ``ETag``, then
``Last-Modified``, then — when the response exposes neither — the **instant of
the read**. The record therefore distinguishes "the server named this version"
from "this is when we looked", instead of letting a timestamp imply the page is
pinned. design-A9's rule applies: say so in the record rather than lowering the
contract to fit the source.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator, Optional, Sequence, Tuple

from ..locator_grammar import web_locator
from ..scope_record import KIND_WEB, ScopeRecord
from ..source_port import (
    AdapterError,
    OBLIGATION_READABLE,
    OBLIGATION_SOURCE_IDENTITY,
    ReadResult,
    SourceAdapter,
    SourceItem,
)

# The truncation sentinel the shipped fetch appends when it caps a response
# (`_factcheck_engine._fetch_one_url`). Recognised here so the adapter can REPORT
# a truncation the fetch performed, rather than the port having to infer one.
TRUNCATION_MARKER = "[…truncated at size cap…]"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class WebAdapter(SourceAdapter):
    """Reads web pages. Holds no bound, mints no pin, chooses no evidence branch."""

    kind = KIND_WEB

    # Declared capability, read by the port. A page can be re-opened by a checker
    # holding no credentials — that is what design-A29 means by "the existing
    # prefetch is what lets a checker re-open it", and it is why web resolves to
    # the `original` evidence path rather than the captured one.
    can_reopen_without_credentials = True

    def __init__(self,
                 fetch: Callable[[str, float, int], Tuple[str, str]],
                 *,
                 urls: Sequence[str] = (),
                 timeout_s: float = 15.0,
                 max_bytes: int = 512 * 1024,
                 version_of: Optional[Callable[[str], Optional[str]]] = None) -> None:
        """`fetch` is injected — see the module docstring for why.

        `max_bytes` is the budget the CALLER computed for this read and is
        forwarded to `fetch` verbatim; this adapter neither chooses it nor caps
        it. `version_of` is an optional hook returning a server-declared version
        for a URL (an ETag or Last-Modified the caller captured); when it returns
        nothing, the read instant is used and the record says which it was.
        """
        self._fetch = fetch
        self._urls = tuple(urls)
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._version_of = version_of

    # -- enumeration -------------------------------------------------------- #

    def enumerate_within(self, scope: ScopeRecord) -> Iterator[SourceItem]:
        """Yield each URL this run was asked to open, lazily.

        Filters **nothing**: containment is the port's decision, made against the
        declaration before any read. An adapter that skipped an out-of-scope URL
        here would make the refusal silent, which is the failure mode the port's
        single passage point exists to prevent — the same reason `code_base` yields
        a `CLAUDE.md` instead of hiding it.
        """
        for url in self._urls:
            yield SourceItem(item_id=url, kind=KIND_WEB, target=url, depth=0)

    # -- read --------------------------------------------------------------- #

    def read_with_pin(self, item: SourceItem) -> ReadResult:
        """Fetch one URL and report what the pin needs.

        Raises :class:`AdapterError` naming the failed obligation — the port turns
        it into a recorded degradation. This adapter never decides that an item is
        rejected; it reports which obligation it could not satisfy.
        """
        url = item.target
        try:
            status, payload = self._fetch(url, self._timeout_s, self._max_bytes)
        except Exception as e:                       # noqa: BLE001
            # A mis-behaving fetch is a failed obligation, not a crash — the port
            # forbids a third outcome, so nothing propagates out of here.
            raise AdapterError(OBLIGATION_READABLE,
                               f"fetch raised {type(e).__name__}: {e}") from None

        if status != "ok":
            # The shipped fetch reports its own reason ("HTTP 404", a timeout, a
            # URL error). Carried through verbatim so the recorded degradation
            # says what actually happened rather than "unreadable".
            raise AdapterError(OBLIGATION_READABLE, str(payload))

        content = payload if isinstance(payload, str) else str(payload)
        truncated = TRUNCATION_MARKER in content

        # Source identity is the page's own address. It is deliberately NOT the
        # host: design-A29 pins the page, and the reference document says so in
        # as many words ("Pins the page, not the site").
        source_id = url
        if not source_id:
            raise AdapterError(OBLIGATION_SOURCE_IDENTITY,
                               "the source exposes no identity to pin")

        declared_version = self._version_of(url) if self._version_of else None
        version = declared_version or f"read-at:{_utc_now()}"

        return ReadResult(source_id=source_id, version=version,
                          locator=web_locator(url),
                          content=content, dirty=False, truncated=truncated)
