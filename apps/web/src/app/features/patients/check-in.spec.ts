import { TestBed } from '@angular/core/testing';
import { ApiService } from '../../core/account/api.service';
import {
  type Appointment,
  AppointmentsService,
  checkInOpensLabel,
  checkInState,
  checkedInLabel,
} from '../../core/appointments/appointments.service';
import { CheckInControl } from './check-in';

const START = '2027-06-07T09:30:00Z';

function appointment(extra: Partial<Appointment> = {}): Appointment {
  return {
    id: 7,
    number: 'APT-000007',
    status: 'scheduled',
    start_at: START,
    end_at: '2027-06-07T10:00:00Z',
    start_local: 'Monday, June 7, 2027 · 09:30',
    timezone: 'UTC',
    reason: null,
    provider: {
      id: 1,
      staff_number: 'W-000001',
      first_name: 'Alex',
      last_name: 'Example',
      role: 'doctor',
      specialty: 'General practice',
      weekdays: [0, 1, 2, 3, 4],
    },
    patient: {
      id: 1,
      mrn: 'P-000001',
      first_name: 'Pat',
      last_name: 'Dummy',
      date_of_birth: '1985-03-14',
      created_at: '2027-01-01T00:00:00Z',
    },
    created_at: '2027-06-01T00:00:00Z',
    canceled_at: null,
    office_name: 'Medic Office',
    office_address: null,
    link: null,
    calendar: null,
    confirmed_at: null,
    confirmed_via: null,
    reminder_sent_at: null,
    reminder_channels: [],
    reminder_note: null,
    payment: 'insurance',
    coverage_status: 'active',
    self_pay_agreed_at: null,
    checked_in_at: null,
    checked_in_via: null,
    check_in_opens_at: '2027-06-06T09:30:00Z', // 24 hours before
    check_in_closes_at: '2027-06-07T10:00:00Z', // the end of the visit
    can_check_in: false,
    ...extra,
  };
}

describe('check-in state', () => {
  const at = (iso: string) => new Date(iso);

  it('is open from 24 hours before the visit until it ends', () => {
    const a = appointment();
    expect(checkInState(a, at('2027-06-06T09:29:59Z'))).toBe('not-yet');
    expect(checkInState(a, at('2027-06-06T09:30:00Z'))).toBe('open');
    expect(checkInState(a, at('2027-06-07T09:45:00Z'))).toBe('open'); // the visit has started
    expect(checkInState(a, at('2027-06-07T10:00:00Z'))).toBe('closed');
  });

  it('is done once checked in, and not offered for visits that are not scheduled', () => {
    const now = at('2027-06-07T09:00:00Z');
    expect(checkInState(appointment({ checked_in_at: '2027-06-07T08:50:00Z' }), now)).toBe(
      'checked-in',
    );
    expect(checkInState(appointment({ status: 'canceled' }), now)).toBe('none');
    expect(checkInState(appointment({ check_in_opens_at: undefined }), now)).toBe('none');
  });

  it('shows a link the office sent, and a failed check-in with its reason', () => {
    const now = at('2027-06-07T09:00:00Z');
    const summary = {
      id: 3,
      via: 'staff' as const,
      started_at: '2027-06-07T08:30:00Z',
      expires_at: '2027-06-07T09:30:00Z',
      link_channels: ['email'],
      link_sent_to: ['email:p•••@example.com'],
      link_sent_at: '2027-06-07T08:30:00Z',
      link_delivery_note: null,
      failure_code: null,
      failure_reason: null,
      completed_at: null,
      estimate_cents: null,
      currency: 'USD',
      payment_id: null,
    };
    expect(checkInState(appointment({ check_in: { ...summary, status: 'pending' } }), now)).toBe(
      'link-sent',
    );
    // The link ran out: the patient's own window applies again (the server records the failure).
    expect(
      checkInState(
        appointment({ check_in: { ...summary, status: 'pending' } }),
        at('2027-06-07T09:40:00Z'),
      ),
    ).toBe('open');
    const failed = {
      ...summary,
      status: 'failed' as const,
      failure_code: 'no_payment_method',
      failure_reason: 'No validated payment method on file.',
    };
    expect(checkInState(appointment({ check_in: failed }), now)).toBe('failed');
    // A patient's own pending page does not block the button (a new page replaces it).
    expect(
      checkInState(
        appointment({ check_in: { ...summary, via: 'account', status: 'pending' } }),
        now,
      ),
    ).toBe('open');
  });

  it('labels in the office time zone', () => {
    expect(checkInOpensLabel(appointment())).toBe('Sun, Jun 6, 09:30 UTC');
    expect(checkInOpensLabel(appointment({ timezone: 'America/New_York' }))).toBe(
      'Sun, Jun 6, 05:30 EDT',
    );
    expect(
      checkedInLabel(
        appointment({ checked_in_at: '2027-06-07T09:12:00Z', checked_in_via: 'staff' }),
      ),
    ).toBe("Checked in with the front desk's link at 09:12 UTC");
    expect(
      checkedInLabel(
        appointment({ checked_in_at: '2027-06-07T09:12:00Z', checked_in_via: 'account' }),
      ),
    ).toBe('Checked in in the app at 09:12 UTC');
    expect(checkedInLabel(appointment())).toBe('');
  });
});

describe('AppointmentsService check-in calls', () => {
  it('opens the check-in page from the account (token), the link and the lookup page (no token)', async () => {
    const calls: { path: string; body: unknown; opts: unknown }[] = [];
    TestBed.configureTestingModule({
      providers: [
        {
          provide: ApiService,
          useValue: {
            post: (path: string, body: unknown, opts: unknown) => {
              calls.push({ path, body, opts });
              return Promise.resolve({ token: 'dummy-page', url: 'x', check_in: null });
            },
          },
        },
      ],
    });
    const service = TestBed.inject(AppointmentsService);
    const identity = {
      first_name: 'Pat',
      last_name: 'Dummy',
      date_of_birth: '1985-03-14',
      email: null,
      phone: null,
    };
    expect((await service.checkInMine(7)).token).toBe('dummy-page');
    await service.checkInByToken('dummy-tok/x');
    await service.checkIn(7, identity);
    expect(calls.map((c) => c.path)).toEqual([
      '/me/appointments/7/check-in',
      '/appointments/by-token/dummy-tok%2Fx/check-in',
      '/appointments/7/check-in',
    ]);
    expect(calls[0].opts).toBeUndefined(); // the patient's session
    expect((calls[1].opts as { auth: boolean }).auth).toBe(false);
    expect((calls[2].opts as { auth: boolean }).auth).toBe(false);
    expect(calls[2].body).toEqual(identity);
  });
});

describe('CheckInControl', () => {
  afterEach(() => vi.useRealTimers());

  function mount(a: Appointment) {
    const fixture = TestBed.createComponent(CheckInControl);
    fixture.componentRef.setInput('appointment', a);
    fixture.componentRef.setInput('prefix', 'mine');
    fixture.detectChanges();
    return fixture;
  }

  it('offers the button inside the window and emits', () => {
    vi.useFakeTimers({ now: new Date('2027-06-07T08:00:00Z'), toFake: ['Date'] });
    const fixture = mount(appointment());
    const emitted: Appointment[] = [];
    fixture.componentInstance.checkIn.subscribe((a) => emitted.push(a));
    const el: HTMLElement = fixture.nativeElement;
    const button = el.querySelector<HTMLButtonElement>('#btn-mine-checkin-7')!;
    expect(button.textContent?.trim()).toBe('Check in');
    button.click();
    expect(emitted.map((a) => a.id)).toEqual([7]);
  });

  it('says when check-in opens, that the desk takes over, or that it is done', () => {
    vi.useFakeTimers({ now: new Date('2027-06-05T12:00:00Z'), toFake: ['Date'] });
    let el: HTMLElement = mount(appointment()).nativeElement;
    expect(el.querySelector('#btn-mine-checkin-7')).toBeNull();
    expect(el.querySelector('#mine-checkin-later-7')?.textContent).toContain(
      'Check-in opens Sun, Jun 6, 09:30 UTC',
    );

    vi.setSystemTime(new Date('2027-06-07T11:00:00Z'));
    el = mount(appointment()).nativeElement;
    expect(el.querySelector('#mine-checkin-desk-7')?.textContent).toContain('front desk');

    el = mount(
      appointment({ checked_in_at: '2027-06-07T09:05:00Z', checked_in_via: 'staff' }),
    ).nativeElement;
    expect(el.querySelector('#mine-checked-in-7')?.textContent).toContain(
      "Checked in with the front desk's link at 09:05 UTC",
    );
  });

  it('shows why a check-in failed — with "Try again" inside the window — and a link the office sent', () => {
    vi.useFakeTimers({ now: new Date('2027-06-07T08:00:00Z'), toFake: ['Date'] });
    const base = {
      id: 3,
      via: 'account' as const,
      started_at: '2027-06-07T07:30:00Z',
      expires_at: '2027-06-07T08:30:00Z',
      link_channels: ['email'],
      link_sent_to: [],
      link_sent_at: null,
      link_delivery_note: null,
      failure_code: null,
      failure_reason: null,
      completed_at: null,
      estimate_cents: null,
      currency: 'USD',
      payment_id: null,
    };
    let el: HTMLElement = mount(
      appointment({
        check_in: {
          ...base,
          status: 'failed',
          failure_code: 'payment_declined',
          failure_reason: 'The pre-authorization of $40.00 was declined.',
        },
      }),
    ).nativeElement;
    expect(el.querySelector('#mine-checkin-failed-7')?.textContent).toContain(
      'Check-in failed: The pre-authorization of $40.00 was declined.',
    );
    expect(el.querySelector('#btn-mine-checkin-7')?.textContent?.trim()).toBe('Try again');

    el = mount(
      appointment({
        check_in: { ...base, via: 'staff', status: 'pending', link_sent_at: base.started_at },
      }),
    ).nativeElement;
    expect(el.querySelector('#mine-checkin-link-7')?.textContent).toContain(
      'The front desk sent you a check-in link by e-mail — please complete it by 08:30 UTC',
    );
    expect(el.querySelector('#btn-mine-checkin-7')?.textContent?.trim()).toBe('Check in here');

    // Outside the window: the reason, and the desk — no button.
    vi.setSystemTime(new Date('2027-06-07T11:00:00Z'));
    el = mount(
      appointment({ check_in: { ...base, status: 'failed', failure_reason: 'Expired.' } }),
    ).nativeElement;
    expect(el.querySelector('#btn-mine-checkin-7')).toBeNull();
    expect(el.textContent).toContain('Please check in at the front desk.');
  });
});
