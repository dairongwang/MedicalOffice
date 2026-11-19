/**
 * Coarse region for contextual sponsored content — derived *on the device*, never from the
 * network address. The result is an ISO 3166-1 alpha-2 country code, "ZZ" (no region) or
 * null (nothing could be derived). The user sees and can override it (AdInfoDialog).
 */

/** Region subtag of a BCP 47 locale: "de-AT" → "AT", "en" → null. */
export function regionFromLocale(locale: string | undefined | null): string | null {
  if (!locale) return null;
  try {
    const region = new Intl.Locale(locale).region;
    return region && /^[A-Z]{2}$/.test(region) ? region : null;
  } catch {
    const m = /[-_]([A-Za-z]{2})(?:[-_]|$)/.exec(locale);
    return m ? m[1].toUpperCase() : null;
  }
}

/** A few time zones map unambiguously to a country (used when the locale has no region). */
const TZ_COUNTRY: Record<string, string> = {
  'Europe/Berlin': 'DE',
  'Europe/Vienna': 'AT',
  'Europe/Zurich': 'CH',
  'Europe/Paris': 'FR',
  'Europe/Madrid': 'ES',
  'Europe/Rome': 'IT',
  'Europe/Amsterdam': 'NL',
  'Europe/Brussels': 'BE',
  'Europe/Lisbon': 'PT',
  'Europe/Dublin': 'IE',
  'Europe/London': 'GB',
  'Europe/Stockholm': 'SE',
  'Europe/Oslo': 'NO',
  'Europe/Copenhagen': 'DK',
  'Europe/Helsinki': 'FI',
  'Europe/Warsaw': 'PL',
  'Europe/Prague': 'CZ',
  'Europe/Budapest': 'HU',
  'Europe/Athens': 'GR',
  'Europe/Istanbul': 'TR',
  'Asia/Tokyo': 'JP',
  'Asia/Seoul': 'KR',
  'Asia/Singapore': 'SG',
  'Asia/Kolkata': 'IN',
  'Asia/Dubai': 'AE',
  'Australia/Sydney': 'AU',
  'Australia/Melbourne': 'AU',
  'Pacific/Auckland': 'NZ',
  'America/Toronto': 'CA',
  'America/Vancouver': 'CA',
  'America/Mexico_City': 'MX',
  'America/Sao_Paulo': 'BR',
  'America/Argentina/Buenos_Aires': 'AR',
  'Africa/Johannesburg': 'ZA',
};

/** US time zones share names with no other country. */
const US_TZ =
  /^America\/(New_York|Chicago|Denver|Los_Angeles|Phoenix|Anchorage|Detroit|Boise|Indiana)/;

export function regionFromTimeZone(tz: string | undefined | null): string | null {
  if (!tz) return null;
  if (US_TZ.test(tz) || tz.startsWith('US/') || tz === 'Pacific/Honolulu') return 'US';
  return TZ_COUNTRY[tz] ?? null;
}

/** Best local guess: locale region first, then the time zone. */
export function detectRegion(): string | null {
  const locales = [navigator.language, ...(navigator.languages ?? [])];
  for (const l of locales) {
    const r = regionFromLocale(l);
    if (r) return r;
  }
  try {
    return regionFromTimeZone(Intl.DateTimeFormat().resolvedOptions().timeZone);
  } catch {
    return null;
  }
}

/** Primary language subtag for the request ("de-AT" → "de"). */
export function primaryLanguage(): string {
  const l = navigator.language || 'en';
  return l.split(/[-_]/)[0].toLowerCase();
}

export function isValidRegion(code: string | null | undefined): code is string {
  return !!code && /^[A-Z]{2}$/.test(code);
}
