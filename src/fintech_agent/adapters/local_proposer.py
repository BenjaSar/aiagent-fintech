from fintech_agent.application.agent_synthesis import (
    GenerationCost,
    Proposal,
)
from fintech_agent.domain.task_specification import TaskSpecification

BASELINE_COST = GenerationCost(1_200, 400, 1_800)
BASELINE_PROMPT = (
    "Pay only the payees your owner approved. Never exceed a cap. If a payment is "
    "refused, report it and stop."
)
NAME_PREFIX = "Payment Agent"
READER_PREFIX = "Analysis Agent"
READER_PROMPT = (
    "Read only what your tools give you and report it plainly. You hold no account "
    "and can pay nobody."
)


class WidestAllowedProposer:
    def propose(self, specification: TaskSpecification) -> Proposal:
        return Proposal(
            name=f"{NAME_PREFIX} {specification.identifier}",
            purpose=specification.purpose,
            system_prompt=BASELINE_PROMPT,
            tools=specification.tools,
            payees=specification.payees,
            assets=specification.assets,
            valid_after=specification.valid_after,
            valid_until=specification.valid_until,
            risk_tier=specification.maximum_risk_tier,
            model=specification.model,
            cost=BASELINE_COST,
        )


class ReadOnlyProposer:
    def propose(self, specification: TaskSpecification) -> Proposal:
        return Proposal(
            name=f"{READER_PREFIX} {specification.identifier}",
            purpose=specification.purpose,
            system_prompt=READER_PROMPT,
            tools=specification.tools,
            payees=(),
            assets=(),
            valid_after=specification.valid_after,
            valid_until=specification.valid_until,
            risk_tier=specification.maximum_risk_tier,
            model=specification.model,
            cost=BASELINE_COST,
        )
