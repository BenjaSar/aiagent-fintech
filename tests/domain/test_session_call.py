import pytest

from fintech_agent.domain.session_call import (
    EXECUTE_AS_SESSION_SELECTOR,
    Execution,
    UnsupportedSessionCallError,
    decode_session_call,
    encode_batch_session_call,
    encode_single_session_call,
)
from tests.policy_fixtures import RECIPIENT, TARGET

WORD = 32
PAYLOAD_START = 4 + 3 * WORD


def word(value: int) -> bytes:
    return value.to_bytes(WORD, "big")


def mode_word(call_type: int = 0, execution_type: int = 0, rest: bytes = b"") -> bytes:
    return (bytes([call_type, execution_type]) + rest).ljust(WORD, b"\x00")


def outer(mode: bytes, payload: bytes) -> bytes:
    padding = bytes((-len(payload)) % WORD)
    return (
        EXECUTE_AS_SESSION_SELECTOR
        + mode
        + word(2 * WORD)
        + word(len(payload))
        + payload
        + padding
    )


def refused(call_data: bytes) -> None:
    with pytest.raises(UnsupportedSessionCallError):
        decode_session_call(call_data)


def test_a_single_execution_round_trips() -> None:
    execution = Execution(TARGET, 5, bytes.fromhex("12345678"))

    assert decode_session_call(encode_single_session_call(execution)) == (execution,)


def test_a_batch_round_trips_and_keeps_its_order() -> None:
    executions = (
        Execution(TARGET, 0, b""),
        Execution(RECIPIENT, 9, bytes(70)),
        Execution(TARGET, 1, b"\x01"),
    )

    assert decode_session_call(encode_batch_session_call(executions)) == executions


def test_another_entry_point_is_refused() -> None:
    call_data = (
        bytes.fromhex("b61d27f6")
        + encode_single_session_call(Execution(TARGET, 0, b""))[4:]
    )

    refused(call_data)


def test_empty_call_data_is_refused() -> None:
    refused(b"")


def test_delegatecall_and_other_call_types_are_refused() -> None:
    payload = bytes.fromhex(TARGET.removeprefix("0x")) + word(0)

    refused(outer(mode_word(call_type=0xFF), payload))
    refused(outer(mode_word(call_type=0x02), payload))


def test_a_try_execution_type_is_refused() -> None:
    payload = bytes.fromhex(TARGET.removeprefix("0x")) + word(0)

    refused(outer(mode_word(execution_type=1), payload))


def test_a_mode_selector_or_payload_is_refused() -> None:
    payload = bytes.fromhex(TARGET.removeprefix("0x")) + word(0)

    refused(outer(mode_word(rest=b"\x00\x00\x00\x00\x01"), payload))
    refused(outer(mode_word(rest=bytes(10) + b"\x01"), payload))


def test_a_single_execution_shorter_than_a_target_and_value_is_refused() -> None:
    refused(outer(mode_word(), bytes(51)))


def test_a_non_canonical_outer_offset_is_refused() -> None:
    canonical = encode_single_session_call(Execution(TARGET, 0, b""))
    shifted = canonical[:36] + word(3 * WORD) + word(0) + canonical[68:]

    refused(shifted)


def test_trailing_bytes_after_the_call_data_are_refused() -> None:
    refused(encode_single_session_call(Execution(TARGET, 0, b"")) + b"\x00")


def test_non_zero_padding_is_refused() -> None:
    canonical = bytearray(encode_single_session_call(Execution(TARGET, 0, b"")))
    canonical[-1] = 1

    refused(bytes(canonical))


def test_a_truncated_payload_is_refused() -> None:
    canonical = encode_single_session_call(Execution(TARGET, 0, b"abc"))

    refused(canonical[:-40])


def test_an_empty_batch_is_refused() -> None:
    refused(encode_batch_session_call(()))


def test_a_batch_above_sixteen_executions_is_refused() -> None:
    executions = tuple(Execution(TARGET, 0, b"") for _ in range(17))

    refused(encode_batch_session_call(executions))


def test_sixteen_executions_are_accepted() -> None:
    executions = tuple(Execution(TARGET, 0, b"") for _ in range(16))

    assert len(decode_session_call(encode_batch_session_call(executions))) == 16


def test_a_batch_with_a_shifted_element_offset_is_refused() -> None:
    canonical = bytearray(encode_batch_session_call((Execution(TARGET, 0, b""),)))
    head = PAYLOAD_START + 2 * WORD
    canonical[head : head + WORD] = word(0x40)

    refused(bytes(canonical))


def test_a_batch_target_with_dirty_upper_bytes_is_refused() -> None:
    canonical = bytearray(encode_batch_session_call((Execution(TARGET, 0, b""),)))
    element = PAYLOAD_START + 3 * WORD
    canonical[element] = 1

    refused(bytes(canonical))


def test_a_batch_with_an_absurd_count_is_refused() -> None:
    canonical = bytearray(encode_batch_session_call((Execution(TARGET, 0, b""),)))
    count = PAYLOAD_START + WORD
    canonical[count : count + WORD] = word(2**200)

    refused(bytes(canonical))


def element(
    target: str, value: int, data: bytes, bytes_offset: int = 3 * WORD
) -> bytes:
    padding = bytes((-len(data)) % WORD)
    gap = bytes(bytes_offset - 3 * WORD)
    return (
        bytes(12)
        + bytes.fromhex(target.removeprefix("0x"))
        + word(value)
        + word(bytes_offset)
        + gap
        + word(len(data))
        + data
        + padding
    )


def batch_payload(elements: list[bytes], gaps: list[int]) -> bytes:
    offsets: list[int] = []
    position = len(elements) * WORD
    for body, gap in zip(elements, gaps, strict=True):
        position += gap
        offsets.append(position)
        position += len(body)
    placed = b"".join(
        bytes(gap) + body for body, gap in zip(elements, gaps, strict=True)
    )
    return (
        word(WORD)
        + word(len(elements))
        + b"".join(word(offset) for offset in offsets)
        + placed
    )


def test_the_helper_builds_the_canonical_batch() -> None:
    elements = [element(TARGET, 1, b"ab"), element(RECIPIENT, 2, b"")]
    expected = (
        Execution(TARGET, 1, b"ab"),
        Execution(RECIPIENT, 2, b""),
    )

    call_data = outer(mode_word(call_type=1), batch_payload(elements, [0, 0]))

    assert decode_session_call(call_data) == expected


def test_a_batch_with_a_gap_between_elements_is_refused() -> None:
    elements = [element(TARGET, 1, b"ab"), element(RECIPIENT, 2, b"")]

    refused(outer(mode_word(call_type=1), batch_payload(elements, [0, WORD])))


def test_a_batch_with_a_gap_before_the_first_element_is_refused() -> None:
    elements = [element(TARGET, 1, b"ab")]

    refused(outer(mode_word(call_type=1), batch_payload(elements, [WORD])))


def test_an_element_with_a_shifted_bytes_offset_is_refused() -> None:
    elements = [element(TARGET, 1, b"ab", bytes_offset=4 * WORD)]

    refused(outer(mode_word(call_type=1), batch_payload(elements, [0])))
