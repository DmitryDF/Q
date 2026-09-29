"""The credential path — hold and retrieve a secret by a non-secret identifier.

research-source-adapters S8 / plan action A5 (design-A5).

**On the name.** `credential_path` is the design's word, and it names a *path* in
the sense of a ROUTE TO A CREDENTIAL — not a filesystem path. Said here because
the module sits beside `scope_record`, whose "path" always means the filesystem
kind, and a reader who carried that meaning across would misread every function
in this file (Design Review item 5).

What this module is for
-----------------------
`DeclaredSource.connection_id` (`scope_record.py`) has shipped since S3: a
bounded-length opaque handle, validated to refuse secret-shaped values, carried
through the picker and serialised into the approval record. What never existed
was the HOLDER that identifier addresses. This module is that holder, and it is
the whole of S8's answer to "where does the token live" — the answer being
"somewhere the approval record is not".

**Source-agnostic on purpose, and this is load-bearing rather than tidy.** The
locked Guiding Policy says: *"Concentrate effort on that contract, not on the
adapters. Once the contract holds, an adapter is a small thing — which is what
makes Linear-now and Confluence-or-Jira-later an addition rather than a
rewrite."* So there is NO Linear vocabulary in this file, no Linear import, and
no Linear-shaped parameter. A credential is a string held under an identifier;
which remote it opens is not this module's business. If a future edit adds a
`workspace` or an `issue` argument here, the responsibility has leaked and the
split was wrong (Cockburn's Responsibility Alignment Test — the name, the
responsibility and the signatures must agree).

The port/adapter split
----------------------
`CredentialPort` is the interface the application depends on.
`InMemoryCredentialStore` is the test double. `KeychainCredentialStore` is the
production adapter, backed by the OS keychain. Per the four-step growth order in
`code_first_architecture.md`, the double comes first and the production adapter
is exercised against it before anything real is wired.

Dependency direction: this module imports nothing from the package.
"""

from __future__ import annotations

import re
import subprocess
from abc import ABC, abstractmethod
from typing import Dict, Optional

#: The keychain service name every credential here is filed under. One constant,
#: so a stored item can be found and revoked by hand without reading this code.
KEYCHAIN_SERVICE = "claude-research-source-adapters"

#: A connection identifier is the SAME grammar `scope_record.CONNECTION_ID_RE`
#: validates, restated rather than imported to keep the dependency direction
#: clean (this module imports nothing from the package). The two must agree; a
#: test asserts they do, so a change to one that is not made to the other fails
#: rather than drifting silently.
CONNECTION_ID_RE = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")


class CredentialError(RuntimeError):
    """Raised when a credential cannot be stored, found, or removed.

    A missing credential is an ERROR here, never an empty string. See
    :meth:`CredentialPort.fetch` for why that direction is the safe one.
    """


class CredentialPort(ABC):
    """Store, fetch and forget a secret addressed by a non-secret identifier."""

    @abstractmethod
    def store(self, connection_id: str, secret: str) -> None:
        """Record `secret` under `connection_id`, replacing any prior value.

        Replacing rather than refusing is deliberate: two sessions authorizing
        the same connection concurrently must leave the store holding one valid
        credential, not a corrupted pair (Design Review edge case).
        """

    @abstractmethod
    def fetch(self, connection_id: str) -> str:
        """Return the secret stored under `connection_id`.

        **Raises :class:`CredentialError` when nothing is stored.** It does NOT
        return `""` or `None`. An empty string would flow onward and be sent as
        an empty bearer token, and the failure would surface as a remote's
        confusing 401 rather than as the local, actionable fact that this
        connection was never made.
        """

    @abstractmethod
    def forget(self, connection_id: str) -> None:
        """Remove any secret stored under `connection_id`.

        Idempotent: forgetting an absent credential is a no-op, not an error.
        The postcondition a caller wants is "nothing is stored", and that is
        already true.
        """

    # -- shared validation -------------------------------------------------- #

    @staticmethod
    def _validate_id(connection_id: str) -> str:
        if not isinstance(connection_id, str) or not CONNECTION_ID_RE.match(
                connection_id):
            raise CredentialError(
                "connection_id must be an opaque identifier of at most 128 "
                "characters from [A-Za-z0-9._:-]; it is written into the "
                "approval record, so it may never be secret-shaped")
        return connection_id

    @staticmethod
    def _validate_secret(secret: str) -> str:
        if not isinstance(secret, str) or not secret:
            raise CredentialError(
                "refusing to store an empty credential; an empty secret would "
                "later be fetched successfully and sent as an empty bearer "
                "token, turning a local error into a remote 401")
        return secret


class InMemoryCredentialStore(CredentialPort):
    """The test double. Never used in production — it does not survive the process.

    Rung one of the growth order: the port's own contract, exercised with no OS
    dependency, so a test suite can drive the credential path on any machine.
    """

    def __init__(self) -> None:
        self._items: Dict[str, str] = {}

    def store(self, connection_id: str, secret: str) -> None:
        self._items[self._validate_id(connection_id)] = self._validate_secret(secret)

    def fetch(self, connection_id: str) -> str:
        cid = self._validate_id(connection_id)
        try:
            return self._items[cid]
        except KeyError:
            raise CredentialError(
                f"no credential stored for connection {cid!r}; this connection "
                "has not been made, or it was forgotten") from None

    def forget(self, connection_id: str) -> None:
        self._items.pop(self._validate_id(connection_id), None)


class KeychainCredentialStore(CredentialPort):
    """The production adapter — the OS keychain, reached through `security`.

    The secret is passed to the keychain and never written to a file this
    process controls, which is the entire point: the approval record and every
    other artifact a run leaves behind can then be committed or shared, because
    none of them has anywhere to put a token.

    `runner` is injectable so the adapter can be driven against a fake in tests
    (rung two of the growth order) without touching the operator's real
    keychain. It mirrors the injectable-runner shape `GitCommitGuard` already
    uses in this tree rather than inventing a second convention.
    """

    def __init__(self, service: str = KEYCHAIN_SERVICE, runner=None) -> None:
        self._service = service
        self._run = runner or self._default_runner

    @staticmethod
    def _default_runner(argv):
        return subprocess.run(argv, capture_output=True, text=True)

    def store(self, connection_id: str, secret: str) -> None:
        cid = self._validate_id(connection_id)
        secret = self._validate_secret(secret)
        # `-U` updates in place when the item exists, which is what makes a
        # second authorization of the same connection replace rather than
        # duplicate. `-w` takes the secret as an argument; the keychain, not
        # this process, is where it comes to rest.
        result = self._run(["security", "add-generic-password",
                            "-a", cid, "-s", self._service, "-w", secret, "-U"])
        if getattr(result, "returncode", 1) != 0:
            raise CredentialError(
                f"could not store the credential for connection {cid!r}: "
                f"{(getattr(result, 'stderr', '') or '').strip()}")

    def fetch(self, connection_id: str) -> str:
        cid = self._validate_id(connection_id)
        result = self._run(["security", "find-generic-password",
                            "-a", cid, "-s", self._service, "-w"])
        if getattr(result, "returncode", 1) != 0:
            raise CredentialError(
                f"no credential stored for connection {cid!r}; this connection "
                "has not been made, or it was forgotten")
        secret = (getattr(result, "stdout", "") or "").rstrip("\n")
        if not secret:
            # The keychain answered but gave nothing. Treated as absence rather
            # than returned, for the reason `fetch`'s contract gives.
            raise CredentialError(
                f"the keychain returned an empty credential for connection "
                f"{cid!r}; treating it as absent rather than sending an empty "
                "bearer token")
        return secret

    def forget(self, connection_id: str) -> None:
        cid = self._validate_id(connection_id)
        result = self._run(["security", "delete-generic-password",
                            "-a", cid, "-s", self._service])
        rc = getattr(result, "returncode", 1)
        if rc != 0:
            stderr = (getattr(result, "stderr", "") or "").lower()
            # Absent is the postcondition the caller wanted; only a real failure
            # is an error. Matching on the keychain's own words for "not found"
            # rather than on an exit code, because `security` uses 44 for that
            # and 1 for several unrelated conditions.
            if "could not be found" in stderr or "specified item could not" in stderr:
                return
            raise CredentialError(
                f"could not remove the credential for connection {cid!r}: "
                f"{(getattr(result, 'stderr', '') or '').strip()}")


def default_store(*, in_memory: bool = False) -> CredentialPort:
    """The store a caller gets when it does not choose one.

    Production is the keychain. `in_memory=True` is for tests and must never be
    the production default: an in-memory credential store would make every run
    re-authorize, which trains a person to click through consent screens — the
    opposite of what the connect gate is for.
    """
    return InMemoryCredentialStore() if in_memory else KeychainCredentialStore()


# --------------------------------------------------------------------------- #
# The artifact-wide no-secret scan (A5b — the gate for C13).
#
# WHY THIS IS A BUILT CHECK AND NOT A TABLED ONE. The Guiding Policy carries a
# lesson forward from S7: a gate written into a plan and never built protects
# nothing. C13 — "no artifact a research run leaves behind contains a secret
# obtained from the authorization" — was, at the point this was written, observed
# by no action's validation gate at all; its scan appeared only as a Gate-2
# mechanism row with nothing committing to build it. An independent checker found
# that, and this function is the repair.
#
# WHY IT IS ABSENCE-OBSERVING BY CONSTRUCTION. A scan that reports "clean" when it
# was pointed at nothing is worse than no scan, because it produces the assurance
# without the check. So `scan_artifacts_for_secret` REFUSES an empty artifact set
# and refuses an empty secret, and its test plants the sentinel into each artifact
# in turn and requires a FAILURE naming that artifact.
# --------------------------------------------------------------------------- #

#: The four artifact classes C13 enumerates. Named so a finding can say WHICH
#: artifact held the secret — "a secret leaked" is not actionable; "the findings
#: report holds it" is.
ARTIFACT_APPROVAL_RECORD = "approval record"
ARTIFACT_FINDINGS_REPORT = "findings report"
ARTIFACT_ADMISSION_RECORDS = "evidence and degradation records"
ARTIFACT_COMMITTED_FILES = "files committed to a repository"

C13_ARTIFACT_CLASSES = (
    ARTIFACT_APPROVAL_RECORD,
    ARTIFACT_FINDINGS_REPORT,
    ARTIFACT_ADMISSION_RECORDS,
    ARTIFACT_COMMITTED_FILES,
)

#: Below this length a secret is indistinguishable from ordinary text, so the
#: scan refuses rather than reporting matches nobody can act on. Editorial, and
#: stated as such: no source fixes this number. It is well below any real OAuth
#: access token and well above the length at which false matches begin.
_MIN_SCANNABLE_SECRET = 12


class SecretLeak(RuntimeError):
    """A secret was found in an artifact the run leaves behind.

    Carries `artifact` so the failure names the place, and deliberately does NOT
    carry the secret: an exception message is itself a thing that gets logged, and
    a leak detector that prints the leaked value has moved the leak rather than
    reported it.
    """

    def __init__(self, artifact: str) -> None:
        super().__init__(
            f"a secret from the authorization was found in the {artifact}; "
            "nothing a research run leaves behind may hold one")
        self.artifact = artifact


def scan_artifacts_for_secret(secret: str,
                              artifacts: Dict[str, str]) -> None:
    """Raise :class:`SecretLeak` if `secret` appears in any artifact's text.

    `artifacts` maps an artifact NAME (use the `ARTIFACT_*` constants) to the text
    that artifact holds. Returns ``None`` on a clean scan; the absence of a raise
    IS the pass, so a caller cannot mistake a falsy return for a failure.

    Refuses rather than passes on a vacuous call:

    * an empty or whitespace-only `secret` — there is nothing to look for, so a
      "clean" verdict would be meaningless;
    * an empty `artifacts` map — a scan of nothing finds nothing, which is the
      exact shape of a check that protects nothing.

    Substring matching, deliberately. A token can be embedded in a URL, a JSON
    blob, a shell line or a serialised header, and every one of those is a real
    leak; requiring a whole-word or line match would miss the common cases. The
    cost is that a very short secret could match innocent text — which is why the
    minimum length below refuses to scan for one, rather than reporting a false
    leak nobody can act on.
    """
    value = (secret or "").strip()
    if not value:
        raise ValueError(
            "scan_artifacts_for_secret needs a non-empty secret; scanning for "
            "nothing would report every artifact clean and check nothing")
    if len(value) < _MIN_SCANNABLE_SECRET:
        raise ValueError(
            f"a secret shorter than {_MIN_SCANNABLE_SECRET} characters cannot be "
            "scanned for without matching ordinary text; this refuses rather than "
            "reporting a leak that is not one")
    if not artifacts:
        raise ValueError(
            "scan_artifacts_for_secret needs at least one artifact; a scan over "
            "an empty set reports clean and protects nothing")
    for name, text in artifacts.items():
        if value in (text or ""):
            raise SecretLeak(name)
