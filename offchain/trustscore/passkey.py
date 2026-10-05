"""Passkey reviews: build WebAuthn assertions and relay them to PasskeyReviews (the reviewer pays no gas).

The browser frontend gets assertions from a real authenticator (Face ID / Touch ID). SoftwarePasskey produces the
same bytes from a P256 key held in a file, for tests and scripted demos only: it proves nothing about a human.

    python -m trustscore.passkey review --agent 1944 --value 90 --passkey demo-alice
"""

import argparse
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from eth_account import Account
from web3 import Web3

from .config import MONAD_TESTNET, REPO_ROOT, load_abi

P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
FLAG_USER_PRESENT, FLAG_USER_VERIFIED = 0x01, 0x04
PASSKEY_DIR = REPO_ROOT / "data" / "passkeys"


@dataclass
class Assertion:
    """The WebAuthnAuth struct PasskeyReviews.submit expects."""

    authenticator_data: bytes
    client_data_json: str
    challenge_index: int
    type_index: int
    r: int
    s: int

    def as_tuple(self) -> tuple:
        return (self.authenticator_data, self.client_data_json, self.challenge_index, self.type_index,
                self.r.to_bytes(32, "big"), self.s.to_bytes(32, "big"))

    @classmethod
    def from_browser(cls, authenticator_data: bytes, client_data_json: str, r: int, s: int) -> "Assertion":
        """From a browser assertion: indices are located in the JSON, s is put in low form."""
        return cls(authenticator_data, client_data_json, client_data_json.index('"challenge"'),
                   client_data_json.index('"type"'), r, min(s, P256_N - s))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftwarePasskey:
    """A P256 key acting as an authenticator. For tests and demos only."""

    def __init__(self, key: ec.EllipticCurvePrivateKey, rp_id: str = "unmask.app"):
        self.key, self.rp_id = key, rp_id

    @classmethod
    def load_or_create(cls, name: str) -> "SoftwarePasskey":
        path = PASSKEY_DIR / f"{name}.pem"
        if path.exists():
            return cls(serialization.load_pem_private_key(path.read_bytes(), password=None))
        key = ec.generate_private_key(ec.SECP256R1())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        return cls(key)

    @property
    def xy(self) -> tuple[bytes, bytes]:
        n = self.key.public_key().public_numbers()
        return n.x.to_bytes(32, "big"), n.y.to_bytes(32, "big")

    def assert_(self, challenge: bytes, flags: int = FLAG_USER_PRESENT | FLAG_USER_VERIFIED) -> Assertion:
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + (1).to_bytes(4, "big")
        client = json.dumps({"type": "webauthn.get", "challenge": b64url(challenge),
                             "origin": f"https://{self.rp_id}", "crossOrigin": False}, separators=(",", ":"))
        # WebAuthn signs authenticatorData || sha256(clientDataJSON) with ECDSA-SHA256.
        der = self.key.sign(auth_data + hashlib.sha256(client.encode()).digest(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return Assertion.from_browser(auth_data, client, r, s)


class Relayer:
    """Submits signed passkey reviews, paying gas from its own wallet."""

    def __init__(self, w3: Web3, contract: str, key: str):
        self.w3, self.acct = w3, Account.from_key(key)
        self.contract = w3.eth.contract(address=Web3.to_checksum_address(contract), abi=load_abi("PasskeyReviews"))

    def challenge(self, agent_id: int, value: int, tag: bytes, x: bytes, y: bytes) -> bytes:
        return self.contract.functions.challengeFor(agent_id, value, tag, x, y).call()

    def submit(self, agent_id: int, value: int, tag: bytes, x: bytes, y: bytes, a: Assertion) -> str:
        fn = self.contract.functions.submit(agent_id, value, tag, x, y, a.as_tuple())
        gas = int(fn.estimate_gas({"from": self.acct.address}) * 1.15)  # Monad bills the gas limit
        tx = fn.build_transaction({"from": self.acct.address, "gas": gas,
                                   "nonce": self.w3.eth.get_transaction_count(self.acct.address),
                                   "gasPrice": int(self.w3.eth.gas_price * 1.1), "chainId": self.w3.eth.chain_id})
        h = self.w3.eth.send_raw_transaction(self.acct.sign_transaction(tx).raw_transaction)
        rcpt = self.w3.eth.wait_for_transaction_receipt(h, timeout=180)
        if rcpt.status != 1:
            raise RuntimeError(f"submit reverted: {Web3.to_hex(h)}")
        return Web3.to_hex(h)

    def review(self, passkey: SoftwarePasskey, agent_id: int, value: int, tag: str = "quality") -> str:
        x, y = passkey.xy
        t = tag.encode().ljust(32, b"\0")[:32]
        return self.submit(agent_id, value, t, x, y, passkey.assert_(self.challenge(agent_id, value, t, x, y)))


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review", help="sign a review with a software passkey and relay it")
    r.add_argument("--agent", type=int, required=True)
    r.add_argument("--value", type=int, required=True)
    r.add_argument("--tag", default="quality")
    r.add_argument("--passkey", default="demo", help="name of a software passkey under data/passkeys/")
    args = p.parse_args()

    contract, key = os.environ.get("PASSKEY_REVIEWS_ADDRESS"), os.environ.get("RELAYER_PRIVATE_KEY") or os.environ.get(
        "ORACLE_PRIVATE_KEY")
    if not (contract and key):
        p.error("PASSKEY_REVIEWS_ADDRESS and RELAYER_PRIVATE_KEY (or ORACLE_PRIVATE_KEY) must be set in .env")
    relayer = Relayer(Web3(Web3.HTTPProvider(MONAD_TESTNET.rpc_url)), contract, key)
    pk = SoftwarePasskey.load_or_create(args.passkey)
    h = relayer.review(pk, args.agent, args.value, args.tag)
    reviewer = Web3.keccak(pk.xy[0] + pk.xy[1]).hex()
    print(f"review {args.value} for agent {args.agent} by passkey {args.passkey} (reviewer {reviewer[:18]}...) -> {h}")


if __name__ == "__main__":
    main()
