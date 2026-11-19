/**
 * Sign-in verifier, identical to the main app (apps/web/src/app/core/sync/crypto.ts) and the
 * server-side twin (services/account/clientcrypto.py):
 *
 *   PBKDF2-SHA256(password, SHA-256(email), 600 000) → master → HKDF("auth") → auth key (hex)
 *
 * The password itself never leaves the browser; the server only ever sees the auth key.
 */

const enc = new TextEncoder();
const ITERATIONS = 600_000;

export async function deriveAuthKey(password, email) {
  const salt = await crypto.subtle.digest("SHA-256", enc.encode(email.trim().toLowerCase()));
  const base = await crypto.subtle.importKey("raw", enc.encode(password.normalize("NFKC")), "PBKDF2", false, [
    "deriveBits",
  ]);
  const master = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations: ITERATIONS },
    base,
    256,
  );
  const hkdf = await crypto.subtle.importKey("raw", master, "HKDF", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(32), info: enc.encode("auth") },
    hkdf,
    256,
  );
  return [...new Uint8Array(bits)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
