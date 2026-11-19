import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { routes } from '../../app.routes';
import { AccountService } from '../../core/account/account.service';
import {
  type PaymentHistory,
  type PaymentHistoryEntry,
  PaymentMethodsService,
} from '../../core/payments/payment-methods.service';
import { ShellService } from '../../core/ui/shell.service';
import { PatientsPage } from './patients-page';
import { PaymentsPage } from './payments-page';

function entry(
  id: string,
  extra: Partial<PaymentHistoryEntry> & Pick<PaymentHistoryEntry, 'entry' | 'kind' | 'status'>,
): PaymentHistoryEntry {
  return {
    id,
    at: '2027-06-01T10:00:00Z',
    amount_cents: 4000,
    refunded_cents: 0,
    currency: 'USD',
    description: 'Copay · visit with Alex Example',
    message: '',
    method: { id: 1, kind: 'card', display: 'Visa •••• 4242 · 12/2031', removed: false },
    appointment: null,
    patient: null,
    reference: null,
    provider: 'fake',
    by: 'patient',
    payment: null,
    ...extra,
  };
}

const HISTORY: PaymentHistory = {
  entries: [
    entry('payment-3', {
      entry: 'payment',
      kind: 'copay',
      status: 'authorized',
      at: '2027-06-03T10:00:00Z',
    }),
    entry('payment-2', {
      entry: 'payment',
      kind: 'statement',
      status: 'partially_refunded',
      amount_cents: 2550,
      refunded_cents: 1000,
      description: 'Statement June',
      at: '2027-06-02T10:00:00Z',
    }),
    entry('payment-1', {
      entry: 'payment',
      kind: 'self_pay',
      status: 'released',
      amount_cents: 15000,
      method: {
        id: null,
        kind: 'bank',
        display: 'Example Bank · Checking •••• 6789',
        removed: true,
      },
    }),
    entry('validation-1', {
      entry: 'validation',
      kind: 'validation',
      status: 'authorized',
      amount_cents: 100,
      description: 'Validation of Visa •••• 4242 · 12/2031',
      at: '2027-05-30T10:00:00Z',
    }),
  ],
  totals: {
    currency: 'USD',
    charged_cents: 1550,
    refunded_cents: 1000,
    held_cents: 4000,
    count: 3,
  },
};

describe('PaymentsPage — payment methods and payment history in one tab', () => {
  const signedIn = signal(false);
  let calls = 0;
  let lists = 0;

  beforeEach(() => {
    signedIn.set(false);
    calls = 0;
    lists = 0;
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        { provide: AccountService, useValue: { signedIn } },
        { provide: ShellService, useValue: { openAccount: () => undefined } },
        {
          provide: PaymentMethodsService,
          useValue: {
            history: () => {
              calls++;
              return Promise.resolve(HISTORY);
            },
            list: () => {
              lists++;
              return Promise.resolve([]);
            },
          },
        },
      ],
    });
  });

  const settle = async (fixture: { detectChanges(): void }, n = 3) => {
    for (let i = 0; i < n; i++) {
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();
    }
  };
  const text = (el: Element, sel: string) => el.querySelector(sel)?.textContent?.trim() ?? '';

  it('shows the payment methods and the history once signed in — totals, entries, filters', async () => {
    const fixture = TestBed.createComponent(PaymentsPage);
    fixture.autoDetectChanges();
    await fixture.whenStable();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#payments-signed-out')).not.toBeNull();
    expect(el.querySelector('#pay-methods')).toBeNull();
    expect(el.querySelector('#payment-history')).toBeNull();
    expect(calls).toBe(0);

    signedIn.set(true);
    await settle(fixture, 4);
    expect(el.querySelector('#payments-signed-out')).toBeNull();
    expect(calls).toBe(1);
    expect(lists).toBe(1);
    // One page: the methods first, the history below.
    const methods = el.querySelector('#pay-methods')!;
    const history = el.querySelector('#payment-history')!;
    expect(
      methods.compareDocumentPosition(history) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(text(el, '#payhist-title')).toBe('Payment history');
    expect(text(el, '#payhist-charged')).toBe('$15.50');
    expect(text(el, '#payhist-held')).toBe('$40.00');
    expect(text(el, '#payhist-refunded')).toBe('$10.00');
    expect(text(el, '#payhist-count')).toBe('3');
    expect(el.querySelectorAll('#payhist-list .pay-entry')).toHaveLength(4);
    expect(text(el, '#payhist-payment-3')).toContain('on hold');
    expect(text(el, '#payhist-payment-2')).toContain('partly refunded');
    expect(text(el, '#payhist-payment-2')).toContain('−$10.00 refunded');
    expect(text(el, '#payhist-payment-1')).toContain('($150.00)'); // released: in brackets
    expect(text(el, '#payhist-payment-1')).toContain('Example Bank · Checking •••• 6789');
    expect(el.querySelector('#payhist-payment-1 .pay-removed')).not.toBeNull(); // the method is gone, the line stays
    expect(el.querySelector('#payhist-payment-2 .pay-removed')).toBeNull();
    expect(text(el, '#payhist-validation-1')).toContain('validated');

    el.querySelector<HTMLButtonElement>('#payhist-filter-charges')!.click();
    await settle(fixture);
    expect(el.querySelectorAll('#payhist-list .pay-entry')).toHaveLength(1);
    el.querySelector<HTMLButtonElement>('#payhist-filter-holds')!.click();
    await settle(fixture);
    expect(text(el, '#payhist-list')).toContain('Copay');
    el.querySelector<HTMLButtonElement>('#payhist-filter-validations')!.click();
    await settle(fixture);
    expect(el.querySelectorAll('#payhist-list .pay-entry')).toHaveLength(1);
    expect(text(el, '#payhist-filter-refunds')).toContain('1');
  });
});

describe('Payments — one tab instead of Payment methods + Payment history', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        { provide: AccountService, useValue: { signedIn: signal(false) } },
        { provide: ShellService, useValue: { openAccount: () => undefined } },
        { provide: PaymentMethodsService, useValue: {} },
      ],
    });
  });

  it('has a single Payments link, and the old history address leads to it', async () => {
    const fixture = TestBed.createComponent(PatientsPage);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    const links = [...el.querySelectorAll('.subnav-link')].map((a) => a.textContent!.trim());
    expect(links).toContain('Payments');
    expect(links).not.toContain('Payment methods');
    expect(links).not.toContain('Payment history');
    expect(el.querySelector('#subnav-payments')!.getAttribute('href')).toBe('/patients/payments');
    expect(el.querySelector('#subnav-history')).toBeNull();

    const router = TestBed.inject(Router);
    await router.navigateByUrl('/patients/history');
    expect(router.url).toBe('/patients/payments#payment-history');
  });
});
