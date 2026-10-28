import { qrImage, qrModules } from './qr';

const URI =
  'otpauth://totp/Personal%20Assistant%3Aann%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Personal%20Assistant&algorithm=SHA1&digits=6&period=30';

function finder(m: boolean[][], r0: number, c0: number): boolean {
  // A 7×7 finder pattern: dark ring, light ring, dark 3×3 centre.
  for (let r = 0; r < 7; r++)
    for (let c = 0; c < 7; c++) {
      const edge = r === 0 || r === 6 || c === 0 || c === 6;
      const centre = r >= 2 && r <= 4 && c >= 2 && c <= 4;
      if (m[r0 + r][c0 + c] !== (edge || centre)) return false;
    }
  return true;
}

describe('QR code for the authenticator set-up', () => {
  it('is a valid-looking symbol: square, 21 + 4k modules, three finder patterns', () => {
    const m = qrModules(URI);
    const n = m.length;
    expect(n).toBeGreaterThanOrEqual(21);
    expect((n - 21) % 4).toBe(0);
    expect(m.every((row) => row.length === n)).toBe(true);
    expect(finder(m, 0, 0)).toBe(true);
    expect(finder(m, 0, n - 7)).toBe(true);
    expect(finder(m, n - 7, 0)).toBe(true);
  });

  it('renders one path with a quiet zone around the modules', () => {
    const m = qrModules(URI);
    const img = qrImage(URI);
    expect(img.size).toBe(m.length + 8);
    const dark = m.flat().filter(Boolean).length;
    expect(img.path.split('M').length - 1).toBe(dark);
    expect(img.path.startsWith('M4 4h1v1h-1z')).toBe(true); // top-left finder corner, offset by the quiet zone
  });

  it('is deterministic and grows with the payload', () => {
    expect(qrImage(URI)).toEqual(qrImage(URI));
    expect(qrModules('otpauth://totp/a?secret=ABC').length).toBeLessThan(qrModules(URI).length);
  });
});
