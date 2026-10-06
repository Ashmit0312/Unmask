/**
 * Relays a passkey-signed review to PasskeyReviews and pays the gas, so reviewers need no wallet or MON.
 * The review is simulated first: an invalid signature reverts there and costs nothing. The relayer holds no user
 * keys; it can only submit what a passkey signed, and the contract rejects any field it changes.
 */
import { createWalletClient, http, isHex, type Hex } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { ADDRESSES, RPC_URL, chain, passkeyReviewsAbi } from "@/lib/config";
import { publicClient } from "@/lib/chain";

type Body = {
  agentId: string; value: number; tag: Hex; x: Hex; y: Hex;
  auth: { authenticatorData: Hex; clientDataJSON: string; challengeIndex: number; typeIndex: number; r: Hex; s: Hex };
};

const isWord = (v: unknown): v is Hex => typeof v === "string" && isHex(v) && v.length === 66;

function parse(b: Body): string | null {
  if (!/^\d{1,30}$/.test(b?.agentId ?? "")) return "agentId";
  if (!Number.isInteger(b.value) || b.value < 0 || b.value > 100) return "value";
  if (![b.tag, b.x, b.y, b.auth?.r, b.auth?.s].every(isWord)) return "tag/x/y/r/s";
  if (!isHex(b.auth.authenticatorData) || typeof b.auth.clientDataJSON !== "string" || b.auth.clientDataJSON.length > 2048) return "auth";
  return null;
}

export async function POST(request: Request) {
  const key = process.env.RELAYER_PRIVATE_KEY as Hex | undefined;
  if (!key) return Response.json({ error: "Relayer is not configured" }, { status: 503 });

  let body: Body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }
  const bad = parse(body);
  if (bad) return Response.json({ error: `Invalid field: ${bad}` }, { status: 400 });

  const account = privateKeyToAccount(key);
  const args = [
    BigInt(body.agentId), body.value, body.tag, body.x, body.y,
    { ...body.auth, challengeIndex: BigInt(body.auth.challengeIndex), typeIndex: BigInt(body.auth.typeIndex) },
  ] as const;
  try {
    const { request: tx } = await publicClient.simulateContract({
      account, address: ADDRESSES.passkeyReviews, abi: passkeyReviewsAbi, functionName: "submit", args,
    });
    const gas = await publicClient.estimateContractGas({
      account, address: ADDRESSES.passkeyReviews, abi: passkeyReviewsAbi, functionName: "submit", args,
    });
    const wallet = createWalletClient({ account, chain, transport: http(RPC_URL) });
    // Monad bills the gas limit, so keep it close to the estimate.
    const hash = await wallet.writeContract({ ...tx, gas: (gas * BigInt(115)) / BigInt(100) });
    const receipt = await publicClient.waitForTransactionReceipt({ hash });
    return Response.json({ hash, status: receipt.status, block: Number(receipt.blockNumber) });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    const reason = /InvalidPasskeySignature/.test(msg) ? "The passkey signature did not verify"
      : /ValueOutOfRange/.test(msg) ? "Rating must be 0-100"
      : /NonexistentToken|ownerOf/.test(msg) ? "No such agent"
      : "The review could not be submitted";
    return Response.json({ error: reason }, { status: 422 });
  }
}
