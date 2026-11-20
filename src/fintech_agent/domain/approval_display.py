import re
from collections.abc import Mapping
from dataclasses import dataclass, field

SYMBOL_PATTERN = re.compile(r"[A-Za-z0-9]{1,12}")
LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
MAXIMUM_DECIMALS = 36
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400
DURATION_UNITS = (
    ("day", SECONDS_PER_DAY),
    ("hour", SECONDS_PER_HOUR),
    ("minute", SECONDS_PER_MINUTE),
    ("second", 1),
)


class DisplayCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class AssetDisplay:
    symbol: str
    decimals: int

    def __post_init__(self) -> None:
        if not SYMBOL_PATTERN.fullmatch(self.symbol):
            raise DisplayCatalogError(
                "a symbol must be 1 to 12 plain letters or digits"
            )
        if not 0 <= self.decimals <= MAXIMUM_DECIMALS:
            raise DisplayCatalogError("the decimals are out of range")


@dataclass(frozen=True)
class DisplayCatalog:
    native: AssetDisplay
    tokens: Mapping[str, AssetDisplay] = field(default_factory=dict)
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for address in (*self.tokens, *self.labels):
            if address != address.lower():
                raise DisplayCatalogError("catalog addresses must be lowercase")
        for label in self.labels.values():
            if not LABEL_PATTERN.fullmatch(label):
                raise DisplayCatalogError("a label must be short plain text")


def format_units(amount: int, decimals: int) -> str:
    whole, fraction = divmod(amount, 10**decimals)
    digits = f"{whole:,}"
    if decimals == 0 or fraction == 0:
        return digits
    trimmed = str(fraction).rjust(decimals, "0").rstrip("0")
    return f"{digits}.{trimmed}"


def format_duration(seconds: int) -> str:
    parts: list[str] = []
    remaining = seconds
    for name, size in DURATION_UNITS:
        count, remaining = divmod(remaining, size)
        if count:
            parts.append(f"{count} {name}{'s' if count != 1 else ''}")
    return " ".join(parts) if parts else "0 seconds"
