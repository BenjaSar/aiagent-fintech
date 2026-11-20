from dataclasses import dataclass

from .keccak import keccak256

WORD_BYTES = 32
SELECTOR_BYTES = 4
ADDRESS_BYTES = 20
SINGLE_HEADER_BYTES = ADDRESS_BYTES + WORD_BYTES
EXECUTE_AS_SESSION_SELECTOR = keccak256(b"executeAsSession(bytes32,bytes)")[
    :SELECTOR_BYTES
]
SINGLE_CALL_TYPE = 0x00
BATCH_CALL_TYPE = 0x01
DEFAULT_EXECUTION_TYPE = 0x00
BYTES_OFFSET = 2 * WORD_BYTES
ARRAY_OFFSET = WORD_BYTES
ELEMENT_HEAD_BYTES = 3 * WORD_BYTES
MAXIMUM_EXECUTIONS = 16


class UnsupportedSessionCallError(ValueError):
    pass


@dataclass(frozen=True)
class Execution:
    target: str
    value: int
    data: bytes


def decode_session_call(call_data: bytes) -> tuple[Execution, ...]:
    if call_data[:SELECTOR_BYTES] != EXECUTE_AS_SESSION_SELECTOR:
        raise UnsupportedSessionCallError(
            "the call is not executeAsSession, the only entry point for session operations"
        )
    mode, payload = _decode_outer(call_data[SELECTOR_BYTES:])
    call_type = _call_type(mode)
    if call_type == SINGLE_CALL_TYPE:
        executions: tuple[Execution, ...] = (_decode_single(payload),)
    else:
        executions = _decode_batch(payload)
    if not executions or len(executions) > MAXIMUM_EXECUTIONS:
        raise UnsupportedSessionCallError(
            f"a session call must hold between 1 and {MAXIMUM_EXECUTIONS} executions"
        )
    return executions


def encode_single_session_call(execution: Execution) -> bytes:
    payload = (
        bytes.fromhex(execution.target.removeprefix("0x"))
        + _word(execution.value)
        + execution.data
    )
    return EXECUTE_AS_SESSION_SELECTOR + _encode_outer(_mode(SINGLE_CALL_TYPE), payload)


def encode_batch_session_call(executions: tuple[Execution, ...]) -> bytes:
    return EXECUTE_AS_SESSION_SELECTOR + _encode_outer(
        _mode(BATCH_CALL_TYPE), _encode_batch(executions)
    )


def encode_batch_payload(executions: tuple[Execution, ...]) -> bytes:
    return _encode_batch(executions)


def _mode(call_type: int) -> bytes:
    return bytes([call_type]) + bytes(WORD_BYTES - 1)


def _call_type(mode: bytes) -> int:
    if mode[1] != DEFAULT_EXECUTION_TYPE or any(mode[2:]):
        raise UnsupportedSessionCallError(
            "only the default execution type with no mode selector or payload is allowed"
        )
    if mode[0] not in (SINGLE_CALL_TYPE, BATCH_CALL_TYPE):
        raise UnsupportedSessionCallError("only single and batch calls are allowed")
    return mode[0]


def _decode_outer(arguments: bytes) -> tuple[bytes, bytes]:
    if len(arguments) < 3 * WORD_BYTES:
        raise UnsupportedSessionCallError("the call data is too short")
    mode = arguments[:WORD_BYTES]
    length = _read_word(arguments, 2 * WORD_BYTES)
    payload = arguments[3 * WORD_BYTES : 3 * WORD_BYTES + length]
    if len(payload) != length:
        raise UnsupportedSessionCallError("the call data is truncated")
    if arguments != _encode_outer(mode, payload):
        raise UnsupportedSessionCallError(
            "the call data is not in canonical ABI encoding"
        )
    return mode, payload


def _encode_outer(mode: bytes, payload: bytes) -> bytes:
    return mode + _word(BYTES_OFFSET) + _encode_bytes(payload)


def _decode_single(payload: bytes) -> Execution:
    if len(payload) < SINGLE_HEADER_BYTES:
        raise UnsupportedSessionCallError("a single execution is too short")
    return Execution(
        "0x" + payload[:ADDRESS_BYTES].hex(),
        int.from_bytes(payload[ADDRESS_BYTES:SINGLE_HEADER_BYTES], "big"),
        payload[SINGLE_HEADER_BYTES:],
    )


def _decode_batch(payload: bytes) -> tuple[Execution, ...]:
    if _read_word(payload, 0) != ARRAY_OFFSET:
        raise UnsupportedSessionCallError("the batch is not in canonical ABI encoding")
    count = _read_word(payload, ARRAY_OFFSET)
    if count > MAXIMUM_EXECUTIONS:
        raise UnsupportedSessionCallError(
            f"a session call must hold between 1 and {MAXIMUM_EXECUTIONS} executions"
        )
    area = ARRAY_OFFSET + WORD_BYTES
    executions = tuple(
        _decode_element(payload, area, _read_word(payload, area + index * WORD_BYTES))
        for index in range(count)
    )
    if payload != _encode_batch(executions):
        raise UnsupportedSessionCallError("the batch is not in canonical ABI encoding")
    return executions


def _decode_element(payload: bytes, area: int, offset: int) -> Execution:
    start = area + offset
    target_word = _slice(payload, start, WORD_BYTES)
    if any(target_word[:-ADDRESS_BYTES]):
        raise UnsupportedSessionCallError("an execution target is not an address")
    length = _read_word(payload, start + 2 * WORD_BYTES + WORD_BYTES)
    return Execution(
        "0x" + target_word[-ADDRESS_BYTES:].hex(),
        _read_word(payload, start + WORD_BYTES),
        _slice(payload, start + ELEMENT_HEAD_BYTES + WORD_BYTES, length),
    )


def _encode_batch(executions: tuple[Execution, ...]) -> bytes:
    elements = [_encode_element(execution) for execution in executions]
    offsets: list[int] = []
    position = len(executions) * WORD_BYTES
    for element in elements:
        offsets.append(position)
        position += len(element)
    return (
        _word(ARRAY_OFFSET)
        + _word(len(executions))
        + b"".join(_word(offset) for offset in offsets)
        + b"".join(elements)
    )


def _encode_element(execution: Execution) -> bytes:
    return (
        bytes(WORD_BYTES - ADDRESS_BYTES)
        + bytes.fromhex(execution.target.removeprefix("0x"))
        + _word(execution.value)
        + _word(ELEMENT_HEAD_BYTES)
        + _encode_bytes(execution.data)
    )


def _encode_bytes(data: bytes) -> bytes:
    padding = (-len(data)) % WORD_BYTES
    return _word(len(data)) + data + bytes(padding)


def _word(value: int) -> bytes:
    return value.to_bytes(WORD_BYTES, "big")


def _read_word(data: bytes, offset: int) -> int:
    return int.from_bytes(_slice(data, offset, WORD_BYTES), "big")


def _slice(data: bytes, offset: int, length: int) -> bytes:
    piece = data[offset : offset + length]
    if offset < 0 or len(piece) != length:
        raise UnsupportedSessionCallError("the call data is truncated")
    return piece
