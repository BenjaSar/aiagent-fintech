import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum

from .approval_display import (
    AssetDisplay,
    DisplayCatalog,
    format_duration,
    format_units,
)
from .session_policy import NATIVE_ASSET, AssetRule, SessionPolicy
from .session_policy_document import policy_to_document

SECONDS_PER_DAY = 86400
UNLIMITED_CAP_THRESHOLD = 2**128
DIGEST_GROUP = 8
DIGEST_GROUPS_SHOWN = 4


class ApprovalRefusedError(ValueError):
    pass


class NarrowingError(ValueError):
    pass


class WarningCode(Enum):
    UNLABELLED_TARGET = "unlabelled-target"
    UNVERIFIED_ASSET = "unverified-asset"
    NATIVE_VALUE_TO_TARGETS = "native-value-to-targets"
    LONG_SESSION = "long-session"


@dataclass(frozen=True)
class ApprovalLimits:
    maximum_session_seconds: int = 30 * SECONDS_PER_DAY
    long_session_seconds: int = 7 * SECONDS_PER_DAY
    unlimited_cap_threshold: int = UNLIMITED_CAP_THRESHOLD


@dataclass(frozen=True)
class PolicyPresentation:
    policy: SessionPolicy
    catalog: DisplayCatalog
    limits: ApprovalLimits
    now: int


@dataclass(frozen=True)
class ApprovalWarning:
    code: WarningCode
    subject: str
    text: str

    @property
    def key(self) -> str:
        return f"{self.code.value}:{self.subject}"


@dataclass(frozen=True)
class AssetLine:
    asset: str
    symbol: str
    cap_text: str
    session_maximum_text: str
    verified: bool


@dataclass(frozen=True)
class TargetLine:
    address: str
    label: str | None


@dataclass(frozen=True)
class ApprovalScreen:
    policy: SessionPolicy
    policy_digest: str
    starts_at: str
    ends_at: str
    session_length: str
    window_length: str
    asset_lines: tuple[AssetLine, ...]
    target_lines: tuple[TargetLine, ...]
    gas_cap_text: str
    sponsors: tuple[str, ...]
    permitted: tuple[str, ...]
    never: tuple[str, ...]
    caveats: tuple[str, ...]
    control: str
    warnings: tuple[ApprovalWarning, ...]

    @property
    def required_acknowledgements(self) -> frozenset[str]:
        return frozenset(warning.key for warning in self.warnings)

    @property
    def short_digest(self) -> str:
        groups = [
            self.policy_digest[start : start + DIGEST_GROUP]
            for start in range(0, DIGEST_GROUP * DIGEST_GROUPS_SHOWN, DIGEST_GROUP)
        ]
        return " ".join(groups)


@dataclass(frozen=True)
class Acknowledgement:
    policy_digest: str
    warning_keys: frozenset[str]


@dataclass(frozen=True)
class ApprovedPolicy:
    policy: SessionPolicy
    policy_digest: str


@dataclass(frozen=True)
class Narrowing:
    lowered_caps: tuple[AssetRule, ...] = ()
    removed_targets: frozenset[str] = frozenset()
    removed_assets: frozenset[str] = frozenset()
    earlier_end: int | None = None


NEVER_STATEMENTS = (
    ("Change its own limits, or call your account, the policy hook or the EntryPoint."),
    "Send an unlisted token, or more than a listed cap, from your account.",
    "Act after the end time.",
)
CAVEAT_STATEMENTS = (
    (
        "A contract the agent may call can still use any allowance you gave it "
        "earlier, outside these caps."
    ),
)
CONTROL_STATEMENT = (
    "You can end this session at any time with your owner key alone, without "
    "anyone else."
)


def policy_digest(policy: SessionPolicy) -> str:
    document = json.dumps(
        policy_to_document(policy), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(document.encode("ascii")).hexdigest()


def describe_policy(presentation: PolicyPresentation) -> ApprovalScreen:
    policy = presentation.policy
    _refuse_unapprovable(presentation)
    asset_lines = tuple(_asset_line(presentation, rule) for rule in policy.rules)
    target_lines = tuple(
        TargetLine(address, presentation.catalog.labels.get(address))
        for address in sorted(policy.allowed_targets)
    )
    return ApprovalScreen(
        policy=policy,
        policy_digest=policy_digest(policy),
        starts_at=_timestamp(policy.valid_after),
        ends_at=_timestamp(policy.valid_until),
        session_length=format_duration(policy.valid_until - policy.valid_after),
        window_length=format_duration(policy.window_seconds),
        asset_lines=asset_lines,
        target_lines=target_lines,
        gas_cap_text=_gas_text(presentation),
        sponsors=tuple(sorted(policy.allowed_paymasters)),
        permitted=_permitted(presentation, asset_lines, target_lines),
        never=NEVER_STATEMENTS,
        caveats=CAVEAT_STATEMENTS,
        control=CONTROL_STATEMENT,
        warnings=_warnings(presentation, asset_lines, target_lines),
    )


def approve(screen: ApprovalScreen, acknowledgement: Acknowledgement) -> ApprovedPolicy:
    if acknowledgement.policy_digest != screen.policy_digest:
        raise ApprovalRefusedError("the acknowledgement is for a different policy")
    if acknowledgement.warning_keys != screen.required_acknowledgements:
        raise ApprovalRefusedError("every warning must be acknowledged, and only those")
    return ApprovedPolicy(screen.policy, screen.policy_digest)


def narrow(policy: SessionPolicy, narrowing: Narrowing) -> SessionPolicy:
    rules = {rule.asset: rule for rule in policy.rules}
    for lowered in narrowing.lowered_caps:
        current = rules.get(lowered.asset)
        if current is None or not 0 < lowered.cap < current.cap:
            raise NarrowingError(f"the cap for {lowered.asset} can only be lowered")
        rules[lowered.asset] = lowered
    for asset in narrowing.removed_assets:
        if rules.pop(asset, None) is None:
            raise NarrowingError(f"{asset} is not in the policy")
    unknown = narrowing.removed_targets - policy.allowed_targets
    if unknown:
        raise NarrowingError("only allowed targets can be removed")
    end = policy.valid_until if narrowing.earlier_end is None else narrowing.earlier_end
    if not policy.valid_after < end <= policy.valid_until:
        raise NarrowingError("the end time can only be moved earlier")
    return replace(
        policy,
        rules=tuple(rules.values()),
        allowed_targets=policy.allowed_targets - narrowing.removed_targets,
        valid_until=end,
    )


def _refuse_unapprovable(presentation: PolicyPresentation) -> None:
    policy = presentation.policy
    limits = presentation.limits
    if presentation.now >= policy.valid_until:
        raise ApprovalRefusedError("the session has already ended")
    if policy.valid_until - policy.valid_after > limits.maximum_session_seconds:
        raise ApprovalRefusedError("the session is longer than the maximum allowed")
    for rule in policy.rules:
        if rule.cap >= limits.unlimited_cap_threshold:
            raise ApprovalRefusedError(
                f"the cap for {rule.asset} is effectively unlimited"
            )
    for address in policy.allowed_targets:
        known = address in presentation.catalog.tokens
        if known and policy.cap_of(address) is None:
            raise ApprovalRefusedError(
                f"the token {address} is allowed as a target without a cap, "
                "which would let the agent approve unlimited spending"
            )


def _display_of(presentation: PolicyPresentation, asset: str) -> AssetDisplay | None:
    if asset == NATIVE_ASSET:
        return presentation.catalog.native
    return presentation.catalog.tokens.get(asset)


def _asset_line(presentation: PolicyPresentation, rule: AssetRule) -> AssetLine:
    policy = presentation.policy
    display = _display_of(presentation, rule.asset)
    windows = -(-(policy.valid_until - policy.valid_after) // policy.window_seconds)
    if display is None:
        return AssetLine(
            rule.asset,
            "unknown token",
            f"{rule.cap} raw units",
            f"{rule.cap * windows} raw units",
            False,
        )
    return AssetLine(
        rule.asset,
        display.symbol,
        f"{format_units(rule.cap, display.decimals)} {display.symbol}",
        f"{format_units(rule.cap * windows, display.decimals)} {display.symbol}",
        True,
    )


def _gas_text(presentation: PolicyPresentation) -> str:
    native = presentation.catalog.native
    amount = format_units(presentation.policy.maximum_gas_cost, native.decimals)
    return f"{amount} {native.symbol}"


def _timestamp(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _permitted(
    presentation: PolicyPresentation,
    asset_lines: tuple[AssetLine, ...],
    target_lines: tuple[TargetLine, ...],
) -> tuple[str, ...]:
    policy = presentation.policy
    window = format_duration(policy.window_seconds)
    statements: list[str] = []
    for line in asset_lines:
        statements.append(_asset_statement(line, window, bool(target_lines)))
    for target in target_lines:
        name = target.label or "an unlabelled contract"
        statements.append(
            f"Call any function, with any data, on {name} ({target.address}). "
            "The policy does not limit which functions."
        )
    if not statements:
        statements.append(
            "Nothing: the policy grants no spending and no contract calls."
        )
    return tuple(statements)


def _asset_statement(line: AssetLine, window: str, has_targets: bool) -> str:
    if line.asset == NATIVE_ASSET:
        where = (
            "attached to calls to the allowed contracts below"
            if has_targets
            else "nowhere, because no contract is allowed"
        )
        return (
            f"Send up to {line.cap_text} of native currency per rolling {window}, "
            f"{where}."
        )
    return (
        f"Transfer or approve {line.symbol} ({line.asset}) to any address, up to "
        f"{line.cap_text} per rolling {window}. Approvals count against the same cap."
    )


def _warnings(
    presentation: PolicyPresentation,
    asset_lines: tuple[AssetLine, ...],
    target_lines: tuple[TargetLine, ...],
) -> tuple[ApprovalWarning, ...]:
    policy = presentation.policy
    warnings: list[ApprovalWarning] = []
    for line in asset_lines:
        if not line.verified:
            warnings.append(
                ApprovalWarning(
                    WarningCode.UNVERIFIED_ASSET,
                    line.asset,
                    f"{line.asset} is not in the token list, so its amounts are shown "
                    "in raw units and its name cannot be confirmed.",
                )
            )
    for target in target_lines:
        if target.label is None:
            warnings.append(
                ApprovalWarning(
                    WarningCode.UNLABELLED_TARGET,
                    target.address,
                    f"{target.address} is not a known contract. If it is a token, the "
                    "agent could approve unlimited spending through it.",
                )
            )
    if policy.cap_of(NATIVE_ASSET) is not None and target_lines:
        warnings.append(
            ApprovalWarning(
                WarningCode.NATIVE_VALUE_TO_TARGETS,
                NATIVE_ASSET,
                "Native currency can be attached to calls to the allowed contracts.",
            )
        )
    if (
        policy.valid_until - policy.valid_after
        > presentation.limits.long_session_seconds
    ):
        warnings.append(
            ApprovalWarning(
                WarningCode.LONG_SESSION,
                "session",
                "The session is long; the totals above can be spent again in every "
                "rolling window.",
            )
        )
    return tuple(warnings)
