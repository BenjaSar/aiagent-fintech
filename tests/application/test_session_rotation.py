from dataclasses import dataclass, field, replace

import pytest

from fintech_agent.application.ports import EnclaveRefusedError
from fintech_agent.application.session_rotation import (
    GrantFailedError,
    InstalledSession,
    RetirementFailedError,
    RotateSession,
    RotationApproval,
    RotationDeclinedError,
    RotationFailedError,
    RotationParts,
    RotationRequest,
    RotationReview,
    SessionGrant,
    SessionStateUnreadableError,
    key_fingerprint,
)
from fintech_agent.application.sign_user_operation import GroupConfiguration
from fintech_agent.domain.approval_display import AssetDisplay, DisplayCatalog
from fintech_agent.domain.policy_approval import (
    Acknowledgement,
    ApprovalLimits,
    ApprovalRefusedError,
)
from fintech_agent.domain.policy_transition import HookChange, HookChangeKind
from fintech_agent.domain.session_policy import AssetRule, SessionPolicy
from tests.application.fixtures import group_configuration
from tests.policy_fixtures import ETHER, NOW, STRANGER, TARGET, TOKEN, build_policy

CATALOG = DisplayCatalog(
    AssetDisplay("ETH", 18),
    {TOKEN: AssetDisplay("USDT", 6)},
    {TARGET: "DeFi Yield Vault"},
)
GROUP: GroupConfiguration = group_configuration()
PUBLIC_KEY = GROUP.group_public_key.serialize()


@dataclass
class World:
    events: list[str] = field(default_factory=list)
    installed_session: InstalledSession | None = None
    decline: bool = False
    wrong_fingerprint: bool = False
    drop_warnings: bool = False
    issue_failure: Exception | None = None
    apply_failure: GrantFailedError | None = None
    apply_installs: bool = True
    installs_wrong_key: bool = False
    installed_unreadable: bool = False
    retire_failure: bool = False
    grants: list[SessionGrant] = field(default_factory=list)
    reviews: list[RotationReview] = field(default_factory=list)


class Ceremony:
    def __init__(self, world: World) -> None:
        self._world = world

    def generate(self) -> GroupConfiguration:
        self._world.events.append("dkg")
        return GROUP

    def discard(self) -> None:
        self._world.events.append("discard")


class Issuer:
    def __init__(self, world: World) -> None:
        self._world = world

    def issue(self, policy: SessionPolicy) -> None:
        self._world.events.append("issue")
        if self._world.issue_failure is not None:
            raise self._world.issue_failure


class Approver:
    def __init__(self, world: World) -> None:
        self._world = world

    def review(self, review: RotationReview) -> RotationApproval | None:
        world = self._world
        world.events.append("review")
        world.reviews.append(review)
        if world.decline:
            return None
        keys = (
            frozenset()
            if world.drop_warnings
            else review.screen.required_acknowledgements
        )
        fingerprint = "0" * 32 if world.wrong_fingerprint else review.key_fingerprint
        return RotationApproval(
            Acknowledgement(review.screen.policy_digest, keys), fingerprint
        )


class Grantor:
    def __init__(self, world: World, following: SessionPolicy) -> None:
        self._world = world
        self._following = following

    def apply(self, grant: SessionGrant) -> None:
        world = self._world
        world.events.append("apply")
        world.grants.append(grant)
        if world.apply_installs:
            key = (
                b"\x02" + bytes(32)
                if world.installs_wrong_key
                else grant.group_public_key
            )
            world.installed_session = InstalledSession(
                key, self._following.valid_after, self._following.valid_until
            )
        if world.apply_failure is not None:
            raise world.apply_failure

    def installed(self) -> InstalledSession | None:
        if self._world.installed_unreadable:
            raise SessionStateUnreadableError("node unreachable")
        return self._world.installed_session


class Retirement:
    def __init__(self, world: World) -> None:
        self._world = world

    def retire(self) -> None:
        self._world.events.append("retire")
        if self._world.retire_failure:
            raise RetirementFailedError("cannot stop")


def request(following: SessionPolicy | None = None) -> RotationRequest:
    base = build_policy()
    return RotationRequest(
        previous=base,
        following=following
        or replace(
            base, valid_after=base.valid_until, valid_until=base.valid_until + 900
        ),
        catalog=CATALOG,
        limits=ApprovalLimits(),
        now=NOW,
    )


def rotation(world: World, following: SessionPolicy | None = None) -> RotateSession:
    target = following or request().following
    return RotateSession(
        RotationParts(
            Ceremony(world),
            Issuer(world),
            Approver(world),
            Grantor(world, target),
            Retirement(world),
        )
    )


def test_a_rotation_runs_the_ceremony_in_order_and_retires_the_old_session() -> None:
    world = World()
    outcome = rotation(world).execute(request())
    assert world.events == ["dkg", "review", "issue", "apply", "retire"]
    assert outcome.group_public_key == PUBLIC_KEY
    assert outcome.previous_retired
    assert outcome.hook_changes == ()
    assert outcome.key_fingerprint == key_fingerprint(outcome.policy_digest, PUBLIC_KEY)


def test_the_user_is_shown_the_policy_and_the_new_key_fingerprint() -> None:
    world = World()
    outcome = rotation(world).execute(request())
    review = world.reviews[0]
    assert review.screen.policy_digest == outcome.policy_digest
    assert review.key_fingerprint == outcome.key_fingerprint


def test_the_grant_carries_the_approved_policy_the_key_and_the_old_policy() -> None:
    world = World()
    rotation(world).execute(request())
    grant = world.grants[0]
    assert grant.group_public_key == PUBLIC_KEY
    assert grant.approved.policy == request().following
    assert grant.previous == build_policy()


def test_hook_changes_are_planned_from_the_two_policies() -> None:
    base = build_policy()
    following = replace(
        base,
        valid_after=base.valid_until,
        valid_until=base.valid_until + 900,
        rules=(AssetRule(base.rules[0].asset, 2 * ETHER), base.rules[1]),
        allowed_targets=frozenset({TARGET, STRANGER}),
    )
    world = World()
    outcome = rotation(world, following).execute(request(following))
    assert [change.kind for change in outcome.hook_changes] == [
        HookChangeKind.SET_CAP,
        HookChangeKind.ALLOW_TARGET,
    ]
    assert HookChange(HookChangeKind.ALLOW_TARGET, STRANGER) in outcome.hook_changes


def test_a_first_session_has_no_previous_policy() -> None:
    world = World()
    first = replace(request(), previous=None)
    outcome = rotation(world).execute(first)
    assert outcome.hook_changes == ()
    assert world.grants[0].previous is None


def test_a_declined_session_changes_nothing_and_discards_the_new_key() -> None:
    world = World(decline=True)
    with pytest.raises(RotationDeclinedError):
        rotation(world).execute(request())
    assert world.events == ["dkg", "review", "discard"]


def test_an_approval_for_another_key_is_refused_and_discarded() -> None:
    world = World(wrong_fingerprint=True)
    with pytest.raises(RotationFailedError, match="different session key"):
        rotation(world).execute(request())
    assert world.events == ["dkg", "review", "discard"]


def test_every_warning_must_be_acknowledged() -> None:
    world = World(drop_warnings=True)
    with pytest.raises(ApprovalRefusedError, match="every warning"):
        rotation(world).execute(request())
    assert "apply" not in world.events and "discard" in world.events


def test_an_unapprovable_policy_stops_before_any_key_is_generated() -> None:
    base = request().following
    unlimited = replace(base, rules=(AssetRule(TOKEN, 2**200),))
    world = World()
    with pytest.raises(ApprovalRefusedError, match="unlimited"):
        rotation(world, unlimited).execute(request(unlimited))
    assert world.events == []


def test_an_impossible_transition_stops_before_any_key_is_generated() -> None:
    moved = replace(request().following, window_seconds=3_600)
    world = World()
    with pytest.raises(Exception, match="rolling window"):
        rotation(world, moved).execute(request(moved))
    assert world.events == []


def test_an_enclave_that_refuses_the_policy_discards_the_new_key() -> None:
    world = World(issue_failure=EnclaveRefusedError("no"))
    with pytest.raises(EnclaveRefusedError):
        rotation(world).execute(request())
    assert world.events == ["dkg", "review", "issue", "discard"]


def test_a_failed_grant_that_did_not_install_discards_the_new_key() -> None:
    world = World(apply_failure=GrantFailedError("rejected"), apply_installs=False)
    with pytest.raises(RotationFailedError, match="grant: rejected"):
        rotation(world).execute(request())
    assert world.events[-1] == "discard" and "retire" not in world.events


def test_a_grant_that_failed_after_installing_the_key_is_still_completed() -> None:
    world = World(apply_failure=GrantFailedError("timeout after inclusion"))
    outcome = rotation(world).execute(request())
    assert outcome.previous_retired
    assert "discard" not in world.events


def test_a_failed_grant_that_installed_another_key_discards_the_new_key() -> None:
    world = World(apply_failure=GrantFailedError("rejected"), installs_wrong_key=True)
    with pytest.raises(RotationFailedError, match="grant: rejected"):
        rotation(world).execute(request())
    assert world.events[-1] == "discard" and "retire" not in world.events


def test_the_key_fingerprint_depends_on_the_key_and_the_policy() -> None:
    base = key_fingerprint("ab" * 32, PUBLIC_KEY)
    assert base != key_fingerprint("ab" * 32, b"" + PUBLIC_KEY[1:])
    assert base != key_fingerprint("cd" * 32, PUBLIC_KEY)
    assert len(base) == 32


def test_an_unknown_grant_outcome_keeps_everything() -> None:
    world = World(apply_failure=GrantFailedError("timeout"), installed_unreadable=True)
    with pytest.raises(RotationFailedError, match="outcome is unknown"):
        rotation(world).execute(request())
    assert "discard" not in world.events and "retire" not in world.events


def test_a_validator_holding_another_key_fails_verification_and_retires_nothing() -> (
    None
):
    world = World(installs_wrong_key=True)
    with pytest.raises(RotationFailedError, match="approved key and window"):
        rotation(world).execute(request())
    assert "retire" not in world.events


def test_a_validator_holding_another_window_fails_verification() -> None:
    base = request().following
    other = replace(base, valid_until=base.valid_until + 1)
    world = World()
    target = RotateSession(
        RotationParts(
            Ceremony(world),
            Issuer(world),
            Approver(world),
            Grantor(world, other),
            Retirement(world),
        )
    )
    with pytest.raises(RotationFailedError, match="approved key and window"):
        target.execute(request())


def test_a_failure_to_retire_is_reported_not_hidden() -> None:
    world = World(retire_failure=True)
    outcome = rotation(world).execute(request())
    assert not outcome.previous_retired
