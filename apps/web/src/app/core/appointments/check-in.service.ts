import { Injectable, inject } from '@angular/core';
import { ApiService } from '../account/api.service';
import type { PatientPayment } from '../payments/payment-methods.service';
import type { Appointment, CheckInStart, CheckInSummary } from './appointments.service';

/** One question of the check-in questionnaire (server: schemas.CheckInQuestion). */
export interface CheckInQuestion {
  id: string;
  label: string;
  /** text · textarea · choice · yesno · scale (0–10) · consent (must be ticked) */
  kind: 'text' | 'textarea' | 'choice' | 'yesno' | 'scale' | 'consent' | string;
  required: boolean;
  options: { value: string; label: string }[];
  hint: string | null;
  max_length: number | null;
}

export interface CheckInQuestionnaire {
  version: string;
  title: string;
  intro: string;
  questions: CheckInQuestion[];
}

/** The estimated cost not covered by insurance (server: schemas.CheckInEstimate). */
export interface CheckInEstimate {
  currency: string;
  total_cents: number;
  lines: { label: string; amount_cents: number }[];
  covered: boolean;
  coverage_status: string;
  coverage_message: string;
  notes: string[];
  /** Where it is pre-authorized ("Visa •••• 4242 · 12/2031") — null: nothing on file. */
  payment_method: string | null;
  /** The booking's hold this pre-authorization replaces (released when the check-in completes). */
  replaces_hold_cents: number;
}

export type CheckInAnswers = Record<string, string | number | boolean | null>;

/** The check-in page (server: schemas.CheckInSession). */
export interface CheckInSession extends CheckInSummary {
  appointment: Appointment;
  questionnaire: CheckInQuestionnaire;
  estimate: CheckInEstimate | null;
  answers: CheckInAnswers;
  answers_signer_name: string | null;
  answers_signed_at: string | null;
  financial_signer_name: string | null;
  financial_signed_at: string | null;
  payment: PatientPayment | null;
  can_restart: boolean;
}

export interface CheckInSignature {
  signer_name: string;
  /** data:image/png;base64,… — the drawn signature. */
  image_png: string;
}

export interface CheckInSubmission {
  answers: CheckInAnswers;
  answers_signature: CheckInSignature;
  /** The amount the patient was shown and accepts (a changed estimate is refused: 409). */
  estimate_cents: number;
  accept_financial_responsibility: boolean;
  financial_signature: CheckInSignature;
}

/**
 * The check-in page (/check-in/<token>): no sign-in — the token in the address is the proof,
 * like the appointment link. Opened by "Check in" in the app, or sent by the office.
 */
@Injectable({ providedIn: 'root' })
export class CheckInService {
  private readonly api = inject(ApiService);

  get(token: string): Promise<CheckInSession> {
    return this.api.get<CheckInSession>(`/check-ins/${encodeURIComponent(token)}`, {
      auth: false,
    });
  }

  /** Completed (checked in, estimate pre-authorized) or failed (with the reason) — both are 200. */
  submit(token: string, body: CheckInSubmission): Promise<CheckInSession> {
    return this.api.post<CheckInSession>(`/check-ins/${encodeURIComponent(token)}`, body, {
      auth: false,
    });
  }

  /** After a failure: a new check-in page for the same visit (within the window). */
  restart(token: string): Promise<CheckInStart> {
    return this.api.post<CheckInStart>(
      `/check-ins/${encodeURIComponent(token)}/restart`,
      {},
      { auth: false },
    );
  }
}

/** Which required questions are still unanswered (labels), before the round trip. */
export function missingAnswers(q: CheckInQuestionnaire, answers: CheckInAnswers): string[] {
  return q.questions
    .filter((x) => x.required)
    .filter((x) => {
      const v = answers[x.id];
      if (x.kind === 'consent') return v !== true;
      return v === null || v === undefined || (typeof v === 'string' && !v.trim());
    })
    .map((x) => x.label);
}
