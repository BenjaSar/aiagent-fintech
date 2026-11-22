import ast
from pathlib import Path

from fintech_agent.application.agent_runtime import AgentDecider, AgentRuntime
from fintech_agent.application.agent_synthesis import CandidateProposer
from fintech_agent.application.agent_tools import IntentSink

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "fintech_agent"
AGENT_PLANE_MODULES = (
    "fintech_agent.application.agent_runtime",
    "fintech_agent.application.agent_tools",
    "fintech_agent.application.agent_synthesis",
    "fintech_agent.adapters.scripted_decider",
    "fintech_agent.adapters.local_proposer",
)
FORBIDDEN_PARTS = (
    "frost",
    "sign_user_operation",
    "enclave_node",
    "enclave_service",
    "dkg_enclave",
    "key_share_record",
    "key_generation",
    "dpapi",
    "http_enclave",
    "x25519_channel",
    "bundler_rpc",
    "foundry_toolkit",
    "session_rotation",
    "composition",
)


def module_path(module: str) -> Path:
    return SOURCE_ROOT.joinpath(*module.split(".")).with_suffix(".py")


def imported_modules(module: str) -> set[str]:
    tree = ast.parse(module_path(module).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        found |= _names_of(node, module)
    return {name for name in found if name.startswith(PACKAGE)}


def _names_of(node: ast.AST, module: str) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if isinstance(node, ast.ImportFrom):
        return {_resolved(node, module)}
    return set()


def _resolved(node: ast.ImportFrom, module: str) -> str:
    if node.level == 0:
        return node.module or ""
    parent = module.rsplit(".", node.level)[0]
    return f"{parent}.{node.module}" if node.module else parent


def reachable_from(module: str) -> set[str]:
    seen: set[str] = set()
    frontier = {module}
    while frontier:
        current = frontier.pop()
        seen.add(current)
        frontier |= {name for name in imported_modules(current) if name not in seen}
        frontier = {name for name in frontier if module_path(name).exists()}
    return seen


def test_the_agent_plane_cannot_reach_any_key_bearing_module() -> None:
    for module in AGENT_PLANE_MODULES:
        offenders = [
            reached
            for reached in reachable_from(module)
            if any(part in reached for part in FORBIDDEN_PARTS)
        ]
        assert offenders == [], f"{module} can reach {offenders}"


def test_the_walk_would_notice_a_forbidden_import() -> None:
    reached = reachable_from("fintech_agent.composition.drill_toolbox")
    assert any("sign_user_operation" in name for name in reached)
    assert any("frost" in name for name in reached)


def test_the_runtime_reaches_the_policy_it_must_obey_and_nothing_that_signs() -> None:
    reached = reachable_from("fintech_agent.application.agent_tools")
    assert "fintech_agent.domain.session_policy" in reached
    assert "fintech_agent.domain.payment_intent" in reached
    assert not any("adapters" in name for name in reached)


def test_the_ports_the_agent_plane_is_given_offer_nothing_but_their_one_method() -> (
    None
):
    assert _public_methods(IntentSink) == {"submit"}
    assert _public_methods(AgentDecider) == {"decide"}
    assert _public_methods(CandidateProposer) == {"propose"}
    assert _public_methods(AgentRuntime) == {"run"}


def test_the_generator_reaches_the_catalog_and_the_registry_and_nothing_that_pays() -> (
    None
):
    reached = reachable_from("fintech_agent.application.agent_synthesis")
    assert "fintech_agent.domain.tool_catalog" in reached
    assert "fintech_agent.application.payee_registry" in reached
    assert not any("agent_tools" in name for name in reached)
    assert not any("payment_journal" in name for name in reached)


def _public_methods(target: type) -> set[str]:
    return {
        name
        for name in vars(target)
        if callable(getattr(target, name)) and not name.startswith("_")
    }
