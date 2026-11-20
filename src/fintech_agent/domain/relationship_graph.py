import re
from dataclasses import dataclass
from enum import Enum

RESOURCE_IDENTIFIER = re.compile(r"[a-z]{2,16}:[a-z0-9][a-z0-9._-]{0,63}")
SUBJECT_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:%-]{0,255}")
TASK_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
MAXIMUM_GRANTS_PER_TASK = 32
MAXIMUM_DELEGATION_DEPTH = 4
MAXIMUM_MEMBERSHIP_DEPTH = 4
MAXIMUM_PARENTS_PER_RESOURCE = 8


class Action(Enum):
    READ = "read"
    INVOKE = "invoke"


class RelationshipError(ValueError):
    pass


class AccessDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class Grant:
    action: Action
    resource: str


@dataclass(frozen=True)
class MembershipEdge:
    resource: str
    collection: str


@dataclass(frozen=True)
class TaskOpening:
    task_identifier: str
    agent: str
    grants: frozenset[Grant]
    expires_at: int


@dataclass(frozen=True)
class Delegation:
    parent_task: str
    opening: TaskOpening


@dataclass(frozen=True)
class AccessRequest:
    agent: str
    task: str
    action: Action
    resource: str


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


@dataclass
class _Task:
    opening: TaskOpening
    parent: str | None
    revoked: bool = False


class RelationshipGraph:
    def __init__(self) -> None:
        self._parents: dict[str, set[str]] = {}
        self._tasks: dict[str, _Task] = {}

    def add_membership(self, edge: MembershipEdge) -> None:
        _require_resource(edge.resource)
        _require_resource(edge.collection)
        if edge.resource == edge.collection:
            raise RelationshipError("a resource cannot be a member of itself")
        parents = self._parents.setdefault(edge.resource, set())
        if len(parents) >= MAXIMUM_PARENTS_PER_RESOURCE:
            raise RelationshipError("the resource belongs to too many collections")
        parents.add(edge.collection)

    def remove_membership(self, edge: MembershipEdge) -> None:
        self._parents.get(edge.resource, set()).discard(edge.collection)

    def open_task(self, opening: TaskOpening, now: int) -> None:
        self._validate_opening(opening, now)
        self._tasks[opening.task_identifier] = _Task(opening, None)

    def delegate(self, delegation: Delegation, now: int) -> None:
        opening = delegation.opening
        parent = self._tasks.get(delegation.parent_task)
        if parent is None or not self._is_live(delegation.parent_task, now):
            raise RelationshipError("the parent task is not live")
        self._validate_opening(opening, now)
        if opening.expires_at > parent.opening.expires_at:
            raise RelationshipError("a sub-task cannot outlive its parent")
        if len(self._chain(delegation.parent_task)) >= MAXIMUM_DELEGATION_DEPTH:
            raise RelationshipError("the delegation chain is too deep")
        for grant in opening.grants:
            if not self._chain_allows(delegation.parent_task, grant):
                raise RelationshipError(
                    f"the parent task cannot pass on {grant.action.value} {grant.resource}"
                )
        self._tasks[opening.task_identifier] = _Task(opening, delegation.parent_task)

    def revoke_task(self, task_identifier: str) -> None:
        task = self._tasks.get(task_identifier)
        if task is None:
            raise RelationshipError("the task does not exist")
        task.revoked = True

    def decide(self, request: AccessRequest, now: int) -> Decision:
        task = self._tasks.get(request.task)
        if task is None:
            return Decision(False, "the task does not exist")
        if task.opening.agent != request.agent:
            return Decision(False, "the task is not assigned to this agent")
        if not self._is_live(request.task, now):
            return Decision(
                False, "the task or one of its parents is revoked or expired"
            )
        grant = Grant(request.action, request.resource)
        if not self._chain_allows(request.task, grant):
            return Decision(False, "the task's scope does not include this access")
        return Decision(True, "within the task's scope")

    def require(self, request: AccessRequest, now: int) -> None:
        decision = self.decide(request, now)
        if not decision.allowed:
            raise AccessDeniedError(decision.reason)

    def _validate_opening(self, opening: TaskOpening, now: int) -> None:
        if not TASK_IDENTIFIER.fullmatch(opening.task_identifier):
            raise RelationshipError("the task identifier is malformed")
        if opening.task_identifier in self._tasks:
            raise RelationshipError("the task identifier is already used")
        if not SUBJECT_IDENTIFIER.fullmatch(opening.agent):
            raise RelationshipError("the agent identifier is malformed")
        if not 0 < len(opening.grants) <= MAXIMUM_GRANTS_PER_TASK:
            raise RelationshipError("a task needs a few explicit grants")
        for grant in opening.grants:
            _require_resource(grant.resource)
        if opening.expires_at <= now:
            raise RelationshipError("the task is already expired")

    def _chain(self, task_identifier: str) -> list[_Task]:
        chain: list[_Task] = []
        current: str | None = task_identifier
        while current is not None:
            task = self._tasks[current]
            chain.append(task)
            current = task.parent
        return chain

    def _is_live(self, task_identifier: str, now: int) -> bool:
        return all(
            not task.revoked and now < task.opening.expires_at
            for task in self._chain(task_identifier)
        )

    def _chain_allows(self, task_identifier: str, grant: Grant) -> bool:
        return all(
            self._task_allows(task, grant) for task in self._chain(task_identifier)
        )

    def _task_allows(self, task: _Task, wanted: Grant) -> bool:
        return any(
            granted.action == wanted.action
            and self._covers(granted.resource, wanted.resource)
            for granted in task.opening.grants
        )

    def _covers(self, granted_resource: str, resource: str) -> bool:
        frontier = {resource}
        visited = {resource}
        for _ in range(MAXIMUM_MEMBERSHIP_DEPTH + 1):
            if granted_resource in frontier:
                return True
            frontier = {
                parent
                for member in frontier
                for parent in self._parents.get(member, set())
            } - visited
            visited |= frontier
        return False


def _require_resource(resource: str) -> None:
    if not RESOURCE_IDENTIFIER.fullmatch(resource):
        raise RelationshipError(f"the resource {resource!r} is malformed")
