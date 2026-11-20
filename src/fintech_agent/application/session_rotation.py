import hashlib
from dataclasses import dataclass
from typing import Protocol

from fintech_agent.application.ports import PolicyIssuer
from fintech_agent.application.sign_user_operation import GroupConfiguration
from fintech_agent.domain.approval_display import DisplayCatalog
from fintech_agent.domain.policy_approval import (
    Acknowledgement,
    ApprovalLimits,
    ApprovalScreen,
    ApprovedPolicy,
    PolicyPresentation,
    approve,
    describe_policy,
)
from fintech_agent.domain.policy_transition import (
    HookChange,
    PolicyTransition,
    hook_changes,
)
from fintech_agent.domain.session_policy import SessionPolicy

FINGERPRINT_BYTES = 16


class RotationFailedError(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage


class RotationDeclinedError(RotationFailedError):
    pass


class GrantFailedError(Exception):
    pass


class SessionStateUnreadableError(Exception):
    pass


class RetirementFailedError(Exception):
    pass


@dataclass(frozen=True)
class InstalledSession:
    public_key: bytes
    valid_after: int
    valid_until: int


@dataclass(frozen=True)
class RotationReview:
    screen: ApprovalScreen
    key_fingerprint: str


@dataclass(frozen=True)
class RotationApproval:
    acknowledgement: Acknowledgement
    key_fingerprint: str


@dataclass(frozen=True)
class SessionGrant:
    approved: ApprovedPolicy
    group_public_key: bytes
    previous: SessionPolicy | None


class KeyCeremony(Protocol):
    def generate(self) -> GroupConfiguration: ...

    def discard(self) -> None: ...


class PolicyApprover(Protocol):
    def review(self, review: RotationReview) -> RotationApproval | None: ...


class SessionGrantor(Protocol):
    def apply(self, grant: SessionGrant) -> None: ...

    def installed(self) -> InstalledSession | None: ...


class PreviousSession(Protocol):
    def retire(self) -> None: ...


@dataclass(frozen=True)
class RotationParts:
    key_ceremony: KeyCeremony
    policy_issuer: PolicyIssuer
    approver: PolicyApprover
    grantor: SessionGrantor
    previous_session: PreviousSession


@dataclass(frozen=True)
class RotationRequest:
    previous: SessionPolicy | None
    following: SessionPolicy
    catalog: DisplayCatalog
    limits: ApprovalLimits
    now: int


@dataclass(frozen=True)
class RotationOutcome:
    group_public_key: bytes
    policy_digest: str
    key_fingerprint: str
    hook_changes: tuple[HookChange, ...]
    previous_retired: bool


def key_fingerprint(policy_digest: str, group_public_key: bytes) -> str:
    digest = hashlib.sha256(bytes.fromhex(policy_digest) + group_public_key).digest()
    return digest[:FINGERPRINT_BYTES].hex()


class RotateSession:
    def __init__(self, parts: RotationParts) -> None:
        self._parts = parts

    def execute(self, request: RotationRequest) -> RotationOutcome:
        changes = self._planned_changes(request)
        screen = describe_policy(
            PolicyPresentation(
                request.following, request.catalog, request.limits, request.now
            )
        )
        group = self._parts.key_ceremony.generate()
        public_key = group.group_public_key.serialize()
        try:
            approved = self._approved(screen, public_key)
            self._parts.policy_issuer.issue(approved.policy)
        except Exception:
            self._parts.key_ceremony.discard()
            raise
        self._grant(SessionGrant(approved, public_key, request.previous))
        self._verify(request, public_key)
        return RotationOutcome(
            public_key,
            screen.policy_digest,
            key_fingerprint(screen.policy_digest, public_key),
            changes,
            self._retire(),
        )

    def _planned_changes(self, request: RotationRequest) -> tuple[HookChange, ...]:
        if request.previous is None:
            return ()
        return hook_changes(PolicyTransition(request.previous, request.following))

    def _approved(self, screen: ApprovalScreen, public_key: bytes) -> ApprovedPolicy:
        expected = key_fingerprint(screen.policy_digest, public_key)
        response = self._parts.approver.review(RotationReview(screen, expected))
        if response is None:
            raise RotationDeclinedError("approval", "the user declined the session")
        if response.key_fingerprint != expected:
            raise RotationFailedError(
                "approval", "the approval is for a different session key"
            )
        return approve(screen, response.acknowledgement)

    def _grant(self, grant: SessionGrant) -> None:
        grantor = self._parts.grantor
        try:
            grantor.apply(grant)
        except GrantFailedError as failure:
            self._settle_failed_grant(grant, failure)

    def _settle_failed_grant(
        self, grant: SessionGrant, failure: GrantFailedError
    ) -> None:
        try:
            installed = self._parts.grantor.installed()
        except SessionStateUnreadableError as unreadable:
            raise RotationFailedError(
                "grant", f"the outcome is unknown ({failure}); nothing was retired"
            ) from unreadable
        if installed is not None and installed.public_key == grant.group_public_key:
            return
        self._parts.key_ceremony.discard()
        raise RotationFailedError("grant", str(failure)) from failure

    def _verify(self, request: RotationRequest, public_key: bytes) -> None:
        installed = self._parts.grantor.installed()
        expected = InstalledSession(
            public_key,
            request.following.valid_after,
            request.following.valid_until,
        )
        if installed != expected:
            raise RotationFailedError(
                "verify", "the validator does not hold the approved key and window"
            )

    def _retire(self) -> bool:
        try:
            self._parts.previous_session.retire()
        except RetirementFailedError:
            return False
        return True
