import { Component, computed, inject, output, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MembershipService } from '../membership/membership.service';
import type { AdRegionMode } from '../settings/settings.service';
import { DialogDirective } from '../ui/dialog.directive';
import { TipDirective } from '../ui/tip.directive';
import { ToastService } from '../ui/toast.service';
import { AdsService } from './ads.service';
import { isValidRegion } from './region';

/**
 * "Why this?" — what the sponsored-content panel does and the controls for it: on/off,
 * region (auto / chosen / none), panel size, third-party consent, and the ad-free upgrade.
 */
@Component({
  selector: 'app-ad-info-dialog',
  imports: [FormsModule, DialogDirective, TipDirective],
  template: `
    <dialog appDialog id="ad-info-dialog" aria-labelledby="ad-info-title">
      <form (ngSubmit)="close()">
        <h3 id="ad-info-title">Sponsored content</h3>

        <p class="small muted">
          Free memberships help pay for the app by showing a small sponsored card. It is chosen
          <strong>contextually</strong> — from a coarse region and your language, both decided on
          this device — never from who you are or what you keep in the app. Your tasks and events
          never leave your device unencrypted.
        </p>

        @if (ads.source(); as src) {
          <div class="update-summary" id="ad-info-source">
            <div>
              <span class="muted small">Source</span>
              <span>{{ src.title }}{{ src.mode === 'fake' ? ' (built-in fake)' : '' }}</span>
            </div>
            <div>
              <span class="muted small">Region used</span>
              <span>{{ regionText() }}</span>
            </div>
            <div>
              <span class="muted small">Collected</span>
              <span>{{
                src.third_party
                  ? 'Whatever the provider collects once allowed (see its policy)'
                  : 'Anonymous daily view/click totals per region — no identifiers'
              }}</span>
            </div>
            <div>
              <span class="muted small">Third party</span>
              <span>{{ src.third_party ? src.provider_name || 'yes' : 'none' }}</span>
            </div>
          </div>
          @if (src.disclosure) {
            <p class="small muted">{{ src.disclosure }}</p>
          }
        } @else if (ads.error()) {
          <div class="callout error">
            <strong>Could not load the sponsored content.</strong><span>{{ ads.error() }}</span>
          </div>
        }

        <label
          class="checkbox"
          for="ad-enabled"
          appTip="Off: the panel disappears and this device never asks for sponsored content."
        >
          <input
            type="checkbox"
            id="ad-enabled"
            [checked]="ads.enabled()"
            (change)="toggleEnabled($any($event.target).checked)"
          />
          <span>Show sponsored content on this device</span>
        </label>

        <div class="field">
          <label for="ad-region-mode">Region</label>
          <select
            class="input"
            id="ad-region-mode"
            name="regionMode"
            [ngModel]="mode()"
            (ngModelChange)="setMode($event)"
            appTip="Auto: derived from your language and time zone on this device. Choose: a country you pick. None: no region is sent."
          >
            <option value="auto">Automatic (from language / time zone)</option>
            <option value="manual">Choose a country</option>
            <option value="off">Do not send a region</option>
          </select>
        </div>
        @if (mode() === 'manual') {
          <div class="field">
            <label for="ad-region">Country code (two letters, e.g. US, DE)</label>
            <input
              class="input"
              id="ad-region"
              name="region"
              maxlength="2"
              autocomplete="off"
              [ngModel]="regionInput()"
              (ngModelChange)="regionInput.set($event)"
              (blur)="applyRegion()"
              appTip="ISO country code. Saved when you leave the field."
            />
          </div>
        }

        <label
          class="checkbox"
          for="ad-large"
          appTip="Large: the showcase layout with the targeting shown. Off: a compact card."
        >
          <input
            type="checkbox"
            id="ad-large"
            [checked]="ads.large()"
            (change)="ads.setLarge($any($event.target).checked)"
          />
          <span>Large panel (show how it was targeted)</span>
        </label>

        @if (ads.source()?.third_party) {
          <div class="callout" id="ad-consent-state">
            <strong>Third-party provider</strong>
            @if (ads.consent()) {
              <span
                >You allowed {{ ads.source()?.provider_name || 'the provider' }} on this device.
                <button
                  class="link"
                  type="button"
                  id="ad-consent-withdraw"
                  (click)="withdraw()"
                  appTip="The provider's script is no longer loaded; the consent card comes back."
                >
                  Withdraw
                </button></span
              >
            } @else {
              <span>Not allowed yet — the panel shows a consent card instead of the provider.</span>
            }
            @if (ads.source()?.privacy_url; as url) {
              <a [href]="url" target="_blank" rel="noopener noreferrer" class="small"
                >Privacy policy ↗</a
              >
            }
          </div>
        }

        <div class="dialog-actions">
          @if (membership.canUpgrade()) {
            <button
              class="btn"
              type="button"
              id="ad-info-upgrade"
              (click)="goUpgrade()"
              appTip="Pro and Lifetime memberships have no sponsored content."
            >
              Go ad-free with Pro
            </button>
          }
          <span class="spacer"></span>
          <button class="btn primary" type="submit" appTip="Close this window.">Done</button>
        </div>
      </form>
    </dialog>
  `,
})
export class AdInfoDialog {
  readonly ads = inject(AdsService);
  readonly membership = inject(MembershipService);
  private readonly toast = inject(ToastService);
  readonly dialog = viewChild.required(DialogDirective);
  readonly upgrade = output<void>();

  readonly mode = signal<AdRegionMode>('auto');
  readonly regionInput = signal('');

  readonly regionText = computed(() => {
    const r = this.ads.response()?.region ?? this.ads.region();
    const how =
      this.ads.regionMode() === 'manual'
        ? 'chosen by you'
        : this.ads.regionMode() === 'off'
          ? 'not sent'
          : 'derived on this device';
    return !r || r === 'ZZ' ? `none (${how})` : `${r} (${how})`;
  });

  open(): void {
    this.mode.set(this.ads.regionMode());
    this.regionInput.set(this.ads.region() && this.ads.region() !== 'ZZ' ? this.ads.region()! : '');
    this.dialog().open();
  }

  close(): void {
    this.applyRegion();
    this.dialog().close();
  }

  toggleEnabled(on: boolean): void {
    this.ads.setEnabled(on);
    this.toast.show(on ? 'Sponsored content is on' : 'Sponsored content is off on this device');
  }

  setMode(mode: AdRegionMode): void {
    this.mode.set(mode);
    if (mode !== 'manual') this.ads.setRegionMode(mode);
    else this.applyRegion();
  }

  applyRegion(): void {
    if (this.mode() !== 'manual') return;
    const code = this.regionInput().trim().toUpperCase();
    if (isValidRegion(code)) {
      this.regionInput.set(code);
      this.ads.setRegionMode('manual', code);
    } else if (code) {
      this.toast.error('Please enter a two-letter country code, e.g. US or DE.');
    }
  }

  withdraw(): void {
    this.ads.setConsent(false);
    this.toast.show('Consent withdrawn — the provider is no longer loaded.');
  }

  goUpgrade(): void {
    this.dialog().close();
    this.upgrade.emit();
  }
}
