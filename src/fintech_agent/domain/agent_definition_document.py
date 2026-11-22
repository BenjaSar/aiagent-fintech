import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .agent_definition import (
    AgentDefinition,
    AgentType,
    Budget,
    ModelChoice,
)
from .session_policy import AssetRule

Document = Mapping[str, Any]


def definition_to_document(definition: AgentDefinition) -> dict[str, Any]:
    return {
        "schema_version": definition.schema_version,
        "agent_type": definition.agent_type.value,
        "name": definition.name,
        "purpose": definition.purpose,
        "model": {
            "identifier": definition.model.identifier,
            "temperature_hundredths": definition.model.temperature_hundredths,
            "maximum_output_tokens": definition.model.maximum_output_tokens,
        },
        "system_prompt": definition.system_prompt,
        "tools": list(definition.tools),
        "payees": list(definition.payees),
        "assets": [[rule.asset, str(rule.cap)] for rule in definition.assets],
        "window_seconds": definition.window_seconds,
        "valid_after": definition.valid_after,
        "valid_until": definition.valid_until,
        "budget": _budget_document(definition.budget),
        "risk_tier": definition.risk_tier,
        "owner": definition.owner,
        "provenance_identifier": definition.provenance_identifier,
    }


def definition_from_document(document: Document) -> AgentDefinition:
    model = document["model"]
    return AgentDefinition(
        schema_version=int(document["schema_version"]),
        agent_type=AgentType(str(document["agent_type"])),
        name=str(document["name"]),
        purpose=str(document["purpose"]),
        model=ModelChoice(
            str(model["identifier"]),
            int(model["temperature_hundredths"]),
            int(model["maximum_output_tokens"]),
        ),
        system_prompt=str(document["system_prompt"]),
        tools=tuple(str(item) for item in document["tools"]),
        payees=tuple(str(item) for item in document["payees"]),
        assets=tuple(
            AssetRule(str(declared), int(cap)) for declared, cap in document["assets"]
        ),
        window_seconds=int(document["window_seconds"]),
        valid_after=int(document["valid_after"]),
        valid_until=int(document["valid_until"]),
        budget=_budget_of(document["budget"]),
        risk_tier=int(document["risk_tier"]),
        owner=str(document["owner"]),
        provenance_identifier=str(document["provenance_identifier"]),
    )


def _budget_document(budget: Budget | None) -> list[str] | None:
    if budget is None:
        return None
    return [budget.asset, str(budget.total)]


def _budget_of(held: Any) -> Budget | None:
    if held is None:
        return None
    asset, total = held
    return Budget(str(asset), int(total))


def document_digest(document: Document) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def definition_digest(definition: AgentDefinition) -> str:
    return document_digest(definition_to_document(definition))
