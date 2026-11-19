import { TestBed } from '@angular/core/testing';
import { ApiService } from '../account/api.service';
import {
  AppointmentsService,
  isoDate,
  providerLabel,
  weekdayOf,
  weekdaysLabel,
} from './appointments.service';

describe('AppointmentsService', () => {
  let service: AppointmentsService;
  let calls: { method: string; path: string; body?: unknown; opts: unknown }[];

  beforeEach(() => {
    calls = [];
    TestBed.configureTestingModule({
      providers: [
        {
          provide: ApiService,
          useValue: {
            get: (path: string, opts: unknown) => {
              calls.push({ method: 'GET', path, opts });
              return Promise.resolve(
                path.startsWith('/appointments/providers')
                  ? {
                      providers: [],
                      slot_minutes: 30,
                      max_days_ahead: 90,
                      timezone: 'UTC',
                      payment_method_required: true,
                      self_pay_visit_cents: 15000,
                      currency: 'USD',
                    }
                  : { provider_id: 1, date: '2027-06-07', timezone: 'UTC', slots: [], note: null },
              );
            },
            post: (path: string, body: unknown, opts: unknown) => {
              calls.push({ method: 'POST', path, body, opts });
              return Promise.resolve({ id: 7, number: 'APT-000007', status: 'scheduled' });
            },
          },
        },
      ],
    });
    service = TestBed.inject(AppointmentsService);
  });

  const identity = {
    first_name: 'Pat',
    last_name: 'Dummy',
    date_of_birth: '1985-03-14',
    email: null,
    phone: null,
  };

  it('talks to the public endpoints without a bearer token (booking sends it: the payment hold)', async () => {
    await service.providers();
    await service.slots(3, '2027-06-07');
    await service.lookup(identity);
    const booked = await service.book({
      ...identity,
      provider_id: 3,
      start_at: '2027-06-07T09:00:00Z',
      reason: null,
    });
    await service.cancel(booked.id, identity);
    expect(calls.map((c) => c.path)).toEqual([
      '/appointments/providers',
      '/appointments/slots?provider_id=3&date=2027-06-07',
      '/appointments/lookup',
      '/appointments',
      '/appointments/7/cancel',
    ]);
    expect(
      calls
        .filter((c) => c.path !== '/appointments')
        .every((c) => (c.opts as { auth: boolean }).auth === false),
    ).toBe(true);
    expect((calls[3].opts as { auth: boolean }).auth).toBe(true);
    expect(calls[3].body).toMatchObject({ provider_id: 3, first_name: 'Pat' });
  });

  it('keeps the identity typed on the registration page for the scheduler', () => {
    expect(service.prefill()).toBeNull();
    service.prefill.set(identity);
    expect(service.prefill()?.last_name).toBe('Dummy');
  });

  it('formats providers and dates', () => {
    expect(
      providerLabel({
        id: 1,
        staff_number: 'W-000001',
        first_name: 'Alex',
        last_name: 'Example',
        role: 'doctor',
        specialty: 'General practice',
        weekdays: [0, 1, 2, 3, 4],
      }),
    ).toBe('Dr. Alex Example — Doctor · General practice');
    expect(
      providerLabel({
        id: 2,
        staff_number: 'W-000002',
        first_name: 'Sam',
        last_name: 'Sample',
        role: 'nurse',
        specialty: null,
        weekdays: [0],
      }),
    ).toBe('Sam Sample — Nurse');
    expect(weekdaysLabel([0, 2, 4])).toBe('Mon, Wed, Fri');
    expect(isoDate(new Date(2027, 5, 7, 23, 30))).toBe('2027-06-07');
    expect(weekdayOf('2027-06-07')).toBe(0); // a Monday
    expect(weekdayOf('2027-06-13')).toBe(6); // a Sunday
  });
});
