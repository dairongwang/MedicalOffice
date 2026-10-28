/**
 * The few places where the two-factor screens adapt to the device. Everything degrades to
 * "type the code by hand", which works in every browser on phones, tablets, macOS, Linux and
 * Windows alike.
 *
 * - Codes by text message: Android/Chrome can read the code from the incoming SMS (WebOTP —
 *   the server appends "@host #code" when SMS_WEBOTP_HOST is set); iOS/iPadOS/macOS Safari offer
 *   it above the keyboard through `autocomplete="one-time-code"` (set on every code field).
 * - Authenticator set-up: a QR code cannot be scanned by the phone that shows it, so touch
 *   devices also get an "open in authenticator app" link (`otpauth://` is handled by the
 *   installed app) next to the key to copy.
 */

/** Phones and tablets (primary pointer is a finger). */
export function isTouchDevice(): boolean {
  try {
    return typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;
  } catch {
    return false;
  }
}

/** The browser can read a one-time code from an incoming text message (WebOTP). */
export function supportsSmsAutofill(): boolean {
  return typeof window !== 'undefined' && 'OTPCredential' in window && !!navigator.credentials;
}

/**
 * Waits for the text message carrying the code (WebOTP). Resolves with the code, or null when
 * unsupported, aborted (the step ended) or the user declined.
 */
export async function receiveSmsCode(signal: AbortSignal): Promise<string | null> {
  if (!supportsSmsAutofill()) return null;
  try {
    const options = { otp: { transport: ['sms'] }, signal } as unknown as CredentialRequestOptions;
    const credential = (await navigator.credentials.get(options)) as unknown as {
      code?: string;
    } | null;
    return credential?.code ?? null;
  } catch {
    return null;
  }
}

/** Only authenticator URIs the server built may become a link (Angular would otherwise mark the scheme unsafe). */
export function isOtpauthUri(uri: string): boolean {
  return /^otpauth:\/\/totp\/[^\s"'<>]+$/.test(uri);
}
