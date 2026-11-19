import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { AccountService } from '../../core/account/account.service';
import { ApiError } from '../../core/account/api.service';
import {
  type Appointment,
  AppointmentsService,
  type MyAppointments as MyAppointmentsData,
} from '../../core/appointments/appointments.service';
import { PatientsService } from '../../core/patients/patients.service';
import { PaymentMethodsService } from '../../core/payments/payment-methods.service';
import { ShellService } from '../../core/ui/shell.service';
import { ToastService } from '../../core/ui/toast.service';
import { MyAppointments } from './my-appointments';

const PATIENT = {
  id: 1,
  mrn: 'P-000001',
  first_name: 'Pat',
  last_name: 'Dummy',
  date_of_birth: '1985-03-14',
  created_at: '2027-01-01T00:00:00Z',
};

function visit(id: number, opens: string, closes: string): Appointment {
  return {
    id,
    number: `APT-${String(id).padStart(6, '0')}`,
    status: 'scheduled',
    start_at: closes,
    end_at: closes,
    start_local: 'Monday, June 7, 2027 · 09:30',
    timezone: 'UTC',
    reason: null,
    provider: {
      id: 1,
      staff_number: 'W-000001',
      first_name: 'Alex',
      last_name: 'Example',
      role: 'doctor',
      specialty: null,
      weekdays: [0, 1, 2, 3, 4],
    },
    patient: PATIENT,
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
    check_in_opens_at: opens,
    check_in_closes_at: closes,
  };
}

describe('MyAppointments — check-in from the patient account', () => {
  let data: MyAppointmentsData;
  let calls: string[];
  let toasts: string[];
  let refuse: ApiError | null;

  beforeEach(() => {
    vi.useFakeTimers({ now: new Date('2027-06-07T08:00:00Z'), toFake: ['Date'] });
    calls = [];
    toasts = [];
    refuse = null;
    data = {
      patients: [],
      upcoming: [
        visit(1, '2027-06-06T09:30:00Z', '2027-06-07T10:00:00Z'), // today: open
        visit(2, '2027-06-09T09:30:00Z', '2027-06-10T10:00:00Z'), // in three days: not yet
      ],
      past: [],
    };
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: AccountService, useValue: { signedIn: signal(true) } },
        { provide: ShellService, useValue: { openAccount: () => undefined } },
        { provide: PatientsService, useValue: {} },
        { provide: PaymentMethodsService, useValue: { list: () => Promise.resolve([]) } },
        {
          provide: ToastService,
          useValue: {
            show: (m: string) => toasts.push(m),
            error: (m: string) => toasts.push('error: ' + m),
          },
        },
        {
          provide: AppointmentsService,
          useValue: {
            mine: () => Promise.resolve(data),
            checkInMine: (id: number) => {
              calls.push(`checkIn ${id}`);
              if (refuse) return Promise.reject(refuse);
              return Promise.resolve({ token: `dummy-page-${id}`, url: 'x', check_in: null });
            },
          },
        },
      ],
    });
  });
  afterEach(() => vi.useRealTimers());

  const settle = async (fixture: { detectChanges(): void }, n = 3) => {
    for (let i = 0; i < n; i++) {
      await new Promise((r) => setTimeout(r, 0));
      fixture.detectChanges();
    }
  };
  const text = (el: Element, sel: string) => el.querySelector(sel)?.textContent?.trim() ?? '';

  it('offers check-in only inside the window and opens the check-in page', async () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const fixture = TestBed.createComponent(MyAppointments);
    fixture.autoDetectChanges();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#btn-mine-checkin-1')).not.toBeNull();
    expect(el.querySelector('#btn-mine-checkin-2')).toBeNull();
    expect(text(el, '#mine-checkin-later-2')).toContain('Check-in opens Wed, Jun 9, 09:30 UTC');

    el.querySelector<HTMLButtonElement>('#btn-mine-checkin-1')!.click();
    await settle(fixture);
    expect(calls).toEqual(['checkIn 1']);
    expect(navigate).toHaveBeenCalledWith(['/check-in', 'dummy-page-1']);
  });

  it('shows on the board why the last check-in failed', async () => {
    data.upcoming[0] = {
      ...data.upcoming[0],
      check_in: {
        id: 4,
        status: 'failed',
        via: 'staff',
        started_at: '2027-06-07T06:00:00Z',
        expires_at: '2027-06-07T07:00:00Z',
        link_channels: ['email'],
        link_sent_to: ['email:p•••@example.com'],
        link_sent_at: '2027-06-07T06:00:00Z',
        link_delivery_note: null,
        failure_code: 'expired',
        failure_reason:
          'The patient did not complete the check-in link sent at 06:00 within 60 minutes.',
        completed_at: null,
        estimate_cents: null,
        currency: 'USD',
        payment_id: null,
      },
    };
    const fixture = TestBed.createComponent(MyAppointments);
    fixture.autoDetectChanges();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el, '#mine-checkin-failed-1')).toContain('did not complete the check-in link');
    expect(text(el, '#btn-mine-checkin-1')).toBe('Try again');
  });

  it("shows the server's reason when it refuses", async () => {
    refuse = new ApiError('Check-in opens 24 hours before your appointment.', 409);
    const fixture = TestBed.createComponent(MyAppointments);
    fixture.autoDetectChanges();
    await settle(fixture);
    fixture.nativeElement.querySelector('#btn-mine-checkin-1').click();
    await settle(fixture);
    expect(toasts.at(-1)).toBe('error: Check-in opens 24 hours before your appointment.');
    expect(fixture.nativeElement.querySelector('#btn-mine-checkin-1')).not.toBeNull();
  });
});
