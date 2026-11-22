from collections.abc import Mapping
from typing import Any

from .session_policy import AssetRule, SessionPolicy
from .user_operation_hash import HashingDomain

Document = Mapping[str, Any]


def policy_to_document(policy: SessionPolicy) -> dict[str, Any]:
    return {
        "chain_id": policy.domain.chain_id,
        "entry_point": policy.domain.entry_point,
        "account": policy.account,
        "validator": policy.validator,
        "hook": policy.hook,
        "allowed_targets": sorted(policy.allowed_targets),
        "rules": [[rule.asset, str(rule.cap)] for rule in policy.rules],
        "allowed_paymasters": sorted(policy.allowed_paymasters),
        "allowed_payees": sorted(policy.allowed_payees),
        "payments_only": policy.payments_only,
        "valid_after": policy.valid_after,
        "valid_until": policy.valid_until,
        "window_seconds": policy.window_seconds,
        "maximum_gas_cost": str(policy.maximum_gas_cost),
    }


def policy_from_document(document: Document) -> SessionPolicy:
    return SessionPolicy(
        domain=HashingDomain(int(document["chain_id"]), str(document["entry_point"])),
        account=str(document["account"]),
        validator=str(document["validator"]),
        hook=str(document["hook"]),
        allowed_targets=frozenset(str(item) for item in document["allowed_targets"]),
        rules=tuple(
            AssetRule(str(asset), int(cap)) for asset, cap in document["rules"]
        ),
        allowed_paymasters=frozenset(
            str(item) for item in document["allowed_paymasters"]
        ),
        allowed_payees=frozenset(str(item) for item in document["allowed_payees"]),
        valid_after=int(document["valid_after"]),
        valid_until=int(document["valid_until"]),
        window_seconds=int(document["window_seconds"]),
        maximum_gas_cost=int(document["maximum_gas_cost"]),
        payments_only=bool(document["payments_only"]),
    )
