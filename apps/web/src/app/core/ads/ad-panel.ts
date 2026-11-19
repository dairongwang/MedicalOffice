import {
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { TipDirective } from '../ui/tip.directive';
import { type AdCreative, type AdEmbed, AdsService } from './ads.service';

/**
 * The sponsored-content card in the dashboard sidebar (free memberships only).
 *
 * Two render paths, decided by the server's active source:
 *  - `creatives`: first-party cards (sponsor, headline, blurb, call to action) — no third party
 *    is contacted; impressions/clicks are counted anonymously.
 *  - `embed`: a third-party provider tag (AdSense, Microsoft, network). Its script is loaded
 *    only after the user consented on this device; until then a consent card is shown.
 */
@Component({
  selector: 'app-ad-panel',
  imports: [TipDirective],
  template: `
    @if (ads.active() && source(); as src) {
      <section
        class="card ad-panel"
        [class.collapsed]="ads.collapsed()"
        [class.large]="ads.large()"
        id="ad-panel"
        aria-label="Sponsored content"
        [attr.data-source]="src.kind"
      >
        <div class="ad-head">
          <span class="ad-badge">{{ src.label }}</span>
          @if (src.mode === 'fake') {
            <span
              class="ad-fake"
              appTip="Built-in stand-in: no ad provider is configured on this server, nothing leaves the machine."
              >FAKE</span
            >
          }
          @if (!ads.collapsed() && current(); as ad) {
            <span class="ad-sponsor">{{ ad.sponsor }}</span>
          } @else if (!ads.collapsed() && src.third_party) {
            <span class="ad-sponsor">{{ src.provider_name || src.title }}</span>
          } @else {
            <span class="ad-sponsor">Sponsored content</span>
          }
          <span class="ad-tools">
            <button
              class="ad-tool"
              type="button"
              id="ad-why"
              (click)="why.emit()"
              appTip="Why you see this, how it is chosen and what is (not) collected — and where to turn it off."
            >
              Why this?
            </button>
            <button
              class="ad-tool"
              type="button"
              id="ad-collapse"
              (click)="ads.setCollapsed(!ads.collapsed())"
              [attr.aria-expanded]="!ads.collapsed()"
              [appTip]="
                ads.collapsed()
                  ? 'Show the sponsored content again.'
                  : 'Fold the panel to a single line.'
              "
            >
              {{ ads.collapsed() ? 'Show' : 'Hide' }}
            </button>
          </span>
        </div>

        @if (!ads.collapsed()) {
          @if (ads.large() && src.render === 'creatives') {
            <div class="ad-target">
              <span class="ad-target-label">Chosen for</span>
              <span class="ad-hl">{{ regionLabel() }}</span>
              <span class="ad-plus">+</span>
              <span class="ad-hl">{{ languageLabel() }}</span>
              <span class="ad-target-source">{{ regionSource() }}</span>
            </div>
          }

          @if (src.render === 'creatives') {
            @if (current(); as ad) {
              <div class="ad-body">
                <a
                  class="ad-link"
                  id="ad-link"
                  [href]="ad.target_url === 'app:upgrade' ? '#' : ad.target_url"
                  [attr.target]="ad.target_url === 'app:upgrade' ? null : '_blank'"
                  rel="noopener noreferrer nofollow sponsored"
                  (click)="onClick($event, ad)"
                  appTip="Opens the sponsor's page in a new tab. Only an anonymous click count is recorded."
                >
                  @if (ad.image_url) {
                    <img class="ad-img" [src]="ad.image_url" alt="" loading="lazy" />
                  }
                  <span class="ad-text">
                    <span class="ad-headline">{{ ad.headline }}</span>
                    @if (ad.blurb) {
                      <span class="ad-blurb">{{ ad.blurb }}</span>
                    }
                    <span class="ad-cta">{{ ad.cta_label }} →</span>
                    @if (src.disclosure) {
                      <span class="ad-disclosure" [class.prominent]="src.kind === 'affiliate'">{{
                        src.disclosure
                      }}</span>
                    }
                  </span>
                </a>
                <button
                  class="ad-dismiss"
                  type="button"
                  id="ad-dismiss"
                  aria-label="Show another"
                  (click)="ads.next()"
                  appTip="Not interested — show the next one."
                >
                  ✕
                </button>
              </div>
            } @else {
              <div class="ad-empty">
                <span>No sponsored content for your region and language right now.</span>
                @if (ads.large()) {
                  <span class="small muted"
                    >Region <code>{{ regionLabel() }}</code> · language
                    <code>{{ languageLabel() }}</code></span
                  >
                }
              </div>
            }
          } @else {
            <div class="ad-body-embed">
              @if (!ads.consent()) {
                <div class="ad-consent" id="ad-consent">
                  <span>
                    This space is filled by <strong>{{ src.provider_name || src.title }}</strong
                    >, a third party. Loading it sends your IP address and browser details to that
                    provider, which may use cookies to personalise what you see.
                  </span>
                  @if (src.privacy_url) {
                    <a [href]="src.privacy_url" target="_blank" rel="noopener noreferrer"
                      >Provider's privacy policy ↗</a
                    >
                  }
                  <span class="ad-consent-actions">
                    <button
                      class="btn primary"
                      type="button"
                      id="ad-consent-accept"
                      (click)="ads.setConsent(true)"
                      appTip="Allow the provider's script on this device. You can withdraw this any time under 'Why this?'."
                    >
                      Allow
                    </button>
                    <button
                      class="btn"
                      type="button"
                      id="ad-consent-upgrade"
                      (click)="upgrade.emit()"
                      appTip="Pro memberships have no sponsored content at all."
                    >
                      Go ad-free
                    </button>
                  </span>
                </div>
              } @else {
                <div #slot class="ad-slot" id="ad-slot"></div>
                <div class="ad-slot-status muted">
                  @if (embedError()) {
                    {{ embedError() }}
                  } @else if (!embedLoaded()) {
                    Loading {{ src.provider_name || src.title }}…
                  }
                </div>
              }
            </div>
          }
        }
      </section>
    }
  `,
})
export class AdPanel {
  readonly ads = inject(AdsService);
  /** "Why this?" — the app opens the sponsored-content info dialog. */
  readonly why = output<void>();
  /** The user wants to go ad-free (house ad / consent card) — the app opens the account screen. */
  readonly upgrade = output<void>();

  readonly source = this.ads.source;
  readonly current = this.ads.current;
  readonly slot = viewChild<ElementRef<HTMLElement>>('slot');
  readonly embedLoaded = signal(false);
  readonly embedError = signal<string | null>(null);

  readonly regionLabel = computed(() => {
    const r = this.ads.response()?.region ?? this.ads.region();
    if (!r || r === 'ZZ') return 'no region';
    try {
      return new Intl.DisplayNames([navigator.language], { type: 'region' }).of(r) ?? r;
    } catch {
      return r;
    }
  });
  readonly languageLabel = computed(() => {
    try {
      return (
        new Intl.DisplayNames([navigator.language], { type: 'language' }).of(this.ads.language) ??
        this.ads.language
      );
    } catch {
      return this.ads.language;
    }
  });
  readonly regionSource = computed(() => {
    switch (this.ads.regionMode()) {
      case 'manual':
        return 'Region chosen by you (Why this? → Region).';
      case 'off':
        return 'No region is sent — you turned it off.';
      default:
        return 'Region derived on this device from your language and time zone; never from your IP address.';
    }
  });

  private embeddedFor: string | null = null;

  constructor() {
    // Count an impression once per creative shown.
    effect(() => {
      const ad = this.current();
      if (ad && this.ads.active() && !this.ads.collapsed()) this.ads.impression(ad);
    });
    // Third-party embed: mount the provider tag once the slot exists and consent was given.
    effect(() => {
      const src = this.source();
      const slot = this.slot()?.nativeElement;
      const consent = this.ads.consent();
      if (!src || src.render !== 'embed' || !src.embed || !slot || !consent) {
        this.embeddedFor = null;
        return;
      }
      const key = src.embed.script_url + '|' + JSON.stringify(src.embed.container_attrs);
      if (this.embeddedFor === key) return;
      this.embeddedFor = key;
      this.mountEmbed(slot, src.embed);
    });
  }

  onClick(event: MouseEvent, ad: AdCreative): void {
    this.ads.click(ad);
    if (ad.target_url === 'app:upgrade') {
      event.preventDefault();
      this.upgrade.emit();
    }
  }

  private mountEmbed(slot: HTMLElement, embed: AdEmbed): void {
    this.embedLoaded.set(false);
    this.embedError.set(null);
    slot.replaceChildren();
    const container = document.createElement(embed.container_tag || 'div');
    if (embed.container_class) container.className = embed.container_class;
    if (embed.container_style) container.setAttribute('style', embed.container_style);
    for (const [k, v] of Object.entries(embed.container_attrs ?? {})) container.setAttribute(k, v);
    slot.appendChild(container);

    const script = document.createElement('script');
    script.async = true;
    script.src = embed.script_url;
    for (const [k, v] of Object.entries(embed.script_attrs ?? {})) script.setAttribute(k, v);
    script.onload = () => {
      this.embedLoaded.set(true);
      if (embed.push_global) {
        const w = window as unknown as Record<string, { push(x: object): unknown } | undefined>;
        try {
          (w[embed.push_global] ??= [] as unknown as { push(x: object): unknown }).push({});
        } catch {
          /* the provider's contract differs — nothing we can do */
        }
      }
    };
    script.onerror = () =>
      this.embedError.set(
        'The ad provider could not be loaded (blocked by the browser or offline).',
      );
    slot.appendChild(script);
  }
}
