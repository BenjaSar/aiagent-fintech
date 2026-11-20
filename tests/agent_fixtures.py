import copy
import json
from typing import Any

from fintech_agent.adapters.ed25519_card_signatures import (
    CardSigningKey,
    generate_card_signing_key,
    sign_card_body,
)
from fintech_agent.domain.agent_card import canonical_body

SELLER_KEY = generate_card_signing_key()
SELLER_ORIGIN = "https://agent.fintech-example.com"
SELLER_DID = "did:example:seller-agent"
REGISTRY = "0x" + "ab" * 20


def card_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "name": "Market Data Seller",
        "description": "Sells real-time market data feeds.",
        "url": SELLER_ORIGIN,
        "version": "1.2.0",
        "identity": {
            "did": SELLER_DID,
            "erc8004Registry": REGISTRY,
            "agentTokenId": "42",
        },
        "capabilities": [
            {
                "name": "MarketDataFeed",
                "version": "1.0.0",
                "description": "Streams quotes for listed pairs.",
            }
        ],
        "endpoints": {
            "rpc": SELLER_ORIGIN + "/api/v1/a2a/rpc",
            "events": SELLER_ORIGIN + "/api/v1/a2a/events",
            "publicKey": SELLER_ORIGIN + "/.well-known/jwks.json",
        },
        "authPolicy": {
            "type": "ReBAC",
            "allowedSchemes": ["Bearer"],
            "maxSpendLimitUSDPer24h": 1000,
            "supportedCurrencies": ["USDC"],
        },
        "signature": {
            "type": "Signature2026",
            "created": "2026-11-15T12:00:00Z",
            "creator": "did:example:agent-controller",
            "signatureProof": "00" * 64,
        },
    }
    document.update(copy.deepcopy(overrides))
    return document


def signed_card(key: CardSigningKey = SELLER_KEY, **overrides: Any) -> bytes:
    document = card_document(**overrides)
    proof = sign_card_body(key, canonical_body(document)).hex()
    document["signature"]["signatureProof"] = proof
    return json.dumps(document).encode("utf-8")


def encode(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode("utf-8")
