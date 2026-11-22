from dataclasses import replace

import pytest

from fintech_agent.application.policy_derivation import derive_session_policy
from fintech_agent.domain.agent_approval import (
    AgentAcknowledgement,
    AgentApprovalError,
    AgentApprovalScreen,
    AgentPresentation,
    agent_fingerprint,
    approve_agent,
    describe_agent,
)
from fintech_agent.domain.agent_definition import AgentDefinition
from fintech_agent.domain.agent_definition_document import definition_digest
from fintech_agent.domain.approval_display import AssetDisplay, DisplayCatalog
from fintech_agent.domain.approval_text import render_agent_text
from fintech_agent.domain.policy_approval import (
    ApprovalLimits,
    ApprovalScreen,
    policy_digest,
)
from fintech_agent.domain.session_policy import AssetRule
from fintech_agent.domain.tool_catalog import STAGE_ONE_PAYMENT_CATALOG
from tests.definition_fixtures import (
    OWNER,
    PAYEE_LABEL,
    build_definition,
    build_registry,
    build_request,
)
from tests.policy_fixtures import ETHER, NOW, STRANGER, TARGET

SESSION_KEY = bytes.fromhex("02" + "11" * 32)
OTHER_KEY = bytes.fromhex("03" + "22" * 32)
APPROVED_AT = NOW + 1
FINGERPRINT_CHARACTERS = 32


def display_catalog() -> DisplayCatalog:
    return DisplayCatalog(
        AssetDisplay("ETH", 18), {}, build_registry().labels_by_address
    )


def present(
    definition: AgentDefinition | None = None,
    key: bytes = SESSION_KEY,
) -> AgentApprovalScreen:
    definition = definition or build_definition()
    policy = derive_session_policy(build_request(definition))
    return describe_agent(
        AgentPresentation(
            definition,
            policy,
            key,
            STAGE_ONE_PAYMENT_CATALOG,
            display_catalog(),
            ApprovalLimits(),
            NOW,
        )
    )


def policy_of(screen: AgentApprovalScreen) -> ApprovalScreen:
    policy_screen = screen.policy_screen
    assert policy_screen is not None
    return policy_screen


def acknowledge(
    screen: AgentApprovalScreen, **overrides: object
) -> AgentAcknowledgement:
    fields: dict[str, object] = {
        "fingerprint": screen.fingerprint,
        "warning_keys": screen.required_acknowledgements,
        "approver": OWNER,
        "approved_at": APPROVED_AT,
    }
    fields.update(overrides)
    return AgentAcknowledgement(**fields)  # type: ignore[arg-type]


def test_the_screen_shows_the_definition_and_its_derived_policy() -> None:
    screen = present()
    assert screen.definition.name == "Supplier Payments"
    assert screen.model_text.startswith("claude-sonnet-5-5, temperature 0.00")
    assert screen.budget_text == "0.5 ETH"
    assert [line.identifier for line in screen.tool_lines] == [
        "list_payees",
        "get_balance",
        "get_payment_status",
        "pay_payee",
    ]
    assert [line.kind for line in screen.tool_lines] == [
        "read",
        "read",
        "read",
        "payment",
    ]
    assert [line.label for line in policy_of(screen).payee_lines] == [PAYEE_LABEL]


def test_the_fingerprint_binds_the_definition_the_policy_and_the_key() -> None:
    screen = present()
    assert len(screen.fingerprint) == FINGERPRINT_CHARACTERS
    assert screen.fingerprint == agent_fingerprint(screen.binding)
    assert screen.binding.definition_digest == definition_digest(build_definition())
    assert screen.binding.policy_digest == policy_digest(policy_of(screen).policy)
    assert screen.short_fingerprint.replace(" ", "") == screen.fingerprint


def test_the_fingerprint_changes_with_each_part_of_the_binding() -> None:
    binding = present().binding
    assert agent_fingerprint(binding) != agent_fingerprint(
        replace(binding, definition_digest="ab" * 32)
    )
    assert agent_fingerprint(binding) != agent_fingerprint(
        replace(binding, policy_digest="cd" * 32)
    )
    assert agent_fingerprint(binding) != agent_fingerprint(
        replace(binding, group_public_key=OTHER_KEY)
    )


def test_an_approval_by_the_owner_returns_the_approved_agent() -> None:
    screen = present()
    approved = approve_agent(screen, acknowledge(screen))
    assert approved.definition == build_definition()
    assert approved.policy == policy_of(screen).policy
    assert approved.fingerprint == screen.fingerprint
    assert approved.approver == OWNER
    assert approved.approved_at == APPROVED_AT


def test_an_approval_cannot_be_replayed_onto_another_definition() -> None:
    first = present()
    second = present(build_definition(name="Reserve Payments"))
    with pytest.raises(AgentApprovalError, match="another agent"):
        approve_agent(second, acknowledge(first))


def test_an_approval_cannot_be_replayed_onto_another_session_key() -> None:
    screen = present()
    rekeyed = present(key=OTHER_KEY)
    with pytest.raises(AgentApprovalError, match="another agent"):
        approve_agent(rekeyed, acknowledge(screen))


def test_a_changed_definition_needs_a_fresh_approval() -> None:
    before = present()
    after = present(
        build_definition(
            assets=(AssetRule(before.definition.assets[0].asset, ETHER // 2),)
        )
    )
    assert after.fingerprint != before.fingerprint
    with pytest.raises(AgentApprovalError, match="another agent"):
        approve_agent(after, acknowledge(before))


def test_only_the_owner_named_in_the_definition_can_approve() -> None:
    screen = present()
    with pytest.raises(AgentApprovalError, match="only the owner"):
        approve_agent(screen, acknowledge(screen, approver=STRANGER))


def test_every_warning_must_be_acknowledged() -> None:
    screen = present()
    with pytest.raises(AgentApprovalError, match="every warning"):
        approve_agent(screen, acknowledge(screen, warning_keys=frozenset({"made-up"})))


def test_an_approval_needs_its_time() -> None:
    screen = present()
    with pytest.raises(AgentApprovalError, match="the time it was given"):
        approve_agent(screen, acknowledge(screen, approved_at=0))


def test_an_unlabelled_payee_still_needs_its_acknowledgement() -> None:
    definition = build_definition()
    policy = derive_session_policy(build_request(definition))
    screen = describe_agent(
        AgentPresentation(
            definition,
            policy,
            SESSION_KEY,
            STAGE_ONE_PAYMENT_CATALOG,
            DisplayCatalog(AssetDisplay("ETH", 18)),
            ApprovalLimits(),
            NOW,
        )
    )
    assert screen.required_acknowledgements
    with pytest.raises(AgentApprovalError, match="every warning"):
        approve_agent(screen, acknowledge(screen, warning_keys=frozenset()))


def test_a_policy_that_is_not_the_definitions_is_refused() -> None:
    definition = build_definition()
    policy = derive_session_policy(build_request(definition))
    for mismatch in (
        replace(policy, rules=(AssetRule(policy.rules[0].asset, ETHER * 2),)),
        replace(policy, window_seconds=3_600),
        replace(policy, valid_until=policy.valid_until + 1),
        replace(policy, payments_only=False, allowed_payees=frozenset()),
    ):
        with pytest.raises(AgentApprovalError):
            describe_agent(
                AgentPresentation(
                    definition,
                    mismatch,
                    SESSION_KEY,
                    STAGE_ONE_PAYMENT_CATALOG,
                    display_catalog(),
                    ApprovalLimits(),
                    NOW,
                )
            )


def test_a_missing_session_key_is_refused() -> None:
    with pytest.raises(AgentApprovalError, match="session key is missing"):
        present(key=b"")


def test_the_text_shows_the_tools_the_instructions_and_the_caveat() -> None:
    text = render_agent_text(present())
    assert "APPROVE A NEW AGENT" in text
    assert "pay_payee (payment)" in text
    assert "Pay only approved payees and never exceed a cap." in text
    assert "They grant it nothing" in text
    assert "Agent fingerprint:" in text
    assert TARGET in text
    assert "It may pay only these addresses:" in text
