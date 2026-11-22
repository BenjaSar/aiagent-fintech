import pytest

from fintech_agent.application.payee_registry import (
    ApprovedPayee,
    PayeeRegistry,
    PayeeRegistryError,
)
from tests.definition_fixtures import (
    APPROVED_AT,
    OWNER,
    PAYEE,
    PAYEE_LABEL,
    build_registry,
)
from tests.policy_fixtures import RECIPIENT, TARGET

SECOND = ApprovedPayee("supplier", RECIPIENT, "Acme Supplies", OWNER, APPROVED_AT)


def test_an_approved_payee_can_be_resolved_with_its_provenance() -> None:
    registry = build_registry()
    payee = registry.payee(PAYEE)
    assert payee.address == TARGET
    assert payee.label == PAYEE_LABEL
    assert payee.approved_by == OWNER
    assert payee.approved_at == APPROVED_AT
    assert registry.identifiers == (PAYEE,)


def test_resolving_keeps_the_order_asked_for() -> None:
    registry = build_registry()
    registry.approve(SECOND)
    resolved = registry.resolve(("supplier", PAYEE))
    assert [payee.identifier for payee in resolved] == ["supplier", PAYEE]


def test_an_unknown_payee_is_refused() -> None:
    registry = build_registry()
    with pytest.raises(PayeeRegistryError, match="not an approved payee"):
        registry.payee("stranger")
    with pytest.raises(PayeeRegistryError, match="not an approved payee"):
        registry.resolve((PAYEE, "stranger"))


def test_a_repeated_identifier_or_address_is_refused() -> None:
    registry = build_registry()
    with pytest.raises(PayeeRegistryError, match="already approved"):
        registry.approve(ApprovedPayee(PAYEE, RECIPIENT, "Other", OWNER, APPROVED_AT))
    with pytest.raises(PayeeRegistryError, match="already approved"):
        registry.approve(ApprovedPayee("other", TARGET, "Other", OWNER, APPROVED_AT))
    with pytest.raises(PayeeRegistryError, match="once"):
        registry.resolve((PAYEE, PAYEE))


def test_a_payee_can_be_withdrawn_once() -> None:
    registry = build_registry()
    registry.withdraw(PAYEE)
    assert registry.identifiers == ()
    with pytest.raises(PayeeRegistryError, match="not an approved payee"):
        registry.withdraw(PAYEE)


def test_labels_are_offered_by_address_for_the_approval_screen() -> None:
    registry = build_registry()
    registry.approve(SECOND)
    assert registry.labels_by_address == {
        TARGET: PAYEE_LABEL,
        RECIPIENT: "Acme Supplies",
    }


def test_an_empty_registry_resolves_nothing() -> None:
    assert PayeeRegistry().resolve(()) == ()
    assert PayeeRegistry().labels_by_address == {}


@pytest.mark.parametrize(
    "arguments",
    [
        ("Vault", TARGET, "Vault", OWNER, APPROVED_AT),
        ("v", TARGET, "Vault", OWNER, APPROVED_AT),
        ("vault", TARGET.upper().replace("0X", "0x"), "Vault", OWNER, APPROVED_AT),
        ("vault", "0xabc", "Vault", OWNER, APPROVED_AT),
        ("vault", TARGET, "", OWNER, APPROVED_AT),
        ("vault", TARGET, "Vault\u200bOne", OWNER, APPROVED_AT),
        ("vault", TARGET, "Vault\nOne", OWNER, APPROVED_AT),
        ("vault", TARGET, "l" * 41, OWNER, APPROVED_AT),
        ("vault", TARGET, "Vault", "0xabc", APPROVED_AT),
        ("vault", TARGET, "Vault", OWNER, 0),
    ],
)
def test_a_malformed_payee_is_refused(
    arguments: tuple[str, str, str, str, int],
) -> None:
    with pytest.raises(PayeeRegistryError):
        ApprovedPayee(*arguments)
