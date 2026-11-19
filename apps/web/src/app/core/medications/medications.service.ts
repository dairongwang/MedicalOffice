import { Injectable, inject } from '@angular/core';
import { ApiService } from '../account/api.service';

/** One medicine on My medicines (server: schemas.MedicationEntry). */
export interface MedicationEntry {
  name: string;
  strength: string | null;
  form: string | null;
  /** "Lisinopril 10 mg tablet" (or the medication list's name). */
  line: string;
  /** How to take it — the prescription's directions ("Take 1 tablet by mouth once daily"). */
  directions: string | null;
  /** The prescriber's plain-language advice ("Take it with food …"). */
  patient_instructions: string | null;
  /** What it is for (the prescription's diagnosis). */
  indication: string | null;
  status: 'current' | 'stopped';
  /** Prescribed at this office (else it is on the medication list only — another doctor's, …). */
  prescribed: boolean;
  prescription_id: number | null;
  prescription_number: string | null;
  prescribed_on: string | null;
  prescriber_name: string | null;
  quantity: number | null;
  quantity_unit: string | null;
  refills: number | null;
  sent_to_pharmacy: string | null;
  /** Why it was stopped / the list's note. */
  note: string | null;
  updated_at: string | null;
}

/** One line of a prescription issued at the office (server: schemas.MedicationHistoryEntry). */
export interface MedicationHistoryEntry {
  prescription_id: number;
  prescription_number: string;
  prescribed_on: string;
  prescriber_name: string | null;
  line: string;
  directions: string;
  patient_instructions: string | null;
  quantity: number;
  quantity_unit: string;
  refills: number;
  indication: string | null;
}

/** One patient record's medicines — one tab (server: schemas.PatientMedications). */
export interface PatientMedications {
  patient_id: number;
  patient_name: string;
  first_name: string;
  mrn: string;
  date_of_birth: string;
  allergies: { name: string; detail: string | null }[];
  current: MedicationEntry[];
  stopped: MedicationEntry[];
  history: MedicationHistoryEntry[];
  pharmacy_name: string | null;
  pharmacy_phone: string | null;
  last_reviewed_at: string | null;
}

/** Answer of GET /me/medications (server: schemas.MyMedications). */
export interface MyMedications {
  records: PatientMedications[];
  office_name: string;
  office_phone: string | null;
  generated_at: string;
}

/** "90 tablets · 3 refills" / "1 inhaler · no refills". */
export function supplyText(m: {
  quantity: number | null;
  quantity_unit: string | null;
  refills: number | null;
}): string {
  const parts: string[] = [];
  if (m.quantity != null) parts.push(`${m.quantity} ${m.quantity_unit ?? ''}`.trim());
  if (m.refills != null) {
    parts.push(m.refills === 0 ? 'no refills' : `${m.refills} refill${m.refills === 1 ? '' : 's'}`);
  }
  return parts.join(' · ');
}

/** "Mar 1, 2027" from an ISO date-time / date (local calendar day). */
export function shortDate(iso: string | null): string {
  if (!iso) return '';
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T12:00:00`) : new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/**
 * My medicines (services/account/patient_medications.py): for every patient record on the signed-in
 * account, the prescription medicines to take with the dosing, the directions and the prescriber's
 * instructions — My medicines (/patients/medicines) shows one tab per record.
 */
@Injectable({ providedIn: 'root' })
export class MedicationsService {
  private readonly api = inject(ApiService);

  mine(): Promise<MyMedications> {
    return this.api.get<MyMedications>('/me/medications');
  }
}
