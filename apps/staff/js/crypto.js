/**
 * Sign-in verifier, identical to the main app (apps/web/src/app/core/sync/crypto.ts) and the
 * server-side twin (services/account/clientcrypto.py):
 *
 *   PBKDF2-SHA256(password, SHA-256(email), 600 000) → master → HKDF("auth") → auth key (hex)
 *                                                            → HKDF("wrap") → wrap key (AES-256-GCM)
 *   wrapped data key = base64(iv ‖ AES-GCM(wrap key, random 32-byte data key, aad "data-key"))
 *
 * The password itself never leaves the browser; the server only ever sees the auth key (and,
 * when an account is created, the wrapped data key it cannot open).
 */

const enc = new TextEncoder();
const ITERATIONS = 600_000;

/** The key-derivation parameters stored with a new account (the same as the main app's). */
export const KDF_PARAMS = { kdf: "pbkdf2-sha256", iterations: ITERATIONS, hkdf: "sha256", v: 1 };

async function master(password, email) {
  const salt = await crypto.subtle.digest("SHA-256", enc.encode(email.trim().toLowerCase()));
  const base = await crypto.subtle.importKey("raw", enc.encode(password.normalize("NFKC")), "PBKDF2", false, [
    "deriveBits",
  ]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations: ITERATIONS },
    base,
    256,
  );
  return crypto.subtle.importKey("raw", bits, "HKDF", false, ["deriveBits"]);
}

async function hkdf(key, info) {
  return crypto.subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(32), info: enc.encode(info) },
    key,
    256,
  );
}

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

export async function deriveAuthKey(password, email) {
  return hex(await hkdf(await master(password, email), "auth"));
}

/** A new account's keys: the auth key and a freshly generated data key wrapped with the password. */
export async function newAccountKeys(password, email) {
  const m = await master(password, email);
  const authKey = hex(await hkdf(m, "auth"));
  const wrapKey = await crypto.subtle.importKey("raw", await hkdf(m, "wrap"), "AES-GCM", false, ["encrypt"]);
  const dataKey = crypto.getRandomValues(new Uint8Array(32));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const sealed = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: enc.encode("data-key") },
    wrapKey,
    dataKey,
  );
  const blob = new Uint8Array(iv.length + sealed.byteLength);
  blob.set(iv, 0);
  blob.set(new Uint8Array(sealed), iv.length);
  let binary = "";
  for (const b of blob) binary += String.fromCharCode(b);
  return { auth_key: authKey, wrapped_data_key: btoa(binary), kdf_params: KDF_PARAMS };
}
