import pytest

from fintech_agent.domain.tool_catalog import (
    AUTHORITY_KINDS,
    READING_KINDS,
    STAGE_ONE_PAYMENT_CATALOG,
    CatalogTool,
    ToolCatalog,
    ToolCatalogError,
    ToolKind,
    authority_tools,
    carries_payment_authority,
)

READ_TOOL = CatalogTool("list_payees", ToolKind.READ, "List the approved payees.")
PAYMENT_TOOL = CatalogTool("pay_payee", ToolKind.PAYMENT, "Pay an approved payee.")


def test_the_stage_one_catalog_holds_four_tools_and_one_can_pay() -> None:
    catalog = STAGE_ONE_PAYMENT_CATALOG
    assert catalog.identifiers == (
        "get_balance",
        "get_payment_status",
        "list_payees",
        "pay_payee",
    )
    assert catalog.tool("pay_payee").kind is ToolKind.PAYMENT
    paying = [
        identifier
        for identifier in catalog.identifiers
        if catalog.tool(identifier).kind is ToolKind.PAYMENT
    ]
    assert paying == ["pay_payee"]


def test_resolving_keeps_the_order_the_agent_declared() -> None:
    resolved = STAGE_ONE_PAYMENT_CATALOG.resolve(("pay_payee", "get_balance"))
    assert [tool.identifier for tool in resolved] == ["pay_payee", "get_balance"]


def test_an_uncatalogued_tool_is_refused() -> None:
    with pytest.raises(ToolCatalogError, match="not in the tool catalog"):
        STAGE_ONE_PAYMENT_CATALOG.resolve(("pay_payee", "drain_account"))
    with pytest.raises(ToolCatalogError, match="not in the tool catalog"):
        STAGE_ONE_PAYMENT_CATALOG.tool("swap")


def test_an_empty_or_repeated_tool_list_is_refused() -> None:
    with pytest.raises(ToolCatalogError, match="at least one tool"):
        STAGE_ONE_PAYMENT_CATALOG.resolve(())
    with pytest.raises(ToolCatalogError, match="once"):
        STAGE_ONE_PAYMENT_CATALOG.resolve(("pay_payee", "pay_payee"))


def test_payment_authority_is_recognised() -> None:
    assert carries_payment_authority((READ_TOOL, PAYMENT_TOOL))
    assert not carries_payment_authority((READ_TOOL,))
    assert not carries_payment_authority(())


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "a",
        "Pay_Payee",
        "pay-payee",
        "1pay",
        "pay payee",
        "p" * 49,
        "pay\u200bpayee",
    ],
)
def test_a_malformed_tool_identifier_is_refused(identifier: str) -> None:
    with pytest.raises(ToolCatalogError, match="identifier"):
        CatalogTool(identifier, ToolKind.READ, "A summary.")


@pytest.mark.parametrize("summary", ["", "s" * 161])
def test_a_summary_must_be_short(summary: str) -> None:
    with pytest.raises(ToolCatalogError, match="short summary"):
        CatalogTool("pay_payee", ToolKind.READ, summary)


def test_a_summary_must_be_plain_text() -> None:
    with pytest.raises(ToolCatalogError, match="not plain"):
        CatalogTool("pay_payee", ToolKind.READ, "Pay\u200ba payee.")


def test_a_catalog_needs_tools_and_refuses_repeats() -> None:
    with pytest.raises(ToolCatalogError, match="at least one tool"):
        ToolCatalog(())
    with pytest.raises(ToolCatalogError, match="each tool once"):
        ToolCatalog((READ_TOOL, READ_TOOL))


def test_every_tool_kind_is_classified_as_authority_or_reading() -> None:
    assert AUTHORITY_KINDS | READING_KINDS == set(ToolKind)
    assert not AUTHORITY_KINDS & READING_KINDS


def test_only_the_authority_kinds_are_reported_as_authority() -> None:
    tools = STAGE_ONE_PAYMENT_CATALOG.resolve(
        ("list_payees", "pay_payee", "get_balance")
    )

    held = authority_tools(tools)

    assert [tool.identifier for tool in held] == ["pay_payee"]
    assert authority_tools(STAGE_ONE_PAYMENT_CATALOG.resolve(("list_payees",))) == ()
