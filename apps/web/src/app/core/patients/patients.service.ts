import { Injectable, inject, signal } from '@angular/core';
import { ApiService } from '../account/api.service';

export type PatientSex = 'female' | 'male' | 'other' | 'unspecified';

/** What identifies a patient for the "are you new here?" check (server: schemas.PatientIdentity). */
export interface PatientIdentity {
  first_name: string;
  last_name: string;
  /** ISO date, YYYY-MM-DD */
  date_of_birth: string;
  email: string | null;
  phone: string | null;
}

/** Whose name the insurance policy is in. */
export type InsuranceRelationship = 'self' | 'spouse' | 'parent' | 'child' | 'other';
export type InsuranceCardSide = 'front' | 'back';

export const INSURANCE_RELATIONSHIPS: { value: InsuranceRelationship; label: string }[] = [
  { value: 'self', label: 'Myself' },
  { value: 'spouse', label: 'My spouse / partner' },
  { value: 'parent', label: 'My parent / guardian' },
  { value: 'child', label: 'My child' },
  { value: 'other', label: 'Someone else' },
];

/** The full intake form (server: schemas.PatientIn). */
export interface PatientForm extends PatientIdentity {
  sex: PatientSex | null;
  address_line: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country: string | null;
  /** Insurance section — everything optional (self-pay, or "I'll show my card at the desk"). */
  insurance_provider: string | null;
  insurance_plan_name: string | null;
  insurance_member_id: string | null;
  insurance_group_number: string | null;
  insurance_policyholder_name: string | null;
  insurance_policyholder_relationship: InsuranceRelationship | null;
  /** Photos of the card as image data URLs (data:image/jpeg;base64,…), see card-photo.ts. */
  insurance_card_front: string | null;
  insurance_card_back: string | null;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  reason_for_visit: string | null;
  consent: boolean;
}

/**
 * What a patient may change themselves (server: schemas.PatientUpdate): everything but the date
 * of birth. Sent in full — a blank field clears it. Card photos: a data URL replaces the side,
 * `remove_insurance_card_*` drops it, otherwise it is kept.
 */
export interface PatientUpdate {
  first_name: string;
  last_name: string;
  email: string | null;
  phone: string | null;
  sex: PatientSex | null;
  address_line: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country: string | null;
  insurance_provider: string | null;
  insurance_plan_name: string | null;
  insurance_member_id: string | null;
  insurance_group_number: string | null;
  insurance_policyholder_name: string | null;
  insurance_policyholder_relationship: InsuranceRelationship | null;
  insurance_card_front: string | null;
  insurance_card_back: string | null;
  remove_insurance_card_front: boolean;
  remove_insurance_card_back: boolean;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  reason_for_visit: string | null;
}

/** A code went to the contact details on the record (server: schemas.PatientUpdateChallenge). */
export interface PatientUpdateChallenge {
  challenge: string;
  patient: PatientSummary;
  /** Masked, e.g. "email:p•••@example.com", "sms:+•••••••0301". */
  sent_to: string[];
  channels: string[];
  expires_in: number;
  fake: boolean;
  outbox_url: string | null;
}

export interface PatientUpdateSession {
  edit_token: string;
  expires_in: number;
  patient: PatientRecord;
}

/** What the office reveals about an existing record: number, name, date of birth. */
export interface PatientSummary {
  id: number;
  /** Medical record number, e.g. P-000042 */
  mrn: string;
  first_name: string;
  last_name: string;
  date_of_birth: string;
  created_at: string;
}

/** One side of the insurance card on file (metadata; the image itself is for administrators). */
export interface InsuranceCardInfo {
  side: InsuranceCardSide;
  content_type: string;
  size_bytes: number;
  uploaded_at: string;
  url: string;
}

export type VerificationStatus =
  'active' | 'inactive' | 'pending' | 'not_found' | 'incomplete' | 'unavailable';
export type DeductibleStatus = 'met' | 'partially_met' | 'not_met' | 'none';

/**
 * One eligibility & benefits check of the insurance (server: schemas.InsuranceVerificationOut):
 * coverage and effective dates, copay, deductible status, coinsurance, referral and
 * prior-authorization requirements. Amounts are integer cents in `currency`; null = not said.
 */
export interface InsuranceVerification {
  id: number;
  status: VerificationStatus;
  /** One line for a human, e.g. "Active PPO coverage · $25 copay · deductible met". */
  message: string;
  triggered_by: 'registration' | 'staff' | 'seed' | string;
  /** 'fake' = the built-in stand-in (development), otherwise the clearinghouse's name. */
  provider: string;
  reference: string | null;
  payer_name: string | null;
  plan_name: string | null;
  plan_type: string | null;
  member_id: string | null;
  group_number: string | null;
  coverage_start: string | null;
  coverage_end: string | null;
  currency: string;
  copay_cents: number | null;
  specialist_copay_cents: number | null;
  deductible_cents: number | null;
  deductible_met_cents: number | null;
  deductible_remaining_cents: number | null;
  deductible_status: DeductibleStatus | null;
  coinsurance_percent: number | null;
  out_of_pocket_max_cents: number | null;
  out_of_pocket_met_cents: number | null;
  referral_required: boolean | null;
  referral_notes: string | null;
  prior_auth_required: boolean | null;
  prior_auth_notes: string | null;
  checked_at: string;
}

export const VERIFICATION_STATUS_LABEL: Record<VerificationStatus, string> = {
  active: 'Coverage active',
  inactive: 'Coverage not active',
  pending: 'Coverage starts later',
  not_found: 'Member not found',
  incomplete: 'Not enough details to check',
  unavailable: 'Could not be checked',
};

export const DEDUCTIBLE_STATUS_LABEL: Record<DeductibleStatus, string> = {
  met: 'Met for this year',
  partially_met: 'Partially met',
  not_met: 'Not met yet',
  none: 'No deductible',
};

/** `2500` → "$25.00" in the check's currency (the browser's locale for the digits). */
export function formatCents(cents: number | null | undefined, currency = 'USD'): string {
  if (cents === null || cents === undefined) return '—';
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

export interface PatientRecord extends PatientSummary {
  sex: string | null;
  email: string | null;
  phone: string | null;
  address_line: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country: string | null;
  insurance_provider: string | null;
  insurance_plan_name: string | null;
  insurance_member_id: string | null;
  insurance_group_number: string | null;
  insurance_policyholder_name: string | null;
  insurance_policyholder_relationship: string | null;
  insurance_cards: InsuranceCardInfo[];
  /** The newest eligibility & benefits check, when the record has insurance details. */
  insurance_verification: InsuranceVerification | null;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  reason_for_visit: string | null;
  updated_at: string;
}

/** A record that belongs to the signed-in patient account (server: schemas.MyPatientRecord). */
export interface MyPatientSummary extends PatientSummary {
  email: string | null;
  phone: string | null;
  /** 'email' (same address as the account) or 'identity' (attached by name + date of birth). */
  linked_by: 'email' | 'identity';
}

/** Which rules matched an existing record. */
export type MatchRule = 'name_dob' | 'email_dob' | 'phone_dob';

export interface PatientCheck {
  is_new: boolean;
  matched_on: MatchRule[];
  patient: PatientSummary | null;
}

export type PatientRegistration =
  | { status: 'new'; matched_on: MatchRule[]; patient: PatientRecord }
  | { status: 'existing'; matched_on: MatchRule[]; patient: PatientSummary };

/**
 * The public patient intake API: no account needed — a patient fills the form before the
 * office knows them. The server decides whether they are new (name + date of birth, or the
 * same e-mail/phone with the same date of birth) and creates the record only then.
 */
@Injectable({ providedIn: 'root' })
export class PatientsService {
  private readonly api = inject(ApiService);

  check(identity: PatientIdentity): Promise<PatientCheck> {
    return this.api.post<PatientCheck>('/patients/check', identity, { auth: false });
  }

  /** Works without an account; signed in with a patient account, the new record is the account's. */
  register(form: PatientForm): Promise<PatientRegistration> {
    return this.api.post<PatientRegistration>('/patients', form, { auth: true });
  }

  // ---- a patient updates their own record (patient_updates.py)

  /**
   * A record the signed-in account wants to edit — handed over from the My appointments page
   * to the registration page, which opens the update form for it. Session-only.
   */
  readonly editRequest = signal<PatientSummary | null>(null);

  /** By code, step 1: a one-time code goes to the e-mail / phone *on the record*. */
  requestUpdate(identity: PatientIdentity): Promise<PatientUpdateChallenge> {
    return this.api.post<PatientUpdateChallenge>('/patients/update/request', identity, {
      auth: false,
    });
  }

  /** By code, step 2: the code → an editing session (token + the record in full). */
  confirmUpdate(challenge: string, code: string): Promise<PatientUpdateSession> {
    return this.api.post<PatientUpdateSession>(
      '/patients/update/confirm',
      { challenge, code },
      { auth: false },
    );
  }

  /** Save with the editing session's token. */
  update(editToken: string, form: PatientUpdate): Promise<PatientRecord> {
    return this.api.put<PatientRecord>('/patients/update', form, {
      auth: false,
      headers: { 'X-Edit-Token': editToken },
    });
  }

  /** Signed in: the records that belong to the account (summaries). */
  myRecords(): Promise<MyPatientSummary[]> {
    return this.api.get<MyPatientSummary[]>('/me/patients');
  }

  /** Signed in: every record of the account, in full (the registration tab's overview). */
  async myRecordsInFull(): Promise<PatientRecord[]> {
    const rows = await this.myRecords();
    return Promise.all(rows.map((r) => this.myRecord(r.id)));
  }

  /** Signed in: one of the account's records, in full. */
  myRecord(id: number): Promise<PatientRecord> {
    return this.api.get<PatientRecord>(`/me/patients/${id}`);
  }

  /** Signed in: save one of the account's records. */
  updateMine(id: number, form: PatientUpdate): Promise<PatientRecord> {
    return this.api.put<PatientRecord>(`/me/patients/${id}`, form);
  }
}

/** "e-mail (p•••@example.com) and text message (+•••••••0301)" from the masked destinations. */
export function describeDestinations(sentTo: string[]): string {
  return sentTo
    .map((entry) => {
      const [channel, ...rest] = entry.split(':');
      return `${channel === 'sms' ? 'text message' : 'e-mail'} (${rest.join(':')})`;
    })
    .join(' and ');
}

/** Short form of a relationship, for running text: "policyholder: Sam Lee (parent / guardian)". */
export const RELATIONSHIP_SHORT: Record<InsuranceRelationship, string> = {
  self: 'myself',
  spouse: 'spouse / partner',
  parent: 'parent / guardian',
  child: 'child',
  other: 'someone else',
};

/** "Example Health · PPO Silver · member XYZ123 · group 77 · policyholder: Sam Lee (parent / guardian)". */
export function describeInsurance(
  p: Pick<
    PatientRecord,
    | 'insurance_provider'
    | 'insurance_plan_name'
    | 'insurance_member_id'
    | 'insurance_group_number'
    | 'insurance_policyholder_name'
    | 'insurance_policyholder_relationship'
  >,
): string {
  const parts = [
    p.insurance_provider,
    p.insurance_plan_name,
    p.insurance_member_id ? `member ${p.insurance_member_id}` : null,
    p.insurance_group_number ? `group ${p.insurance_group_number}` : null,
  ].filter(Boolean) as string[];
  const rel = p.insurance_policyholder_relationship as InsuranceRelationship | null;
  if (rel && rel !== 'self') {
    const who = RELATIONSHIP_SHORT[rel] ?? rel;
    parts.push(
      p.insurance_policyholder_name
        ? `policyholder: ${p.insurance_policyholder_name} (${who})`
        : `policyholder: ${who}`,
    );
  }
  return parts.join(' · ');
}

export function describeMatch(rules: MatchRule[]): string {
  const parts = rules.map((r) =>
    r === 'name_dob'
      ? 'your name and date of birth'
      : r === 'email_dob'
        ? 'your e-mail address and date of birth'
        : 'your phone number and date of birth',
  );
  return parts.length ? parts.join(' and ') : 'your details';
}
