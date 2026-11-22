import hashlib
import re

IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9-]{1,31}")
TREASURY_IDENTIFIER = "treasury"
SALT_DOMAIN = b"fintech-agent/fleet-account-salt/v1"


class FleetIdentityError(ValueError):
    pass


def account_salt(identifier: str) -> str:
    _require_pattern(identifier)
    digest = hashlib.sha256(SALT_DOMAIN + identifier.encode("ascii")).digest()
    return "0x" + digest.hex()


def require_agent_identifier(identifier: str) -> str:
    _require_pattern(identifier)
    if identifier == TREASURY_IDENTIFIER:
        raise FleetIdentityError(
            f"{TREASURY_IDENTIFIER} is reserved for the funding account"
        )
    return identifier


def _require_pattern(identifier: str) -> None:
    if not IDENTIFIER_PATTERN.fullmatch(identifier):
        raise FleetIdentityError(
            "an identifier is 2 to 32 characters of lowercase letters, digits or "
            "hyphens, starting with a letter"
        )
