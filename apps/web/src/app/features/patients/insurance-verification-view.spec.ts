import { TestBed } from '@angular/core/testing';
import type { InsuranceVerification } from '../../core/patients/patients.service';
import { InsuranceVerificationView } from './insurance-verification-view';

function check(extra: Partial<InsuranceVerification>): InsuranceVerification {
  return {
    id: 5,
    status: 'active',
    message: 'msg',
    triggered_by: 'staff',
    provider: 'fake',
    reference: 'FAKE-ELIG-1',
    payer_name: 'Example Health',
    plan_name: 'HMO Basic',
    plan_type: 'HMO',
    member_id: 'M1',
    group_number: null,
    coverage_start: '2027-01-01',
    coverage_end: null,
    currency: 'USD',
    copay_cents: 3000,
    specialist_copay_cents: null,
    deductible_cents: 0,
    deductible_met_cents: 0,
    deductible_remaining_cents: 0,
    deductible_status: 'none',
    coinsurance_percent: null,
    out_of_pocket_max_cents: null,
    out_of_pocket_met_cents: null,
    referral_required: true,
    referral_notes: 'PCP referral for specialists.',
    prior_auth_required: true,
    prior_auth_notes: null,
    checked_at: '2027-07-20T09:00:00Z',
    ...extra,
  };
}

describe('InsuranceVerificationView', () => {
  async function mount(v: InsuranceVerification, audience: 'patient' | 'staff' = 'patient') {
    const fixture = TestBed.createComponent(InsuranceVerificationView);
    fixture.componentRef.setInput('verification', v);
    fixture.componentRef.setInput('audience', audience);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }
  const text = (el: Element, sel: string) => el.querySelector(sel)?.textContent?.trim() ?? '';

  it('shows an HMO with open-ended coverage, no deductible and both requirements', async () => {
    const el = await mount(check({}));
    expect(text(el, '.verification-badge')).toBe('Coverage active');
    expect(text(el, '#verification-dates')).toMatch(/Jan 1, 2027 – open-ended/);
    expect(text(el, '#verification-copay')).toBe('$30.00 office visit');
    expect(text(el, '#verification-deductible')).toBe('No deductible');
    expect(text(el, '#verification-coinsurance')).toBe('—');
    expect(text(el, '#verification-referral')).toContain('Required — PCP referral');
    expect(text(el, '#verification-prior-auth')).toBe('Required');
    expect(text(el, '#verification-next')).toContain('referral');
    expect(text(el, '.verification-head')).toContain('built-in fake');
  });

  it('words inactive, pending, not found and unavailable for the patient', async () => {
    let el = await mount(check({ status: 'inactive', coverage_end: '2027-05-31' }));
    expect(el.querySelector('.verification')!.getAttribute('data-status')).toBe('inactive');
    expect(text(el, '#verification-next')).toContain('self-pay');
    expect(el.querySelector('#verification-copay')).toBeNull();
    el = await mount(check({ status: 'pending' }));
    expect(text(el, '#verification-next')).toContain('not active yet');
    el = await mount(check({ status: 'not_found' }));
    expect(text(el, '#verification-next')).toContain('member id');
    expect(el.querySelector('.verification-details')).toBeNull();
    el = await mount(check({ status: 'unavailable' }), 'staff');
    expect(el.querySelector('#verification-next')).toBeNull();
  });
});
