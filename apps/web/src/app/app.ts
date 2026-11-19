import {
  AfterViewInit,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AccountDialog } from './core/account/account-dialog';
import { AccountService } from './core/account/account.service';
import { AdInfoDialog } from './core/ads/ad-info-dialog';
import { AdsService } from './core/ads/ads.service';
import { AdminDialog } from './core/admin/admin-dialog';
import { MembershipService } from './core/membership/membership.service';
import { RELEASE } from './core/release-info';
import { SyncService } from './core/sync/sync.service';
import { LinksService } from './core/links.service';
import { ShellService } from './core/ui/shell.service';
import { TipDirective } from './core/ui/tip.directive';
import { ToastService } from './core/ui/toast.service';
import { TooltipService, topLayerHide, topLayerShow } from './core/ui/tooltip.service';
import { UiStateService, isTyping } from './core/ui/ui-state.service';
import { ForcedUpdate } from './core/update/forced-update';
import { UpdateDialog } from './core/update/update-dialog';
import { UpdateService } from './core/update/update.service';

@Component({
  selector: 'app-root',
  imports: [
    FormsModule,
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    TipDirective,
    UpdateDialog,
    AccountDialog,
    AdminDialog,
    AdInfoDialog,
    ForcedUpdate,
  ],
  templateUrl: './app.html',
})
export class App implements AfterViewInit {
  readonly account = inject(AccountService);
  readonly membership = inject(MembershipService);
  readonly sync = inject(SyncService);
  readonly update = inject(UpdateService);
  readonly ads = inject(AdsService);
  readonly toast = inject(ToastService);
  readonly tooltips = inject(TooltipService);
  readonly links = inject(LinksService);
  private readonly ui = inject(UiStateService);
  private readonly shell = inject(ShellService);
  private readonly router = inject(Router);

  readonly version = RELEASE.version;
  readonly online = signal(navigator.onLine);
  readonly todayLabel = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
  readonly updateDialog = viewChild.required(UpdateDialog);
  readonly accountDialog = viewChild.required(AccountDialog);
  readonly adminDialog = viewChild.required(AdminDialog);
  readonly adInfoDialog = viewChild.required(AdInfoDialog);
  readonly tooltipHost = viewChild.required<ElementRef<HTMLElement>>('tooltipHost');
  readonly toastHost = viewChild.required<ElementRef<HTMLElement>>('toastHost');

  /** Header badge: the membership, e.g. "Free" or "Pro". */
  readonly membershipBadge = computed(() => this.membership.strategy().shortLabel);
  readonly membershipTip = computed(() => {
    const strategy = this.membership.strategy();
    const who = strategy.hasAccount
      ? `Signed in as ${this.account.session()?.email ?? ''}. `
      : 'No account. ';
    const hint = strategy.upgradeHint();
    return `${who}${strategy.label} membership.${hint ? ' ' + hint : ''} Tap for account options.`;
  });
  readonly accountTip = computed(() => {
    if (!this.account.signedIn()) return 'Account: log in or register.';
    const s = this.sync.state();
    const who = this.account.session()?.email ?? '';
    if (s === 'syncing') return `Signed in as ${who}. Syncing…`;
    if (s === 'error')
      return `Signed in as ${who}. Sync problem: ${this.sync.error()} Tap for details.`;
    if (s === 'update_required')
      return `Signed in as ${who}. Sync is paused until the app is updated.`;
    if (s === 'off') return `Signed in as ${who}. Sync is off (needs Pro). Tap to start a trial.`;
    return `Signed in as ${who}. ${this.sync.pending() ? `${this.sync.pending()} changes waiting to sync.` : 'Everything is synced.'} Tap for account and devices.`;
  });
  readonly updateBannerVisible = computed(
    () =>
      this.update.decision().ready && !this.update.decision().required && !this.update.autoUpdate(),
  );

  constructor() {
    // Routed pages (patient registration, appointments) open the app-wide dialogs through the shell.
    this.shell.register({
      openAccount: (mode) => this.openAccount(mode),
      openAdInfo: () => this.openAdInfo(),
    });
    // Toast: show/hide in the top layer (above dialogs).
    effect(() => {
      const t = this.toast.current();
      const host = this.toastHost().nativeElement;
      if (t) {
        topLayerShow(host);
        host.classList.remove('show');
        requestAnimationFrame(() => host.classList.add('show'));
      } else {
        host.classList.remove('show');
        setTimeout(() => !this.toast.current() && topLayerHide(host), 250);
      }
    });
    effect(() => {
      const host = this.tooltipHost().nativeElement;
      host.hidden = !this.tooltips.visible();
    });
  }

  ngAfterViewInit(): void {
    void this.init();
  }

  private async init() {
    this.tooltips.attach(this.tooltipHost().nativeElement);
    this.tooltips.maybeShowFirstRunHint();
    window.addEventListener('online', () => this.online.set(true));
    window.addEventListener('offline', () => this.online.set(false));
    document.addEventListener('focusin', () => this.ui.poke());
    document.addEventListener('focusout', () => setTimeout(() => this.ui.poke(), 0));
    document.addEventListener('visibilitychange', () => this.ui.poke());
    document.addEventListener('keydown', (e) => this.shortcuts(e));

    await this.account.init();
    this.sync.init();
    this.update.init();
    this.ads.init();
    void this.links.load();

    await this.finishOAuthReturn();

    const params = new URLSearchParams(location.search);
    if (params.get('billing') === 'canceled') {
      history.replaceState(null, '', '/');
      this.toast.show('Checkout canceled — nothing was charged.');
    }
    if (params.get('billing') === 'managed') {
      history.replaceState(null, '', '/');
      await this.account.refreshMe();
      this.toast.show('Billing details refreshed.');
    }
    if (params.get('billing') === 'done') {
      history.replaceState(null, '', '/');
      await this.account.refreshMe();
      this.toast.show(
        this.account.canSync()
          ? 'Thanks! Pro is active — sync is on.'
          : 'Payment received; entitlements refresh shortly.',
      );
    }
  }

  /** Back from "Sign in with Google/Facebook": finish the sign-in (second factor / passphrase
   *  steps happen in the account dialog), or report what went wrong. */
  private async finishOAuthReturn() {
    const back = this.account.consumeOAuthReturn();
    if (!back) return;
    if (back.kind === 'linked') {
      await this.account.refreshMe();
      this.toast.show(`${back.provider.charAt(0).toUpperCase() + back.provider.slice(1)} linked`);
      setTimeout(() => this.openAccount(), 50);
      return;
    }
    if (back.kind === 'error') {
      this.account.oauthError.set(back.message);
      setTimeout(() => this.openAccount('login'), 50);
      return;
    }
    try {
      const result = await this.account.completeOAuth(back.ticket, back.provider);
      if (result === 'signed-in') {
        this.toast.show('Signed in');
        void this.router.navigateByUrl('/patients/mine'); // a patient signs in to see their appointments
      } else setTimeout(() => this.openAccount('login'), 50); // code or passphrase step
    } catch (e) {
      this.account.oauthError.set(e instanceof Error ? e.message : String(e));
      setTimeout(() => this.openAccount('login'), 50);
    }
  }

  private shortcuts(e: KeyboardEvent) {
    if (isTyping() || e.metaKey || e.ctrlKey || e.altKey || document.querySelector('dialog[open]'))
      return;
    if (e.key === '?') {
      e.preventDefault();
      this.toggleHelp();
    }
  }

  openAdmin() {
    this.adminDialog().open();
  }
  openAdInfo() {
    this.adInfoDialog().open();
  }
  openUpdate() {
    this.updateDialog().open();
  }
  openAccount(mode?: 'login' | 'register') {
    this.accountDialog().open(mode);
  }
  async logout() {
    const who = this.account.session()?.email ?? '';
    await this.account.logout();
    this.toast.show(`Logged out${who ? ' of ' + who : ''} — your data stays on this device`);
  }
  toggleHelp() {
    this.tooltips.setHelpMode(!this.tooltips.helpMode());
  }

  setAutoUpdate(on: boolean) {
    this.update.setAutoUpdate(on);
    this.toast.show(
      on
        ? 'New versions will be installed automatically'
        : 'Automatic updates are off — use the Update button to install them',
    );
  }
}
