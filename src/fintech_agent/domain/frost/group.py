from __future__ import annotations

from dataclasses import dataclass

FIELD_PRIME = 2**256 - 2**32 - 977
GROUP_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
CURVE_COEFFICIENT = 7
GENERATOR_X = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GENERATOR_Y = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
COORDINATE_LENGTH = 32
SCALAR_LENGTH = 32
COMPRESSED_POINT_LENGTH = 33
VALID_PREFIXES = (2, 3)

Coordinates = tuple[int, int]


class InvalidEncodingError(ValueError):
    pass


@dataclass(frozen=True)
class Point:
    coordinates: Coordinates | None

    @classmethod
    def identity(cls) -> Point:
        return cls(None)

    @property
    def is_identity(self) -> bool:
        return self.coordinates is None

    def add(self, other: Point) -> Point:
        if self.coordinates is None:
            return other
        if other.coordinates is None:
            return self
        return _add_finite_points(self.coordinates, other.coordinates)

    def negate(self) -> Point:
        if self.coordinates is None:
            return self
        x_coordinate, y_coordinate = self.coordinates
        return Point((x_coordinate, (-y_coordinate) % FIELD_PRIME))

    def multiply(self, scalar: int) -> Point:
        remaining = scalar % GROUP_ORDER
        result = Point.identity()
        addend = self
        while remaining:
            if remaining & 1:
                result = result.add(addend)
            addend = addend.add(addend)
            remaining >>= 1
        return result

    def serialize(self) -> bytes:
        if self.coordinates is None:
            raise InvalidEncodingError("the identity element cannot be serialized")
        x_coordinate, y_coordinate = self.coordinates
        prefix = 2 + y_coordinate % 2
        return bytes([prefix]) + x_coordinate.to_bytes(COORDINATE_LENGTH, "big")

    @classmethod
    def deserialize(cls, encoded: bytes) -> Point:
        x_coordinate = _decode_x_coordinate(encoded)
        y_coordinate = _square_root((x_coordinate**3 + CURVE_COEFFICIENT) % FIELD_PRIME)
        if y_coordinate % 2 != encoded[0] % 2:
            y_coordinate = FIELD_PRIME - y_coordinate
        return cls((x_coordinate, y_coordinate))


GENERATOR = Point((GENERATOR_X, GENERATOR_Y))


def base_multiply(scalar: int) -> Point:
    return GENERATOR.multiply(scalar)


def serialize_scalar(scalar: int) -> bytes:
    return (scalar % GROUP_ORDER).to_bytes(SCALAR_LENGTH, "big")


def deserialize_scalar(encoded: bytes) -> int:
    if len(encoded) != SCALAR_LENGTH:
        raise InvalidEncodingError("a scalar must be exactly 32 bytes")
    scalar = int.from_bytes(encoded, "big")
    if scalar >= GROUP_ORDER:
        raise InvalidEncodingError("a scalar must be below the group order")
    return scalar


def _add_finite_points(first: Coordinates, second: Coordinates) -> Point:
    first_x, first_y = first
    second_x, second_y = second
    if first_x == second_x and (first_y + second_y) % FIELD_PRIME == 0:
        return Point.identity()
    slope = _slope(first, second)
    result_x = (slope * slope - first_x - second_x) % FIELD_PRIME
    result_y = (slope * (first_x - result_x) - first_y) % FIELD_PRIME
    return Point((result_x, result_y))


def _slope(first: Coordinates, second: Coordinates) -> int:
    first_x, first_y = first
    second_x, second_y = second
    if first == second:
        numerator = 3 * first_x * first_x
        denominator = 2 * first_y
    else:
        numerator = second_y - first_y
        denominator = second_x - first_x
    return numerator * pow(denominator, -1, FIELD_PRIME) % FIELD_PRIME


def _decode_x_coordinate(encoded: bytes) -> int:
    if len(encoded) != COMPRESSED_POINT_LENGTH or encoded[0] not in VALID_PREFIXES:
        raise InvalidEncodingError("a point must be 33 bytes with prefix 0x02 or 0x03")
    x_coordinate = int.from_bytes(encoded[1:], "big")
    if x_coordinate >= FIELD_PRIME:
        raise InvalidEncodingError("a coordinate must be below the field prime")
    return x_coordinate


def _square_root(value: int) -> int:
    candidate = pow(value, (FIELD_PRIME + 1) // 4, FIELD_PRIME)
    if candidate * candidate % FIELD_PRIME != value:
        raise InvalidEncodingError("the coordinate is not on the curve")
    return candidate
