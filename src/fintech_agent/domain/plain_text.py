import unicodedata

FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def is_plain_text(value: str) -> bool:
    return not any(
        unicodedata.category(character) in FORBIDDEN_CATEGORIES for character in value
    )
