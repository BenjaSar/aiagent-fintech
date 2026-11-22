import pytest

from fintech_agent.application.enclave_service import EnclaveService
from fintech_agent.application.enclave_tenancy import (
    PRIMARY_TENANT,
    TenancyParts,
    TenantedEnclave,
    TenantRequest,
    UnknownTenantError,
    require_tenant_name,
)
from tests.application.fixtures import build_node, service_for

FIRST_TOKEN = b"0123456789abcdef0123456789abcdef"
SECOND_TOKEN = b"fedcba9876543210fedcba9876543210"
FIRST = "agent-one"
SECOND = "agent-two"


class Directory:
    def __init__(self, tokens: dict[str, bytes]) -> None:
        self.tokens = tokens

    def token_of(self, tenant: str) -> bytes:
        return self.tokens.get(tenant, b"")


class Builder:
    def __init__(self) -> None:
        self.built: list[str] = []

    def __call__(self, tenant: str) -> EnclaveService:
        self.built.append(tenant)
        return service_for(build_node(len(self.built)))


def enclave(tokens: dict[str, bytes]) -> tuple[TenantedEnclave, Builder]:
    builder = Builder()
    return TenantedEnclave(TenancyParts(Directory(tokens), builder)), builder


def test_a_tenant_name_of_letters_digits_and_dashes_is_accepted() -> None:
    assert require_tenant_name("agent-one-r2") == "agent-one-r2"


@pytest.mark.parametrize(
    "name", ["", "Agent-One", "agent one", "../escape", "agent/one", "-leading"]
)
def test_a_tenant_name_outside_the_alphabet_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="tenant name"):
        require_tenant_name(name)


def test_an_over_long_tenant_name_is_refused() -> None:
    with pytest.raises(ValueError, match="at most"):
        require_tenant_name("a" * 65)


def test_a_tenant_presenting_its_own_token_is_admitted() -> None:
    tenanted, _ = enclave({FIRST: FIRST_TOKEN})

    assert tenanted.admits(TenantRequest(FIRST, FIRST_TOKEN))


def test_a_tenant_presenting_another_tenants_token_is_refused() -> None:
    tenanted, _ = enclave({FIRST: FIRST_TOKEN, SECOND: SECOND_TOKEN})

    assert not tenanted.admits(TenantRequest(SECOND, FIRST_TOKEN))


def test_an_unadmitted_tenant_is_refused_with_a_real_token() -> None:
    tenanted, _ = enclave({FIRST: FIRST_TOKEN})

    assert not tenanted.admits(TenantRequest("agent-three", FIRST_TOKEN))


def test_a_tenant_without_a_token_is_never_admitted() -> None:
    tenanted, _ = enclave({FIRST: b""})

    assert not tenanted.admits(TenantRequest(FIRST, b""))


def test_the_default_tenant_is_not_admitted_unless_it_was_provisioned() -> None:
    tenanted, _ = enclave({FIRST: FIRST_TOKEN})

    assert not tenanted.admits(TenantRequest(PRIMARY_TENANT, FIRST_TOKEN))


def test_an_admitted_tenant_is_served_by_one_cached_service() -> None:
    tenanted, builder = enclave({FIRST: FIRST_TOKEN})
    request = TenantRequest(FIRST, FIRST_TOKEN)

    first = tenanted.service_for(request)

    assert tenanted.service_for(request) is first
    assert builder.built == [FIRST]


def test_two_tenants_are_served_by_two_services() -> None:
    tenanted, builder = enclave({FIRST: FIRST_TOKEN, SECOND: SECOND_TOKEN})

    first = tenanted.service_for(TenantRequest(FIRST, FIRST_TOKEN))
    second = tenanted.service_for(TenantRequest(SECOND, SECOND_TOKEN))

    assert first is not second
    assert builder.built == [FIRST, SECOND]


def test_a_refused_request_is_served_nothing() -> None:
    tenanted, builder = enclave({FIRST: FIRST_TOKEN, SECOND: SECOND_TOKEN})

    with pytest.raises(UnknownTenantError):
        tenanted.service_for(TenantRequest(SECOND, FIRST_TOKEN))
    assert builder.built == []


def test_a_forgotten_tenant_is_built_again_from_its_storage() -> None:
    tenanted, builder = enclave({FIRST: FIRST_TOKEN})
    request = TenantRequest(FIRST, FIRST_TOKEN)
    first = tenanted.service_for(request)

    tenanted.forget(FIRST)

    assert tenanted.service_for(request) is not first
    assert builder.built == [FIRST, FIRST]


def test_forgetting_an_unknown_tenant_changes_nothing() -> None:
    tenanted, builder = enclave({FIRST: FIRST_TOKEN})
    served = tenanted.service_for(TenantRequest(FIRST, FIRST_TOKEN))

    tenanted.forget(SECOND)

    assert tenanted.service_for(TenantRequest(FIRST, FIRST_TOKEN)) is served
    assert builder.built == [FIRST]
