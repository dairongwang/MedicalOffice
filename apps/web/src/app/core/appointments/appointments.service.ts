import { Injectable, inject, signal } from '@angular/core';
import { ApiError, ApiService } from '../account/api.service';
import type {
  InsuranceVerification,
  PatientIdentity,
  PatientSummary,
} from '../patients/patients.service';
import type { PatientPayment } from '../payments/payment-methods.service';

export type AppointmentStatus = 'scheduled' | 'canceled' | 'completed' | 'no_show';

/** A member of staff patients can book with (server: schemas.ProviderOut). */
export interface Provider {
  id: number;
  staff_number: string;
  first_name: string;
  last_name: string;
  role: string;
  specialty: string | null;
  /** Weekdays with bookable hours, 0 = Monday … 6 = Sunday. */
  weekdays: number[];
}

export interface ProviderList {
  providers: Provider[];
  slot_minutes: number;
  max_days_ahead: number;
  /** IANA name of the office's time zone; slot labels are in it. */
  timezone: string;
  /** Booking needs a signed-in patient with a validated payment method (the copay / fee goes on hold). */
  payment_method_required: boolean;
  /** What a self-pay visit costs up front (minor units). */
  self_pay_visit_cents: number;
  currency: string;
}

export interface Slot {
  start_at: string;
  end_at: string;
  /** "09:30" in the office's time zone. */
  label: string;
  available: boolean;
}

export interface DaySlots {
  provider_id: number;
  date: string;
  timezone: string;
  slots: Slot[];
  note: string | null;
}

/** The patient's link to one appointment and how it was delivered (server: schemas.AppointmentLink). */
export interface AppointmentLink {
  url: string;
  /** Channels the message actually went out on: 'email' / 'sms'. */
  channels: string[];
  /** Masked destinations, e.g. "email:p•••@example.com". */
  sent_to: string[];
  sent_at: string | null;
  note: string | null;
  /** The built-in fake delivered it (development): nothing left the machine. */
  fake: boolean;
  /** Where to read a fake delivery (the API's test inbox). */
  outbox_url: string | null;
  opens: number;
  last_opened_at: string | null;
}

/** "Add to calendar" (server: schemas.CalendarLinks). */
export interface CalendarLinks {
  ics_url: string;
  google_url: string;
  outlook_url: string;
}

export interface Appointment {
  id: number;
  /** Confirmation number, e.g. APT-000042 */
  number: string;
  status: AppointmentStatus;
  start_at: string;
  end_at: string;
  /** "Monday, June 7, 2027 · 09:30" in the office's time zone. */
  start_local: string;
  timezone: string;
  reason: string | null;
  provider: Provider;
  patient: PatientSummary;
  created_at: string;
  canceled_at: string | null;
  office_name: string;
  office_address: string | null;
  link: AppointmentLink | null;
  /** Only on the page behind the link (GET /appointments/by-token/…). */
  calendar: CalendarLinks | null;
  /** The patient confirmed they are coming (from the reminder's link, their account or the desk). */
  confirmed_at: string | null;
  confirmed_via: 'link' | 'account' | 'identity' | 'staff' | null;
  /** The reminder the day before: when it went out and on which channels. */
  reminder_sent_at: string | null;
  reminder_channels: string[];
  reminder_note: string | null;
  /** Who pays: the coverage was active for the day ('insurance') or the patient agreed to self-pay. */
  payment: 'insurance' | 'self_pay';
  coverage_status: string | null;
  self_pay_agreed_at: string | null;
  /** The copay / self-pay hold placed at booking (charged after the visit, released if canceled). */
  charge?: PatientPayment | null;
  /** Check-in: when, and how — from the app (account · link · identity) or by the office (staff). */
  checked_in_at?: string | null;
  checked_in_via?: CheckInVia | null;
  /** The patient's check-in window: opens 24 h (CHECK_IN_WINDOW_HOURS) before, closes when the visit ends. */
  check_in_opens_at?: string | null;
  check_in_closes_at?: string | null;
  /** The server's answer to "may the patient check in from the app right now?" when it was loaded. */
  can_check_in?: boolean;
  /** The latest check-in attempt: pending (page open / link sent), completed, failed (why), canceled. */
  check_in?: CheckInSummary | null;
  /** The visit summary the doctor signed after the visit (the patient sees signed ones only). */
  visit_summary?: VisitSummaryBrief | null;
}

/** On every appointment: is there a visit summary, and where does it stand (server: schemas.VisitSummaryBrief). */
export interface VisitSummaryBrief {
  id: number;
  /** VS-000042 */
  number: string;
  version: number;
  status: 'draft' | 'signed' | 'superseded';
  signed_at: string | null;
  signer_name: string | null;
  sent_at: string | null;
  /** When the patient first opened it (null = not yet: the app marks it "new"). */
  patient_viewed_at: string | null;
}

/** What the summary says, in plain words (server: schemas.VisitSummaryContent). */
export interface VisitSummaryContent {
  visit_number: string;
  visit_date: string;
  visit_start_at: string;
  timezone: string;
  office_name: string;
  office_address: string | null;
  office_phone: string | null;
  provider_name: string;
  provider_role: string;
  provider_specialty: string | null;
  patient_name: string;
  patient_mrn: string;
  patient_date_of_birth: string;
  reason: string | null;
  vitals: string | null;
  vitals_note: string | null;
  diagnoses: string[];
  prescriptions: string[];
  medications: string[];
  allergies: string[];
  referrals: string[];
  tests: string[];
}

/** The signed visit summary (server: schemas.VisitSummaryOut, as the patient gets it). */
export interface VisitSummary {
  id: number;
  number: string;
  appointment_id: number;
  appointment_number: string;
  version: number;
  status: 'draft' | 'signed' | 'superseded';
  content: VisitSummaryContent;
  assessment: string | null;
  plan: string | null;
  instructions: string | null;
  follow_up: string | null;
  provider_name: string | null;
  signer_name: string | null;
  signed_at: string | null;
  /** The doctor's drawn signature (PNG data URL). */
  signature_png: string | null;
  /** "VS-000042-ABCDEF1234" — printed on the PDF too. */
  verification: string | null;
  amendment_reason: string | null;
  sent_at: string | null;
  sent_channels: string[];
  patient_viewed_at: string | null;
}

/** One check-in attempt, as the boards show it (server: schemas.CheckInSummary). */
export interface CheckInSummary {
  id: number;
  status: 'pending' | 'completed' | 'failed' | 'canceled';
  /** Who started it: the patient in the app (account · link · identity) or the office (staff). */
  via: CheckInVia;
  started_at: string;
  /** The page must be completed by then (60 minutes), or the check-in fails. */
  expires_at: string;
  link_channels: string[];
  link_sent_to: string[];
  link_sent_at: string | null;
  link_delivery_note: string | null;
  failure_code: string | null;
  /** Why the check-in failed — shown to the patient and to the office. */
  failure_reason: string | null;
  completed_at: string | null;
  estimate_cents: number | null;
  currency: string;
  payment_id: number | null;
  /** Completed: the appointment's provider ("Dr. Alex Example") was told the patient checked in. */
  staff_name?: string | null;
  staff_notified_at?: string | null;
  staff_notify_channels?: string[];
  staff_notified_to?: string[];
  staff_notify_note?: string | null;
}

/** "Check in" opened a check-in page: go to `token` → /check-in/<token> (server: schemas.CheckInStart). */
export interface CheckInStart {
  token: string;
  url: string;
  check_in: CheckInSummary;
}

export type CheckInVia = 'account' | 'link' | 'identity' | 'staff';

/**
 * Where an appointment stands for check-in from the app:
 *  - `checked-in`  done (by the patient or by the office)
 *  - `open`        the window is open — show "Check in"
 *  - `not-yet`     scheduled, the window opens later (24 h before the visit)
 *  - `closed`      the visit is over; the front desk takes over
 *  - `none`        not a scheduled visit (canceled, completed, no-show) or no window known
 * Computed from the window itself, so a page left open switches to `open` on its own.
 */
export type CheckInState =
  'checked-in' | 'link-sent' | 'failed' | 'open' | 'not-yet' | 'closed' | 'none';

/**
 * Where an appointment stands for check-in:
 *  - `checked-in`  the check-in page was signed and processed (by the patient, or via the office's link)
 *  - `link-sent`   the office sent the check-in page's link — to be completed by `check_in.expires_at`
 *  - `failed`      the last check-in failed — `check_in.failure_reason` says why
 *  - `open` / `not-yet` / `closed`   the patient's own window (24 h before the visit until it ends)
 *  - `none`        not a scheduled visit
 */
export function checkInState(a: Appointment, now: Date = new Date()): CheckInState {
  if (a.checked_in_at) return 'checked-in';
  if (a.status !== 'scheduled') return 'none';
  const c = a.check_in;
  if (c?.status === 'pending' && c.via === 'staff' && now.getTime() < Date.parse(c.expires_at))
    return 'link-sent';
  if (c?.status === 'failed') return 'failed';
  return checkInWindow(a, now);
}

/** The patient's own check-in window only (ignoring earlier attempts). */
export function checkInWindow(
  a: Appointment,
  now: Date = new Date(),
): 'open' | 'not-yet' | 'closed' | 'none' {
  if (a.status !== 'scheduled' || !a.check_in_opens_at || !a.check_in_closes_at) return 'none';
  const t = now.getTime();
  if (t < Date.parse(a.check_in_opens_at)) return 'not-yet';
  if (t >= Date.parse(a.check_in_closes_at)) return 'closed';
  return 'open';
}

/**
 * A moment in the office's time zone, always with the zone named ("14:05 UTC", "Sun, Jun 6,
 * 09:30 EDT") — the visit's own time (`start_local`) is in that zone too, and a patient elsewhere
 * must not read it as their local time.
 */
function inOfficeZone(iso: string, timezone: string, opts: Intl.DateTimeFormatOptions): string {
  const d = new Date(iso);
  const zoned: Intl.DateTimeFormatOptions = { ...opts, hourCycle: 'h23', timeZoneName: 'short' };
  try {
    return d.toLocaleString('en-US', { ...zoned, timeZone: timezone || 'UTC' });
  } catch {
    return d.toLocaleString('en-US', { ...zoned, timeZone: 'UTC' });
  }
}

/** "14:05 UTC" in the office's time zone. */
export function officeClock(iso: string, timezone: string): string {
  return inOfficeZone(iso, timezone, { hour: '2-digit', minute: '2-digit' });
}

/** "Tue, Jun 6, 09:30 UTC" — when check-in opens, in the office's time zone (like the visit's time). */
export function checkInOpensLabel(a: Appointment): string {
  if (!a.check_in_opens_at) return '';
  return inOfficeZone(a.check_in_opens_at, a.timezone, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

const CHECK_IN_VIA_LABELS: Record<CheckInVia, string> = {
  account: 'in the app',
  link: 'from your appointment link',
  identity: 'in the app',
  staff: "with the front desk's link",
};

/** "Checked in in the app at 09:12 UTC" — for the patient. */
export function checkedInLabel(a: Appointment): string {
  if (!a.checked_in_at) return '';
  const how = a.checked_in_via ? CHECK_IN_VIA_LABELS[a.checked_in_via] : '';
  return `Checked in${how ? ' ' + how : ''} at ${officeClock(a.checked_in_at, a.timezone)}`;
}

/** Before booking: is the insurance good for the day of the visit? (server: schemas.CoverageCheck) */
export interface CoverageCheck {
  covered: boolean;
  /** Not covered and the office requires coverage → the patient must agree to self-pay to book. */
  self_pay_required: boolean;
  has_insurance: boolean;
  /** The check's status, or 'none' without insurance on the record. */
  status: string;
  message: string;
  service_date: string;
  verification: InsuranceVerification | null;
}

/** A patient record that belongs to the signed-in patient account (server: schemas.MyPatientRecord). */
export interface MyPatientRecord extends PatientSummary {
  email: string | null;
  phone: string | null;
  /** 'email' (same address as the account) or 'identity' (attached by name + date of birth). */
  linked_by: 'email' | 'identity';
}

export interface MyAppointments {
  patients: MyPatientRecord[];
  upcoming: Appointment[];
  past: Appointment[];
}

export interface AppointmentLookup {
  patient: PatientSummary;
  appointments: Appointment[];
}

export interface AppointmentRequest extends PatientIdentity {
  provider_id: number;
  start_at: string;
  reason: string | null;
  /** The patient agreed to pay for the visit themselves (needed when the coverage is not active). */
  self_pay_agreed?: boolean;
}

/**
 * The public appointment API (no account needed): a registered patient proves who they are
 * the same way as on the intake page, then books, sees and cancels their own appointments.
 * Providers and free slots come from the staff roster's weekly availability.
 */
@Injectable({ providedIn: 'root' })
export class AppointmentsService {
  private readonly api = inject(ApiService);

  /**
   * Identity the patient typed on the registration page, so the scheduler can start with it
   * filled in ("Book an appointment" right after registering). Session-only, never stored.
   */
  readonly prefill = signal<PatientIdentity | null>(null);

  providers(): Promise<ProviderList> {
    return this.api.get<ProviderList>('/appointments/providers', { auth: false });
  }

  slots(providerId: number, date: string): Promise<DaySlots> {
    const q = new URLSearchParams({ provider_id: String(providerId), date });
    return this.api.get<DaySlots>(`/appointments/slots?${q}`, { auth: false });
  }

  lookup(identity: PatientIdentity): Promise<AppointmentLookup> {
    return this.api.post<AppointmentLookup>('/appointments/lookup', identity, { auth: false });
  }

  /** Whether the insurance covers the day of the slot — and so whether self-pay must be agreed to. */
  coverage(identity: PatientIdentity, startAt: string): Promise<CoverageCheck> {
    return this.api.post<CoverageCheck>(
      '/appointments/coverage',
      { ...identity, start_at: startAt },
      { auth: false },
    );
  }

  /** Signed in: the account's validated payment method takes the copay / self-pay hold. */
  book(request: AppointmentRequest): Promise<Appointment> {
    return this.api.post<Appointment>('/appointments', request, { auth: true });
  }

  cancel(id: number, identity: PatientIdentity): Promise<Appointment> {
    return this.api.post<Appointment>(`/appointments/${id}/cancel`, identity, { auth: false });
  }

  /** Start the check-in from the lookup page (identity again) — within the window; opens the page. */
  checkIn(id: number, identity: PatientIdentity): Promise<CheckInStart> {
    return this.api.post<CheckInStart>(`/appointments/${id}/check-in`, identity, { auth: false });
  }

  /** The page behind the link the patient received (token = last part of the link). */
  byToken(token: string): Promise<Appointment> {
    return this.api.get<Appointment>(`/appointments/by-token/${encodeURIComponent(token)}`, {
      auth: false,
    });
  }

  /** The reminder's "Confirm" link: the patient is coming. */
  confirmByToken(token: string): Promise<Appointment> {
    return this.api.post<Appointment>(
      `/appointments/by-token/${encodeURIComponent(token)}/confirm`,
      {},
      { auth: false },
    );
  }

  /** Start the check-in from the link's page (the reminder links with `?action=check-in`). */
  checkInByToken(token: string): Promise<CheckInStart> {
    return this.api.post<CheckInStart>(
      `/appointments/by-token/${encodeURIComponent(token)}/check-in`,
      {},
      { auth: false },
    );
  }

  cancelByToken(token: string): Promise<Appointment> {
    return this.api.post<Appointment>(
      `/appointments/by-token/${encodeURIComponent(token)}/cancel`,
      {},
      { auth: false },
    );
  }

  // ---- a signed-in patient account (role = patient): own records and appointments

  /** The records that belong to the account, and every appointment on them. */
  mine(): Promise<MyAppointments> {
    return this.api.get<MyAppointments>('/me/appointments');
  }

  /** Attach a record by identity (a family member registered without this e-mail, …). */
  linkRecord(identity: PatientIdentity): Promise<MyPatientRecord[]> {
    return this.api.post<MyPatientRecord[]>('/me/patients/link', identity);
  }

  confirmMine(id: number): Promise<Appointment> {
    return this.api.post<Appointment>(`/me/appointments/${id}/confirm`);
  }

  /** Start the check-in from the patient account — within the window; opens the page. */
  checkInMine(id: number): Promise<CheckInStart> {
    return this.api.post<CheckInStart>(`/me/appointments/${id}/check-in`);
  }

  cancelMine(id: number): Promise<Appointment> {
    return this.api.post<Appointment>(`/me/appointments/${id}/cancel`);
  }

  /** The visit summary the doctor signed after one of the account's visits (404 until it is signed). */
  visitSummary(id: number): Promise<VisitSummary> {
    return this.api.get<VisitSummary>(`/me/appointments/${id}/visit-summary`);
  }

  /** The same summary as a PDF (signed in: the session token goes along). */
  async visitSummaryPdf(id: number): Promise<Blob> {
    const token = this.api.token();
    let res: Response;
    try {
      res = await fetch(`${this.api.base}/me/appointments/${id}/visit-summary/pdf`, {
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

  /** Absolute address of an API path such as the .ics or the test inbox (the API may live elsewhere). */
  apiUrl(path: string): string {
    if (/^https?:\/\//.test(path)) return path;
    const origin = this.api.base.replace(/\/api\/v1$/, '');
    return (origin || location.origin) + path;
  }
}

/** "e-mail (p•••@example.com) and text message (+•••0401)" from the masked destinations. */
export function describeDelivery(link: AppointmentLink): string {
  const parts = link.sent_to.map((entry) => {
    const [channel, ...rest] = entry.split(':');
    const to = rest.join(':');
    return `${channel === 'sms' ? 'text message' : 'e-mail'} (${to})`;
  });
  return parts.join(' and ');
}

const ROLE_LABELS: Record<string, string> = {
  doctor: 'Doctor',
  nurse: 'Nurse',
  physician_assistant: 'Physician assistant',
  technician: 'Technician',
  receptionist: 'Receptionist',
  administrator: 'Administrator',
  other: 'Staff',
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

/** "Dr. Alex Example · General practice" — how a provider is shown in lists. */
export function providerLabel(p: Provider): string {
  const prefix = p.role === 'doctor' ? 'Dr. ' : '';
  const what = p.specialty ? `${roleLabel(p.role)} · ${p.specialty}` : roleLabel(p.role);
  return `${prefix}${p.first_name} ${p.last_name} — ${what}`;
}

export const WEEKDAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** "Mon, Tue, Wed, Thu, Fri" from the provider's weekday numbers. */
export function weekdaysLabel(weekdays: number[]): string {
  return weekdays.map((d) => WEEKDAY_SHORT[d] ?? '').join(', ');
}

/** Local ISO date (YYYY-MM-DD) of a Date, without the UTC shift of toISOString(). */
export function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Monday = 0 … Sunday = 6 for a YYYY-MM-DD string (matches the server's weekday numbers). */
export function weekdayOf(iso: string): number {
  const d = new Date(iso + 'T00:00:00');
  return (d.getDay() + 6) % 7;
}
