import re
from dataclasses import dataclass
from enum import Enum

from .plain_text import is_plain_text

TOOL_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{1,47}")
MAXIMUM_SUMMARY = 160


class ToolCatalogError(ValueError):
    pass


class ToolKind(Enum):
    READ = "read"
    PAYMENT = "payment"


AUTHORITY_KINDS = frozenset({ToolKind.PAYMENT})
READING_KINDS = frozenset({ToolKind.READ})


@dataclass(frozen=True)
class CatalogTool:
    identifier: str
    kind: ToolKind
    summary: str

    def __post_init__(self) -> None:
        if not TOOL_IDENTIFIER.fullmatch(self.identifier):
            raise ToolCatalogError(
                "a tool identifier is 2 to 48 lowercase letters, digits or underscores"
            )
        if not 0 < len(self.summary) <= MAXIMUM_SUMMARY:
            raise ToolCatalogError(f"{self.identifier} needs a short summary")
        if not is_plain_text(self.summary):
            raise ToolCatalogError(f"{self.identifier} has a summary that is not plain")


class ToolCatalog:
    def __init__(self, tools: tuple[CatalogTool, ...]) -> None:
        identifiers = [tool.identifier for tool in tools]
        if not tools:
            raise ToolCatalogError("a catalog holds at least one tool")
        if len(set(identifiers)) != len(identifiers):
            raise ToolCatalogError("a catalog holds each tool once")
        self._tools = {tool.identifier: tool for tool in tools}

    @property
    def identifiers(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def tool(self, identifier: str) -> CatalogTool:
        known = self._tools.get(identifier)
        if known is None:
            raise ToolCatalogError(f"{identifier} is not in the tool catalog")
        return known

    def resolve(self, identifiers: tuple[str, ...]) -> tuple[CatalogTool, ...]:
        if not identifiers:
            raise ToolCatalogError("an agent needs at least one tool")
        if len(set(identifiers)) != len(identifiers):
            raise ToolCatalogError("an agent names each tool once")
        return tuple(self.tool(identifier) for identifier in identifiers)


def carries_payment_authority(tools: tuple[CatalogTool, ...]) -> bool:
    return any(tool.kind is ToolKind.PAYMENT for tool in tools)


def authority_tools(tools: tuple[CatalogTool, ...]) -> tuple[CatalogTool, ...]:
    return tuple(tool for tool in tools if tool.kind in AUTHORITY_KINDS)


STAGE_ONE_PAYMENT_CATALOG = ToolCatalog(
    (
        CatalogTool(
            "list_payees", ToolKind.READ, "List the payees the owner has approved."
        ),
        CatalogTool(
            "get_balance", ToolKind.READ, "Read the agent account's own balance."
        ),
        CatalogTool(
            "get_payment_status",
            ToolKind.READ,
            "Read the state of one payment by its idempotency key.",
        ),
        CatalogTool(
            "pay_payee",
            ToolKind.PAYMENT,
            "Pay an approved payee an amount of an approved asset.",
        ),
    )
)
