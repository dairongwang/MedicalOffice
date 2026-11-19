import { Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import { ApiService } from '../account/api.service';
import { MembershipService } from '../membership/membership.service';
import { type AdRegionMode, SettingsService } from '../settings/settings.service';
import { detectRegion, isValidRegion, primaryLanguage } from './region';

/** One first-party creative, as GET /ads/serve returns it — nothing about the viewer. */
export interface AdCreative {
  id: string;
  sponsor: string;
  headline: string;
  blurb: string;
  cta_label: string;
  /** https URL, or `app:upgrade` for house ads (opens the account screen). */
  target_url: string;
  image_url?: string | null;
}

/** Script-tag description of a third-party provider (render = embed; ad_sources.py). */
export interface AdEmbed {
  script_url: string;
  script_attrs?: Record<string, string>;
  container_tag?: string;
  container_class?: string;
  container_style?: string;
  container_attrs?: Record<string, string>;
  /** Global array the provider's script reads (AdSense: `adsbygoogle`) — pushed once loaded. */
  push_global?: string | null;
}

/** The active ad source (schemas.AdSource). */
export interface AdSource {
  kind: 'internal' | 'sponsored' | 'affiliate' | 'adsense' | 'microsoft' | 'network' | string;
  label: string;
  title: string;
  mode: 'fake' | 'live' | 'unavailable' | string;
  render: 'creatives' | 'embed';
  third_party: boolean;
  consent_required: boolean;
  provider_name?: string;
  privacy_url?: string;
  disclosure?: string;
  embed?: AdEmbed | null;
}

export interface AdServeResponse {
  ads: AdCreative[];
  /** The region the selection used ("ZZ" = none). */
  region: string;
  rotation_seconds: number;
  enabled: boolean;
  source: AdSource;
}

type AdEventKind = 'impression' | 'click';
interface AdEvent {
  ad_id: string;
  kind: AdEventKind;
  region?: string | null;
}

/** Third-party consent is per device and per provider kind (never synced, never sent anywhere). */
const CONSENT_KEY = 'pa.ads.consent.v1';
/** Anonymous counts are sent in small batches (the server accepts at most 20 per request). */
const FLUSH_DELAY_MS = 4000;
const MAX_BATCH = 20;
/** Ask the server again after this long even without a rotation hint (new campaigns). */
const REFRESH_MS = 30 * 60 * 1000;

/**
 * Sponsored content for free memberships (docs/ADVERTISING.md): contextual only.
 *
 *  - What is asked for: GET /ads/serve?region=&lang= **without** the session token — a
 *    creative request can never be tied to an account. The region is decided on this device
 *    (region.ts: locale / time zone, or chosen by the user, or "ZZ" = none); never the IP.
 *  - What is reported: anonymous impression/click counts (POST /ads/metrics, also without
 *    the token), batched, each creative's impression once per time it is shown.
 *  - Paid and admin memberships (feature `ad_free`) never ask at all; the user can switch the
 *    panel off on this device (settings `adsEnabled`).
 *  - Third-party sources (AdSense, Microsoft, network script) are consent-gated: the panel shows
 *    a consent card until the user allowed that provider on this device.
 */
@Injectable({ providedIn: 'root' })
export class AdsService {
  private readonly api = inject(ApiService);
  private readonly settings = inject(SettingsService);
  private readonly membership = inject(MembershipService);

  /** Primary language subtag sent with the request ("de-AT" → "de"). */
  readonly language = primaryLanguage();

  readonly response = signal<AdServeResponse | null>(null);
  readonly error = signal<string | null>(null);
  readonly loading = signal(false);
  private readonly index = signal(0);
  private readonly consents = signal<Record<string, boolean>>(this.loadConsents());

  // ---- device settings -------------------------------------------------------------------
  readonly enabled = computed(() => this.settings.settings().adsEnabled);
  readonly collapsed = computed(() => this.settings.settings().adsCollapsed);
  readonly large = computed(() => this.settings.settings().adsLarge);
  readonly regionMode = computed<AdRegionMode>(() => this.settings.settings().adsRegionMode);
  /** The region this device would send: chosen, derived, or "ZZ" (the user said none). */
  readonly region = computed<string | null>(() => {
    const s = this.settings.settings();
    if (s.adsRegionMode === 'off') return 'ZZ';
    if (s.adsRegionMode === 'manual') return isValidRegion(s.adsRegion) ? s.adsRegion : null;
    return detectRegion();
  });

  // ---- what the panel shows --------------------------------------------------------------
  /** Should this device show (and ask for) sponsored content at all? */
  readonly wanted = computed(() => this.enabled() && !this.membership.adFree());
  readonly source = computed<AdSource | null>(() => {
    const res = this.response();
    return res && res.enabled ? res.source : null;
  });
  /** The panel is on screen: wanted, and the server has an active source. */
  readonly active = computed(() => this.wanted() && this.source() !== null);
  readonly current = computed<AdCreative | null>(() => {
    const ads = this.response()?.ads ?? [];
    return ads.length ? ads[this.index() % ads.length] : null;
  });
  /** Has the user allowed the active third-party provider on this device? */
  readonly consent = computed(() => {
    const src = this.source();
    if (!src) return false;
    if (!src.third_party && !src.consent_required) return true;
    return !!this.consents()[src.kind];
  });

  private started = false;
  private rotationTimer: ReturnType<typeof setInterval> | null = null;
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private queue: AdEvent[] = [];
  private readonly counted = new Set<string>();

  constructor() {
    // (Re)load whenever what the request depends on changes — membership, on/off, region.
    effect(() => {
      const wanted = this.wanted();
      const region = this.region();
      if (!this.started) return;
      untracked(() => {
        if (wanted) void this.load(region);
        else this.clear();
      });
    });
  }

  /** Called once by the app shell after the account state is known. */
  init(): void {
    if (this.started) return;
    this.started = true;
    if (this.wanted()) void this.load(this.region());
    setInterval(() => {
      if (this.wanted() && navigator.onLine) void this.load(this.region());
    }, REFRESH_MS);
    window.addEventListener('pagehide', () => this.flush(true));
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') this.flush(true);
    });
  }

  /** GET /ads/serve — anonymous: the session token is never sent here. */
  async load(region: string | null = this.region()): Promise<void> {
    if (!this.wanted()) return;
    this.loading.set(true);
    const params = new URLSearchParams();
    if (region) params.set('region', region);
    if (this.language) params.set('lang', this.language);
    try {
      const res = await this.api.get<AdServeResponse>(`/ads/serve?${params.toString()}`, {
        auth: false,
      });
      if (res.source?.embed?.script_url) {
        res.source.embed = {
          ...res.source.embed,
          script_url: this.absolute(res.source.embed.script_url),
        };
      }
      this.response.set(res);
      this.index.set(0);
      this.counted.clear();
      this.error.set(null);
      this.startRotation(res.rotation_seconds);
    } catch (e) {
      // Offline or the server has no ads: the panel simply stays away.
      this.error.set(e instanceof Error ? e.message : String(e));
    } finally {
      this.loading.set(false);
    }
  }

  /** Show the next creative ("not interested" / rotation). */
  next(): void {
    const n = this.response()?.ads.length ?? 0;
    if (n > 1) this.index.update((i) => (i + 1) % n);
  }

  /** Count an impression once per creative and response (the panel calls this on display). */
  impression(ad: AdCreative): void {
    const key = `${ad.id}:${this.index()}`;
    if (this.counted.has(key)) return;
    this.counted.add(key);
    this.enqueue({ ad_id: ad.id, kind: 'impression' });
  }

  click(ad: AdCreative): void {
    this.enqueue({ ad_id: ad.id, kind: 'click' });
    this.flush();
  }

  // ---- settings (device-local) -------------------------------------------------------------

  setEnabled(on: boolean): void {
    this.settings.update({ adsEnabled: on });
  }

  setCollapsed(collapsed: boolean): void {
    this.settings.update({ adsCollapsed: collapsed });
  }

  setLarge(large: boolean): void {
    this.settings.update({ adsLarge: large });
  }

  setRegionMode(mode: AdRegionMode, region?: string | null): void {
    const patch: { adsRegionMode: AdRegionMode; adsRegion?: string | null } = {
      adsRegionMode: mode,
    };
    if (mode === 'manual') patch.adsRegion = isValidRegion(region ?? null) ? region! : null;
    this.settings.update(patch);
  }

  /** Allow / withdraw the active third-party provider on this device. */
  setConsent(allowed: boolean): void {
    const kind = this.source()?.kind;
    if (!kind) return;
    const next = { ...this.consents(), [kind]: allowed };
    if (!allowed) delete next[kind];
    this.consents.set(next);
    try {
      localStorage.setItem(CONSENT_KEY, JSON.stringify(next));
    } catch {
      /* private mode — keep it for this session */
    }
  }

  // ---- internals --------------------------------------------------------------------------

  private clear(): void {
    this.stopRotation();
    this.response.set(null);
    this.index.set(0);
    this.counted.clear();
  }

  private startRotation(seconds: number): void {
    this.stopRotation();
    if (!seconds || seconds <= 0) return;
    this.rotationTimer = setInterval(() => {
      if (document.visibilityState === 'visible' && !this.collapsed()) this.next();
    }, seconds * 1000);
  }

  private stopRotation(): void {
    if (this.rotationTimer) clearInterval(this.rotationTimer);
    this.rotationTimer = null;
  }

  private enqueue(event: AdEvent): void {
    const region = this.response()?.region;
    this.queue.push({ ...event, region: region && region !== 'ZZ' ? region : null });
    if (this.queue.length >= MAX_BATCH) {
      this.flush();
      return;
    }
    if (!this.flushTimer) this.flushTimer = setTimeout(() => this.flush(), FLUSH_DELAY_MS);
  }

  /** POST /ads/metrics — anonymous counts only (no token). `beacon`: the page is going away. */
  private flush(beacon = false): void {
    if (this.flushTimer) clearTimeout(this.flushTimer);
    this.flushTimer = null;
    while (this.queue.length) {
      const events = this.queue.splice(0, MAX_BATCH);
      const body = JSON.stringify({ events });
      if (beacon && 'sendBeacon' in navigator) {
        try {
          const blob = new Blob([body], { type: 'application/json' });
          if (navigator.sendBeacon(this.api.base + '/ads/metrics', blob)) continue;
        } catch {
          /* fall through to fetch */
        }
      }
      void this.api.post('/ads/metrics', { events }, { auth: false }).catch(() => {
        /* counts are best effort — never bother the user */
      });
    }
  }

  private loadConsents(): Record<string, boolean> {
    try {
      const raw = JSON.parse(localStorage.getItem(CONSENT_KEY) ?? '{}');
      return raw && typeof raw === 'object' ? (raw as Record<string, boolean>) : {};
    } catch {
      return {};
    }
  }

  /** The API may live on another origin (meta api-base): provider paths it serves itself
   *  (the built-in fakes, "/api/v1/ads/fake/…") must point there. */
  private absolute(url: string): string {
    if (!url.startsWith('/')) return url;
    const origin = this.api.base.replace(/\/api\/v1$/, '');
    return origin + url;
  }
}
