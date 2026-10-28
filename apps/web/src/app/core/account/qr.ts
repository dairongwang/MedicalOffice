import qrcode from 'qrcode-generator';

/** A QR code as one SVG path (`viewBox="0 0 size size"`), rendered entirely on the device. */
export interface QrImage {
  /** Modules per side, quiet zone included. */
  size: number;
  /** `M x y h1 v1 h-1 z` per dark module — bind to `<path [attr.d]>`. */
  path: string;
}

const QUIET_ZONE = 4; // modules of white margin around the symbol (the spec's minimum)

/**
 * Encodes `text` (e.g. an `otpauth://` URI) at error-correction level M. Authenticator apps
 * scan it straight off the screen; nothing is sent anywhere to draw it.
 */
export function qrImage(text: string): QrImage {
  const qr = qrcode(0, 'M'); // 0 = smallest version that fits
  qr.addData(text, 'Byte');
  qr.make();
  const n = qr.getModuleCount();
  const parts: string[] = [];
  for (let r = 0; r < n; r++) {
    for (let c = 0; c < n; c++) {
      if (qr.isDark(r, c)) parts.push(`M${c + QUIET_ZONE} ${r + QUIET_ZONE}h1v1h-1z`);
    }
  }
  return { size: n + 2 * QUIET_ZONE, path: parts.join('') };
}

/** `qrImage` as a square boolean matrix (tests, text rendering). */
export function qrModules(text: string): boolean[][] {
  const qr = qrcode(0, 'M');
  qr.addData(text, 'Byte');
  qr.make();
  const n = qr.getModuleCount();
  return Array.from({ length: n }, (_, r) => Array.from({ length: n }, (_, c) => qr.isDark(r, c)));
}
