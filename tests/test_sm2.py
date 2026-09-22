"""Low-level SM2/SM3 tests."""

import pytest
from unittest.mock import patch
from gmssl import sm2
from Cryptodome.Util.asn1 import DerInteger, DerSequence

from atp.security.sm2 import SM2PrivateKey, SM2PublicKey, sm3_digest


def test_sm3_official_abc_vector():
    assert sm3_digest(b"abc").hex() == (
        "66c7f0f462eeedd9d1f2d46bdc10e4e"
        "24167c4875cf2f7a2297da02b8f4ba8e0"
    )


def test_sm2_round_trip_and_tamper_detection():
    private_key = SM2PrivateKey.generate()
    signature = private_key.sign(b"ATP national cryptography")

    assert signature[0] == 0x30  # DER SEQUENCE
    assert private_key.public_key().verify(signature, b"ATP national cryptography")
    assert not private_key.public_key().verify(signature, b"tampered")


def test_sm2_standard_message_signature_vector():
    """Verify the public SM2 example using ID 1234567812345678."""
    public_key = SM2PublicKey(
        "09F9DF311E5421A150DD7D161E4BC5C672179FAD1833FC076BB08FF356F35020"
        "CCEA490CE26775A52DC6EA718CC1AA600AED05FBF35E084A6632F6072DA9AD13"
    )
    r = int(
        "F5A03B0648D2C4630EEAC513E1BB81A15944DA3827D5B74143AC7EACEEE720B3",
        16,
    )
    s = int(
        "B1B6AA29DF212FD8763182BC0D421CA1BB9038FD1F7F42D4840B69C485BBC1AA",
        16,
    )
    signature = DerSequence([DerInteger(r), DerInteger(s)]).encode()

    assert public_key.verify(signature, b"message digest")


def test_sm2_public_key_raw_round_trip():
    public_key = SM2PrivateKey.generate().public_key()
    restored = SM2PublicKey.from_raw_bytes(public_key.raw_bytes())
    assert restored == public_key


def test_bare_public_key_whose_x_coordinate_starts_with_04_is_not_truncated():
    # 11 * G has an X coordinate beginning with 0x04.  The bare X || Y form
    # must not confuse those coordinate bytes with an SEC1 point prefix.
    private_key = SM2PrivateKey(
        f"{11:064x}",
        "04b3cb10c9c6d8e27c1aab770f67f543125dcdd589c2ff82668c74d78ce20ace"
        "63516355287e39fe4918e5c02e2b0b930c94816e63c4bc72739a8fd805174a4b",
    )

    assert private_key.public_key_hex.startswith("04")
    assert len(private_key.public_key_hex) == 128
    assert SM2PublicKey.from_raw_bytes(private_key.public_key().raw_bytes()) == (
        private_key.public_key()
    )

    signature = private_key.sign(b"leading 04 coordinate")
    assert private_key.public_key().verify(signature, b"leading 04 coordinate")


@pytest.mark.parametrize(
    "bad_key",
    [b"", b"\x04" + b"\x00" * 63, b"\x04" + b"\x00" * 64],
)
def test_invalid_sm2_public_key_is_rejected(bad_key):
    with pytest.raises(ValueError):
        SM2PublicKey.from_raw_bytes(bad_key)


def test_corrupted_private_public_pair_is_rejected():
    first = SM2PrivateKey.generate()
    second = SM2PrivateKey.generate()
    with pytest.raises(ValueError, match="does not match"):
        SM2PrivateKey(first.private_key_hex, second.public_key_hex)


def test_private_scalar_n_minus_one_is_rejected():
    n = int(sm2.default_ecc_table['n'], 16)
    with pytest.raises(ValueError, match='scalar is out of range'):
        SM2PrivateKey(f'{n - 1:064x}', sm2.default_ecc_table['g'])


def test_largest_generated_private_scalar_can_sign():
    # Force the upper edge of the random range rather than relying on chance.
    with patch('atp.security.sm2.secrets.randbelow', side_effect=lambda bound: bound - 1):
        key = SM2PrivateKey.generate()
    signature = key.sign(b'largest signing key')
    assert key.public_key().verify(signature, b'largest signing key')
