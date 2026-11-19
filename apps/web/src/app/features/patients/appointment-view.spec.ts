import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { ApiError } from '../../core/account/api.service';
import {
  type Appointment,
  AppointmentsService,
} from '../../core/appointments/appointments.service';
import { AppointmentView } from './appointment-view';

const APPOINTMENT: Appointment = {
  id: 7,
  number: 'APT-000007',
  status: 'scheduled',
  start_at: '2027-06-07T09:30:00Z',
  end_at: '2027-06-07T10:00:00Z',
  start_local: 'Monday, June 7, 2027 · 09:30',
  timezone: 'UTC',
  reason: 'Check-up',
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
  office_address: '1 Example Street',
  link: null,
  calendar: {
    ics_url: '/api/v1/appointments/by-token/dummy-tok/calendar.ics',
    google_url: 'https://calendar.google.com/calendar/render?action=TEMPLATE&text=x',
    outlook_url: 'https://outlook.live.com/calendar/0/deeplink/compose?subject=x',
  },
  confirmed_at: null,
  confirmed_via: null,
  reminder_sent_at: '2027-06-06T09:30:00Z',
  reminder_channels: ['email', 'sms'],
  reminder_note: 'E-mail to p•••@example.com (built-in fake — see the test inbox)',
  payment: 'insurance',
  coverage_status: 'active',
  self_pay_agreed_at: null,
};

describe('AppointmentView (the page behind the link)', () => {
  const calls: string[] = [];
  let known = true;
  let current: Appointment = APPOINTMENT;

  beforeEach(() => {
    calls.length = 0;
    known = true;
    current = APPOINTMENT;
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        {
          provide: AppointmentsService,
          useValue: {
            apiUrl: (p: string) => 'http://localhost:8000' + p,
            byToken: (token: string) => {
              calls.push(`byToken ${token}`);
              return known
                ? Promise.resolve(current)
                : Promise.reject(new ApiError('This appointment link is not valid.', 404));
            },
            cancelByToken: (token: string) => {
              calls.push(`cancel ${token}`);
              return Promise.resolve({ ...APPOINTMENT, status: 'canceled' });
            },
            checkInByToken: (token: string) => {
              calls.push(`checkIn ${token}`);
              return Promise.resolve({ token: 'dummy-page', url: 'x', check_in: null });
            },
            confirmByToken: (token: string) => {
              calls.push(`confirm ${token}`);
              return Promise.resolve({
                ...APPOINTMENT,
                confirmed_at: '2027-06-06T10:00:00Z',
                confirmed_via: 'link',
              });
            },
          },
        },
      ],
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  async function mount(action?: string) {
    const fixture = TestBed.createComponent(AppointmentView);
    fixture.componentRef.setInput('token', 'dummy-tok');
    if (action) fixture.componentRef.setInput('action', action);
    fixture.autoDetectChanges();
    await fixture.whenStable();
    await new Promise((r) => setTimeout(r, 0));
    fixture.detectChanges();
    return fixture;
  }
  const text = (el: Element, sel: string) => el.querySelector(sel)?.textContent?.trim() ?? '';

  it('shows the appointment with add-to-calendar links and cancels', async () => {
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    expect(calls).toEqual(['byToken dummy-tok']);
    expect(text(el, '#view-heading')).toBe('Monday, June 7, 2027 · 09:30');
    expect(text(el, '#view-number')).toBe('APT-000007');
    expect(text(el, '#view-provider')).toContain('Dr. Alex Example');
    expect(text(el, '#view-status')).toBe('Scheduled');
    expect(el.querySelector('#btn-view-ics')?.getAttribute('href')).toBe(
      'http://localhost:8000/api/v1/appointments/by-token/dummy-tok/calendar.ics',
    );
    expect(el.querySelector('#btn-view-ics')?.hasAttribute('download')).toBe(true);
    expect(el.querySelector('#btn-view-google')?.getAttribute('href')).toContain(
      'calendar.google.com',
    );
    expect(el.querySelector('#btn-view-outlook')?.getAttribute('href')).toContain(
      'outlook.live.com',
    );

    el.querySelector<HTMLButtonElement>('#btn-view-cancel')!.click();
    await new Promise((r) => setTimeout(r, 0));
    fixture.detectChanges();
    expect(calls.at(-1)).toBe('cancel dummy-tok');
    expect(text(el, '#view-heading')).toBe('This appointment was canceled');
    expect(text(el, '#view-status')).toBe('Canceled');
    expect(el.querySelector('#view-calendar')).toBeNull();
    expect(el.querySelector('#btn-view-cancel')).toBeNull();
  });

  it('asks "are you coming?" after the reminder and confirms', async () => {
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el, '#view-reminder')).toContain('Sent');
    expect(el.querySelector('#view-confirm-ask')).not.toBeNull();
    el.querySelector<HTMLButtonElement>('#btn-view-confirm')!.click();
    await new Promise((r) => setTimeout(r, 0));
    fixture.detectChanges();
    expect(calls.at(-1)).toBe('confirm dummy-tok');
    expect(el.querySelector('#view-confirmed')).not.toBeNull();
    expect(el.querySelector('#view-confirm-ask')).toBeNull();
    expect(text(el, '#view-status')).toBe('Scheduled · confirmed');
  });

  it("confirms on opening the reminder's confirm link, and asks before cancelling", async () => {
    let fixture = await mount('confirm');
    await new Promise((r) => setTimeout(r, 0));
    fixture.detectChanges();
    expect(calls).toEqual(['byToken dummy-tok', 'confirm dummy-tok']);
    expect(fixture.nativeElement.querySelector('#view-confirmed')).not.toBeNull();

    calls.length = 0;
    fixture = await mount('cancel');
    const el: HTMLElement = fixture.nativeElement;
    expect(calls).toEqual(['byToken dummy-tok']);
    expect(el.querySelector('#view-cancel-asked')).not.toBeNull();
    expect(el.querySelector('#btn-view-cancel')).not.toBeNull();
  });

  it('explains an invalid link and offers the lookup page', async () => {
    known = false;
    const fixture = await mount();
    const el: HTMLElement = fixture.nativeElement;
    expect(text(el, '#view-error')).toContain('not valid');
    expect(el.querySelector('#view-error a')?.getAttribute('href')).toBe('/patients/appointments');
  });

  describe('check-in (from 24 hours before the visit until it ends)', () => {
    const WINDOW = {
      check_in_opens_at: '2027-06-06T09:30:00Z',
      check_in_closes_at: '2027-06-07T10:00:00Z',
    };
    afterEach(() => vi.useRealTimers());

    it('offers check-in inside the window and opens the check-in page', async () => {
      vi.useFakeTimers({ now: new Date('2027-06-07T09:00:00Z'), toFake: ['Date'] });
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
      current = { ...APPOINTMENT, ...WINDOW, can_check_in: true };
      const fixture = await mount();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('#view-check-in')).not.toBeNull();
      el.querySelector<HTMLButtonElement>('#btn-view-checkin')!.click();
      await new Promise((r) => setTimeout(r, 0));
      expect(calls.at(-1)).toBe('checkIn dummy-tok');
      expect(navigate).toHaveBeenCalledWith(['/check-in', 'dummy-page']);
    });

    it("opens the check-in page from the reminder's check-in link once the window is open", async () => {
      vi.useFakeTimers({ now: new Date('2027-06-07T08:00:00Z'), toFake: ['Date'] });
      const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
      current = { ...APPOINTMENT, ...WINDOW, can_check_in: true };
      await mount('check-in');
      await new Promise((r) => setTimeout(r, 0));
      expect(calls).toEqual(['byToken dummy-tok', 'checkIn dummy-tok']);
      expect(navigate).toHaveBeenCalledWith(['/check-in', 'dummy-page']);
    });

    it('shows why the last check-in failed, with "Try again" in the window', async () => {
      vi.useFakeTimers({ now: new Date('2027-06-07T09:00:00Z'), toFake: ['Date'] });
      current = {
        ...APPOINTMENT,
        ...WINDOW,
        check_in: {
          id: 2,
          status: 'failed',
          via: 'link',
          started_at: '2027-06-07T08:00:00Z',
          expires_at: '2027-06-07T09:00:00Z',
          link_channels: [],
          link_sent_to: [],
          link_sent_at: null,
          link_delivery_note: null,
          failure_code: 'no_payment_method',
          failure_reason:
            'No validated payment method on file to pre-authorize the estimated $40.00.',
          completed_at: null,
          estimate_cents: 4000,
          currency: 'USD',
          payment_id: null,
        },
      };
      const el: HTMLElement = (await mount()).nativeElement;
      expect(text(el, '#view-check-in-failure')).toContain('No validated payment method');
      expect(text(el, '#btn-view-checkin')).toBe('Try again');
    });

    it('explains when check-in opens — and does not try earlier', async () => {
      vi.useFakeTimers({ now: new Date('2027-06-05T09:00:00Z'), toFake: ['Date'] });
      current = { ...APPOINTMENT, ...WINDOW };
      let fixture = await mount('check-in');
      let el: HTMLElement = fixture.nativeElement;
      expect(calls).toEqual(['byToken dummy-tok']);
      expect(el.querySelector('#view-check-in-later')?.classList).toContain('callout');
      expect(text(el, '#view-check-in-later')).toContain('Sun, Jun 6, 09:30');
      expect(el.querySelector('#btn-view-checkin')).toBeNull();

      fixture = await mount();
      el = fixture.nativeElement;
      expect(text(el, '#view-check-in-later')).toContain('Check-in opens Sun, Jun 6, 09:30 UTC');
      expect(el.querySelector('#view-confirm-ask')).not.toBeNull(); // "are you coming?" still asked
    });

    it('sends the patient to the front desk after the visit', async () => {
      vi.useFakeTimers({ now: new Date('2027-06-07T10:30:00Z'), toFake: ['Date'] });
      current = { ...APPOINTMENT, ...WINDOW };
      const el: HTMLElement = (await mount()).nativeElement;
      expect(text(el, '#view-check-in-desk')).toContain('front desk');
      expect(el.querySelector('#btn-view-checkin')).toBeNull();
    });

    it('shows a check-in done by the office', async () => {
      current = {
        ...APPOINTMENT,
        ...WINDOW,
        checked_in_at: '2027-06-07T09:20:00Z',
        checked_in_via: 'staff',
      };
      const el: HTMLElement = (await mount()).nativeElement;
      expect(text(el, '#view-checked-in')).toContain(
        "Checked in with the front desk's link at 09:20 UTC",
      );
    });
  });
});
