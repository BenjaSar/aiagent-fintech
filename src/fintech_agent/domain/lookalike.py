SIMILARITY_DISTANCE = 2
CHARACTER_FOLDING = str.maketrans({"0": "o", "1": "l", "i": "l", "5": "s"})
SEQUENCE_FOLDING = (("rn", "m"), ("vv", "w"), ("cl", "d"))
SEPARATORS = str.maketrans("", "", "-_. ")


def skeleton(text: str) -> str:
    folded = text.lower().translate(SEPARATORS).translate(CHARACTER_FOLDING)
    for sequence, replacement in SEQUENCE_FOLDING:
        folded = folded.replace(sequence, replacement)
    return folded


def edit_distance(first: str, second: str) -> int:
    previous = list(range(len(second) + 1))
    for row, first_character in enumerate(first, start=1):
        current = [row]
        for column, second_character in enumerate(second, start=1):
            substitution = previous[column - 1] + (first_character != second_character)
            current.append(
                min(previous[column] + 1, current[column - 1] + 1, substitution)
            )
        previous = current
    return previous[-1]


def are_lookalikes(first: str, second: str) -> bool:
    if first == second:
        return False
    if skeleton(first) == skeleton(second):
        return True
    return edit_distance(first.lower(), second.lower()) <= SIMILARITY_DISTANCE
