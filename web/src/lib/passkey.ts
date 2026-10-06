"use client";

/**
 * One passkey, two jobs:
 *  - Account: Mera turns the passkey's PRF output into an EVM account (PRF -> BIP-39 -> m/44'/60'/0'/0/0).
 *  - Reviews: the passkey's own P256 key signs each review; PasskeyReviews verifies it onchain through Monad's
 *    P256 precompile. The public key is captured once, at creation, by the WebAuthnClient handed to Mera.
 */
import { createPasskeyWithPrfOutput, getPasskeyPrfOutput, type WebAuthnClient } from "@category-labs/mera";
import { entropyToMnemonic } from "@scure/bip39";
import { wordlist } from "@scure/bip39/wordlists/english.js";
import { bytesToHex, type Hex, toHex } from "viem";
import { mnemonicToAccount } from "viem/accounts";

const STORE = "unmask.passkey.v1";
const ES256 = -7; // P256: the only key type PasskeyReviews can verify
const P256_N = BigInt("0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551");

export type StoredPasskey = { credentialId: string; transports?: string[]; x: Hex; y: Hex };
export type Session = { passkey: StoredPasskey; address: Hex };

const b64url = (bytes: Uint8Array) =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromB64url = (s: string) =>
  Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));

export function loadPasskey(): StoredPasskey | null {
  try {
    const raw = localStorage.getItem(STORE);
    return raw ? (JSON.parse(raw) as StoredPasskey) : null;
  } catch {
    return null;
  }
}

function savePasskey(p: StoredPasskey) {
  try {
    localStorage.setItem(STORE, JSON.stringify(p));
  } catch {
    // private mode: the session still works until the tab closes
  }
}

export function forgetPasskey() {
  try {
    localStorage.removeItem(STORE);
  } catch {}
}

/** P256 public key (x, y) from the SPKI DER the browser returns: the last 64 bytes after the 0x04 prefix. */
function spkiToXY(spki: ArrayBuffer): { x: Hex; y: Hex } {
  const b = new Uint8Array(spki);
  return { x: bytesToHex(b.slice(b.length - 64, b.length - 32)), y: bytesToHex(b.slice(b.length - 32)) };
}

/** A Mera WebAuthnClient that behaves like Mera's browser client, restricted to P256, keeping the public key. */
function capturingClient(captured: { xy?: { x: Hex; y: Hex } }): WebAuthnClient {
  return {
    async createCredential(req) {
      const cred = (await navigator.credentials.create({
        publicKey: {
          rp: req.rp, user: req.user, challenge: req.challenge,
          pubKeyCredParams: [{ type: "public-key", alg: ES256 }],
          ...(req.timeout !== undefined ? { timeout: req.timeout } : {}),
          attestation: req.attestation,
          authenticatorSelection: { residentKey: req.residentKey, requireResidentKey: true, userVerification: req.userVerification },
          extensions: { prf: { eval: { first: req.prfSalt } } } as AuthenticationExtensionsClientInputs,
        },
      })) as PublicKeyCredential | null;
      if (!cred) throw new Error("Passkey creation was cancelled");
      const res = cred.response as AuthenticatorAttestationResponse;
      if (res.getPublicKeyAlgorithm() !== ES256) throw new Error("This authenticator did not create a P256 passkey");
      const spki = res.getPublicKey();
      if (!spki) throw new Error("The browser did not return the passkey's public key");
      captured.xy = spkiToXY(spki);
      const prf = (cred.getClientExtensionResults() as { prf?: { enabled?: boolean; results?: { first?: ArrayBuffer } } }).prf;
      return {
        credentialId: new Uint8Array(cred.rawId),
        transports: res.getTransports?.(),
        prfEnabled: prf?.enabled === true,
        ...(prf?.results?.first ? { prfOutput: new Uint8Array(prf.results.first) } : {}),
      };
    },
    async getCredential(req) {
      const cred = (await navigator.credentials.get({
        publicKey: {
          rpId: req.rpId, challenge: req.challenge, userVerification: req.userVerification,
          ...(req.timeout !== undefined ? { timeout: req.timeout } : {}),
          extensions: { prf: { eval: { first: req.prfSalt } } } as AuthenticationExtensionsClientInputs,
          ...(req.allowCredential ? { allowCredentials: [{ type: "public-key", id: req.allowCredential.credentialId }] } : {}),
        },
      })) as PublicKeyCredential | null;
      if (!cred) throw new Error("Passkey request was cancelled");
      const prf = (cred.getClientExtensionResults() as { prf?: { results?: { first?: ArrayBuffer } } }).prf;
      return {
        credentialId: new Uint8Array(cred.rawId),
        ...(prf?.results?.first ? { prfOutput: new Uint8Array(prf.results.first) } : {}),
      };
    },
  };
}

function accountFromPrf(prfOutput: Uint8Array): Hex {
  return mnemonicToAccount(entropyToMnemonic(prfOutput, wordlist)).address;
}

/** Create a passkey (one Face ID prompt; some authenticators ask twice) and derive the Mera account. */
export async function createAccount(name: string): Promise<Session> {
  const captured: { xy?: { x: Hex; y: Hex } } = {};
  const created = await createPasskeyWithPrfOutput({
    rp: { id: location.hostname, name: "Unmask" },
    user: { name, displayName: name },
    webAuthnClient: capturingClient(captured),
  });
  if (!captured.xy) throw new Error("Passkey public key was not captured");
  const passkey: StoredPasskey = { credentialId: created.credentialId, transports: created.transports as string[] | undefined, ...captured.xy };
  savePasskey(passkey);
  return { passkey, address: accountFromPrf(created.prfOutput) };
}

/** Sign back in with the passkey stored on this device. */
export async function signIn(): Promise<Session> {
  const passkey = loadPasskey();
  if (!passkey) throw new Error("No passkey on this device yet: create one first");
  const { prfOutput } = await getPasskeyPrfOutput({
    rpId: location.hostname,
    credential: { credentialId: passkey.credentialId, transports: passkey.transports },
    webAuthnClient: capturingClient({}),
  });
  return { passkey, address: accountFromPrf(prfOutput) };
}

/** DER-encoded ECDSA signature -> (r, s), with s in low form as the onchain verifier requires. */
function derToRS(der: Uint8Array): { r: Hex; s: Hex } {
  let i = 2;
  const read = () => {
    if (der[i] !== 0x02) throw new Error("Malformed signature");
    const len = der[i + 1];
    const v = der.slice(i + 2, i + 2 + len);
    i += 2 + len;
    return BigInt(bytesToHex(v));
  };
  const r = read();
  let s = read();
  if (s > P256_N / BigInt(2)) s = P256_N - s;
  return { r: toHex(r, { size: 32 }), s: toHex(s, { size: 32 }) };
}

export type SignedReview = {
  authenticatorData: Hex; clientDataJSON: string; challengeIndex: number; typeIndex: number; r: Hex; s: Hex;
};

/** One Face ID prompt: sign the review challenge the contract expects with the passkey's P256 key. */
export async function signReview(passkey: StoredPasskey, challenge: Hex): Promise<SignedReview> {
  const cred = (await navigator.credentials.get({
    publicKey: {
      rpId: location.hostname,
      challenge: Uint8Array.from(challenge.slice(2).match(/../g)!.map((h) => parseInt(h, 16))),
      userVerification: "required",
      allowCredentials: [{ type: "public-key", id: fromB64url(passkey.credentialId) }],
    },
  })) as PublicKeyCredential | null;
  if (!cred) throw new Error("Review signing was cancelled");
  const res = cred.response as AuthenticatorAssertionResponse;
  const clientDataJSON = new TextDecoder().decode(res.clientDataJSON);
  return {
    authenticatorData: bytesToHex(new Uint8Array(res.authenticatorData)),
    clientDataJSON,
    challengeIndex: clientDataJSON.indexOf('"challenge"'),
    typeIndex: clientDataJSON.indexOf('"type"'),
    ...derToRS(new Uint8Array(res.signature)),
  };
}

export { b64url };
