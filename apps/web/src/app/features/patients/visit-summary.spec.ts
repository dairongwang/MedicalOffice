import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AccountService } from '../../core/account/account.service';
import { ApiError } from '../../core/account/api.service';
import {
  type Appointment,
  AppointmentsService,
  type MyAppointments as MyAppointmentsData,
  type VisitSummary,
} from '../../core/appointments/appointments.service';
import { PatientsService } from '../../core/patients/patients.service';
import { PaymentMethodsService } from '../../core/payments/payment-methods.service';
import { ShellService } from '../../core/ui/shell.service';
import { ToastService } from '../../core/ui/toast.service';
import { MyAppointments } from './my-appointments';

const PAST: Appointment = {
  id: 7,
  number: 'APT-000007',
  status: 'completed',
  start_at: '2027-03-01T10:00:00Z',
  end_at: '2027-03-01T10:30:00Z',
  start_local: 'Monday, March 1, 2027 · 10:00',
  timezone: 'UTC',
  reason: 'Blood pressure check (dummy)',
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
  created_at: '2027-02-01T00:00:00Z',
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
  visit_summary: {
    id: 3,
    number: 'VS-000003',
    version: 1,
    status: 'signed',
    signed_at: '2027-03-01T10:40:00Z',
    signer_name: 'Alex Example',
    sent_at: '2027-03-01T10:41:00Z',
    patient_viewed_at: null,
  },
};

const SUMMARY: VisitSummary = {
  id: 3,
  number: 'VS-000003',
  appointment_id: 7,
  appointment_number: 'APT-000007',
  version: 1,
  status: 'signed',
  content: {
    visit_number: 'APT-000007',
    visit_date: 'Monday, March 1, 2027 · 10:00',
    visit_start_at: '2027-03-01T10:00:00Z',
    timezone: 'UTC',
    office_name: 'Medic Office',
    office_address: null,
    office_phone: '+1 555 010 0000',
    provider_name: 'Dr. Alex Example',
    provider_role: 'Doctor',
    provider_specialty: 'General practice',
    patient_name: 'Pat Dummy',
    patient_mrn: 'P-000001',
    patient_date_of_birth: '1985-03-14',
    reason: 'Blood pressure check (dummy)',
    vitals: 'BP 146/92 mmHg · pulse 78/min',
    vitals_note: null,
    diagnoses: ['Essential hypertension (I10) — primary'],
    prescriptions: ['Lisinopril 10 mg tablet — Take 1 tablet by mouth once daily'],
    medications: ['Lisinopril — 10 mg tablet'],
    allergies: [],
    referrals: [],
    tests: [],
  },
  assessment: 'Blood pressure not at goal.',
  plan: 'Continue lisinopril.',
  instructions: 'Check your blood pressure twice a week.',
  follow_up: 'In three months.',
  provider_name: 'Dr. Alex Example',
  signer_name: 'Alex Example',
  signed_at: '2027-03-01T10:40:00Z',
  signature_png: 'data:image/png;base64,iVBORw0KGgo=',
  verification: 'VS-000003-ABCDEF1234',
  amendment_reason: null,
  sent_at: '2027-03-01T10:41:00Z',
  sent_channels: ['email'],
  patient_viewed_at: null,
};

describe('MyAppointments — the visit summary the doctor signed', () => {
  let data: MyAppointmentsData;
  let calls: string[];
  let refuse: ApiError | null;

  beforeEach(() => {
    calls = [];
    refuse = null;
    data = { patients: [], upcoming: [], past: [PAST] };
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: AccountService, useValue: { signedIn: signal(true) } },
        { provide: ShellService, useValue: { openAccount: () => undefined } },
        { provide: PatientsService, useValue: {} },
        { provide: PaymentMethodsService, useValue: { list: () => Promise.resolve([]) } },
        { provide: ToastService, useValue: { show: () => undefined, error: () => undefined } },
        {
          provide: AppointmentsService,
          useValue: {
            mine: () => Promise.resolve(data),
            visitSummary: (id: number) => {
              calls.push(`summary ${id}`);
              return refuse ? Promise.reject(refuse) : Promise.resolve(SUMMARY);
            },
            visitSummaryPdf: (id: number) => {
              calls.push(`pdf ${id}`);
              return Promise.resolve(new Blob(['%PDF-1.4'], { type: 'application/pdf' }));
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

  it('marks a new summary, opens it under the visit and downloads the PDF', async () => {
    const fixture = TestBed.createComponent(MyAppointments);
    fixture.autoDetectChanges();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#mine-past-7 .tag.new-summary')?.textContent).toContain('new summary');

    el.querySelector<HTMLButtonElement>('#btn-mine-summary-7')!.click();
    await settle(fixture);
    expect(calls).toEqual(['summary 7']);
    const view = el.querySelector('#visit-summary-7')!;
    expect(view.textContent).toContain('Essential hypertension (I10) — primary');
    expect(view.textContent).toContain('Check your blood pressure twice a week.');
    expect(view.textContent).toContain('No known allergies recorded.');
    expect(view.textContent).toContain('Electronically signed by Dr. Alex Example');
    expect(view.textContent).toContain('VS-000003-ABCDEF1234');
    expect(el.querySelector('#mine-past-7 .tag.new-summary')).toBeNull(); // read now

    URL.createObjectURL ??= () => 'blob:dummy';
    URL.revokeObjectURL ??= () => undefined;
    el.querySelector<HTMLButtonElement>('#btn-visit-summary-pdf-7')!.click();
    await settle(fixture);
    expect(calls).toEqual(['summary 7', 'pdf 7']);

    el.querySelector<HTMLButtonElement>('#btn-mine-summary-7')!.click();
    await settle(fixture);
    expect(el.querySelector('#visit-summary-7')).toBeNull();
  });

  it('offers no summary for a visit without a signed one, and explains a refusal', async () => {
    data.past = [
      { ...PAST, visit_summary: null },
      { ...PAST, id: 8, number: 'APT-000008' },
    ];
    refuse = new ApiError('There is no visit summary for this visit yet.', 404);
    const fixture = TestBed.createComponent(MyAppointments);
    fixture.autoDetectChanges();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#btn-mine-summary-7')).toBeNull();
    el.querySelector<HTMLButtonElement>('#btn-mine-summary-8')!.click();
    await settle(fixture);
    expect(el.querySelector('#visit-summary-8 .callout.error')?.textContent).toContain(
      'There is no visit summary for this visit yet.',
    );
  });
});
