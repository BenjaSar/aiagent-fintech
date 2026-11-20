MASK_64 = (1 << 64) - 1
RATE_BYTES = 136
DIGEST_BYTES = 32
LANES = 5
PADDING_START = 0x01
PADDING_END = 0x80
ROUND_CONSTANTS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)
ROTATION_OFFSETS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)

State = list[list[int]]


def keccak256(data: bytes) -> bytes:
    state: State = [[0] * LANES for _ in range(LANES)]
    for block in _blocks(data):
        _absorb(state, block)
        _permute(state)
    return _squeeze(state)


def _blocks(data: bytes) -> list[bytes]:
    padded = bytearray(data)
    padded.append(PADDING_START)
    while len(padded) % RATE_BYTES:
        padded.append(0)
    padded[-1] |= PADDING_END
    return [
        bytes(padded[start : start + RATE_BYTES])
        for start in range(0, len(padded), RATE_BYTES)
    ]


def _absorb(state: State, block: bytes) -> None:
    for lane_index in range(RATE_BYTES // 8):
        lane = int.from_bytes(block[lane_index * 8 : lane_index * 8 + 8], "little")
        state[lane_index % LANES][lane_index // LANES] ^= lane


def _squeeze(state: State) -> bytes:
    output = b"".join(
        state[lane_index % LANES][lane_index // LANES].to_bytes(8, "little")
        for lane_index in range(DIGEST_BYTES // 8)
    )
    return output[:DIGEST_BYTES]


def _rotate_left(value: int, amount: int) -> int:
    amount %= 64
    return ((value << amount) | (value >> (64 - amount))) & MASK_64


def _permute(state: State) -> None:
    for constant in ROUND_CONSTANTS:
        _theta(state)
        rotated = _rho_and_pi(state)
        _chi(state, rotated)
        state[0][0] ^= constant


def _theta(state: State) -> None:
    columns = [
        state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4]
        for x in range(LANES)
    ]
    for x in range(LANES):
        mixed = columns[(x - 1) % LANES] ^ _rotate_left(columns[(x + 1) % LANES], 1)
        for y in range(LANES):
            state[x][y] ^= mixed


def _rho_and_pi(state: State) -> State:
    rotated: State = [[0] * LANES for _ in range(LANES)]
    for x in range(LANES):
        for y in range(LANES):
            rotated[y][(2 * x + 3 * y) % LANES] = _rotate_left(
                state[x][y], ROTATION_OFFSETS[x][y]
            )
    return rotated


def _chi(state: State, rotated: State) -> None:
    for x in range(LANES):
        for y in range(LANES):
            state[x][y] = rotated[x][y] ^ (
                (~rotated[(x + 1) % LANES][y]) & rotated[(x + 2) % LANES][y]
            )
