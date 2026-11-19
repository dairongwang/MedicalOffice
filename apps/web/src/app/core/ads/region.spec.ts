import { isValidRegion, regionFromLocale, regionFromTimeZone } from './region';

describe('ads region detection (on the device)', () => {
  it('takes the region subtag of a locale', () => {
    expect(regionFromLocale('de-AT')).toBe('AT');
    expect(regionFromLocale('en_US')).toBe('US');
    expect(regionFromLocale('en')).toBeNull();
    expect(regionFromLocale(null)).toBeNull();
  });

  it('falls back to unambiguous time zones', () => {
    expect(regionFromTimeZone('Europe/Berlin')).toBe('DE');
    expect(regionFromTimeZone('America/Chicago')).toBe('US');
    expect(regionFromTimeZone('Etc/UTC')).toBeNull();
  });

  it('validates ISO alpha-2 codes', () => {
    expect(isValidRegion('DE')).toBe(true);
    expect(isValidRegion('de')).toBe(false);
    expect(isValidRegion('ZZZ')).toBe(false);
    expect(isValidRegion(null)).toBe(false);
  });
});
