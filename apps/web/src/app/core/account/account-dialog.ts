import { Component, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { TasksService } from '../data/tasks.service';
import { MembershipService } from '../membership/membership.service';
import { SyncService } from '../sync/sync.service';
import { DialogDirective } from '../ui/dialog.directive';
import { PasswordToggleDirective } from '../ui/password-toggle.directive';
import { TipDirective } from '../ui/tip.directive';
import { ToastService } from '../ui/toast.service';
import { relative } from '../update/update-policy';
import {
  AccountService,
  type OAuthProvider,
  type OAuthProviderName,
  type TwoFactorMethod,
  type PaymentMethod,
  type PaymentOut,
  type Plan,
  type PlanInfo,
} from './account.service';
import { ApiError } from './api.service';
import { receiveSmsCode } from './platform';
import { TwoFactorPanel } from './two-factor-panel';

/** Until the server's catalogue is loaded (prices come from its PRICE_* settings). */
const FALLBACK_PLANS: PlanInfo[] = [
  {
    id: 'pro_monthly',
    title: 'Pro — monthly',
    amount: '',
    amount_cents: 0,
    currency: 'USD',
    interval: 'month',
    period_days: 30,
    refund_rule: '',
  },
  {
    id: 'pro_yearly',
    title: 'Pro — yearly',
    amount: '',
    amount_cents: 0,
    currency: 'USD',
    interval: 'year',
    period_days: 365,
    refund_rule: '',
  },
  {
    id: 'lifetime',
    title: 'Lifetime',
    amount: '',
    amount_cents: 0,
    currency: 'USD',
    interval: null,
    period_days: null,
    refund_rule: '',
  },
];

@Component({
  selector: 'app-account-dialog',
  imports: [FormsModule, DialogDirective, PasswordToggleDirective, TipDirective, TwoFactorPanel],
  template: `
    <dialog
      appDialog
      id="account-dialog"
      aria-labelledby="account-dialog-title"
      (closed)="onClosed()"
    >
      <form (ngSubmit)="submit()">
        <h3 id="account-dialog-title">
          {{
            account.signedIn()
              ? 'Account & sync'
              : account.passphraseStep() === 'create'
                ? 'Create your passphrase'
                : account.passphraseStep() === 'unlock'
                  ? 'Unlock your data'
                  : account.twoFactorPending()
                    ? 'Two-factor code'
                    : mode() === 'register'
                      ? 'Create account'
                      : 'Sign in'
          }}
        </h3>

        @if (!account.signedIn() && account.passphraseStep(); as step) {
          <!-- Google/Facebook proved who you are; the passphrase is what protects the data. -->
          <p class="small" id="acc-passphrase-hint">
            @if (step === 'create') {
              You are signed in with {{ providerLabel() }}. Now choose a
              <strong>passphrase</strong>: it encrypts your synced data on this device and becomes
              the password for signing in with your e-mail. It never leaves your device — if you
              lose it, nobody can recover your synced data.
            } @else {
              You are signed in with {{ providerLabel() }}. Enter your <strong>passphrase</strong> —
              the password of this account — to unlock your encrypted data on this device.
            }
          </p>
          <div class="field">
            <label for="acc-passphrase">Passphrase</label>
            <input
              class="input"
              id="acc-passphrase"
              name="passphrase"
              type="password"
              appPasswordToggle
              [(ngModel)]="passphrase"
              required
              minlength="8"
              [attr.autocomplete]="step === 'create' ? 'new-password' : 'current-password'"
              appTip="At least 8 characters. Derived into your encryption key on this device; never sent."
            />
          </div>
          @if (step === 'create') {
            <div class="field">
              <label for="acc-passphrase2">Repeat the passphrase</label>
              <input
                class="input"
                id="acc-passphrase2"
                name="passphrase2"
                type="password"
                appPasswordToggle
                [(ngModel)]="passphrase2"
                required
                minlength="8"
                autocomplete="new-password"
                appTip="Type it again so a typo cannot lock you out."
              />
            </div>
          }
          @if (error()) {
            <div class="callout error" role="alert">
              <p>{{ error() }}</p>
            </div>
          }
          <div class="dialog-actions">
            <button
              class="btn ghost"
              type="button"
              id="acc-passphrase-back"
              (click)="backToPassword()"
              appTip="Stop here: nothing is kept on this device."
            >
              Back
            </button>
            <span class="spacer"></span>
            <button
              class="btn primary"
              type="submit"
              id="acc-passphrase-submit"
              [disabled]="
                account.busy() ||
                passphrase.length < 8 ||
                (step === 'create' && passphrase !== passphrase2)
              "
              appTip="{{
                step === 'create'
                  ? 'Encrypt your data with this passphrase.'
                  : 'Unlock your data on this device.'
              }}"
            >
              {{ account.busy() ? 'Working…' : step === 'create' ? 'Continue' : 'Unlock' }}
            </button>
          </div>
        } @else if (!account.signedIn() && account.twoFactorPending()) {
          <!-- Password accepted; the account wants its second factor before anything is handed out. -->
          <p class="small" id="acc-code-hint">{{ codeHint() }}</p>
          @if (account.twoFactorSent()?.fake_outbox_url) {
            <p class="small fake-hint">
              Built-in fake — nothing was really sent.
              <a
                id="acc-outbox-link"
                [href]="account.apiPage(account.twoFactorSent()!.fake_outbox_url!)"
                target="_blank"
                rel="noopener"
                >Read it in the test inbox ↗</a
              >
            </p>
          }
          @if (account.twoFactorPending()?.send_error && !account.twoFactorSent()) {
            <div class="callout" id="acc-send-error">
              <p>{{ account.twoFactorPending()!.send_error }}</p>
            </div>
          }
          <div class="field">
            <label for="acc-code">Code</label>
            <input
              class="input code-input"
              id="acc-code"
              name="code"
              [attr.inputmode]="loginMethod() === 'recovery' ? 'text' : 'numeric'"
              [(ngModel)]="code"
              required
              minlength="6"
              maxlength="32"
              autocomplete="one-time-code"
              appTip="The 6-digit code from your authenticator app, text message or e-mail — or a recovery code you saved when you turned two-factor authentication on."
            />
          </div>
          <div class="method-switch" id="acc-methods">
            @for (m of otherMethods(); track m) {
              <button
                class="btn ghost"
                type="button"
                [id]="'acc-method-' + m"
                [disabled]="account.busy() || sending()"
                (click)="switchMethod(m)"
                [appTip]="methodTip(m)"
              >
                {{ methodButton(m) }}
              </button>
            }
          </div>
          @if (error()) {
            <div class="callout error" role="alert">
              <p>{{ error() }}</p>
            </div>
          }
          <div class="dialog-actions">
            <button
              class="btn ghost"
              type="button"
              id="acc-code-back"
              (click)="backToPassword()"
              appTip="Start over with e-mail and password."
            >
              Back
            </button>
            <span class="spacer"></span>
            <button
              class="btn"
              type="button"
              (click)="dialog().close()"
              appTip="Close this window."
            >
              Close
            </button>
            <button
              class="btn primary"
              type="submit"
              [disabled]="account.busy()"
              appTip="Check the code and finish signing in."
            >
              {{ account.busy() ? 'Checking…' : 'Verify' }}
            </button>
          </div>
        } @else if (!account.signedIn()) {
          <p class="muted small" id="acc-patient-hint">
            @if (mode() === 'register') {
              A <strong>patient account</strong> lets you see your own appointments, cancel them and
              book new ones without typing your details each time. Registering is optional — booking
              works without an account. Staff and administrators sign in on the staff and office
              dashboards, not here.
            } @else {
              Sign in to your <strong>patient account</strong> to see your appointments. Staff and
              administrators sign in on the staff and office dashboards, not here.
            }
            Your password never leaves this device; the server only stores an encrypted copy of your
            data that it cannot read.
          </p>
          <div class="field">
            <label for="acc-email">E-mail</label>
            <input
              class="input"
              id="acc-email"
              name="email"
              type="email"
              [(ngModel)]="email"
              required
              autocomplete="email"
              appTip="Used to sign in and for receipts. Nothing else is stored about you."
            />
          </div>
          <div class="field">
            <label for="acc-password">Password</label>
            <input
              class="input"
              id="acc-password"
              name="password"
              type="password"
              appPasswordToggle
              [(ngModel)]="password"
              required
              minlength="8"
              autocomplete="current-password"
              appTip="Your encryption key is derived from this password on the device. If you lose it, synced data cannot be recovered by anyone."
            />
          </div>
          @if (error()) {
            <div class="callout error" role="alert">
              <p>{{ error() }}</p>
            </div>
          }
          @if (providers().length) {
            <div class="oauth-row" id="acc-oauth">
              <span class="muted small">or continue with</span>
              @for (p of providers(); track p.name) {
                <button
                  class="btn"
                  type="button"
                  [id]="'acc-oauth-' + p.name"
                  [disabled]="account.busy()"
                  (click)="oauth(p)"
                  appTip="Sign in or create an account with your {{ p.label }} account. {{
                    p.fake
                      ? 'Built-in fake: no ' +
                        p.label +
                        ' credentials are configured, a stand-in page asks for an e-mail.'
                      : p.label +
                        ' confirms who you are; your data stays encrypted with a passphrase only you know.'
                  }}"
                >
                  {{ p.label }}{{ p.fake ? ' (fake)' : '' }}
                </button>
              }
            </div>
          }
          <div class="dialog-actions">
            <button
              class="btn ghost"
              type="button"
              (click)="mode.set(mode() === 'register' ? 'login' : 'register')"
              appTip="Switch between signing in and creating a new account."
            >
              {{ mode() === 'register' ? 'I already have an account' : 'Create a new account' }}
            </button>
            <span class="spacer"></span>
            <button
              class="btn"
              type="button"
              (click)="dialog().close()"
              appTip="Close this window."
            >
              Close
            </button>
            <button
              class="btn primary"
              type="submit"
              [disabled]="account.busy()"
              appTip="{{
                mode() === 'register'
                  ? 'Create the account and encrypt this device\\'s data for sync.'
                  : 'Sign in and unlock your synced data on this device.'
              }}"
            >
              {{
                account.busy() ? 'Working…' : mode() === 'register' ? 'Create account' : 'Sign in'
              }}
            </button>
          </div>
        } @else {
          <div class="update-summary">
            <div>
              <span class="muted">Signed in as</span><strong>{{ account.session()?.email }}</strong>
            </div>
            <div>
              <span class="muted">Membership</span><strong>{{ planLabel() }}</strong>
            </div>
            <div>
              <span class="muted">Billing</span
              ><strong id="account-billing">{{ billingLabel() }}</strong>
            </div>
            <div>
              <span class="muted">Account type</span
              ><span id="account-role">{{
                account.me()?.role === 'patient' ? 'Patient' : (account.me()?.role ?? '–')
              }}</span>
            </div>
            <div>
              <span class="muted">Sync</span><span>{{ syncLabel() }}</span>
            </div>
            <div>
              <span class="muted">Devices</span
              ><span>{{ account.me()?.devices?.length ?? '–' }}</span>
            </div>
          </div>
          @if (sync.error()) {
            <div class="callout error" role="alert">
              <p>{{ sync.error() }}</p>
            </div>
          }

          @if (account.canSync()) {
            <div class="dialog-actions">
              <button
                class="btn"
                type="button"
                (click)="sync.syncNow()"
                [disabled]="sync.state() === 'syncing'"
                appTip="Push local changes and fetch changes from your other devices now."
              >
                Sync now
              </button>
              <button
                class="btn"
                type="button"
                (click)="resetFromServer()"
                appTip="Discard this device's local copy and rebuild it from the server's encrypted master copy. Use this if the data on this device looks wrong."
              >
                Reset from server
              </button>
            </div>
            <label class="checkbox" for="acc-vault">
              <input
                type="checkbox"
                id="acc-vault"
                [checked]="account.vaultEnabled()"
                (change)="setVault($any($event.target).checked)"
                appTip="Keep an encrypted copy of your full data on the server so a new device can restore everything. Off = the server only relays changes between devices for up to 30 days."
              />
              <span>Keep an encrypted backup on the server (vault)</span>
            </label>
          }

          @if (hasPaidSubscription()) {
            <!-- A paid Pro subscription or Lifetime: renewal date, renew / cancel, the
                 provider's billing page. -->
            <div class="callout" id="subscription-callout">
              <p>{{ subscriptionHint() }}</p>
              @if (ent()?.last_payment; as last) {
                @if (last.status === 'failed') {
                  <p class="small" id="payment-failed" role="alert">
                    <strong>Payment failed:</strong> {{ money(last.amount, last.currency) }} for
                    {{ planTitle(last.plan) }} on {{ date(last.paid_at) }}
                    {{ last.failure_reason ? '— ' + last.failure_reason : '' }}
                  </p>
                }
              }
              <div class="dialog-actions">
                @if (ent()?.can_manage) {
                  <button
                    class="btn"
                    type="button"
                    id="btn-manage"
                    [disabled]="busy()"
                    (click)="manage()"
                    appTip="Opens your billing page at the payment provider: payment method, invoices, receipts."
                  >
                    {{
                      ent()?.status === 'past_due' ? 'Update payment method ↗' : 'Manage billing ↗'
                    }}
                  </button>
                }
                @if (ent()?.can_cancel) {
                  <button
                    class="btn danger"
                    type="button"
                    id="btn-cancel"
                    [disabled]="busy()"
                    (click)="cancel()"
                    appTip="{{
                      ent()?.plan === 'lifetime'
                        ? 'End your Lifetime membership now; ' + refundRule('lifetime')
                        : 'End the subscription now; the unused days of the current period are refunded.'
                    }}"
                  >
                    {{ ent()?.plan === 'lifetime' ? 'Cancel membership' : 'Cancel subscription' }}
                  </button>
                }
              </div>
            </div>
          }

          @if (canSubscribe()) {
            <!-- Free, trial, past-due and lapsed accounts: pick a plan and pay. Hidden while a
                 paid subscription renews normally, for Lifetime, and for administrators. -->
            <div class="callout" id="upgrade-callout">
              <p>{{ subscribeHint() }}</p>
              @if (ent()?.trial_available) {
                <div class="dialog-actions">
                  <button
                    class="btn primary"
                    type="button"
                    (click)="trial()"
                    appTip="Start a 14-day Pro trial for encrypted sync between your devices."
                  >
                    Start free trial
                  </button>
                </div>
              }
              <div class="field" id="checkout">
                <label for="acc-plan">{{ subscribeTitle() }}</label>
                <select
                  class="input"
                  id="acc-plan"
                  name="plan"
                  [ngModel]="plan()"
                  (ngModelChange)="plan.set($event)"
                  appTip="Monthly or yearly subscription, or a one-off Lifetime purchase."
                >
                  @for (p of plans(); track p.id) {
                    <option [value]="p.id">{{ planOption(p) }}</option>
                  }
                </select>
                <p class="muted small" id="refund-rule">{{ refundRule(plan()) }}</p>
                @if (methodsForPlan().length) {
                  <div class="dialog-actions payment-methods">
                    @for (m of methodsForPlan(); track m.name) {
                      <button
                        class="btn"
                        [class.primary]="m.available"
                        type="button"
                        [disabled]="paying()"
                        (click)="checkout(m)"
                        [attr.data-method]="m.name"
                        [attr.data-available]="m.available"
                        appTip="{{
                          m.available
                            ? m.description +
                              ' We never see your card or PayPal details.' +
                              (m.fake
                                ? ' Built-in fake: no credentials configured, nothing is charged.'
                                : '')
                            : m.label +
                              ' is not set up on this server yet — its credentials must be added on the server (docs/DEPLOYMENT.md → Payments).'
                        }}"
                      >
                        Pay with {{ m.label
                        }}{{ m.fake ? ' (fake)' : m.sandbox ? ' (test mode)' : '' }}
                      </button>
                    }
                  </div>
                } @else if (methods() === null) {
                  <p class="muted small">Loading payment methods…</p>
                } @else if (methods()?.length) {
                  <p class="muted small">
                    This plan is not available with the configured payment methods.
                  </p>
                } @else {
                  <p class="muted small">Payments are not available right now.</p>
                }
              </div>
            </div>
          }

          @if (history().length) {
            <div class="field" id="payment-history">
              <span class="muted small">Payments</span>
              <ul class="device-list">
                @for (p of history(); track p.id) {
                  <li>
                    <span
                      >{{ date(p.paid_at) }} · {{ planTitle(p.plan) }} ·
                      {{ money(p.amount, p.currency) }} · {{ paymentState(p) }}</span
                    >
                  </li>
                }
              </ul>
            </div>
          }

          @if (account.me(); as me) {
            <div class="field two-factor" id="sign-in-methods">
              <span class="muted small">Sign-in methods</span>
              <ul class="tfa-methods">
                <li class="on">
                  <div class="tfa-method-text">
                    <strong>E-mail and password</strong>
                    <span class="small muted">{{ me.email }}</span>
                  </div>
                </li>
                @for (p of providers(); track p.name) {
                  <li [id]="'method-' + p.name" [class.on]="identity(p.name)">
                    <div class="tfa-method-text">
                      <strong>{{ p.label }}{{ p.fake ? ' (fake)' : '' }}</strong>
                      <span class="small muted">{{
                        identity(p.name) ? 'Linked · ' + identity(p.name)!.email : 'Not linked'
                      }}</span>
                    </div>
                    @if (identity(p.name)) {
                      <button
                        class="btn"
                        type="button"
                        [id]="'acc-unlink-' + p.name"
                        [disabled]="busy()"
                        (click)="unlink(p)"
                        appTip="Stop using {{ p.label }} to sign in to this account."
                      >
                        Unlink
                      </button>
                    } @else {
                      <button
                        class="btn"
                        type="button"
                        [id]="'acc-link-' + p.name"
                        [disabled]="busy()"
                        (click)="link(p)"
                        appTip="Also sign in with your {{
                          p.label
                        }} account. You still enter your passphrase on a new device."
                      >
                        Link
                      </button>
                    }
                  </li>
                }
              </ul>
            </div>
            <app-two-factor-panel />
          }

          @if (account.me()?.devices; as devices) {
            <div class="field">
              <span class="muted small">Devices</span>
              <ul class="device-list">
                @for (d of devices; track d.id) {
                  <li>
                    <span
                      >{{ d.platform }} · v{{ d.client_version || '?' }} · seen
                      {{ rel(d.last_seen_at) }}{{ d.is_current ? ' (this device)' : '' }}</span
                    >
                    @if (!d.is_current) {
                      <button
                        class="btn ghost"
                        type="button"
                        (click)="revoke(d.id)"
                        appTip="Sign this device out. It keeps its local data but stops syncing."
                      >
                        Sign out
                      </button>
                    }
                  </li>
                }
              </ul>
            </div>
          }
          <div class="dialog-actions">
            <button
              class="btn danger"
              type="button"
              (click)="deleteAccount()"
              appTip="Delete your account on the server. Data on your devices is kept."
            >
              Delete account
            </button>
            <span class="spacer"></span>
            <button
              class="btn"
              type="button"
              (click)="dialog().close()"
              appTip="Close this window."
            >
              Close
            </button>
            <button
              class="btn"
              type="button"
              (click)="logout()"
              appTip="Log out on this device. Your data stays on this device."
            >
              Log out
            </button>
          </div>
        }
      </form>
    </dialog>
  `,
})
export class AccountDialog {
  readonly account = inject(AccountService);
  readonly membership = inject(MembershipService);
  readonly tasks = inject(TasksService);
  private readonly router = inject(Router);
  readonly sync = inject(SyncService);
  private readonly toast = inject(ToastService);
  readonly dialog = viewChild.required(DialogDirective);
  readonly mode = signal<'login' | 'register'>('register');
  readonly error = signal<string | null>(null);
  email = '';
  password = '';
  /** Second login step: the authenticator's code or a recovery code. */
  code = '';
  /** After a provider sign-in: the encryption passphrase (and its repeat when creating one). */
  passphrase = '';
  passphrase2 = '';
  /** "Sign in with …" providers the server offers (empty until loaded / none configured). */
  readonly providers = signal<OAuthProvider[]>([]);
  readonly plan = signal<Plan>('pro_yearly');
  /** Payment methods the server offers (null = not loaded yet). */
  readonly methods = signal<PaymentMethod[] | null>(null);
  /** The paid memberships with their prices (from the same catalogue call). */
  readonly plans = signal<PlanInfo[]>(FALLBACK_PLANS);
  /** This account's charges, failures and refunds (newest first). */
  readonly history = signal<PaymentOut[]>([]);
  readonly paying = signal(false);
  /** A cancel / manage call is in flight. */
  readonly busy = signal(false);

  readonly ent = computed(() => this.account.entitlements());
  /** Pay buttons for the selected plan. Configured methods only (a provider without a price
   *  for the plan is skipped); while *nothing* is configured yet every method is shown so the
   *  buttons exist and a click explains what the server is missing. */
  readonly methodsForPlan = computed(() => {
    const all = this.methods() ?? [];
    const configured = all.filter((m) => m.available);
    return (configured.length ? configured : all).filter((m) => m.plans.includes(this.plan()));
  });
  /** A paid Pro subscription or Lifetime that is in force (trial excluded; admins need none). */
  readonly hasPaidSubscription = computed(() => {
    const e = this.ent();
    return (
      !!e &&
      e.membership !== 'admin' &&
      (e.plan === 'pro' || e.plan === 'lifetime') &&
      e.status !== 'trialing'
    );
  });
  /** Anyone who could (still) buy Pro: free, trial and lapsed accounts, a failed renewal
   *  (subscribe again with another method), a subscription the provider ended that is still
   *  running on the paid period. Not admins, not a subscription that renews normally, not
   *  Lifetime. */
  readonly canSubscribe = computed(() => {
    const e = this.ent();
    if (!e || e.membership === 'admin' || e.plan === 'lifetime') return false;
    if (e.plan === 'free' || e.status === 'trialing' || e.status === 'past_due') return true;
    return e.cancel_at_period_end;
  });
  readonly subscribeTitle = computed(() => {
    const e = this.ent();
    if (e?.status === 'trialing') return 'Keep Pro after the trial';
    if (e?.status === 'past_due') return 'Or subscribe again';
    if (e?.cancel_at_period_end) return 'Subscribe again';
    return 'Subscribe to Pro';
  });
  /** Pro that was never charged yet: our free trial, or a subscription whose first charge is
   *  still ahead (the trial carried over to the provider — PayPal reports that as active). */
  readonly onTrial = computed(() => {
    const e = this.ent();
    return (
      !!e &&
      e.plan === 'pro' &&
      !e.last_payment &&
      (e.status === 'trialing' || e.status === 'active')
    );
  });
  /** The summary row: when the paid membership ends / renews. */
  readonly billingLabel = computed(() => {
    const e = this.ent();
    if (!e) return '–';
    if (e.membership === 'admin') return 'Not needed (administrator)';
    const until = e.access_until ? this.date(e.access_until) : null;
    if (e.plan === 'lifetime') return 'Lifetime — never expires';
    if (this.onTrial()) return until ? `Trial ends ${until}` : 'Trial';
    if (e.status === 'past_due')
      return until ? `Payment failed · Pro until ${until}` : 'Payment failed';
    if (e.plan === 'pro' && e.cancel_at_period_end) return until ? `Ends ${until}` : 'Ends soon';
    if (e.plan === 'pro') return until ? `Renews ${until}` : 'Active';
    if (e.status === 'canceled') return 'Canceled';
    return 'No subscription';
  });
  readonly subscriptionHint = computed(() => {
    const e = this.ent();
    if (!e) return '';
    const until = e.access_until ? this.date(e.access_until) : 'the end of the period';
    if (e.plan === 'lifetime')
      return `Lifetime membership — paid once, never expires. ${this.refundRule('lifetime')}`;
    if (e.status === 'past_due')
      return `Pro stays on until ${until} while the payment is retried. Update your payment method at the provider, or subscribe again below.`;
    if (e.cancel_at_period_end)
      return `The provider ended the renewal — Pro runs until ${until}. Subscribe again below to continue afterwards.`;
    if (this.onTrial())
      return `Your subscription starts when the free trial ends (${until}). Cancel before that and nothing is charged.`;
    return `Pro renews automatically on ${until}. Cancel any time — the unused days of the current period are refunded.`;
  });
  readonly subscribeHint = computed(() => {
    const e = this.ent();
    if (e?.status === 'past_due')
      return 'Pick a plan to start a new subscription with another payment method.';
    if (e?.cancel_at_period_end)
      return 'Pick a plan to continue with Pro after the current period.';
    if (e?.status === 'trialing') {
      const end = e.current_period_end ? new Date(e.current_period_end) : null;
      const until = end ? ` until ${this.date(e.current_period_end!)}` : '';
      // The server carries the trial over to the provider (paid billing starts when it ends)
      // unless fewer than 48 h remain — Stripe cannot defer a first charge that close.
      const soon = !end || end.getTime() - Date.now() < 48 * 3600 * 1000;
      return `Your free Pro trial runs${until}. Subscribe now to keep unlimited tasks and encrypted sync — ${
        soon ? 'billing starts right away.' : 'paid billing only starts when the trial ends.'
      }`;
    }
    const hint = this.membership.strategy().upgradeHint() ?? 'Sync needs a Pro plan.';
    return `${hint} ${e?.trial_available ? 'Start a free 14-day trial — no card needed — or subscribe right away.' : 'Subscribe to turn Pro back on.'}`;
  });
  readonly planLabel = computed(() => {
    const e = this.ent();
    if (!e) return this.membership.label();
    const status =
      e.status === 'trialing' ? ' (trial)' : e.status === 'past_due' ? ' (payment due)' : '';
    const plan = e.plan === 'lifetime' ? ' · Lifetime' : '';
    return `${this.membership.label()}${plan}${status}`;
  });
  readonly freeLimitLabel = computed(() => {
    const limit = this.membership.strategy().maxActiveTasks;
    return limit === null ? 'Unlimited active tasks' : `Up to ${limit} active tasks`;
  });
  readonly syncLabel = computed(() => {
    const s = this.sync.state();
    if (s === 'off') return 'off';
    if (s === 'syncing') return 'syncing…';
    if (s === 'error') return 'error';
    if (s === 'update_required') return 'update required';
    const at = this.sync.lastSyncAt();
    return `${this.sync.pending() ? `${this.sync.pending()} pending · ` : ''}${at ? 'synced ' + relative(at) : 'not yet'}`;
  });

  rel(iso: string) {
    return relative(new Date(iso));
  }

  planTitle(plan: string) {
    return this.plans().find((p) => p.id === plan)?.title ?? plan;
  }

  /** "Pro — monthly · $1.00 / month" */
  planOption(p: PlanInfo) {
    if (!p.amount) return p.title;
    const price = this.money(p.amount, p.currency);
    return `${p.title} · ${price}${p.interval ? ' / ' + p.interval : ' once'}`;
  }

  refundRule(plan: string) {
    return this.plans().find((p) => p.id === plan)?.refund_rule ?? '';
  }

  money(amount: string, currency: string) {
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(
        Number(amount),
      );
    } catch {
      return `${amount} ${currency}`;
    }
  }

  paymentState(p: PaymentOut) {
    if (p.status === 'failed') return `failed${p.failure_reason ? ' — ' + p.failure_reason : ''}`;
    if (p.status === 'refunded') return `refunded ${this.money(p.refunded, p.currency)}`;
    if (p.status === 'partially_refunded')
      return `paid · ${this.money(p.refunded, p.currency)} refunded`;
    return 'paid';
  }

  date(iso: string) {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  }

  /** `mode` picks the form for anonymous users (header buttons); signed-in users see their account. */
  open(mode?: 'login' | 'register') {
    this.error.set(this.account.oauthError());
    this.account.oauthError.set(null);
    if (mode) this.mode.set(mode);
    this.dialog().open();
    void this.loadProviders();
    if (this.account.signedIn()) {
      void this.account.refreshMe();
      void this.loadMethods();
    }
  }

  async loadProviders() {
    try {
      this.providers.set(await this.account.oauthProviders());
    } catch {
      this.providers.set([]); // offline: no provider buttons
    }
  }

  /** The provider a pending sign-in came from (for the passphrase hint). */
  providerLabel() {
    const p = this.account.oauthProvider();
    return p ? p.charAt(0).toUpperCase() + p.slice(1) : 'your provider';
  }

  identity(provider: OAuthProviderName) {
    return this.account.me()?.identities.find((i) => i.provider === provider) ?? null;
  }

  /** Off to the provider; the page reloads with the result in the URL (App → completeOAuth). */
  oauth(p: OAuthProvider) {
    this.error.set(null);
    location.href = this.account.oauthStartUrl(p.name);
  }

  async link(p: OAuthProvider) {
    this.busy.set(true);
    try {
      location.href = await this.account.linkProvider(p.name);
    } catch (e) {
      this.toast.error(e instanceof Error ? e.message : String(e));
      this.busy.set(false);
    }
  }

  async unlink(p: OAuthProvider) {
    if (!confirm(`Stop using ${p.label} to sign in to this account?`)) return;
    this.busy.set(true);
    try {
      await this.account.unlinkProvider(p.name);
      this.toast.show(`${p.label} unlinked`);
    } catch (e) {
      this.toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      this.busy.set(false);
    }
  }

  async loadMethods() {
    try {
      const catalog = await this.account.paymentMethods();
      this.methods.set(catalog.methods);
      if (catalog.plans?.length) this.plans.set(catalog.plans);
    } catch {
      this.methods.set([]); // offline or billing not configured: no buttons, no error
    }
    try {
      this.history.set(await this.account.payments());
    } catch {
      this.history.set([]);
    }
  }

  async submit() {
    this.error.set(null);
    if (this.account.signedIn()) return; // the account view has no submit (buttons act on click)
    try {
      if (this.account.passphraseStep()) {
        if (this.account.passphraseStep() === 'create') {
          if (this.passphrase !== this.passphrase2)
            throw new Error('The passphrases do not match.');
          await this.account.createPassphrase(this.passphrase);
        } else {
          await this.account.unlockWithPassphrase(this.passphrase);
        }
        this.passphrase = this.passphrase2 = '';
        this.toast.show('Signed in');
        void this.loadMethods();
        return;
      }
      if (this.account.twoFactorPending()) {
        const result = await this.account.completeLogin(this.code);
        this.stopSmsWait();
        this.code = '';
        if (result === 'passphrase') return; // the dialog now shows the passphrase step
      } else if (this.mode() === 'register') {
        await this.account.register(this.email.trim(), this.password);
      } else if ((await this.account.login(this.email.trim(), this.password)) === 'two-factor') {
        this.password = ''; // the service keeps what the second step needs, in memory only
        this.code = '';
        const challenge = this.account.twoFactorPending();
        this.loginMethod.set(challenge?.default_method ?? 'totp');
        if (challenge?.sent?.method === 'sms') this.waitForSms();
        return; // the dialog now shows the code step
      }
      this.password = '';
      this.toast.show(this.mode() === 'register' ? 'Patient account created' : 'Signed in');
      void this.loadMethods();
      // Close the dialog. The patient pages react to the sign-in themselves (the registration
      // tab shows "My details", the scheduler its payment check …); from anywhere else — the
      // appointment link page, say — a patient signs in to see their appointments.
      this.dialog().close();
      if (!this.router.url.startsWith('/patients'))
        void this.router.navigateByUrl('/patients/mine');
      // Existing local data is adopted by the first sync run (SyncService), not here.
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : String(e));
    }
  }

  /** Abandon the code / passphrase step. */
  backToPassword() {
    this.stopSmsWait();
    this.account.cancelLogin();
    this.code = '';
    this.passphrase = this.passphrase2 = '';
    this.error.set(null);
  }

  // ---- The code step of a two-factor sign-in ----

  /** What the code field expects right now: the challenge's default, or what the user switched to. */
  readonly loginMethod = signal<TwoFactorMethod | 'recovery'>('totp');
  readonly sending = signal(false);
  private smsWait: AbortController | null = null;

  readonly codeHint = computed(() => {
    const c = this.account.twoFactorPending();
    if (!c) return '';
    const sent = this.account.twoFactorSent();
    const recovery = c.methods.includes('recovery')
      ? ' You can also use one of your recovery codes.'
      : '';
    const m = this.loginMethod();
    if (m === 'recovery') return 'Enter one of the recovery codes you saved (each works once).';
    if ((m === 'sms' || m === 'email') && sent?.method === m)
      return `We sent a 6-digit code by ${m === 'sms' ? 'text message' : 'e-mail'} to ${sent.destination}.${recovery}`;
    if (m === 'sms' || m === 'email')
      return `Get a code by ${m === 'sms' ? 'text message' : 'e-mail'} with the button below.${recovery}`;
    return `This account has two-factor authentication. Enter the 6-digit code from your authenticator app.${recovery}`;
  });

  /** The other ways in: the remaining methods, then "use a recovery code". */
  readonly otherMethods = computed<(TwoFactorMethod | 'recovery' | 'resend')[]>(() => {
    const c = this.account.twoFactorPending();
    if (!c) return [];
    const m = this.loginMethod();
    const out: (TwoFactorMethod | 'recovery' | 'resend')[] = [];
    if ((m === 'sms' || m === 'email') && this.account.twoFactorSent()?.method === m)
      out.push('resend');
    for (const x of c.methods) if (x !== m) out.push(x);
    return out;
  });

  methodButton(m: TwoFactorMethod | 'recovery' | 'resend') {
    const sentTo = this.account.twoFactorPending()?.destinations;
    switch (m) {
      case 'resend':
        return 'Send a new code';
      case 'totp':
        return 'Use authenticator app';
      case 'sms':
        return `Text a code to ${sentTo?.sms ?? 'my phone'}`;
      case 'email':
        return `E-mail a code to ${sentTo?.email ?? 'me'}`;
      default:
        return 'Use a recovery code';
    }
  }

  methodTip(m: TwoFactorMethod | 'recovery' | 'resend') {
    if (m === 'resend') return 'Send a fresh code; the previous one stops working.';
    if (m === 'recovery') return 'Lost your phone? Each recovery code you saved signs you in once.';
    if (m === 'totp') return 'Type the 6-digit code your authenticator app shows right now.';
    return `Send a 6-digit code by ${m === 'sms' ? 'text message' : 'e-mail'}.`;
  }

  async switchMethod(m: TwoFactorMethod | 'recovery' | 'resend') {
    this.error.set(null);
    const target = m === 'resend' ? this.loginMethod() : m;
    this.loginMethod.set(target);
    if (target !== 'sms' && target !== 'email') return; // app / recovery code: just type it
    this.sending.set(true);
    try {
      await this.account.sendLoginCode(target);
      this.code = '';
      if (target === 'sms') this.waitForSms();
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : String(e));
    } finally {
      this.sending.set(false);
    }
  }

  /** Android/Chrome: take the code from the incoming text message and submit (WebOTP). */
  private waitForSms() {
    this.stopSmsWait();
    const wait = new AbortController();
    this.smsWait = wait;
    void receiveSmsCode(wait.signal).then((code) => {
      if (!code || this.smsWait !== wait || !this.account.twoFactorPending()) return;
      this.code = code;
      void this.submit();
    });
  }

  private stopSmsWait() {
    this.smsWait?.abort();
    this.smsWait = null;
  }

  /** Closing the dialog mid-sign-in drops the half-done login (nothing lingers in memory). */
  onClosed() {
    if (this.account.twoFactorPending() || this.account.passphraseStep()) this.backToPassword();
  }

  async trial() {
    try {
      await this.account.startTrial();
      this.toast.show('Pro trial started — sync is on');
    } catch (e) {
      this.toast.error(e instanceof ApiError ? e.message : String(e));
    }
  }

  /** Sends the browser to the provider's hosted checkout for the selected plan. An
   *  unconfigured method answers 503 with what is missing — shown as a toast. */
  async checkout(method: PaymentMethod) {
    this.paying.set(true);
    try {
      location.href = await this.account.checkout(this.plan(), method.name);
    } catch (e) {
      this.toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      this.paying.set(false);
    }
  }

  /** Ends the membership now. The server says first what comes back (prorated unused days
   *  of the period, or the flat Lifetime amount) — that is what the confirmation shows. */
  async cancel() {
    const lifetime = this.ent()?.plan === 'lifetime';
    this.busy.set(true);
    try {
      const preview = await this.account.cancelPreview();
      const refund =
        preview.amount_cents > 0
          ? `${this.money(preview.amount, preview.currency)} will be refunded (${preview.note})`
          : `Nothing is refunded (${preview.note})`;
      const question = lifetime
        ? `End your Lifetime membership now? ${refund}. You go back to the Free limits immediately.`
        : `End your Pro subscription now? ${refund}. Pro stops immediately.`;
      if (!confirm(question)) return;
      const result = await this.account.cancelSubscription();
      const back = Number(result.refund.amount_cents) > 0;
      this.toast.show(
        back
          ? `${lifetime ? 'Membership' : 'Subscription'} ended — ${this.money(result.refund.amount, result.refund.currency)} refunded`
          : `${lifetime ? 'Membership' : 'Subscription'} ended`,
      );
      this.history.set(await this.account.payments());
    } catch (e) {
      this.toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      this.busy.set(false);
    }
  }

  /** Sends the browser to the provider's billing page; it returns to /?billing=managed. */
  async manage() {
    this.busy.set(true);
    try {
      location.href = await this.account.manageUrl();
    } catch (e) {
      this.toast.error(e instanceof Error ? e.message : String(e));
    } finally {
      this.busy.set(false);
    }
  }

  async setVault(on: boolean) {
    try {
      await this.account.setVault(on);
      this.toast.show(on ? 'Encrypted backup on' : 'Encrypted backup off');
    } catch (e) {
      this.toast.error(e instanceof Error ? e.message : String(e));
    }
  }

  async resetFromServer() {
    if (!confirm('Replace the data on this device with the copy from the server?')) return;
    await this.sync.resetFromServer();
    this.toast.show(this.sync.error() ?? 'Rebuilt from the server');
  }

  async revoke(id: string) {
    await this.account.revokeDevice(id);
    this.toast.show('Device signed out');
  }

  async logout() {
    await this.account.logout();
    this.toast.show('Logged out — your data stays on this device');
  }

  async deleteAccount() {
    if (!confirm('Delete your account on the server? Data on your devices is kept.')) return;
    await this.account.deleteAccount();
    this.toast.show('Account deleted');
  }
}
