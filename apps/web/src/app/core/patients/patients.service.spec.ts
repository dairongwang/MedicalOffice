import { TestBed } from '@angular/core/testing';
import { ApiService } from '../account/api.service';
import {
  PatientsService,
  describeDestinations,
  describeInsurance,
  describeMatch,
} from './patients.service';

describe('PatientsService', () => {
  let service: PatientsService;
  let calls: { method: string; path: string; body: unknown; opts: unknown }[];

  beforeEach(() => {
    calls = [];
    TestBed.configureTestingModule({
      providers: [
        {
          provide: ApiService,
          useValue: {
            post: (path: string, body: unknown, opts: unknown) => {
              calls.push({ method: 'POST', path, body, opts });
              return Promise.resolve(
                path.endsWith('/check')
                  ? { is_new: true, matched_on: [], patient: null }
                  : { status: 'new', matched_on: [], patient: { id: 1, mrn: 'P-000001' } },
              );
            },
          },
        },
      ],
    });
    service = TestBed.inject(PatientsService);
  });

  it('checks anonymously (no bearer token) and registers with the account when signed in', async () => {
    const identity = {
      first_name: 'Ann',
      last_name: 'Lee',
      date_of_birth: '1990-01-01',
      email: null,
      phone: null,
    };
    const check = await service.check(identity);
    expect(check.is_new).toBe(true);
    expect(calls[0]).toMatchObject({
      path: '/patients/check',
      body: identity,
      opts: { auth: false },
    });

    const result = await service.register({
      ...identity,
      sex: null,
      address_line: null,
      city: null,
      state: null,
      postal_code: null,
      country: null,
      insurance_provider: null,
      insurance_plan_name: null,
      insurance_member_id: null,
      insurance_group_number: null,
      insurance_policyholder_name: null,
      insurance_policyholder_relationship: null,
      insurance_card_front: null,
      insurance_card_back: null,
      emergency_contact_name: null,
      emergency_contact_phone: null,
      reason_for_visit: null,
      consent: true,
    });
    expect(result.status).toBe('new');
    // Registration sends the bearer token when there is one: a signed-in patient (a parent)
    // gets the new record on their account; anonymous registration works the same without it.
    expect(calls[1]).toMatchObject({ path: '/patients', opts: { auth: true } });
  });

  it('summarises the insurance on a record', () => {
    expect(
      describeInsurance({
        insurance_provider: 'Example Health',
        insurance_plan_name: 'PPO Silver',
        insurance_member_id: 'XYZ123',
        insurance_group_number: '77',
        insurance_policyholder_name: 'Sam Lee',
        insurance_policyholder_relationship: 'parent',
      }),
    ).toBe(
      'Example Health · PPO Silver · member XYZ123 · group 77 · policyholder: Sam Lee (parent / guardian)',
    );
    expect(
      describeInsurance({
        insurance_provider: 'Example Health',
        insurance_plan_name: null,
        insurance_member_id: 'XYZ123',
        insurance_group_number: null,
        insurance_policyholder_name: null,
        insurance_policyholder_relationship: 'self',
      }),
    ).toBe('Example Health · member XYZ123');
    expect(
      describeInsurance({
        insurance_provider: null,
        insurance_plan_name: null,
        insurance_member_id: null,
        insurance_group_number: null,
        insurance_policyholder_name: null,
        insurance_policyholder_relationship: null,
      }),
    ).toBe('');
  });

  it('describes where a code went', () => {
    expect(describeDestinations(['email:p•••@example.com', 'sms:+•••••••0301'])).toBe(
      'e-mail (p•••@example.com) and text message (+•••••••0301)',
    );
  });

  it('describes what matched in plain words', () => {
    expect(describeMatch(['name_dob'])).toBe('your name and date of birth');
    expect(describeMatch(['email_dob', 'phone_dob'])).toBe(
      'your e-mail address and date of birth and your phone number and date of birth',
    );
    expect(describeMatch([])).toBe('your details');
  });
});
