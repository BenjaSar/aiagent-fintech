import pytest

from fintech_agent.domain.frost.group import (
    FIELD_PRIME,
    GENERATOR,
    GROUP_ORDER,
    InvalidEncodingError,
    Point,
    base_multiply,
    deserialize_scalar,
    serialize_scalar,
)

from .vectors import FROST_SECP256K1_VECTOR


def group_public_key_bytes() -> bytes:
    return bytes.fromhex(FROST_SECP256K1_VECTOR["group_public_key"])


def test_generator_has_the_group_order() -> None:
    assert GENERATOR.multiply(GROUP_ORDER).is_identity


def test_secret_key_maps_to_documented_group_public_key() -> None:
    secret_key = int(FROST_SECP256K1_VECTOR["group_secret_key"], 16)

    assert base_multiply(secret_key).serialize() == group_public_key_bytes()


def test_hiding_nonce_maps_to_documented_commitment() -> None:
    hiding_nonce = int(FROST_SECP256K1_VECTOR["P1 hiding_nonce"], 16)

    expected = bytes.fromhex(FROST_SECP256K1_VECTOR["P1 hiding_nonce_commitment"])

    assert base_multiply(hiding_nonce).serialize() == expected


def test_serialization_round_trips() -> None:
    point = Point.deserialize(group_public_key_bytes())

    assert Point.deserialize(point.serialize()) == point


def test_point_plus_its_negation_is_identity() -> None:
    assert GENERATOR.add(GENERATOR.negate()).is_identity


def test_doubling_matches_multiplying_by_two() -> None:
    assert GENERATOR.add(GENERATOR) == GENERATOR.multiply(2)


def test_multiplying_by_zero_gives_identity() -> None:
    assert GENERATOR.multiply(0).is_identity


def test_scalar_is_reduced_modulo_group_order() -> None:
    assert GENERATOR.multiply(GROUP_ORDER + 1) == GENERATOR


def test_identity_is_the_neutral_element() -> None:
    assert GENERATOR.add(Point.identity()) == GENERATOR
    assert Point.identity().add(GENERATOR) == GENERATOR


def test_identity_cannot_be_serialized() -> None:
    with pytest.raises(InvalidEncodingError):
        Point.identity().serialize()


def test_deserialization_rejects_wrong_length() -> None:
    with pytest.raises(InvalidEncodingError):
        Point.deserialize(group_public_key_bytes()[:-1])


def test_deserialization_rejects_unknown_prefix() -> None:
    with pytest.raises(InvalidEncodingError):
        Point.deserialize(b"\x04" + group_public_key_bytes()[1:])


def test_deserialization_rejects_coordinate_outside_the_field() -> None:
    with pytest.raises(InvalidEncodingError):
        Point.deserialize(b"\x02" + FIELD_PRIME.to_bytes(32, "big"))


def first_x_coordinate_without_a_curve_point() -> int:
    candidate = 1
    while pow(candidate**3 + 7, (FIELD_PRIME - 1) // 2, FIELD_PRIME) == 1:
        candidate += 1
    return candidate


def test_deserialization_rejects_coordinate_that_is_not_on_the_curve() -> None:
    x_coordinate = first_x_coordinate_without_a_curve_point()

    with pytest.raises(InvalidEncodingError):
        Point.deserialize(b"\x02" + x_coordinate.to_bytes(32, "big"))


def test_scalar_round_trips() -> None:
    scalar = int(FROST_SECP256K1_VECTOR["P1 sig_share"], 16)

    assert deserialize_scalar(serialize_scalar(scalar)) == scalar


def test_scalar_deserialization_rejects_values_at_or_above_the_order() -> None:
    with pytest.raises(InvalidEncodingError):
        deserialize_scalar(GROUP_ORDER.to_bytes(32, "big"))


def test_scalar_deserialization_rejects_wrong_length() -> None:
    with pytest.raises(InvalidEncodingError):
        deserialize_scalar(b"\x01")
