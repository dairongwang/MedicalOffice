import { isOtpauthUri, isTouchDevice, receiveSmsCode, supportsSmsAutofill } from './platform';

describe('two-factor platform helpers', () => {
  it('accepts only the otpauth URIs the server builds as app links', () => {
    expect(
      isOtpauthUri(
        'otpauth://totp/Personal%20Assistant%3Aann%40example.com?secret=JBSWY3DPEHPK3PXP&issuer=Personal%20Assistant',
      ),
    ).toBe(true);
    for (const bad of [
      '',
      'javascript:alert(1)',
      'otpauth://hotp/x?secret=A',
      'https://example.com',
      'otpauth://totp/a b',
      'otpauth://totp/x"onclick=1',
    ])
      expect(isOtpauthUri(bad)).toBe(false);
  });

  it('falls back to typing where WebOTP is missing (desktop browsers, Safari, Firefox, jsdom)', async () => {
    expect(supportsSmsAutofill()).toBe(false);
    expect(await receiveSmsCode(new AbortController().signal)).toBeNull();
  });

  it('uses WebOTP where the browser has it (Android Chrome)', async () => {
    const w = window as unknown as Record<string, unknown>;
    const creds = navigator as unknown as { credentials?: unknown };
    const before = creds.credentials;
    w['OTPCredential'] = class {};
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: { get: async () => ({ code: '123456', type: 'otp' }) },
    });
    try {
      expect(supportsSmsAutofill()).toBe(true);
      expect(await receiveSmsCode(new AbortController().signal)).toBe('123456');
      Object.defineProperty(navigator, 'credentials', {
        configurable: true,
        value: {
          get: async () => {
            throw new DOMException('aborted', 'AbortError');
          },
        },
      });
      expect(await receiveSmsCode(new AbortController().signal)).toBeNull();
    } finally {
      delete w['OTPCredential'];
      Object.defineProperty(navigator, 'credentials', { configurable: true, value: before });
    }
  });

  it('detects touch devices without throwing where matchMedia is missing', () => {
    expect(typeof isTouchDevice()).toBe('boolean');
  });
});
