import { Component, computed, input } from '@angular/core';
import {
  DEDUCTIBLE_STATUS_LABEL,
  type InsuranceVerification,
  VERIFICATION_STATUS_LABEL,
  formatCents,
} from '../../core/patients/patients.service';

/**
 * The result of an insurance eligibility & benefits check, as a small panel: status, effective
 * dates, copay, deductible, coinsurance, referral and prior-authorization requirements.
 *
 * `audience="patient"` (the registration confirmation) words things for the patient and adds
 * what to do next; `"staff"` is the compact factual version.
 */
@Component({
  selector: 'app-insurance-verification-view',
  template: `
    <section
      class="verification"
      [class]="'verification ' + v().status"
      [id]="'verification-' + v().id"
      [attr.data-status]="v().status"
      aria-label="Insurance coverage check"
    >
      <div class="verification-head">
        <span class="verification-badge" [class]="'verification-badge ' + v().status">
          {{ statusLabel() }}
        </span>
        <span class="small muted">
          Checked {{ checkedAt()
          }}{{ v().provider === 'fake' ? ' · built-in fake (nothing left this machine)' : '' }}
        </span>
      </div>
      <p class="verification-message" id="verification-message">{{ v().message }}</p>

      @if (v().status === 'active' || v().status === 'pending' || v().status === 'inactive') {
        <dl class="record verification-details">
          <div>
            <dt>Plan</dt>
            <dd>{{ planLabel() }}{{ v().plan_type ? ' (' + v().plan_type + ')' : '' }}</dd>
          </div>
          <div>
            <dt>Effective dates</dt>
            <dd id="verification-dates">{{ dates() }}</dd>
          </div>
          @if (v().status === 'active') {
            <div>
              <dt>Copay</dt>
              <dd id="verification-copay">
                {{ money(v().copay_cents) }} office visit{{
                  v().specialist_copay_cents !== null
                    ? ' · ' + money(v().specialist_copay_cents) + ' specialist'
                    : ''
                }}
              </dd>
            </div>
            <div>
              <dt>Deductible</dt>
              <dd id="verification-deductible">
                {{ deductible() }}
              </dd>
            </div>
            <div>
              <dt>Coinsurance</dt>
              <dd id="verification-coinsurance">
                {{
                  v().coinsurance_percent === null
                    ? '—'
                    : v().coinsurance_percent + '% your share after the deductible'
                }}
              </dd>
            </div>
            @if (v().out_of_pocket_max_cents !== null) {
              <div>
                <dt>Out-of-pocket maximum</dt>
                <dd>
                  {{ money(v().out_of_pocket_met_cents) }} of
                  {{ money(v().out_of_pocket_max_cents) }} reached
                </dd>
              </div>
            }
            <div>
              <dt>Referral</dt>
              <dd id="verification-referral">
                <strong>{{ yesNo(v().referral_required) }}</strong>
                @if (v().referral_notes) {
                  <span class="small muted"> — {{ v().referral_notes }}</span>
                }
              </dd>
            </div>
            <div>
              <dt>Prior authorization</dt>
              <dd id="verification-prior-auth">
                <strong>{{ yesNo(v().prior_auth_required) }}</strong>
                @if (v().prior_auth_notes) {
                  <span class="small muted"> — {{ v().prior_auth_notes }}</span>
                }
              </dd>
            </div>
          }
        </dl>
      }

      @if (audience() === 'patient') {
        <p class="small muted" id="verification-next">{{ advice() }}</p>
      }
    </section>
  `,
})
export class InsuranceVerificationView {
  readonly verification = input.required<InsuranceVerification>();
  readonly audience = input<'patient' | 'staff'>('staff');

  readonly v = this.verification;
  readonly planLabel = computed(() =>
    [this.v().payer_name, this.v().plan_name].filter(Boolean).join(' · '),
  );
  readonly statusLabel = computed(
    () => VERIFICATION_STATUS_LABEL[this.v().status] ?? this.v().status,
  );
  readonly checkedAt = computed(() => {
    const d = new Date(this.v().checked_at);
    return isNaN(+d)
      ? this.v().checked_at
      : d.toLocaleString(undefined, {
          month: 'short',
          day: 'numeric',
          hour: 'numeric',
          minute: '2-digit',
        });
  });
  readonly dates = computed(() => {
    const { coverage_start: a, coverage_end: b } = this.v();
    if (!a && !b) return '—';
    return `${a ? this.date(a) : '…'} – ${b ? this.date(b) : 'open-ended'}`;
  });
  readonly deductible = computed(() => {
    const v = this.v();
    if (v.deductible_cents === null) return '—';
    const status = v.deductible_status ? DEDUCTIBLE_STATUS_LABEL[v.deductible_status] : '';
    if (v.deductible_status === 'none') return status;
    return `${this.money(v.deductible_met_cents ?? 0)} of ${this.money(v.deductible_cents)} met · ${this.money(v.deductible_remaining_cents)} remaining${status ? ' (' + status.toLowerCase() + ')' : ''}`;
  });
  readonly advice = computed(() => {
    const v = this.v();
    switch (v.status) {
      case 'active': {
        const parts = [
          `Bring your card; expect a ${this.money(v.copay_cents)} copay at the visit.`,
        ];
        if (v.referral_required)
          parts.push('Ask your primary care provider for a referral before a specialist visit.');
        if (v.prior_auth_required)
          parts.push(
            'Some services need prior authorization — the office arranges it before they are scheduled.',
          );
        return parts.join(' ');
      }
      case 'inactive':
        return 'Your insurer says this coverage is not active. If you have a newer card, bring it to the front desk; otherwise the visit is self-pay.';
      case 'pending':
        return 'Your coverage is not active yet. Visits before the start date would be self-pay.';
      case 'not_found':
        return 'Please check the member id on your card — the office will look into it before your visit.';
      case 'incomplete':
        return 'Add the insurance company and member id from your card at the front desk and the office checks your coverage there.';
      default:
        return 'The office will verify your coverage before your visit.';
    }
  });

  money(cents: number | null): string {
    return formatCents(cents, this.v().currency);
  }

  yesNo(value: boolean | null): string {
    return value === null ? 'Unknown' : value ? 'Required' : 'Not required';
  }

  private date(iso: string): string {
    const d = new Date(iso + 'T00:00:00');
    return isNaN(+d)
      ? iso
      : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }
}
