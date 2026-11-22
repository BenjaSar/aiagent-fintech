import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .plain_text import is_plain_text

SIGNATURE_TYPE = "Signature2026"
CARD_TYPE_OF_ACCESS_POLICY = "ReBAC"
MAXIMUM_CARD_BYTES = 16384
MAXIMUM_CAPABILITIES = 16
MAXIMUM_SHORT_TEXT = 64
MAXIMUM_DESCRIPTION = 512

TOP_LEVEL_KEYS = frozenset(
    {
        "name",
        "description",
        "url",
        "version",
        "identity",
        "capabilities",
        "endpoints",
        "authPolicy",
        "signature",
    }
)
IDENTITY_KEYS = frozenset({"did", "erc8004Registry", "agentTokenId"})
CAPABILITY_KEYS = frozenset({"name", "version", "description"})
ENDPOINT_KEYS = frozenset({"rpc", "events", "publicKey"})
POLICY_KEYS = frozenset(
    {"type", "allowedSchemes", "maxSpendLimitUSDPer24h", "supportedCurrencies"}
)
SIGNATURE_KEYS = frozenset({"type", "created", "creator", "signatureProof"})

AGENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9 _-]{0,63}")
CAPABILITY_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
SEMANTIC_VERSION = re.compile(r"(0|[1-9][0-9]{0,8})(\.(0|[1-9][0-9]{0,8})){2}")
DECENTRALIZED_IDENTIFIER = re.compile(r"did:[a-z0-9]{1,32}:[A-Za-z0-9._:%-]{1,200}")
EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
TOKEN_IDENTIFIER = re.compile(r"[0-9]{1,78}")
SCHEME_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,31}")
CURRENCY_CODE = re.compile(r"[A-Z0-9]{2,8}")
TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
SIGNATURE_PROOF = re.compile(r"[0-9a-f]{128}")
HOST_NAME = re.compile(
    r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+"
)
TOP_LEVEL_DOMAIN_LABEL = re.compile(r"[a-z][a-z0-9-]*")
PUNYCODE_PREFIX = "xn--"
DEFAULT_HTTPS_PORT = 443


class AgentCardError(ValueError):
    pass


@dataclass(frozen=True)
class CardCapability:
    name: str
    version: str
    description: str


@dataclass(frozen=True)
class AgentCard:
    name: str
    description: str
    origin: str
    version: str
    decentralized_identifier: str
    registry: str
    token_identifier: str
    capabilities: tuple[CardCapability, ...]
    signed_body: bytes
    signature: bytes

    @property
    def digest(self) -> bytes:
        return hashlib.sha256(self.signed_body).digest()


def normalize_origin(url: str) -> str:
    if not url.isascii():
        raise AgentCardError("a card address must be plain ASCII")
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme != "https" or host is None:
        raise AgentCardError("a card address must be an https address")
    if parts.username is not None or parts.password is not None:
        raise AgentCardError("a card address cannot carry credentials")
    if parts.query or parts.fragment:
        raise AgentCardError("a card address cannot carry a query or fragment")
    _require_plain_host(host)
    try:
        port = parts.port
    except ValueError as failure:
        raise AgentCardError("a card address has an invalid port") from failure
    if port not in (None, DEFAULT_HTTPS_PORT):
        raise AgentCardError("a card address must use the default https port")
    return f"https://{host}"


def _require_plain_host(host: str) -> None:
    if host != host.lower() or not HOST_NAME.fullmatch(host):
        raise AgentCardError("a card host must be a lowercase DNS name")
    labels = host.split(".")
    if any(label.startswith(PUNYCODE_PREFIX) for label in labels):
        raise AgentCardError("an internationalised host is not accepted")
    if not TOP_LEVEL_DOMAIN_LABEL.fullmatch(labels[-1]):
        raise AgentCardError("a card host cannot be an address literal")


def parse_agent_card(raw: bytes) -> AgentCard:
    document = _load(raw)
    _expect_keys(document, TOP_LEVEL_KEYS, "the card")
    identity = _mapping(document["identity"], IDENTITY_KEYS, "identity")
    signature = _mapping(document["signature"], SIGNATURE_KEYS, "signature")
    origin = normalize_origin(_text(document["url"], "url", 256))
    _check_endpoints(document["endpoints"], origin)
    _check_policy(document["authPolicy"])
    _check_signature_block(signature)
    return AgentCard(
        name=_pattern(document["name"], AGENT_NAME, "name"),
        description=_text(document["description"], "description", MAXIMUM_DESCRIPTION),
        origin=origin,
        version=_pattern(document["version"], SEMANTIC_VERSION, "version"),
        decentralized_identifier=_pattern(
            identity["did"], DECENTRALIZED_IDENTIFIER, "did"
        ),
        registry=_pattern(identity["erc8004Registry"], EVM_ADDRESS, "registry"),
        token_identifier=_pattern(identity["agentTokenId"], TOKEN_IDENTIFIER, "token"),
        capabilities=_capabilities(document["capabilities"]),
        signed_body=canonical_body(document),
        signature=bytes.fromhex(signature["signatureProof"]),
    )


def canonical_body(document: dict[str, Any]) -> bytes:
    body = {key: value for key, value in document.items() if key != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("ascii")


def _load(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAXIMUM_CARD_BYTES:
        raise AgentCardError("the card is too large")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_without_duplicates,
            parse_float=_refuse_number,
            parse_constant=_refuse_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as failure:
        raise AgentCardError("the card is not valid JSON") from failure
    if not isinstance(document, dict):
        raise AgentCardError("the card must be a JSON object")
    return document


def _without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise AgentCardError("the card repeats a field")
    return dict(pairs)


def _refuse_number(text: str) -> Any:
    raise AgentCardError(f"the number {text} must be written as an integer or a string")


def _expect_keys(mapping: dict[str, Any], expected: frozenset[str], where: str) -> None:
    if set(mapping) != expected:
        raise AgentCardError(f"{where} must have exactly the fields {sorted(expected)}")


def _mapping(value: Any, expected: frozenset[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentCardError(f"{where} must be an object")
    _expect_keys(value, expected, where)
    return value


def _text(value: Any, where: str, maximum: int) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= maximum:
        raise AgentCardError(f"{where} must be text of at most {maximum} characters")
    if not is_plain_text(value):
        raise AgentCardError(f"{where} contains control or invisible characters")
    return value


def _pattern(value: Any, pattern: re.Pattern[str], where: str) -> str:
    text = _text(value, where, MAXIMUM_DESCRIPTION)
    if not pattern.fullmatch(text):
        raise AgentCardError(f"{where} is malformed")
    return text


def _capabilities(value: Any) -> tuple[CardCapability, ...]:
    if not isinstance(value, list) or not 0 < len(value) <= MAXIMUM_CAPABILITIES:
        raise AgentCardError("capabilities must be a short, non-empty list")
    capabilities = tuple(_capability(entry) for entry in value)
    names = [capability.name for capability in capabilities]
    if len(set(names)) != len(names):
        raise AgentCardError("capability names must be unique")
    return capabilities


def _capability(entry: Any) -> CardCapability:
    mapping = _mapping(entry, CAPABILITY_KEYS, "a capability")
    return CardCapability(
        _pattern(mapping["name"], CAPABILITY_NAME, "capability name"),
        _pattern(mapping["version"], SEMANTIC_VERSION, "capability version"),
        _text(mapping["description"], "capability description", MAXIMUM_DESCRIPTION),
    )


def _check_endpoints(value: Any, origin: str) -> None:
    endpoints = _mapping(value, ENDPOINT_KEYS, "endpoints")
    for name, address in endpoints.items():
        if normalize_origin(_text(address, name, 256)) != origin:
            raise AgentCardError(f"the {name} endpoint is outside the card's origin")


def _check_policy(value: Any) -> None:
    policy = _mapping(value, POLICY_KEYS, "authPolicy")
    if policy["type"] != CARD_TYPE_OF_ACCESS_POLICY:
        raise AgentCardError("the card does not declare ReBAC")
    _check_list(policy["allowedSchemes"], SCHEME_NAME, "allowedSchemes")
    _check_list(policy["supportedCurrencies"], CURRENCY_CODE, "supportedCurrencies")
    limit = policy["maxSpendLimitUSDPer24h"]
    is_integer = isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0
    if not is_integer and not (isinstance(limit, str) and limit.isdecimal()):
        raise AgentCardError("the spend limit must be an integer or a decimal string")


def _check_list(value: Any, pattern: re.Pattern[str], where: str) -> None:
    if not isinstance(value, list) or not 0 < len(value) <= MAXIMUM_CAPABILITIES:
        raise AgentCardError(f"{where} must be a short, non-empty list")
    for entry in value:
        _pattern(entry, pattern, where)


def _check_signature_block(signature: dict[str, Any]) -> None:
    if signature["type"] != SIGNATURE_TYPE:
        raise AgentCardError("the signature type is not supported")
    _pattern(signature["created"], TIMESTAMP, "signature date")
    _pattern(signature["creator"], DECENTRALIZED_IDENTIFIER, "signature creator")
    _pattern(signature["signatureProof"], SIGNATURE_PROOF, "signature proof")
