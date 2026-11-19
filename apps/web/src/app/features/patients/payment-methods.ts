import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiError } from '../../core/account/api.service';
import { AccountService } from '../../core/account/account.service';
import {
  METHOD_KINDS,
  METHOD_KIND_LABELS,
  METHOD_STATUS_LABELS,
  type PaymentMethod,
  type PaymentMethodDraft,
  type PaymentMethodKind,
  PaymentMethodsService,
  type PaymentValidation,
  VALIDATION_RESULT_LABELS,
  cardBrand,
  draftFrom,
  emptyDraft,
  money,
  toInput,
  validateDraft,
} from '../../core/payments/payment-methods.service';
import { TipDirective } from '../../core/ui/tip.directive';
import { ToastService } from '../../core/ui/toast.service';

const KIND_ICONS: Record<PaymentMethodKind, string> = {
  card: '▭',
  stripe: 'S',
  paypal: 'P',
  bank: '⌂',
};

/**
 * The payment methods on the signed-in patient account (<app-payment-methods />): a credit /
 * debit card, a Stripe or PayPal account, or a bank account — to pay copays and self-pay visits.
 *
 * Everything is tokenized by the server: the card number, the account number or the wallet's
 * e-mail address is sent once, validated, handed to the vault and never shown again — only
 * "Visa •••• 4242" or "pa•••@example.com" comes back. Each method is validated by a small
 * authorization that is released at once (nothing is charged); only a validated ("active")
 * method can be the default — the one the copay / self-pay hold goes on when a visit is booked.
 *
 * Shown on the Payments page (/patients/payments, `PaymentsPage`) above the payment history.
 */
@Component({
  selector: 'app-payment-methods',
  imports: [FormsModule, TipDirective],
  template: `
    <section class="card intake-card" id="pay-methods" aria-labelledby="pay-methods-title">
      <div class="card-header">
        <h3 id="pay-methods-title">Payment methods</h3>
        @if (account.signedIn() && !form()) {
          <button
            class="btn primary"
            type="button"
            id="btn-pay-add"
            [disabled]="busy() || loading()"
            (click)="startAdd()"
            appTip="Add a card, your Stripe or PayPal account, or a bank account. It is checked with a small authorization that is released at once — nothing is charged."
          >
            + Add
          </button>
        }
      </div>
      <p class="small muted">
        How you pay the office's copays and self-pay visits. When you book, the amount is put on
        hold on your default method, charged after the visit and released if you cancel. Card and
        account numbers are never stored here — only the last four digits.
      </p>

      @if (!account.signedIn()) {
        <p class="empty" id="pay-signed-out">Sign in to your patient account to manage them.</p>
      } @else if (loading() && !methods().length) {
        <p class="empty" id="pay-loading">Loading your payment methods…</p>
      } @else if (error(); as message) {
        <div class="callout error" id="pay-error" role="alert">
          <strong>Could not load your payment methods</strong>
          <span>{{ message }}</span>
          <button class="btn" type="button" (click)="load()">Try again</button>
        </div>
      } @else {
        @if (methods().length) {
          <ul class="appt-list" id="pay-list">
            @for (m of methods(); track m.id) {
              <li
                class="appt pay-method"
                [class.pending]="m.status === 'pending'"
                [class.failed]="m.status === 'failed'"
                [class.expired]="m.expired"
                [id]="'pay-method-' + m.id"
                [attr.data-status]="m.status"
                [attr.data-kind]="m.kind"
              >
                <span class="pay-icon" [attr.data-kind]="m.kind" aria-hidden="true">{{
                  icons[m.kind]
                }}</span>
                <div class="appt-main">
                  <span class="appt-when"
                    >{{ m.label || kindLabels[m.kind] }}
                    @if (m.is_default) {
                      <span class="tag confirmed">default</span>
                    }
                    <span
                      class="tag"
                      [class.confirmed]="m.status === 'active'"
                      [class.self-pay]="m.status !== 'active'"
                      >{{ statusLabels[m.status] }}</span
                    >
                    @if (m.expired) {
                      <span class="tag self-pay">expired</span>
                    }
                  </span>
                  <span class="appt-meta">{{ m.display }}</span>
                  <span class="appt-meta muted small"
                    >{{ m.holder_name || ''
                    }}{{
                      m.kind === 'bank' && m.bank_routing_masked
                        ? ' · routing ' + m.bank_routing_masked
                        : ''
                    }}</span
                  >
                  @if (m.validation; as v) {
                    <span
                      class="appt-meta small"
                      [class.muted]="v.result === 'authorized'"
                      [class.pay-problem]="v.result !== 'authorized'"
                      [id]="'pay-method-' + m.id + '-validation'"
                      >Last check: {{ resultLabels[v.result] }} ·
                      {{ money(v.amount_cents, v.currency) }} · {{ v.created_at.slice(0, 10) }} —
                      {{ v.message }}</span
                    >
                  }
                  @if (historyFor() === m.id) {
                    <ul class="small muted" [id]="'pay-method-' + m.id + '-history'">
                      @for (v of validations(); track v.id) {
                        <li>
                          {{ v.created_at.slice(0, 16).replace('T', ' ') }} ·
                          {{ resultLabels[v.result] }} · {{ money(v.amount_cents, v.currency) }} ·
                          {{ v.message }}
                        </li>
                      } @empty {
                        <li>No checks yet.</li>
                      }
                    </ul>
                  }
                </div>
                <div class="pay-actions">
                  <button
                    class="btn"
                    type="button"
                    [id]="'btn-pay-validate-' + m.id"
                    [disabled]="busy()"
                    (click)="validate(m)"
                    appTip="Check this method again with a small authorization that is released at once."
                  >
                    {{ m.status === 'active' ? 'Check again' : 'Validate' }}
                  </button>
                  @if (!m.is_default && m.status === 'active') {
                    <button
                      class="btn"
                      type="button"
                      [id]="'btn-pay-default-' + m.id"
                      [disabled]="busy()"
                      (click)="makeDefault(m)"
                      appTip="Use this method for the copay / self-pay hold when you book."
                    >
                      Make default
                    </button>
                  }
                  <button
                    class="btn"
                    type="button"
                    [id]="'btn-pay-history-' + m.id"
                    [disabled]="busy()"
                    (click)="toggleHistory(m)"
                    appTip="Every check of this payment method."
                  >
                    {{ historyFor() === m.id ? 'Hide checks' : 'Checks' }}
                  </button>
                  <button
                    class="btn"
                    type="button"
                    [id]="'btn-pay-edit-' + m.id"
                    [disabled]="busy()"
                    (click)="startEdit(m)"
                    appTip="Change the name, label, expiry date — or replace the card / account / e-mail address."
                  >
                    Edit
                  </button>
                  <button
                    class="btn danger"
                    type="button"
                    [id]="'btn-pay-remove-' + m.id"
                    [disabled]="busy()"
                    (click)="remove(m)"
                    appTip="Remove this payment method. Its payments stay in your payment history."
                  >
                    Remove
                  </button>
                </div>
              </li>
            }
          </ul>
        } @else if (!form()) {
          <p class="empty" id="pay-empty">
            No payment method on file yet. Add one to book appointments.
          </p>
        }

        @if (form(); as f) {
          <form class="attach" id="pay-form" novalidate (ngSubmit)="save()">
            <h4>{{ editing() ? 'Update ' + editing()!.display : 'Add a payment method' }}</h4>
            @if (!editing()) {
              <div class="pay-kinds" role="radiogroup" aria-label="Kind of payment method">
                @for (k of kinds; track k) {
                  <button
                    class="btn pay-kind"
                    type="button"
                    role="radio"
                    [id]="'pay-kind-' + k"
                    [class.primary]="f.kind === k"
                    [attr.aria-checked]="f.kind === k"
                    (click)="setKind(k)"
                  >
                    <span aria-hidden="true">{{ icons[k] }}</span> {{ kindLabels[k] }}
                  </button>
                }
              </div>
            }

            @switch (f.kind) {
              @case ('card') {
                <div class="field">
                  <label for="pay-holder">Name on the card</label>
                  <input
                    class="input"
                    id="pay-holder"
                    name="holder_name"
                    autocomplete="cc-name"
                    maxlength="120"
                    [(ngModel)]="f.holder_name"
                  />
                </div>
                <div class="field">
                  <label for="pay-card-number"
                    >Card number{{
                      editing() ? ' (leave blank to keep •••• ' + editing()!.card_last4 + ')' : ''
                    }}</label
                  >
                  <div class="pay-number">
                    <input
                      class="input"
                      id="pay-card-number"
                      name="card_number"
                      inputmode="numeric"
                      autocomplete="cc-number"
                      maxlength="23"
                      [(ngModel)]="f.card_number"
                    />
                    @if (brand(); as b) {
                      <span class="tag pay-brand" id="pay-card-brand">{{ b }}</span>
                    }
                  </div>
                </div>
                <div class="field-row three">
                  <div class="field">
                    <label for="pay-exp-month">Expiry month</label>
                    <select
                      class="input"
                      id="pay-exp-month"
                      name="card_exp_month"
                      [(ngModel)]="f.card_exp_month"
                    >
                      <option value="">MM</option>
                      @for (mm of months; track mm) {
                        <option [value]="mm">{{ mm.padStart(2, '0') }}</option>
                      }
                    </select>
                  </div>
                  <div class="field">
                    <label for="pay-exp-year">Expiry year</label>
                    <select
                      class="input"
                      id="pay-exp-year"
                      name="card_exp_year"
                      [(ngModel)]="f.card_exp_year"
                    >
                      <option value="">YYYY</option>
                      @for (yy of years; track yy) {
                        <option [value]="yy">{{ yy }}</option>
                      }
                    </select>
                  </div>
                  <div class="field">
                    <label for="pay-cvc">Security code</label>
                    <input
                      class="input"
                      id="pay-cvc"
                      name="card_cvc"
                      inputmode="numeric"
                      autocomplete="cc-csc"
                      maxlength="4"
                      [(ngModel)]="f.card_cvc"
                      appTip="Checked once, never stored."
                    />
                  </div>
                </div>
                <div class="field">
                  <label for="pay-postal">Billing postal code (optional)</label>
                  <input
                    class="input"
                    id="pay-postal"
                    name="billing_postal_code"
                    autocomplete="postal-code"
                    maxlength="20"
                    [(ngModel)]="f.billing_postal_code"
                  />
                </div>
              }
              @case ('bank') {
                <div class="field">
                  <label for="pay-holder">Account holder</label>
                  <input
                    class="input"
                    id="pay-holder"
                    name="holder_name"
                    autocomplete="name"
                    maxlength="120"
                    [(ngModel)]="f.holder_name"
                  />
                </div>
                <div class="field-row">
                  <div class="field">
                    <label for="pay-bank-name">Bank</label>
                    <input
                      class="input"
                      id="pay-bank-name"
                      name="bank_name"
                      maxlength="120"
                      placeholder="Example Bank"
                      [(ngModel)]="f.bank_name"
                    />
                  </div>
                  <div class="field">
                    <label for="pay-bank-type">Account type</label>
                    <select
                      class="input"
                      id="pay-bank-type"
                      name="bank_account_type"
                      [(ngModel)]="f.bank_account_type"
                    >
                      <option value="checking">Checking</option>
                      <option value="savings">Savings</option>
                    </select>
                  </div>
                </div>
                <div class="field-row">
                  <div class="field">
                    <label for="pay-routing"
                      >Routing number{{
                        editing() ? ' (on file: ' + editing()!.bank_routing_masked + ')' : ''
                      }}</label
                    >
                    <input
                      class="input"
                      id="pay-routing"
                      name="bank_routing_number"
                      inputmode="numeric"
                      autocomplete="off"
                      maxlength="34"
                      [(ngModel)]="f.bank_routing_number"
                    />
                  </div>
                  <div class="field">
                    <label for="pay-account"
                      >Account number{{
                        editing()
                          ? ' (leave blank to keep •••• ' + editing()!.bank_account_last4 + ')'
                          : ''
                      }}</label
                    >
                    <input
                      class="input"
                      id="pay-account"
                      name="bank_account_number"
                      inputmode="numeric"
                      autocomplete="off"
                      maxlength="34"
                      [(ngModel)]="f.bank_account_number"
                    />
                  </div>
                </div>
              }
              @default {
                <div class="field">
                  <label for="pay-wallet-email"
                    >E-mail address of your {{ kindLabels[f.kind] }} account{{
                      editing()
                        ? ' (leave blank to keep ' + editing()!.wallet_email_masked + ')'
                        : ''
                    }}</label
                  >
                  <input
                    class="input"
                    id="pay-wallet-email"
                    name="wallet_email"
                    type="email"
                    autocomplete="email"
                    maxlength="320"
                    [(ngModel)]="f.wallet_email"
                  />
                </div>
                <div class="field">
                  <label for="pay-holder">Name (optional)</label>
                  <input
                    class="input"
                    id="pay-holder"
                    name="holder_name"
                    autocomplete="name"
                    maxlength="120"
                    [(ngModel)]="f.holder_name"
                  />
                </div>
              }
            }

            <div class="field-row">
              <div class="field">
                <label for="pay-label">Label (optional)</label>
                <input
                  class="input"
                  id="pay-label"
                  name="label"
                  maxlength="60"
                  placeholder="e.g. Family card"
                  [(ngModel)]="f.label"
                />
              </div>
              <label class="checkbox pay-default" for="pay-is-default">
                <input
                  type="checkbox"
                  id="pay-is-default"
                  name="is_default"
                  [disabled]="!!editing()?.is_default"
                  [(ngModel)]="f.is_default"
                />
                <span>Default payment method</span>
              </label>
            </div>

            @if (formError(); as message) {
              <div class="callout error" id="pay-form-error" role="alert">
                <strong>Please check the form</strong>
                <span>{{ message }}</span>
              </div>
            }

            <div class="dialog-actions">
              <button
                class="btn"
                type="button"
                id="btn-pay-cancel"
                [disabled]="busy()"
                (click)="cancelForm()"
              >
                Cancel
              </button>
              <span class="spacer"></span>
              <button class="btn primary" type="submit" id="btn-pay-save" [disabled]="busy()">
                {{ busy() ? 'Saving…' : editing() ? 'Save changes' : 'Add payment method' }}
              </button>
            </div>
          </form>
        }

        @if (hasFake()) {
          <p class="small muted pay-fake-note" id="pay-fake-note">
            Built-in fake payment vault (development): nothing leaves this machine and nothing is
            ever charged. It declines cards ending in 0002, bank accounts ending in 0000 and entries
            labelled “decline”.
          </p>
        }
      }
    </section>
  `,
})
export class PaymentMethodsPanel {
  readonly account = inject(AccountService);
  private readonly service = inject(PaymentMethodsService);
  private readonly toast = inject(ToastService);

  readonly kinds = METHOD_KINDS;
  readonly kindLabels = METHOD_KIND_LABELS;
  readonly statusLabels = METHOD_STATUS_LABELS;
  readonly resultLabels = VALIDATION_RESULT_LABELS;
  readonly icons = KIND_ICONS;
  readonly money = money;
  readonly months = Array.from({ length: 12 }, (_, i) => String(i + 1));
  readonly years = Array.from({ length: 16 }, (_, i) => String(new Date().getFullYear() + i));

  readonly methods = signal<PaymentMethod[]>([]);
  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  /** The add / edit form (null = closed) and the method being edited (null = adding). */
  readonly form = signal<PaymentMethodDraft | null>(null);
  readonly editing = signal<PaymentMethod | null>(null);
  readonly formError = signal<string | null>(null);
  readonly historyFor = signal<number | null>(null);
  readonly validations = signal<PaymentValidation[]>([]);
  /** Re-evaluated on every change detection through the template (the draft is mutated by ngModel). */
  readonly brand = () => cardBrand(this.form()?.card_number ?? '');
  readonly hasFake = computed(() => this.methods().some((m) => m.provider === 'fake'));

  constructor() {
    // Load on arrival and again after a sign-in / sign-out in the account dialog.
    effect(() => {
      const signedIn = this.account.signedIn();
      untracked(() => {
        if (signedIn) void this.load();
        else {
          this.methods.set([]);
          this.form.set(null);
          this.editing.set(null);
        }
      });
    });
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.methods.set(await this.service.list());
    } catch (e) {
      this.error.set(describe(e));
    } finally {
      this.loading.set(false);
    }
  }

  startAdd(): void {
    this.editing.set(null);
    this.formError.set(null);
    this.form.set({ ...emptyDraft('card'), is_default: !this.methods().some((m) => m.is_default) });
  }

  startEdit(m: PaymentMethod): void {
    this.editing.set(m);
    this.formError.set(null);
    this.form.set(draftFrom(m));
  }

  setKind(kind: PaymentMethodKind): void {
    const f = this.form();
    if (!f || this.editing()) return;
    this.formError.set(null);
    this.form.set({ ...emptyDraft(kind), label: f.label, is_default: f.is_default });
  }

  cancelForm(): void {
    this.form.set(null);
    this.editing.set(null);
    this.formError.set(null);
  }

  async save(): Promise<void> {
    const f = this.form();
    if (!f || this.busy()) return;
    const editing = this.editing();
    const problem = validateDraft(f, editing);
    if (problem) {
      this.formError.set(problem);
      return;
    }
    this.busy.set(true);
    this.formError.set(null);
    try {
      const input = toInput(f);
      const saved = editing
        ? await this.service.update(editing.id, input)
        : await this.service.add(input);
      const v = saved.validation;
      if (saved.status === 'active') this.toast.show(`${saved.display} saved and validated.`);
      else
        this.toast.error(
          `${saved.display} saved, but not validated${v ? ': ' + v.message : '.'} It cannot be used until it is.`,
        );
      this.cancelForm();
      await this.load();
    } catch (e) {
      this.formError.set(describe(e));
    } finally {
      this.busy.set(false);
    }
  }

  async validate(m: PaymentMethod): Promise<void> {
    await this.act(async () => {
      const fresh = await this.service.validate(m.id);
      const v = fresh.validation;
      if (fresh.status === 'active')
        this.toast.show(`${fresh.display}: validated — ${v?.message ?? ''}`);
      else this.toast.error(`${fresh.display}: ${v ? v.message : 'could not be validated.'}`);
      await this.load();
      if (this.historyFor() === m.id) this.validations.set(await this.service.validations(m.id));
    });
  }

  async makeDefault(m: PaymentMethod): Promise<void> {
    await this.act(async () => {
      this.methods.set(await this.service.makeDefault(m.id));
      this.toast.show(`${m.display} is now your default payment method.`);
    });
  }

  async toggleHistory(m: PaymentMethod): Promise<void> {
    if (this.historyFor() === m.id) {
      this.historyFor.set(null);
      return;
    }
    await this.act(async () => {
      this.validations.set(await this.service.validations(m.id));
      this.historyFor.set(m.id);
    });
  }

  async remove(m: PaymentMethod): Promise<void> {
    if (!confirm(`Remove ${m.display}? Its payments stay in your payment history.`)) return;
    await this.act(async () => {
      await this.service.remove(m.id);
      this.toast.show('Payment method removed.');
      if (this.editing()?.id === m.id) this.cancelForm();
      await this.load();
    });
  }

  private async act(fn: () => Promise<void>): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      await fn();
    } catch (e) {
      this.toast.error(describe(e));
    } finally {
      this.busy.set(false);
    }
  }
}

function describe(e: unknown): string {
  if (e instanceof ApiError && e.status === 401) return 'Please sign in again.';
  if (e instanceof ApiError && e.offline)
    return "We can't reach the office right now. Please check your connection and try again.";
  return e instanceof Error ? e.message : String(e);
}
