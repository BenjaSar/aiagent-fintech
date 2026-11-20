import itertools

import pytest

from fintech_agent.domain.relationship_graph import (
    AccessDeniedError,
    AccessRequest,
    Action,
    Delegation,
    Grant,
    MembershipEdge,
    RelationshipError,
    RelationshipGraph,
    TaskOpening,
)

NOW = 1_000
EXPIRY = 5_000
PLANNER = "did:example:planner"
WORKER = "did:example:worker"
OTHER = "did:example:other"
READ_PRICES = Grant(Action.READ, "dataset:prices")
READ_MARKET = Grant(Action.READ, "collection:market")


def opening(
    task: str, agent: str, grants: set[Grant], expires_at: int = EXPIRY
) -> TaskOpening:
    return TaskOpening(task, agent, frozenset(grants), expires_at)


def request(agent: str, task: str, action: Action, resource: str) -> AccessRequest:
    return AccessRequest(agent, task, action, resource)


def graph_with_task() -> RelationshipGraph:
    graph = RelationshipGraph()
    graph.open_task(opening("task-1", PLANNER, {READ_PRICES}), NOW)
    return graph


def denied(graph: RelationshipGraph, access: AccessRequest, reason: str) -> None:
    decision = graph.decide(access, NOW)
    assert not decision.allowed
    assert reason in decision.reason
    with pytest.raises(AccessDeniedError, match=reason):
        graph.require(access, NOW)


def test_access_inside_the_task_scope_is_allowed() -> None:
    graph = graph_with_task()
    access = request(PLANNER, "task-1", Action.READ, "dataset:prices")
    assert graph.decide(access, NOW).allowed
    graph.require(access, NOW)


def test_a_resource_outside_the_scope_is_denied() -> None:
    graph = graph_with_task()
    denied(
        graph,
        request(PLANNER, "task-1", Action.READ, "dataset:customers"),
        "scope",
    )


def test_an_action_outside_the_scope_is_denied() -> None:
    graph = graph_with_task()
    denied(graph, request(PLANNER, "task-1", Action.INVOKE, "dataset:prices"), "scope")


def test_a_task_that_does_not_exist_is_denied() -> None:
    graph = graph_with_task()
    denied(
        graph,
        request(PLANNER, "task-9", Action.READ, "dataset:prices"),
        "does not exist",
    )


def test_another_agent_cannot_use_the_task() -> None:
    graph = graph_with_task()
    denied(
        graph, request(OTHER, "task-1", Action.READ, "dataset:prices"), "not assigned"
    )


def test_an_expired_task_is_denied() -> None:
    graph = graph_with_task()
    access = request(PLANNER, "task-1", Action.READ, "dataset:prices")
    assert not graph.decide(access, EXPIRY).allowed
    assert graph.decide(access, EXPIRY - 1).allowed


def test_a_revoked_task_is_denied() -> None:
    graph = graph_with_task()
    graph.revoke_task("task-1")
    denied(graph, request(PLANNER, "task-1", Action.READ, "dataset:prices"), "revoked")


def test_revoking_an_unknown_task_is_an_error() -> None:
    with pytest.raises(RelationshipError):
        RelationshipGraph().revoke_task("task-9")


def test_a_grant_on_a_collection_covers_its_members() -> None:
    graph = RelationshipGraph()
    graph.add_membership(MembershipEdge("dataset:prices", "collection:market"))
    graph.open_task(opening("task-1", PLANNER, {READ_MARKET}), NOW)
    assert graph.decide(
        request(PLANNER, "task-1", Action.READ, "dataset:prices"), NOW
    ).allowed
    denied(
        graph,
        request(PLANNER, "task-1", Action.READ, "dataset:customers"),
        "scope",
    )


def test_coverage_follows_nested_collections_up_to_a_depth() -> None:
    graph = RelationshipGraph()
    chain = [
        "dataset:prices",
        "collection:a",
        "collection:b",
        "collection:c",
        "collection:d",
    ]
    for member, collection in itertools.pairwise(chain):
        graph.add_membership(MembershipEdge(member, collection))
    graph.open_task(
        opening("task-1", PLANNER, {Grant(Action.READ, "collection:d")}), NOW
    )
    assert graph.decide(
        request(PLANNER, "task-1", Action.READ, "dataset:prices"), NOW
    ).allowed
    graph.add_membership(MembershipEdge("collection:d", "collection:e"))
    graph.open_task(
        opening("task-2", PLANNER, {Grant(Action.READ, "collection:e")}), NOW
    )
    assert not graph.decide(
        request(PLANNER, "task-2", Action.READ, "dataset:prices"), NOW
    ).allowed


def test_a_membership_cycle_does_not_loop_and_does_not_widen_access() -> None:
    graph = RelationshipGraph()
    graph.add_membership(MembershipEdge("collection:a", "collection:b"))
    graph.add_membership(MembershipEdge("collection:b", "collection:a"))
    graph.open_task(
        opening("task-1", PLANNER, {Grant(Action.READ, "collection:a")}), NOW
    )
    assert not graph.decide(
        request(PLANNER, "task-1", Action.READ, "dataset:prices"), NOW
    ).allowed


def test_removing_a_membership_removes_the_access_it_gave() -> None:
    graph = RelationshipGraph()
    edge = MembershipEdge("dataset:prices", "collection:market")
    graph.add_membership(edge)
    graph.open_task(opening("task-1", PLANNER, {READ_MARKET}), NOW)
    access = request(PLANNER, "task-1", Action.READ, "dataset:prices")
    assert graph.decide(access, NOW).allowed
    graph.remove_membership(edge)
    assert not graph.decide(access, NOW).allowed


def test_a_resource_cannot_belong_to_itself_or_to_too_many_collections() -> None:
    graph = RelationshipGraph()
    with pytest.raises(RelationshipError):
        graph.add_membership(MembershipEdge("dataset:prices", "dataset:prices"))
    for number in range(8):
        graph.add_membership(MembershipEdge("dataset:prices", f"collection:c{number}"))
    with pytest.raises(RelationshipError, match="too many"):
        graph.add_membership(MembershipEdge("dataset:prices", "collection:extra"))


def test_a_sub_task_can_hold_a_subset_of_its_parents_grants() -> None:
    graph = RelationshipGraph()
    graph.add_membership(MembershipEdge("dataset:prices", "collection:market"))
    graph.open_task(opening("task-1", PLANNER, {READ_MARKET}), NOW)
    graph.delegate(Delegation("task-1", opening("task-2", WORKER, {READ_PRICES})), NOW)
    assert graph.decide(
        request(WORKER, "task-2", Action.READ, "dataset:prices"), NOW
    ).allowed


def test_a_sub_task_cannot_gain_more_than_its_parent() -> None:
    graph = graph_with_task()
    wider = opening("task-2", WORKER, {Grant(Action.READ, "dataset:customers")})
    with pytest.raises(RelationshipError, match="cannot pass on"):
        graph.delegate(Delegation("task-1", wider), NOW)
    other_action = opening("task-3", WORKER, {Grant(Action.INVOKE, "dataset:prices")})
    with pytest.raises(RelationshipError, match="cannot pass on"):
        graph.delegate(Delegation("task-1", other_action), NOW)


def test_a_sub_task_cannot_outlive_its_parent() -> None:
    graph = graph_with_task()
    longer = opening("task-2", WORKER, {READ_PRICES}, EXPIRY + 1)
    with pytest.raises(RelationshipError, match="outlive"):
        graph.delegate(Delegation("task-1", longer), NOW)


def test_a_sub_task_of_a_dead_parent_cannot_be_created() -> None:
    graph = graph_with_task()
    graph.revoke_task("task-1")
    with pytest.raises(RelationshipError, match="not live"):
        graph.delegate(
            Delegation("task-1", opening("task-2", WORKER, {READ_PRICES})), NOW
        )
    with pytest.raises(RelationshipError, match="not live"):
        graph.delegate(
            Delegation("task-9", opening("task-3", WORKER, {READ_PRICES})), NOW
        )


def test_revoking_a_parent_cuts_off_every_sub_task() -> None:
    graph = graph_with_task()
    graph.delegate(Delegation("task-1", opening("task-2", WORKER, {READ_PRICES})), NOW)
    graph.revoke_task("task-1")
    denied(graph, request(WORKER, "task-2", Action.READ, "dataset:prices"), "revoked")


def test_revoking_a_sub_task_leaves_the_parent_working() -> None:
    graph = graph_with_task()
    graph.delegate(Delegation("task-1", opening("task-2", WORKER, {READ_PRICES})), NOW)
    graph.revoke_task("task-2")
    assert graph.decide(
        request(PLANNER, "task-1", Action.READ, "dataset:prices"), NOW
    ).allowed


def test_a_sub_task_loses_access_when_the_parent_scope_shrinks() -> None:
    graph = RelationshipGraph()
    edge = MembershipEdge("dataset:prices", "collection:market")
    other = MembershipEdge("dataset:quotes", "collection:market")
    graph.add_membership(edge)
    graph.add_membership(other)
    graph.open_task(opening("task-1", PLANNER, {READ_MARKET}), NOW)
    graph.delegate(
        Delegation(
            "task-1", opening("task-2", WORKER, {Grant(Action.READ, "dataset:quotes")})
        ),
        NOW,
    )
    graph.remove_membership(other)
    denied(graph, request(WORKER, "task-2", Action.READ, "dataset:quotes"), "scope")


def test_the_delegation_chain_depth_is_limited() -> None:
    graph = graph_with_task()
    parent = "task-1"
    for number in range(2, 5):
        child = f"task-{number}"
        graph.delegate(
            Delegation(parent, opening(child, f"agent-{number}", {READ_PRICES})), NOW
        )
        parent = child
    with pytest.raises(RelationshipError, match="too deep"):
        graph.delegate(
            Delegation(parent, opening("task-5", "agent-5", {READ_PRICES})), NOW
        )


@pytest.mark.parametrize(
    "bad",
    [
        opening("Task One", PLANNER, {READ_PRICES}),
        opening("task-x", "", {READ_PRICES}),
        opening("task-x", PLANNER, set()),
        opening("task-x", PLANNER, {Grant(Action.READ, "*")}),
        opening("task-x", PLANNER, {Grant(Action.READ, "prices")}),
        opening("task-x", PLANNER, {READ_PRICES}, NOW),
    ],
)
def test_a_malformed_task_is_refused(bad: TaskOpening) -> None:
    with pytest.raises(RelationshipError):
        RelationshipGraph().open_task(bad, NOW)


def test_a_task_identifier_cannot_be_reused() -> None:
    graph = graph_with_task()
    with pytest.raises(RelationshipError, match="already used"):
        graph.open_task(opening("task-1", OTHER, {READ_PRICES}), NOW)


def test_a_task_cannot_carry_too_many_grants() -> None:
    grants = {Grant(Action.READ, f"dataset:d{number}") for number in range(33)}
    with pytest.raises(RelationshipError, match="few explicit"):
        RelationshipGraph().open_task(opening("task-x", PLANNER, grants), NOW)


def test_no_action_moves_funds_or_signs() -> None:
    assert {action.value for action in Action} == {"read", "invoke"}
