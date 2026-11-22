import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .enclave_service import EnclaveService

PRIMARY_TENANT = "primary"
TENANT_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
MAXIMUM_TENANT_LENGTH = 64


class UnknownTenantError(Exception):
    pass


def require_tenant_name(tenant: str) -> str:
    if len(tenant) > MAXIMUM_TENANT_LENGTH:
        raise ValueError("a tenant name is at most 64 characters")
    if TENANT_NAME.fullmatch(tenant) is None:
        raise ValueError("a tenant name uses lower-case letters, digits and dashes")
    return tenant


class TenantDirectory(Protocol):
    def token_of(self, tenant: str) -> bytes: ...


@dataclass(frozen=True)
class TenantRequest:
    tenant: str
    token: bytes


@dataclass(frozen=True)
class TenancyParts:
    directory: TenantDirectory
    build: Callable[[str], EnclaveService]


class TenantedEnclave:
    def __init__(self, parts: TenancyParts) -> None:
        self._parts = parts
        self._services: dict[str, EnclaveService] = {}

    def admits(self, request: TenantRequest) -> bool:
        admitted = self._parts.directory.token_of(request.tenant)
        if not admitted:
            return False
        return hmac.compare_digest(admitted, request.token)

    def service_for(self, request: TenantRequest) -> EnclaveService:
        if not self.admits(request):
            raise UnknownTenantError("this tenant is not admitted to this enclave")
        tenant = request.tenant
        if tenant not in self._services:
            self._services[tenant] = self._parts.build(tenant)
        return self._services[tenant]

    def forget(self, tenant: str) -> None:
        self._services.pop(tenant, None)
