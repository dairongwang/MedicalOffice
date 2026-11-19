import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { ApiError } from '../../core/account/api.service';
import { AccountService } from '../../core/account/account.service';
import {
  PAYMENT_KIND_LABELS,
  PAYMENT_STATUS_LABELS,
  type PaymentHistory,
  type PaymentHistoryEntry,
  PaymentMethodsService,
  money,
} from '../../core/payments/payment-methods.service';
import { TipDirective } from '../../core/ui/tip.directive';

type Filter = 'all' | 'charges' | 'holds' | 'refunds' | 'validations';

const FILTERS: { id: Filter; label: string; tip: string }[] = [
  { id: 'all', label: 'All', tip: 'Everything: holds, charges, refunds and validations.' },
  {
    id: 'charges',
    label: 'Charges',
    tip: 'What was actually charged (copays, self-pay visits, statements).',
  },
  {
    id: 'holds',
    label: 'On hold',
    tip: 'Amounts reserved for upcoming visits — charged after the visit, released if you cancel.',
  },
  { id: 'refunds', label: 'Refunds', tip: 'Money given back to your payment method.' },
  {
    id: 'validations',
    label: 'Validations',
    tip: 'The small authorizations that checked each payment method — released at once, never charged.',
  },
];

/**
 * Payment history (<app-payment-history />, on the Payments page /patients/payments — the old
 * /patients/history address leads there): every movement of money on the signed-in patient's
 * payment methods — the copay / self-pay hold of each visit (charged when the visit is
 * completed, released when it is canceled), statements charged by the office, refunds, and the
 * validations of the methods themselves — across all methods and all records on the account.
 * Opened with the fragment #payment-history (e.g. from the check-in page) it scrolls into view.
 */
@Component({
  selector: 'app-payment-history',
  imports: [TipDirective],
  template: `
    <section class="card intake-card" id="payment-history" aria-labelledby="payhist-title">
      <div class="card-header">
        <h3 id="payhist-title">Payment history</h3>
      </div>
      <p class="muted small">
        Every payment on your account, across all your payment methods: visit copays and self-pay
        fees, statements, refunds — and the small validations of each method.
      </p>

      @if (!account.signedIn()) {
        <p class="empty" id="payhist-signed-out">Sign in to your patient account to see it.</p>
      } @else if (loading() && !data()) {
        <p class="empty" id="payhist-loading">Loading your payments…</p>
      } @else if (error(); as message) {
        <div class="callout error" id="payhist-error" role="alert">
          <strong>Could not load your payment history</strong>
          <span>{{ message }}</span>
          <button class="btn" type="button" (click)="load()">Try again</button>
        </div>
      } @else if (data(); as d) {
        <div id="payhist">
          <div class="pay-totals" id="payhist-totals">
            <div class="pay-total">
              <span class="muted small">Charged</span>
              <strong id="payhist-charged">{{
                money(d.totals.charged_cents, d.totals.currency)
              }}</strong>
            </div>
            <div class="pay-total">
              <span class="muted small">On hold</span>
              <strong id="payhist-held">{{ money(d.totals.held_cents, d.totals.currency) }}</strong>
            </div>
            <div class="pay-total">
              <span class="muted small">Refunded</span>
              <strong id="payhist-refunded">{{
                money(d.totals.refunded_cents, d.totals.currency)
              }}</strong>
            </div>
            <div class="pay-total">
              <span class="muted small">Payments</span>
              <strong id="payhist-count">{{ d.totals.count }}</strong>
            </div>
          </div>

          <div class="pay-filters" role="tablist" aria-label="Show">
            @for (f of filters; track f.id) {
              <button
                class="btn pay-filter"
                type="button"
                role="tab"
                [class.primary]="filter() === f.id"
                [attr.aria-selected]="filter() === f.id"
                [id]="'payhist-filter-' + f.id"
                (click)="filter.set(f.id)"
                [appTip]="f.tip"
              >
                {{ f.label }}
                <span class="chip">{{ count(d, f.id) }}</span>
              </button>
            }
          </div>

          @if (shown().length) {
            <ul class="appt-list" id="payhist-list">
              @for (e of shown(); track e.id) {
                <li
                  class="appt record-row pay-entry"
                  [class.validation]="e.entry === 'validation'"
                  [id]="'payhist-' + e.id"
                  [attr.data-entry]="e.entry"
                  [attr.data-status]="e.status"
                >
                  <div class="appt-main">
                    <span class="appt-when"
                      >{{ e.description }}
                      <span class="tag" [class.confirmed]="good(e)" [class.self-pay]="!good(e)">{{
                        statusLabel(e)
                      }}</span>
                    </span>
                    <span class="appt-meta muted small"
                      >{{ e.at.slice(0, 10) }} · {{ kindLabels[e.kind] }} · {{ e.method.display }}
                      @if (e.method.removed) {
                        <span
                          class="tag pay-removed"
                          title="This payment method has since been removed from your account; the payment stays on record."
                          >removed</span
                        >
                      }
                      {{
                        e.patient ? ' · ' + e.patient.first_name + ' ' + e.patient.last_name : ''
                      }}
                      {{
                        e.appointment
                          ? ' · ' + e.appointment.number + ' · ' + e.appointment.start_local
                          : ''
                      }}</span
                    >
                    @if (e.message) {
                      <span class="appt-meta small muted pay-message">{{ e.message }}</span>
                    }
                  </div>
                  <div class="pay-amount" [class.muted]="e.entry === 'validation'">
                    <strong>{{ amount(e) }}</strong>
                    @if (e.refunded_cents) {
                      <span class="small muted"
                        >−{{ money(e.refunded_cents, e.currency) }} refunded</span
                      >
                    }
                  </div>
                </li>
              }
            </ul>
          } @else {
            <p class="empty" id="payhist-empty">
              {{ d.entries.length ? 'Nothing of this kind yet.' : 'No payments yet.' }}
            </p>
          }
          <p class="small muted">
            A hold is placed on your default payment method when you book a visit (the copay your
            insurance names, or the self-pay visit fee) and charged after the visit — or released if
            the visit is canceled. Payments stay on record even after the card, wallet or bank
            account they were made with is removed from your account (marked
            <span class="tag pay-removed">removed</span>). Questions about a charge? Please ask the
            front desk.
          </p>
          <div class="dialog-actions">
            <span class="spacer"></span>
            <button class="btn" type="button" id="btn-payhist-refresh" (click)="load()">
              Refresh
            </button>
          </div>
        </div>
      }
    </section>
  `,
})
export class PaymentHistoryPanel {
  readonly account = inject(AccountService);
  private readonly service = inject(PaymentMethodsService);
  private readonly route = inject(ActivatedRoute);
  private scrolled = false;

  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly data = signal<PaymentHistory | null>(null);
  readonly filter = signal<Filter>('all');
  readonly filters = FILTERS;
  readonly kindLabels = PAYMENT_KIND_LABELS;
  readonly money = money;

  readonly shown = computed(() => {
    const d = this.data();
    return d ? d.entries.filter((e) => matches(e, this.filter())) : [];
  });

  constructor() {
    effect(() => {
      const signedIn = this.account.signedIn();
      untracked(() => {
        if (signedIn) void this.load();
        else this.data.set(null);
      });
    });
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.data.set(await this.service.history());
      this.scrollIntoViewOnce();
    } catch (e) {
      this.error.set(this.describe(e));
    } finally {
      this.loading.set(false);
    }
  }

  /** Linked as …/payments#payment-history: show the history once it has loaded. */
  private scrollIntoViewOnce(): void {
    if (this.scrolled || this.route.snapshot.fragment !== 'payment-history') return;
    this.scrolled = true;
    setTimeout(() =>
      document.getElementById('payment-history')?.scrollIntoView?.({ block: 'start' }),
    );
  }

  count(d: PaymentHistory, f: Filter): number {
    return d.entries.filter((e) => matches(e, f)).length;
  }

  statusLabel(e: PaymentHistoryEntry): string {
    if (e.entry === 'validation') return e.status === 'authorized' ? 'validated' : e.status;
    return PAYMENT_STATUS_LABELS[e.status] ?? e.status;
  }

  /** Green tag for the outcomes that are fine (charged, released, validated, refunded). */
  good(e: PaymentHistoryEntry): boolean {
    return e.status !== 'declined' && e.status !== 'error';
  }

  amount(e: PaymentHistoryEntry): string {
    const text = money(e.amount_cents, e.currency);
    return e.entry === 'validation' || e.status === 'released' ? `(${text})` : text;
  }

  private describe(e: unknown): string {
    if (e instanceof ApiError && e.status === 401) return 'Please sign in again.';
    if (e instanceof ApiError && e.offline)
      return "We can't reach the office right now. Please check your connection and try again.";
    return e instanceof Error ? e.message : String(e);
  }
}

function matches(e: PaymentHistoryEntry, f: Filter): boolean {
  switch (f) {
    case 'all':
      return true;
    case 'validations':
      return e.entry === 'validation';
    case 'holds':
      return e.entry === 'payment' && e.status === 'authorized';
    case 'refunds':
      return (
        e.entry === 'payment' && (e.status === 'refunded' || e.status === 'partially_refunded')
      );
    case 'charges':
      return (
        e.entry === 'payment' &&
        (e.status === 'captured' || e.status === 'partially_refunded' || e.status === 'refunded')
      );
  }
}
