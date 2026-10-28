import { NgTemplateOutlet } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DomSanitizer, type SafeUrl } from '@angular/platform-browser';
import { TipDirective } from '../ui/tip.directive';
import { ToastService } from '../ui/toast.service';
import {
  AccountService,
  type CodeChannel,
  type CodeDelivery,
  type TwoFactorMethod,
  type TwoFactorSetup,
} from './account.service';
import { isOtpauthUri, isTouchDevice, receiveSmsCode } from './platform';
import { type QrImage, qrImage } from './qr';

/** The panel's steps. `idle` shows the methods; the others are small inline forms. */
type Step = 'idle' | 'password' | 'scan' | 'confirm' | 'codes' | 'remove' | 'regenerate';

const METHOD_LABEL: Record<TwoFactorMethod, string> = {
  totp: 'Authenticator app',
  sms: 'Text message (SMS)',
  email: 'E-mail',
};
/** In running text: "On — authenticator app, text message". */
const METHOD_NAME: Record<TwoFactorMethod, string> = {
  totp: 'authenticator app',
  sms: 'text message',
  email: 'e-mail',
};
const METHOD_HINT: Record<TwoFactorMethod, string> = {
  totp: 'A 6-digit code from an app such as Google Authenticator, Microsoft Authenticator, Authy, 1Password or Bitwarden. Works offline; the safest choice.',
  sms: 'A 6-digit code by text message to your mobile phone.',
  email: 'A 6-digit code by e-mail to your account address.',
};

/**
 * Two-factor authentication on the account screen. Three methods, any combination:
 * authenticator app (password → QR code / key → first code), text message (password + phone
 * number → code sent there) and e-mail (password → code sent to the account address). The first
 * method hands out the recovery codes. Remove one method, turn everything off, or get new
 * recovery codes — each with password + a current code.
 *
 * Lives inside the account dialog's form, so its inputs handle Enter themselves instead of
 * submitting the dialog. Laid out for every screen size (the dialog is a bottom sheet on phones).
 */
@Component({
  selector: 'app-two-factor-panel',
  imports: [FormsModule, NgTemplateOutlet, TipDirective],
  template: `
    <div class="field two-factor" id="two-factor">
      <span class="muted small">Two-factor authentication</span>

      @switch (step()) {
        @case ('idle') {
          <p class="small" id="tfa-status">
            @if (status()?.enabled) {
              <strong>On</strong> since {{ date(status()!.enabled_at) }} — {{ enabledLabels() }} ·
              {{ status()!.recovery_codes_left }} recovery code{{
                status()!.recovery_codes_left === 1 ? '' : 's'
              }}
              left.
              @if (status()!.recovery_codes_left <= 2) {
                <span class="warn">Get new recovery codes before they run out.</span>
              }
            } @else {
              <strong>Off.</strong> Adds a second step to signing in — a code from an app, a text
              message or an e-mail — so a leaked password alone cannot open your account or fetch
              your encrypted data.
            }
          </p>
          <ul class="tfa-methods">
            @for (m of allMethods; track m) {
              <li [id]="'tfa-method-' + m" [class.on]="isOn(m)">
                <div class="tfa-method-text">
                  <strong>{{ label(m) }}</strong>
                  <span class="small muted">
                    @if (isOn(m)) {
                      On{{ m === 'sms' && status()?.phone ? ' · ' + status()!.phone : ''
                      }}{{ m === 'email' && status()?.email ? ' · ' + status()!.email : '' }}
                    } @else if (!isAvailable(m)) {
                      Not available on this server
                    } @else {
                      Off
                    }
                  </span>
                </div>
                @if (isOn(m)) {
                  <span class="tfa-row-actions">
                    <button
                      class="btn"
                      type="button"
                      [id]="'tfa-change-' + m"
                      (click)="startChange(m)"
                      [appTip]="changeTip(m)"
                    >
                      Change
                    </button>
                    <button
                      class="btn"
                      type="button"
                      [id]="'tfa-remove-' + m"
                      (click)="startRemove(m)"
                      appTip="Stop using {{
                        label(m)
                      }} as a second factor. Needs your password and a current code."
                    >
                      Remove
                    </button>
                  </span>
                } @else {
                  <button
                    class="btn"
                    [class.primary]="!status()?.enabled && m === 'totp'"
                    type="button"
                    [id]="'tfa-setup-' + m"
                    [disabled]="!isAvailable(m)"
                    (click)="startSetup(m)"
                    [appTip]="hint(m)"
                  >
                    Set up
                  </button>
                }
              </li>
            }
          </ul>
          @if (status()?.fake_channels?.length) {
            <p class="small muted" id="tfa-fake-note">
              No e-mail/text provider is configured here: the built-in fake delivers codes to the
              <a [href]="outboxUrl()" target="_blank" rel="noopener">test inbox ↗</a>.
            </p>
          }
          @if (status()?.enabled) {
            <div class="dialog-actions">
              <button
                class="btn"
                type="button"
                id="tfa-regenerate"
                (click)="go('regenerate')"
                appTip="Replace your recovery codes with a fresh set of ten. The current codes stop working."
              >
                New recovery codes
              </button>
              <button
                class="btn"
                type="button"
                id="tfa-disable"
                (click)="startRemove(null)"
                appTip="Turn two-factor authentication off completely (every method, and the recovery codes). Needs your password and a current code."
              >
                Turn off
              </button>
            </div>
          }
        }

        @case ('password') {
          <p class="small">
            <strong>{{ changing() ? 'Change' : 'Set up' }}: {{ label(method()) }}</strong>
          </p>
          <p class="muted small">{{ hint(method()) }} {{ startHint() }}</p>
          @if (method() === 'sms') {
            <label class="small" for="tfa-phone">{{
              changing() ? 'New mobile number' : 'Mobile number'
            }}</label>
            <input
              class="input"
              id="tfa-phone"
              name="tfa-phone"
              type="tel"
              inputmode="tel"
              autocomplete="tel"
              placeholder="+1 415 555 0100"
              [(ngModel)]="phone"
              (keydown.enter)="enter($event)"
              appTip="International format: + and the country code, then the number (e.g. +44 7700 900123, +49 151 2345678). We text a code to confirm it."
            />
          }
          @if (method() === 'email') {
            <label class="small" for="tfa-email">{{
              changing() ? 'New e-mail address for codes' : 'E-mail address for codes'
            }}</label>
            <input
              class="input"
              id="tfa-email"
              name="tfa-email"
              type="email"
              inputmode="email"
              autocomplete="email"
              [placeholder]="account.session()?.email ?? ''"
              [(ngModel)]="email"
              (keydown.enter)="enter($event)"
              appTip="Where the codes go. Leave empty for your account e-mail. We send a code there to confirm it."
            />
          }
          @if (changing()) {
            <ng-container *ngTemplateOutlet="passwordAndCode" />
          } @else {
            <label class="small" for="tfa-password">Password</label>
            <input
              class="input"
              id="tfa-password"
              name="tfa-password"
              type="password"
              [(ngModel)]="password"
              autocomplete="current-password"
              (keydown.enter)="enter($event)"
              appTip="Your current password — it is turned into a key on this device and never sent."
            />
          }
          <div class="dialog-actions">
            <button class="btn" type="button" (click)="cancel()" appTip="Leave things as they are.">
              Cancel
            </button>
            <button
              class="btn primary"
              type="button"
              id="tfa-continue"
              [disabled]="
                busy() ||
                !password ||
                (method() === 'sms' && !phone.trim()) ||
                (changing() && code.trim().length < 6)
              "
              (click)="beginSetup()"
              appTip="{{
                method() === 'totp'
                  ? 'Create the key for your authenticator app.'
                  : 'Send a code to confirm it is yours.'
              }}"
            >
              {{ busy() ? 'Working…' : method() === 'totp' ? 'Continue' : 'Send code' }}
            </button>
          </div>
        }

        @case ('scan') {
          <p class="muted small">
            1. Scan this QR code with your authenticator app — or add the key by hand{{
              touch ? ', or open it directly in the app on this device' : ''
            }}.
          </p>
          @if (qr(); as q) {
            <svg
              class="qr"
              id="tfa-qr"
              [attr.viewBox]="'0 0 ' + q.size + ' ' + q.size"
              role="img"
              aria-label="QR code for the authenticator app"
              shape-rendering="crispEdges"
            >
              <rect width="100%" height="100%" fill="#fff" />
              <path [attr.d]="q.path" fill="#000" />
            </svg>
          }
          <p class="small">
            Key: <code id="tfa-secret">{{ spaced(setup()?.secret ?? '') }}</code>
            <span class="muted">
              (time-based, {{ setup()?.digits }} digits, every {{ setup()?.period }} s)</span
            >
          </p>
          <div class="dialog-actions tfa-inline-actions">
            @if (touch && appLink()) {
              <a
                class="btn"
                id="tfa-open-app"
                [href]="appLink()"
                appTip="Opens the authenticator app installed on this phone or tablet with the key filled in."
                >Open in authenticator app</a
              >
            }
            <button
              class="btn"
              type="button"
              id="tfa-copy-key"
              (click)="copyKey()"
              appTip="Copy the key to paste it into an authenticator app."
            >
              Copy key
            </button>
          </div>
          <p class="muted small">
            2. Enter the 6-digit code the {{ changing() ? 'new ' : '' }}app shows now{{
              changing() ? ' — the old app stops working once it is confirmed' : ''
            }}.
          </p>
          <ng-container *ngTemplateOutlet="codeField" />
          <ng-container *ngTemplateOutlet="activateActions" />
        }

        @case ('confirm') {
          <p class="small" id="tfa-sent">
            We sent a code by {{ method() === 'sms' ? 'text message' : 'e-mail' }} to
            <strong>{{ delivery()?.destination }}</strong
            >. Enter it to
            {{ changing() ? 'switch your codes over to it' : 'turn this method on' }}.
          </p>
          <ng-container *ngTemplateOutlet="fakeHint; context: { $implicit: delivery() }" />
          <ng-container *ngTemplateOutlet="codeField" />
          <div class="dialog-actions tfa-inline-actions">
            <button
              class="btn ghost"
              type="button"
              id="tfa-resend"
              [disabled]="busy()"
              (click)="resendSetup()"
              appTip="Send a new code (the previous one stops working)."
            >
              Send a new code
            </button>
          </div>
          <ng-container *ngTemplateOutlet="activateActions" />
        }

        @case ('codes') {
          <div class="callout" id="tfa-recovery">
            <p>
              <strong>Save these recovery codes now.</strong> Each one signs you in once if you lose
              your phone or authenticator. They are shown only this once — the server keeps them
              hashed.
            </p>
            <ul class="recovery-codes">
              @for (c of codes(); track c) {
                <li>
                  <code>{{ c }}</code>
                </li>
              }
            </ul>
          </div>
          <div class="dialog-actions">
            <button
              class="btn"
              type="button"
              id="tfa-copy"
              (click)="copy()"
              appTip="Copy the codes to the clipboard."
            >
              Copy
            </button>
            <button
              class="btn"
              type="button"
              id="tfa-download"
              (click)="download()"
              appTip="Save the codes as a text file."
            >
              Download
            </button>
            <span class="spacer"></span>
            <button
              class="btn primary"
              type="button"
              id="tfa-done"
              (click)="cancel()"
              appTip="I have saved the codes."
            >
              Done
            </button>
          </div>
        }

        @case ('remove') {
          <p class="muted small">
            {{
              removing()
                ? 'Remove ' +
                  name(removing()!) +
                  (onlyMethod(removing()!)
                    ? ' — your last method, so two-factor authentication turns off'
                    : '') +
                  '.'
                : 'Turn two-factor authentication off completely.'
            }}
            Needs your password and a current code (or one of your recovery codes).
          </p>
          <ng-container *ngTemplateOutlet="passwordAndCode" />
          <div class="dialog-actions">
            <button class="btn" type="button" (click)="cancel()" appTip="Keep it.">Cancel</button>
            <button
              class="btn danger"
              type="button"
              id="tfa-disable-confirm"
              [disabled]="busy() || !password || code.trim().length < 6"
              (click)="remove()"
              appTip="{{
                removing()
                  ? 'Remove this method.'
                  : 'Remove the second factor: your password alone signs in again.'
              }}"
            >
              {{ busy() ? 'Working…' : removing() ? 'Remove' : 'Turn off' }}
            </button>
          </div>
        }

        @case ('regenerate') {
          <p class="muted small">
            New recovery codes need your password and a current code (or one of the remaining
            recovery codes). The current set stops working.
          </p>
          <ng-container *ngTemplateOutlet="passwordAndCode" />
          <div class="dialog-actions">
            <button class="btn" type="button" (click)="cancel()" appTip="Keep the current codes.">
              Cancel
            </button>
            <button
              class="btn primary"
              type="button"
              id="tfa-regenerate-confirm"
              [disabled]="busy() || !password || code.trim().length < 6"
              (click)="regenerate()"
              appTip="Replace the recovery codes and show the new ones."
            >
              {{ busy() ? 'Working…' : 'Get new codes' }}
            </button>
          </div>
        }
      }

      @if (error()) {
        <div class="callout error" role="alert" id="tfa-error">
          <p>{{ error() }}</p>
        </div>
      }
    </div>

    <ng-template #codeField>
      <label class="small" for="tfa-code">Code</label>
      <input
        class="input code-input"
        id="tfa-code"
        name="tfa-code"
        inputmode="numeric"
        autocomplete="one-time-code"
        maxlength="8"
        [(ngModel)]="code"
        (keydown.enter)="enter($event)"
        appTip="The 6-digit code. It proves the app, phone or inbox is really yours before anything is enforced."
      />
    </ng-template>

    <ng-template #activateActions>
      <div class="dialog-actions">
        <button
          class="btn"
          type="button"
          (click)="cancel()"
          appTip="Stop here — nothing is turned on until a code is confirmed."
        >
          Cancel
        </button>
        <button
          class="btn primary"
          type="button"
          id="tfa-activate"
          [disabled]="busy() || code.trim().length < 6"
          (click)="activate()"
          appTip="Turn this method on."
        >
          {{ busy() ? 'Checking…' : 'Activate' }}
        </button>
      </div>
    </ng-template>

    <ng-template #fakeHint let-d>
      @if (d?.fake_outbox_url) {
        <p class="small fake-hint">
          Built-in fake — nothing was really sent.
          <a
            [href]="account.apiPage(d.fake_outbox_url)"
            target="_blank"
            rel="noopener"
            class="tfa-outbox-link"
            >Read it in the test inbox ↗</a
          >
        </p>
      }
    </ng-template>

    <ng-template #passwordAndCode>
      <div class="field-row">
        <div class="field">
          <label class="small" for="tfa-password">Password</label>
          <input
            class="input"
            id="tfa-password"
            name="tfa-password"
            type="password"
            [(ngModel)]="password"
            autocomplete="current-password"
            (keydown.enter)="enter($event)"
            appTip="Your current password — turned into a key on this device, never sent."
          />
        </div>
        <div class="field">
          <label class="small" for="tfa-code">Code</label>
          <input
            class="input code-input"
            id="tfa-code"
            name="tfa-code"
            inputmode="text"
            autocomplete="one-time-code"
            maxlength="16"
            [(ngModel)]="code"
            (keydown.enter)="enter($event)"
            appTip="A current code from your authenticator app, a text message or an e-mail — or one of your recovery codes."
          />
        </div>
      </div>
      @if (sendable().length) {
        <div class="dialog-actions tfa-inline-actions">
          @for (c of sendable(); track c) {
            <button
              class="btn ghost"
              type="button"
              [id]="'tfa-send-' + c"
              [disabled]="busy()"
              (click)="sendCode(c)"
              appTip="Send a code to {{ c === 'sms' ? status()?.phone : status()?.email }}."
            >
              {{ c === 'sms' ? 'Text me a code' : 'E-mail me a code' }}
            </button>
          }
        </div>
      }
      @if (delivery(); as d) {
        <p class="small" id="tfa-sent">Code sent to {{ d.destination }}.</p>
        <ng-container *ngTemplateOutlet="fakeHint; context: { $implicit: d }" />
      }
    </ng-template>
  `,
})
export class TwoFactorPanel {
  readonly account = inject(AccountService);
  private readonly toast = inject(ToastService);
  private readonly sanitizer = inject(DomSanitizer);

  readonly allMethods: TwoFactorMethod[] = ['totp', 'sms', 'email'];
  readonly touch = isTouchDevice();
  readonly step = signal<Step>('idle');
  readonly method = signal<TwoFactorMethod>('totp');
  /** The set-up in progress replaces a method that is already on. */
  readonly changing = signal(false);
  /** The method being removed; null = turn everything off. */
  readonly removing = signal<TwoFactorMethod | null>(null);
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);
  readonly setup = signal<TwoFactorSetup | null>(null);
  /** The latest code sent by text/e-mail in this panel. */
  readonly delivery = signal<CodeDelivery | null>(null);
  readonly codes = signal<string[]>([]);
  readonly qr = computed<QrImage | null>(() => {
    const s = this.setup();
    return s?.otpauth_uri ? qrImage(s.otpauth_uri) : null;
  });
  /** `otpauth://` link for phones/tablets (validated: only the URI the server built). */
  readonly appLink = computed<SafeUrl | null>(() => {
    const uri = this.setup()?.otpauth_uri ?? '';
    return isOtpauthUri(uri) ? this.sanitizer.bypassSecurityTrustUrl(uri) : null;
  });
  readonly status = computed(() => this.account.twoFactor());
  readonly enabledLabels = computed(() =>
    (this.status()?.methods ?? []).map((m) => METHOD_NAME[m]).join(', '),
  );
  /** Enabled methods that can send a code for confirming a change. */
  readonly sendable = computed<CodeChannel[]>(() =>
    (this.status()?.methods ?? []).filter((m): m is CodeChannel => m !== 'totp'),
  );
  password = '';
  phone = '';
  email = '';
  code = '';
  private smsWait: AbortController | null = null;

  constructor() {
    inject(DestroyRef).onDestroy(() => this.stopSmsWait());
  }

  label(m: TwoFactorMethod) {
    return METHOD_LABEL[m];
  }
  name(m: TwoFactorMethod) {
    return METHOD_NAME[m];
  }
  hint(m: TwoFactorMethod) {
    return METHOD_HINT[m];
  }
  isOn(m: TwoFactorMethod) {
    return this.status()?.methods?.includes(m) ?? false;
  }
  isAvailable(m: TwoFactorMethod) {
    return this.status()?.available?.includes(m) ?? m === 'totp';
  }
  onlyMethod(m: TwoFactorMethod) {
    const methods = this.status()?.methods ?? [];
    return methods.length === 1 && methods[0] === m;
  }
  outboxUrl() {
    return this.account.apiPage('/api/v1/messages/fake/outbox');
  }

  go(step: Step) {
    this.error.set(null);
    this.password = '';
    this.code = '';
    this.delivery.set(null);
    this.stopSmsWait();
    this.step.set(step);
  }

  startSetup(m: TwoFactorMethod) {
    this.method.set(m);
    this.changing.set(false);
    this.phone = '';
    this.email = '';
    this.go('password');
  }

  /** Replace the app / number / address of a method that is on: password + current code first. */
  startChange(m: TwoFactorMethod) {
    this.method.set(m);
    this.changing.set(true);
    this.phone = '';
    this.email = '';
    this.go('password');
  }

  changeTip(m: TwoFactorMethod) {
    if (m === 'totp')
      return 'Switch to a new authenticator app or phone. Needs your password, a current code, and a code from the new app.';
    if (m === 'sms')
      return 'Send the codes to another mobile number. Needs your password, a current code, and a code texted to the new number.';
    return 'Send the codes to another e-mail address. Needs your password, a current code, and a code sent to the new address.';
  }

  startHint() {
    if (!this.changing()) return 'Confirm your password to start.';
    const old = this.method() === 'totp' ? 'app' : this.method() === 'sms' ? 'number' : 'address';
    return `To prove it is you: your password and a current code from one of your methods (or a recovery code). The old ${old} keeps working until the new one is confirmed, and is told about the change.`;
  }

  startRemove(m: TwoFactorMethod | null) {
    this.removing.set(m);
    this.go('remove');
  }

  /** Back to the method list; an unfinished set-up is simply abandoned (nothing was enforced). */
  cancel() {
    this.setup.set(null);
    this.codes.set([]);
    this.go('idle');
  }

  /** Enter inside one of the panel's inputs runs the step's action (not the dialog's submit). */
  enter(e: Event) {
    e.preventDefault();
    const code = this.code.trim().length >= 6;
    switch (this.step()) {
      case 'password':
        if (
          this.password &&
          (this.method() !== 'sms' || this.phone.trim()) &&
          (!this.changing() || code)
        )
          void this.beginSetup();
        break;
      case 'scan':
      case 'confirm':
        if (code) void this.activate();
        break;
      case 'remove':
        if (this.password && code) void this.remove();
        break;
      case 'regenerate':
        if (this.password && code) void this.regenerate();
        break;
    }
  }

  async beginSetup() {
    await this.run(async () => {
      const out = await this.account.twoFactorSetup(this.password, this.method(), {
        phone: this.phone,
        email: this.email,
        code: this.changing() ? this.code : undefined,
      });
      this.setup.set(out);
      this.password = '';
      this.code = '';
      this.stopSmsWait();
      if (out.method === 'totp') {
        this.step.set('scan');
      } else {
        this.delivery.set(out.delivery);
        this.step.set('confirm');
        if (out.method === 'sms') this.waitForSms();
      }
    });
  }

  async resendSetup() {
    await this.run(async () => {
      this.delivery.set(await this.account.twoFactorResend());
      this.toast.show('New code sent');
      if (this.method() === 'sms') this.waitForSms();
    });
  }

  async activate() {
    await this.run(async () => {
      const out = await this.account.twoFactorEnable(this.code, this.method());
      this.setup.set(null);
      this.password = '';
      this.code = '';
      this.delivery.set(null);
      this.stopSmsWait();
      this.toast.show(
        this.changing()
          ? `${METHOD_LABEL[this.method()]} changed`
          : `${METHOD_LABEL[this.method()]}: two-factor codes are on`,
      );
      this.changing.set(false);
      if (out.recovery_codes.length) {
        this.codes.set(out.recovery_codes);
        this.step.set('codes');
      } else {
        this.go('idle'); // a further method: the recovery codes from the first one stay valid
      }
    });
  }

  async sendCode(channel: CodeChannel) {
    await this.run(async () => {
      this.delivery.set(await this.account.twoFactorSend(channel));
      if (channel === 'sms') this.waitForSms();
    });
  }

  async remove() {
    const m = this.removing();
    await this.run(async () => {
      const out = await this.account.twoFactorDisable(this.password, this.code, m ?? undefined);
      this.go('idle');
      this.toast.show(
        out.enabled ? `${METHOD_LABEL[m!]} removed` : 'Two-factor authentication is off',
      );
    });
  }

  async regenerate() {
    await this.run(async () => {
      const out = await this.account.twoFactorRecoveryCodes(this.password, this.code);
      this.codes.set(out.recovery_codes);
      this.password = '';
      this.code = '';
      this.delivery.set(null);
      this.step.set('codes');
      this.toast.show('New recovery codes');
    });
  }

  /** Android/Chrome: fill the code from the incoming text message (WebOTP) — typing works everywhere. */
  private waitForSms() {
    this.stopSmsWait();
    const wait = new AbortController();
    this.smsWait = wait;
    void receiveSmsCode(wait.signal).then((code) => {
      if (code && this.smsWait === wait && !this.code) this.code = code;
    });
  }

  private stopSmsWait() {
    this.smsWait?.abort();
    this.smsWait = null;
  }

  async copyKey() {
    await this.clip(this.setup()?.secret ?? '', 'Key copied');
  }

  async copy() {
    await this.clip(this.codesText(), 'Recovery codes copied');
  }

  private async clip(text: string, done: string) {
    try {
      await navigator.clipboard.writeText(text);
      this.toast.show(done);
    } catch {
      this.toast.error('Could not copy — select the text and copy it by hand.');
    }
  }

  download() {
    const blob = new Blob([this.codesText()], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'personal-assistant-recovery-codes.txt';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private codesText(): string {
    const who = this.account.session()?.email ?? '';
    // CRLF: opens cleanly in Notepad on Windows as well as on macOS, Linux, iOS and Android.
    return [
      `Personal Assistant - recovery codes for ${who}`,
      'Each code signs you in once if your phone or authenticator is unavailable.',
      '',
      ...this.codes(),
      '',
    ].join('\r\n');
  }

  private async run(fn: () => Promise<void>) {
    this.error.set(null);
    this.busy.set(true);
    try {
      await fn();
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : String(e));
    } finally {
      this.busy.set(false);
    }
  }

  /** "ABCD EFGH …": the secret in groups of four, as authenticator apps print it. */
  spaced(secret: string) {
    return secret.replace(/(.{4})/g, '$1 ').trim();
  }

  date(iso: string | null) {
    return iso
      ? new Date(iso).toLocaleDateString(undefined, {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
        })
      : '';
  }
}
