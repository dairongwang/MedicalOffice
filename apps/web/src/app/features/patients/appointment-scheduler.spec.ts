import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { ApiError } from '../../core/account/api.service';
import { signal } from '@angular/core';
import { AccountService } from '../../core/account/account.service';
import {
  type PaymentMethod,
  PaymentMethodsService,
} from '../../core/payments/payment-methods.service';
import { ShellService } from '../../core/ui/shell.service';
import {
  type Appointment,
  type AppointmentLookup,
  AppointmentsService,
  type DaySlots,
  type ProviderList,
  isoDate,
} from '../../core/appointments/appointments.service';
import { AppointmentScheduler } from './appointment-scheduler';

const PATIENT = {
  id: 1,
  mrn: 'P-000001',
  first_name: 'Pat',
  last_name: 'Dummy',
  date_of_birth: '1985-03-14',
  created_at: '2027-01-01T00:00:00Z',
};
const PROVIDER = {
  id: 3,
  staff_number: 'W-000003',
  first_name: 'Alex',
  last_name: 'Example',
  role: 'doctor',
  specialty: 'General practice',
  weekdays: [0, 1, 2, 3, 4, 5, 6],
};
const PROVIDERS: ProviderList = {
  providers: [PROVIDER],
  slot_minutes: 30,
  max_days_ahead: 90,
  timezone: 'UTC',
  // The existing tests exercise booking without the payment rule; it has its own describe below.
  payment_method_required: false,
  self_pay_visit_cents: 15000,
  currency: 'USD',
};
const BOOKED: Appointment = {
  id: 9,
  number: 'APT-000009',
  status: 'scheduled',
  start_at: '2027-06-07T09:30:00Z',
  end_at: '2027-06-07T10:00:00Z',
  start_local: 'Monday, June 7, 2027 · 09:30',
  timezone: 'UTC',
  reason: 'Check-up',
  provider: PROVIDER,
  patient: PATIENT,
  created_at: '2027-06-01T00:00:00Z',
  canceled_at: null,
  office_name: 'Medic Office',
  office_address: '1 Example Street',
  link: {
    url: 'http://localhost:8000/appointments/dummy-tok',
    channels: ['email'],
    sent_to: ['email:p•••@example.com'],
    sent_at: '2027-06-01T00:00:00Z',
    note: null,
    fake: true,
    outbox_url: '/api/v1/messages/fake/outbox',
    opens: 0,
    last_opened_at: null,
  },
  calendar: null,
  confirmed_at: null,
  confirmed_via: null,
  reminder_sent_at: null,
  reminder_channels: [],
  reminder_note: null,
  payment: 'insurance',
  coverage_status: 'active',
  self_pay_agreed_at: null,
};

describe('AppointmentScheduler (book and manage without an account)', () => {
  let calls: string[];
  let lookup: AppointmentLookup;
  let slotsFor: (date: string) => DaySlots;
  let bookFails: ApiError | null;
  let coverage: {
    covered: boolean;
    self_pay_required: boolean;
    has_insurance: boolean;
    status: string;
  };

  beforeEach(() => {
    calls = [];
    lookup = { patient: PATIENT, appointments: [] };
    bookFails = null;
    coverage = { covered: true, self_pay_required: false, has_insurance: true, status: 'active' };
    slotsFor = (date) => ({
      provider_id: 3,
      date,
      timezone: 'UTC',
      note: null,
      slots: [
        {
          start_at: `${date}T09:00:00Z`,
          end_at: `${date}T09:30:00Z`,
          label: '09:00',
          available: false,
        },
        {
          start_at: `${date}T09:30:00Z`,
          end_at: `${date}T10:00:00Z`,
          label: '09:30',
          available: true,
        },
      ],
    });
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: AccountService, useValue: { signedIn: () => false } },
        { provide: ShellService, useValue: { openAccount: () => undefined } },
        { provide: PaymentMethodsService, useValue: { list: () => Promise.resolve([]) } },
        {
          provide: AppointmentsService,
          useValue: {
            prefill: Object.assign(() => null, { set: () => undefined }),
            apiUrl: (p: string) => 'http://localhost:8000' + p,
            providers: () => {
              calls.push('providers');
              return Promise.resolve(PROVIDERS);
            },
            slots: (id: number, date: string) => {
              calls.push(`slots ${id} ${date}`);
              return Promise.resolve(slotsFor(date));
            },
            lookup: () => {
              calls.push('lookup');
              return Promise.resolve(lookup);
            },
            coverage: (_id: unknown, startAt: string) => {
              calls.push(`coverage ${startAt}`);
              return Promise.resolve({
                ...coverage,
                message: coverage.covered ? 'Active PPO coverage' : 'Coverage is not active',
                service_date: startAt.slice(0, 10),
                verification: null,
              });
            },
            book: (req: { start_at: string; reason: string | null; self_pay_agreed?: boolean }) => {
              calls.push(
                `book ${req.start_at} ${req.reason}${req.self_pay_agreed ? ' self-pay' : ''}`,
              );
              return bookFails
                ? Promise.reject(bookFails)
                : Promise.resolve(
                    req.self_pay_agreed
                      ? { ...BOOKED, payment: 'self_pay', coverage_status: 'inactive' }
                      : BOOKED,
                  );
            },
            cancel: (id: number) => {
              calls.push(`cancel ${id}`);
              return Promise.resolve({ ...BOOKED, status: 'canceled' });
            },
            checkIn: (id: number, identity: { first_name: string; date_of_birth: string }) => {
              calls.push(`checkIn ${id} ${identity.first_name} ${identity.date_of_birth}`);
              return Promise.resolve({ token: `dummy-page-${id}`, url: 'x', check_in: null });
            },
          },
        },
      ],
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  async function mount() {
    const fixture = TestBed.createComponent(AppointmentScheduler);
    fixture.autoDetectChanges();
    await fixture.whenStable();
    return fixture;
  }
  const settle = async (fixture: { detectChanges(): void }, n = 2) => {
    for (let i = 0; i < n; i++) {
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();
    }
  };
  const text = (el: Element, sel: string) => el.querySelector(sel)?.textContent?.trim() ?? '';
  function type(el: Element, sel: string, value: string) {
    const input = el.querySelector<HTMLInputElement>(sel)!;
    input.value = value;
    input.dispatchEvent(new Event('input'));
  }
  async function identify(fixture: Awaited<ReturnType<typeof mount>>) {
    const el: HTMLElement = fixture.nativeElement;
    type(el, '#sched-first-name', 'Pat');
    type(el, '#sched-last-name', 'Dummy');
    type(el, '#sched-dob', '1985-03-14');
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('#btn-sched-lookup')!.click();
    await settle(fixture);
  }

  it('finds the patient, lists upcoming appointments and cancels one', async () => {
    lookup = { patient: PATIENT, appointments: [BOOKED] };
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    await identify(fixture);
    expect(calls).toEqual(['lookup']);
    expect(text(el, '#sched-found')).toContain('P-000001');
    expect(el.querySelectorAll('#sched-upcoming .appt')).toHaveLength(1);
    el.querySelector<HTMLButtonElement>('#btn-cancel-9')!.click();
    await settle(fixture);
    expect(calls).toContain('cancel 9');
    expect(el.querySelector('#sched-none')).not.toBeNull();
  });

  it('opens the check-in page from the lookup within 24 hours of the visit — not earlier', async () => {
    vi.useFakeTimers({ now: new Date('2027-06-07T08:00:00Z'), toFake: ['Date'] });
    try {
      const windowed = {
        ...BOOKED,
        check_in_opens_at: '2027-06-06T09:30:00Z',
        check_in_closes_at: '2027-06-07T10:00:00Z',
      };
      const later = {
        ...windowed,
        id: 10,
        number: 'APT-000010',
        check_in_opens_at: '2027-06-09T09:30:00Z',
        check_in_closes_at: '2027-06-10T10:00:00Z',
      };
      lookup = { patient: PATIENT, appointments: [windowed, later] };
      const fixture = await mount();
      const el: HTMLElement = fixture.nativeElement;
      await identify(fixture);
      expect(el.querySelector('#btn-sched-checkin-10')).toBeNull();
      expect(text(el, '#sched-checkin-later-10')).toContain('Check-in opens Wed, Jun 9, 09:30 UTC');
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
      el.querySelector<HTMLButtonElement>('#btn-sched-checkin-9')!.click();
      await settle(fixture);
      expect(calls).toContain('checkIn 9 Pat 1985-03-14');
      expect(navigate).toHaveBeenCalledWith(['/check-in', 'dummy-page-9']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('books a free slot and shows the link with the fake-delivery note', async () => {
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    await identify(fixture);
    el.querySelector<HTMLButtonElement>('#btn-sched-book')!.click();
    await settle(fixture, 4);
    expect(calls[1]).toBe('providers');
    expect(calls[2]).toBe(`slots 3 ${isoDate(new Date())}`); // starts on the first day with a free slot
    expect(text(el, '#sched-weekdays')).toContain('30 minutes');
    const slots = el.querySelectorAll<HTMLButtonElement>('#sched-slots .slot');
    expect(slots).toHaveLength(2);
    expect(slots[0].disabled).toBe(true); // taken
    expect(el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.disabled).toBe(true);
    slots[1].click();
    await settle(fixture);
    expect(text(el, '#btn-sched-confirm')).toBe('Book 09:30');
    type(el, '#sched-reason', 'Check-up');
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.click();
    await settle(fixture, 3);
    expect(calls.at(-2)).toMatch(/^coverage .*T09:30:00Z$/); // the insurance is checked for that day first
    expect(calls.at(-1)).toMatch(/^book .*T09:30:00Z Check-up$/);
    expect(text(el, '#sched-result-number')).toBe('APT-000009');
    expect(text(el, '#sched-result-payment')).toContain('coverage was active');
    expect(text(el, '#sched-result-when')).toBe('Monday, June 7, 2027 · 09:30');
    expect(text(el, '#sched-link')).toContain('e-mail (p•••@example.com)');
    expect(text(el, '#sched-link')).toContain('test inbox');
    expect(el.querySelector<HTMLAnchorElement>('#sched-link-url')!.href).toBe(
      'http://localhost:8000/appointments/dummy-tok',
    );
  });

  it('reloads the day and keeps the message when the slot was just taken (409)', async () => {
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    await identify(fixture);
    el.querySelector<HTMLButtonElement>('#btn-sched-book')!.click();
    await settle(fixture, 4);
    el.querySelectorAll<HTMLButtonElement>('#sched-slots .slot')[1].click();
    await settle(fixture);
    bookFails = new ApiError('That slot was just taken. Please choose another time.', 409);
    const before = calls.length;
    el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.click();
    await settle(fixture, 3);
    expect(text(el, '#sched-error')).toContain('just taken');
    expect(calls.slice(before).filter((c) => c.startsWith('slots'))).toHaveLength(1);
    expect(el.querySelector('#sched-result')).toBeNull();
  });

  it('makes the patient agree to self-pay when the coverage is not active', async () => {
    coverage = { covered: false, self_pay_required: true, has_insurance: true, status: 'inactive' };
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    await identify(fixture);
    el.querySelector<HTMLButtonElement>('#btn-sched-book')!.click();
    await settle(fixture, 4);
    el.querySelectorAll<HTMLButtonElement>('#sched-slots .slot')[1].click();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.click();
    await settle(fixture, 3);
    // No booking yet: the agreement panel appears instead.
    expect(calls.filter((c) => c.startsWith('book'))).toHaveLength(0);
    expect(el.querySelector('#sched-self-pay')).not.toBeNull();
    expect(text(el, '#sched-coverage-message')).toContain('not active');
    expect(text(el, '#btn-sched-confirm')).toContain('Book as self-pay');
    // Booking without the tick is refused on the page.
    el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.click();
    await settle(fixture, 2);
    expect(calls.filter((c) => c.startsWith('book'))).toHaveLength(0);
    expect(text(el, '#sched-error')).toContain('agree to pay');
    // Ticked → booked as self-pay.
    el.querySelector<HTMLInputElement>('#sched-self-pay-agree')!.click();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.click();
    await settle(fixture, 3);
    expect(calls.at(-1)).toMatch(/^book .* self-pay$/);
    expect(calls.filter((c) => c.startsWith('coverage'))).toHaveLength(1); // not asked twice
    expect(text(el, '#sched-result-payment')).toContain('You agreed to pay');
  });

  it('explains when the patient is not registered', async () => {
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    (fixture.componentInstance.appointments as unknown as { lookup: () => Promise<never> }).lookup =
      () =>
        Promise.reject(new ApiError('We could not find a patient record with these details.', 404));
    await identify(fixture);
    expect(text(el, '#sched-error')).toContain('could not find');
    expect(el.querySelector('#sched-found')).toBeNull();
  });
});

describe('AppointmentScheduler — booking needs a validated payment method', () => {
  const signedIn = signal(false);
  let methods: PaymentMethod[];
  let opened: string[];
  let booked: string[];

  const method = (status: PaymentMethod['status']): PaymentMethod => ({
    id: 1,
    kind: 'card',
    label: null,
    holder_name: 'Pat',
    is_default: status === 'active',
    status,
    validated_at: null,
    validation: null,
    display: 'Visa •••• 4242 · 12/2031',
    expired: false,
    card_brand: 'visa',
    card_last4: '4242',
    card_exp_month: 12,
    card_exp_year: 2031,
    billing_postal_code: null,
    wallet_email_masked: null,
    bank_name: null,
    bank_account_type: null,
    bank_routing_masked: null,
    bank_account_last4: null,
    provider: 'fake',
    created_at: '2027-01-01T00:00:00Z',
    updated_at: '2027-01-01T00:00:00Z',
  });

  beforeEach(() => {
    signedIn.set(false);
    methods = [];
    opened = [];
    booked = [];
    const date = isoDate(new Date());
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: AccountService, useValue: { signedIn } },
        { provide: ShellService, useValue: { openAccount: (m: string) => opened.push(m) } },
        { provide: PaymentMethodsService, useValue: { list: () => Promise.resolve(methods) } },
        {
          provide: AppointmentsService,
          useValue: {
            prefill: Object.assign(() => null, { set: () => undefined }),
            apiUrl: (p: string) => p,
            providers: () => Promise.resolve({ ...PROVIDERS, payment_method_required: true }),
            slots: () =>
              Promise.resolve({
                provider_id: 3,
                date,
                timezone: 'UTC',
                note: null,
                slots: [
                  {
                    start_at: `${date}T09:30:00Z`,
                    end_at: `${date}T10:00:00Z`,
                    label: '09:30',
                    available: true,
                  },
                ],
              }),
            lookup: () => Promise.resolve({ patient: PATIENT, appointments: [] }),
            coverage: () =>
              Promise.resolve({
                covered: true,
                self_pay_required: false,
                has_insurance: true,
                status: 'active',
                message: 'ok',
                service_date: date,
                verification: null,
              }),
            book: (req: { start_at: string }) => {
              booked.push(req.start_at);
              return Promise.resolve({
                ...BOOKED,
                charge: {
                  id: 5,
                  kind: 'copay',
                  status: 'authorized',
                  amount_cents: 4000,
                  refunded_cents: 0,
                  currency: 'USD',
                  description: 'Copay · visit with Alex Example',
                  message: '40.00 USD on hold on Visa •••• 4242 · 12/2031.',
                  method: {
                    id: 1,
                    kind: 'card',
                    display: 'Visa •••• 4242 · 12/2031',
                    removed: false,
                  },
                  appointment: null,
                  patient: null,
                  reference: 'auth_fake_1',
                  refund_reference: null,
                  provider: 'fake',
                  created_by: 'patient',
                  created_at: '2027-06-01T00:00:00Z',
                  captured_at: null,
                  released_at: null,
                  refunded_at: null,
                },
              });
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
  const type = (el: Element, sel: string, value: string) => {
    const input = el.querySelector<HTMLInputElement>(sel)!;
    input.value = value;
    input.dispatchEvent(new Event('input'));
  };
  async function toTime() {
    const fixture = TestBed.createComponent(AppointmentScheduler);
    fixture.autoDetectChanges();
    await fixture.whenStable();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    type(el, '#sched-first-name', 'Pat');
    type(el, '#sched-last-name', 'Dummy');
    type(el, '#sched-dob', '1985-03-14');
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('#btn-sched-lookup')!.click();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('#btn-sched-book')!.click();
    await settle(fixture, 4);
    return { fixture, el };
  }

  it('asks to sign in, then for a validated method, then lets the patient book', async () => {
    const { fixture, el } = await toTime();
    expect(el.querySelector('#sched-gate-signin')).not.toBeNull();
    el.querySelector<HTMLButtonElement>('#btn-sched-login')!.click();
    expect(opened).toEqual(['login']);
    el.querySelector<HTMLButtonElement>('.slot:not([disabled])')!.click();
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.disabled).toBe(true);

    // Signed in, but the only card was declined: still no.
    methods = [method('failed')];
    signedIn.set(true);
    await settle(fixture, 4);
    expect(el.querySelector('#sched-gate-signin')).toBeNull();
    expect(el.querySelector('#sched-gate-method')).not.toBeNull();
    expect(el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.disabled).toBe(true);

    // A validated card: book — the result shows the hold.
    methods = [method('active')];
    signedIn.set(false);
    await settle(fixture);
    signedIn.set(true);
    await settle(fixture, 4);
    expect(el.querySelector('#sched-gate-ok')?.textContent).toContain('Visa •••• 4242');
    el.querySelector<HTMLButtonElement>('.slot:not([disabled])')!.click();
    await settle(fixture);
    expect(el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.disabled).toBe(false);
    el.querySelector<HTMLButtonElement>('#btn-sched-confirm')!.click();
    await settle(fixture, 5);
    expect(booked).toHaveLength(1);
    expect(el.querySelector('#sched-result-charge')?.textContent).toContain('$40.00 copay');
    expect(el.querySelector('#sched-result-charge')?.textContent).toContain('on hold');
  });
});
