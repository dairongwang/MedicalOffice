import { Injectable, inject } from '@angular/core';
import { ApiError, ApiService } from '../account/api.service';

export type ReportKind = 'immunization' | 'annual_exam';
export type ReportPurpose = 'personal' | 'school' | 'work' | 'sports' | 'travel' | 'camp' | 'other';
export type ReportStatus =
  'requested' | 'draft' | 'declined' | 'signed' | 'canceled' | 'superseded';

export const REPORT_KINDS: { id: ReportKind; label: string; tip: string }[] = [
  {
    id: 'immunization',
    label: 'Immunization record',
    tip: 'Every vaccine on your record — for school, work, travel or camp.',
  },
  {
    id: 'annual_exam',
    label: 'Annual health examination report',
    tip: 'The findings of your yearly physical and whether you are fit for the stated purpose (school, work, sports, …).',
  },
];

export const REPORT_PURPOSES: { id: ReportPurpose; label: string }[] = [
  { id: 'personal', label: 'Personal records' },
  { id: 'school', label: 'School' },
  { id: 'work', label: 'Work / employment' },
  { id: 'sports', label: 'Sports' },
  { id: 'travel', label: 'Travel' },
  { id: 'camp', label: 'Camp' },
  { id: 'other', label: 'Other' },
];

export const EXAM_SYSTEMS: [string, string][] = [
  ['general', 'General appearance'],
  ['heent', 'Head, eyes, ears, nose, throat'],
  ['neck', 'Neck and thyroid'],
  ['cardiovascular', 'Heart and circulation'],
  ['respiratory', 'Lungs and breathing'],
  ['abdomen', 'Abdomen'],
  ['musculoskeletal', 'Muscles, bones and joints'],
  ['neurological', 'Nervous system'],
  ['skin', 'Skin'],
];

export const FITNESS_LABELS: Record<string, string> = {
  fit: 'Fit for the stated purpose without restrictions.',
  fit_with_restrictions: 'Fit for the stated purpose with the restrictions below.',
  not_fit: 'Not fit for the stated purpose at this time.',
  further_evaluation: 'Further evaluation is needed before a decision can be made.',
};

/** One dose as the report lists it (server: schemas.ReportImmunization). */
export interface ReportImmunization {
  vaccine: string;
  date_given: string;
  dose: string | null;
  product: string | null;
  site_route: string | null;
  given_by: string | null;
}

/** The part generated from the record (server: schemas.ReportContent — the fields the app shows). */
export interface ReportContent {
  kind: ReportKind;
  title: string;
  office_name: string;
  office_phone: string | null;
  patient_name: string;
  patient_mrn: string;
  patient_date_of_birth: string;
  purpose_label: string;
  generated_for: string | null;
  allergies: string[];
  immunizations: ReportImmunization[];
  exam_visit_number: string | null;
  exam_date: string | null;
  exam_provider: string | null;
  blood_pressure: string | null;
  pulse: string | null;
  temperature: string | null;
  weight: string | null;
  conditions: string[];
  medications: string[];
}

export interface ExamFinding {
  status: 'normal' | 'abnormal' | 'not_examined';
  note: string | null;
}

/** The doctor's part (server: schemas.ReportFindings). */
export interface ReportFindings {
  height: string | null;
  weight: string | null;
  blood_pressure: string | null;
  vision: string | null;
  hearing: string | null;
  systems: Record<string, ExamFinding>;
  fitness: 'fit' | 'fit_with_restrictions' | 'not_fit' | 'further_evaluation' | null;
  restrictions: string | null;
  recommendations: string | null;
  next_exam_due: string | null;
  contraindications: string | null;
  vaccines_due: string | null;
}

/** A report as the patient sees it (server: schemas.PatientReportOut — content only once signed). */
export interface PatientReport {
  id: number;
  /** RPT-000042 */
  number: string;
  kind: ReportKind;
  kind_label: string;
  status: ReportStatus;
  status_label: string;
  version: number;
  patient_id: number;
  patient_name: string;
  patient_mrn: string;
  purpose: ReportPurpose;
  purpose_label: string;
  purpose_detail: string | null;
  needed_by: string | null;
  request_note: string | null;
  requested_via: 'patient' | 'office';
  requested_at: string;
  doctor_name: string | null;
  content: ReportContent | null;
  findings: ReportFindings;
  doctor_note: string | null;
  signer_name: string | null;
  signed_at: string | null;
  signature_png: string | null;
  verification: string | null;
  cancel_reason: string | null;
  amendment_reason: string | null;
  sent_at: string | null;
  patient_viewed_at: string | null;
}

export interface ReportRequest {
  patient_id: number;
  kind: ReportKind;
  purpose: ReportPurpose;
  purpose_detail: string | null;
  needed_by: string | null;
  note: string | null;
}

/**
 * Reports the office prepares on request and a doctor signs (services/account/patient_reports.py):
 * the immunization record and the annual health examination report of one of the account's records.
 */
@Injectable({ providedIn: 'root' })
export class ReportsService {
  private readonly api = inject(ApiService);

  list(): Promise<PatientReport[]> {
    return this.api.get<PatientReport[]>('/me/reports');
  }

  request(body: ReportRequest): Promise<PatientReport> {
    return this.api.post<PatientReport>('/me/reports', body);
  }

  /** One report (a signed one with everything; the first opening is recorded). */
  get(id: number): Promise<PatientReport> {
    return this.api.get<PatientReport>(`/me/reports/${id}`);
  }

  /** Withdraw a request that is still open. */
  cancel(id: number): Promise<PatientReport> {
    return this.api.post<PatientReport>(`/me/reports/${id}/cancel`);
  }

  /** The signed report as a PDF (the session token goes along). */
  async pdf(id: number): Promise<Blob> {
    const token = this.api.token();
    let res: Response;
    try {
      res = await fetch(`${this.api.base}/me/reports/${id}/pdf`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        cache: 'no-store',
      });
    } catch {
      throw new ApiError("You're offline, or the server can't be reached right now.", 0);
    }
    if (!res.ok) {
      let detail = `Request failed (${res.status})`;
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* no body */
      }
      throw new ApiError(detail, res.status);
    }
    return res.blob();
  }
}

/** Open (requested / prepared / returned) — the patient may still withdraw it. */
export function isOpen(r: PatientReport): boolean {
  return r.status === 'requested' || r.status === 'draft' || r.status === 'declined';
}
