import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AccountService } from '../../core/account/account.service';
import { ApiError } from '../../core/account/api.service';
import { AppointmentsService } from '../../core/appointments/appointments.service';
import {
  type PatientReport,
  type ReportRequest,
  ReportsService,
} from '../../core/reports/reports.service';
import { ShellService } from '../../core/ui/shell.service';
import { ToastService } from '../../core/ui/toast.service';
import { ReportsPage } from './reports-page';

const PAT = {
  id: 1,
  mrn: 'P-000001',
  first_name: 'Pat',
  last_name: 'Dummy',
  date_of_birth: '1985-03-14',
  created_at: '2027-01-01T00:00:00Z',
  email: 'dummy-patient@example.com',
  phone: null,
  linked_by: 'email' as const,
};

function report(id: number, over: Partial<PatientReport> = {}): PatientReport {
  return {
    id,
    number: `RPT-${String(id).padStart(6, '0')}`,
    kind: 'annual_exam',
    kind_label: 'Annual health examination report',
    status: 'requested',
    status_label: 'Requested — the office is preparing it',
    version: 1,
    patient_id: 1,
    patient_name: 'Pat Dummy',
    patient_mrn: 'P-000001',
    purpose: 'sports',
    purpose_label: 'Sports — Exampleville Running Club (dummy)',
    purpose_detail: 'Exampleville Running Club (dummy)',
    needed_by: '2027-07-01',
    request_note: null,
    requested_via: 'patient',
    requested_at: '2027-06-01T10:00:00Z',
    doctor_name: null,
    content: null,
    findings: {
      height: null,
      weight: null,
      blood_pressure: null,
      vision: null,
      hearing: null,
      systems: {},
      fitness: null,
      restrictions: null,
      recommendations: null,
      next_exam_due: null,
      contraindications: null,
      vaccines_due: null,
    },
    doctor_note: null,
    signer_name: null,
    signed_at: null,
    signature_png: null,
    verification: null,
    cancel_reason: null,
    amendment_reason: null,
    sent_at: null,
    patient_viewed_at: null,
    ...over,
  };
}

const SIGNED = report(2, {
  kind: 'immunization',
  kind_label: 'Immunization record',
  status: 'signed',
  status_label: 'Ready — signed by the doctor',
  purpose: 'work',
  purpose_label: 'Work / employment — Example Corp onboarding (dummy)',
  doctor_name: 'Dr. Alex Example',
  signer_name: 'Alex Example',
  signed_at: '2027-06-02T10:00:00Z',
  verification: 'RPT-000002-ABCDEF1234',
  signature_png: 'data:image/png;base64,iVBORw0KGgo=',
  content: {
    kind: 'immunization',
    title: 'Immunization record',
    office_name: 'Medic Office',
    office_phone: null,
    patient_name: 'Pat Dummy',
    patient_mrn: 'P-000001',
    patient_date_of_birth: '1985-03-14',
    purpose_label: 'Work / employment',
    generated_for: 'Dr. Alex Example',
    allergies: [],
    immunizations: [
      {
        vaccine: 'Hepatitis B',
        date_given: '1998-03-02',
        dose: '3 of 3',
        product: null,
        site_route: null,
        given_by: 'Exampleville School Clinic (dummy)',
      },
    ],
    exam_visit_number: null,
    exam_date: null,
    exam_provider: null,
    blood_pressure: null,
    pulse: null,
    temperature: null,
    weight: null,
    conditions: [],
    medications: [],
  },
});

describe('ReportsPage — reports signed by a doctor, on request', () => {
  let reports: PatientReport[];
  let calls: string[];
  let refuse: ApiError | null;

  beforeEach(() => {
    calls = [];
    refuse = null;
    reports = [report(1), SIGNED];
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: AccountService, useValue: { signedIn: signal(true) } },
        { provide: ShellService, useValue: { openAccount: () => undefined } },
        { provide: ToastService, useValue: { show: () => undefined, error: () => undefined } },
        {
          provide: AppointmentsService,
          useValue: { mine: () => Promise.resolve({ patients: [PAT], upcoming: [], past: [] }) },
        },
        {
          provide: ReportsService,
          useValue: {
            list: () => Promise.resolve(reports),
            get: (id: number) => {
              calls.push(`get ${id}`);
              return Promise.resolve({ ...SIGNED, patient_viewed_at: '2027-06-03T00:00:00Z' });
            },
            pdf: (id: number) => {
              calls.push(`pdf ${id}`);
              return Promise.resolve(new Blob(['%PDF-1.4']));
            },
            cancel: (id: number) => {
              calls.push(`cancel ${id}`);
              reports = reports.map((r) => (r.id === id ? { ...r, status: 'canceled' } : r));
              return Promise.resolve(reports[0]);
            },
            request: (body: ReportRequest) => {
              calls.push(
                `request ${body.patient_id} ${body.kind} ${body.purpose} ${body.purpose_detail}`,
              );
              if (refuse) return Promise.reject(refuse);
              const r = report(3, { kind: body.kind, kind_label: 'Immunization record' });
              reports = [r, ...reports];
              return Promise.resolve(r);
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

  it('lists the reports, shows a signed one and its PDF, withdraws an open request', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    URL.createObjectURL ??= () => 'blob:dummy';
    URL.revokeObjectURL ??= () => undefined;
    const fixture = TestBed.createComponent(ReportsPage);
    fixture.autoDetectChanges();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#report-1')?.textContent).toContain('the office is preparing it');
    expect(el.querySelector('#btn-report-view-1')).toBeNull();
    expect(el.querySelector('#report-2 .tag.new-summary')).not.toBeNull();

    el.querySelector<HTMLButtonElement>('#btn-report-view-2')!.click();
    await settle(fixture);
    const view = el.querySelector('#report-view-2')!;
    expect(view.textContent).toContain('Hepatitis B');
    expect(view.textContent).toContain('Electronically signed by Dr. Alex Example');
    expect(view.textContent).toContain('RPT-000002-ABCDEF1234');
    expect(el.querySelector('#report-2 .tag.new-summary')).toBeNull();

    el.querySelector<HTMLButtonElement>('#btn-report-pdf-2')!.click();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('#btn-report-cancel-1')!.click();
    await settle(fixture);
    expect(calls).toEqual(['get 2', 'pdf 2', 'cancel 1']);
  });

  it('asks for a report and explains a refusal', async () => {
    const fixture = TestBed.createComponent(ReportsPage);
    fixture.autoDetectChanges();
    await settle(fixture);
    const el: HTMLElement = fixture.nativeElement;
    const detail = el.querySelector<HTMLInputElement>('#report-detail')!;
    detail.value = 'Dummy High School';
    detail.dispatchEvent(new Event('input'));
    const purpose = el.querySelector<HTMLSelectElement>('#report-purpose')!;
    purpose.value = 'school';
    purpose.dispatchEvent(new Event('change'));
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('#btn-report-request')!.click();
    await settle(fixture);
    expect(calls).toEqual(['request 1 immunization school Dummy High School']);
    expect(el.querySelector('#report-3')).not.toBeNull();

    refuse = new ApiError(
      'An immunization record for Pat is already in progress (RPT-000003).',
      409,
    );
    el.querySelector<HTMLButtonElement>('#btn-report-request')!.click();
    await settle(fixture);
    expect(el.querySelector('#report-request-error')?.textContent).toContain('already in progress');
  });
});
