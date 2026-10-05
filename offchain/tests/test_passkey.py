import hashlib
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from trustscore.passkey import FLAG_USER_PRESENT, FLAG_USER_VERIFIED, P256_N, Assertion, SoftwarePasskey, b64url


def _pk():
    return SoftwarePasskey(ec.generate_private_key(ec.SECP256R1()))


def test_assertion_is_a_valid_webauthn_signature():
    pk, challenge = _pk(), b"\x11" * 32
    a = pk.assert_(challenge)
    signed = a.authenticator_data + hashlib.sha256(a.client_data_json.encode()).digest()
    pk.key.public_key().verify(encode_dss_signature(a.r, a.s), signed, ec.ECDSA(hashes.SHA256()))  # raises if bad
    assert a.s <= P256_N // 2


def test_client_data_carries_the_challenge_where_the_contract_looks():
    a = _pk().assert_(b"\x22" * 32)
    cd = json.loads(a.client_data_json)
    assert cd["type"] == "webauthn.get" and cd["challenge"] == b64url(b"\x22" * 32)
    assert a.client_data_json[a.challenge_index:].startswith('"challenge":"')
    assert a.client_data_json[a.type_index:].startswith('"type":"webauthn.get"')


def test_flags_are_written_into_authenticator_data():
    assert _pk().assert_(b"x" * 32).authenticator_data[32] == FLAG_USER_PRESENT | FLAG_USER_VERIFIED
    assert _pk().assert_(b"x" * 32, flags=FLAG_USER_PRESENT).authenticator_data[32] == FLAG_USER_PRESENT


def test_browser_assertions_get_low_s():
    high = P256_N - 5
    a = Assertion.from_browser(b"", '{"type":"webauthn.get","challenge":"x"}', 1, high)
    assert a.s == 5 and a.challenge_index == 23 and a.type_index == 1
