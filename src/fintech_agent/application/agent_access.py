from fintech_agent.application.agent_directory import AgentDirectory
from fintech_agent.domain.relationship_graph import (
    AccessDeniedError,
    AccessRequest,
    Decision,
    RelationshipGraph,
)


class AgentAccessController:
    def __init__(self, directory: AgentDirectory, graph: RelationshipGraph) -> None:
        self._directory = directory
        self._graph = graph

    def decide(self, request: AccessRequest, now: int) -> Decision:
        if not self._directory.is_active(request.agent):
            return Decision(False, "the agent is not an active registered agent")
        return self._graph.decide(request, now)

    def require(self, request: AccessRequest, now: int) -> None:
        decision = self.decide(request, now)
        if not decision.allowed:
            raise AccessDeniedError(decision.reason)
