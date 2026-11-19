import { Injectable, inject } from '@angular/core';
import { ApiService } from '../account/api.service';
import type { PatientSummary } from '../patients/patients.service';

// ---------------------------------------------------------------------------- payment methods

export type PaymentMethodKind = 'card' | 'stripe' | 'paypal' | 'bank';
/** pending = not validated yet · active = the small authorization succeeded · failed = declined */
export type PaymentMethodStatus = 'pending' | 'active' | 'failed';
export type PaymentValidationResult = 'authorized' | 'declined' | 'error';
export type BankAccountType = 'checking' | 'savings';

/** One validation attempt: a small amount authorized and released at once (schemas.PaymentValidationOut). */
export interface PaymentValidation {
  id: number;
  result: PaymentValidationResult;
  amount_cents: number;
  currency: string;
  message: string;
  reference: string | null;
  /** fake = the built-in vault (development): nothing left the machine. */
  provider: string;
  triggered_by: string;
  created_at: string;
}

/**
 * A payment method on the patient account, masked (schemas.PatientPaymentMethodOut). The
 * detail itself — card number, account number, the wallet's e-mail — is never sent back: the
 * server only keeps the vault's token.
 */
export interface PaymentMethod {
  id: number;
  kind: PaymentMethodKind;
  label: string | null;
  holder_name: string | null;
  is_default: boolean;
  status: PaymentMethodStatus;
  validated_at: string | null;
  validation: PaymentValidation | null;
  /** "Visa •••• 4242 · 12/2031", "PayPal · pa•••@example.com", "Example Bank · Checking •••• 6789" */
  display: string;
  expired: boolean;
  card_brand: string | null;
  card_last4: string | null;
  card_exp_month: number | null;
  card_exp_year: number | null;
  billing_postal_code: string | null;
  wallet_email_masked: string | null;
  bank_name: string | null;
  bank_account_type: BankAccountType | null;
  bank_routing_masked: string | null;
  bank_account_last4: string | null;
  provider: string | null;
  created_at: string;
  updated_at: string;
}

/** Create / update (schemas.PatientPaymentMethodIn) — the detail only when it is typed. */
export interface PaymentMethodInput {
  kind: PaymentMethodKind;
  label: string | null;
  holder_name: string | null;
  is_default: boolean;
  card_number?: string;
  card_cvc?: string;
  card_exp_month?: number;
  card_exp_year?: number;
  billing_postal_code?: string | null;
  wallet_email?: string;
  bank_name?: string | null;
  bank_account_type?: BankAccountType;
  bank_routing_number?: string;
  bank_account_number?: string;
}

/** What the add / edit form holds (strings as typed). */
export interface PaymentMethodDraft {
  kind: PaymentMethodKind;
  label: string;
  holder_name: string;
  is_default: boolean;
  card_number: string;
  card_cvc: string;
  card_exp_month: string;
  card_exp_year: string;
  billing_postal_code: string;
  wallet_email: string;
  bank_name: string;
  bank_account_type: BankAccountType;
  bank_routing_number: string;
  bank_account_number: string;
}

// ---------------------------------------------------------------------------- the ledger

/** The method a payment went against, as it was then; `removed` = deleted from the account since. */
export interface PaymentMethodRef {
  id: number | null;
  kind: PaymentMethodKind;
  display: string;
  removed: boolean;
}

export interface PaymentAppointmentRef {
  id: number;
  number: string;
  start_local: string;
  provider: string;
  status: string;
}

/** check_in = the estimate not covered by insurance, pre-authorized when the check-in page is signed. */
export type PatientPaymentKind = 'copay' | 'self_pay' | 'statement' | 'other' | 'check_in';
export type PatientPaymentStatus =
  'authorized' | 'captured' | 'released' | 'declined' | 'refunded' | 'partially_refunded';

/** One hold / charge / refund (schemas.PatientPaymentOut). */
export interface PatientPayment {
  id: number;
  kind: PatientPaymentKind;
  status: PatientPaymentStatus;
  amount_cents: number;
  refunded_cents: number;
  currency: string;
  description: string;
  message: string;
  method: PaymentMethodRef;
  appointment: PaymentAppointmentRef | null;
  patient: PatientSummary | null;
  reference: string | null;
  refund_reference: string | null;
  provider: string;
  created_by: string;
  created_at: string;
  captured_at: string | null;
  released_at: string | null;
  refunded_at: string | null;
}

/** One line of the payment history: a payment or a validation (schemas.PaymentHistoryEntry). */
export interface PaymentHistoryEntry {
  id: string;
  at: string;
  entry: 'payment' | 'validation';
  /** PatientPaymentKind, or 'validation'. */
  kind: string;
  /** PatientPaymentStatus, or a PaymentValidationResult for a validation. */
  status: string;
  amount_cents: number;
  refunded_cents: number;
  currency: string;
  description: string;
  message: string;
  method: PaymentMethodRef;
  appointment: PaymentAppointmentRef | null;
  patient: PatientSummary | null;
  reference: string | null;
  provider: string;
  by: string;
  payment: PatientPayment | null;
}

export interface PaymentTotals {
  currency: string;
  /** Captured, net of refunds. */
  charged_cents: number;
  refunded_cents: number;
  /** On hold (authorized, not charged yet). */
  held_cents: number;
  count: number;
}

export interface PaymentHistory {
  entries: PaymentHistoryEntry[];
  totals: PaymentTotals;
}

// ---------------------------------------------------------------------------- labels & helpers

export const METHOD_KIND_LABELS: Record<PaymentMethodKind, string> = {
  card: 'Credit / debit card',
  stripe: 'Stripe',
  paypal: 'PayPal',
  bank: 'Bank account',
};
export const METHOD_KINDS = Object.keys(METHOD_KIND_LABELS) as PaymentMethodKind[];

export const METHOD_STATUS_LABELS: Record<PaymentMethodStatus, string> = {
  active: 'validated',
  pending: 'not validated',
  failed: 'declined',
};

export const VALIDATION_RESULT_LABELS: Record<PaymentValidationResult, string> = {
  authorized: 'authorized',
  declined: 'declined',
  error: 'not possible',
};

/** Payment / validation kind → label. */
export const PAYMENT_KIND_LABELS: Record<string, string> = {
  copay: 'Copay',
  self_pay: 'Self-pay visit',
  statement: 'Statement',
  other: 'Charge',
  check_in: 'Check-in pre-authorization',
  validation: 'Validation',
};

/** Payment status → the tag in the history. */
export const PAYMENT_STATUS_LABELS: Record<string, string> = {
  authorized: 'on hold',
  captured: 'charged',
  released: 'released',
  declined: 'declined',
  refunded: 'refunded',
  partially_refunded: 'partly refunded',
};

/** Amount in minor units → "$40.00" (falls back to "40.00 USD"). */
export function money(cents: number | null | undefined, currency = 'USD'): string {
  if (cents === null || cents === undefined) return '';
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

export const digitsOf = (value: string | null | undefined): string =>
  String(value ?? '').replace(/\D+/g, '');

/** The check digit of a card number (ISO/IEC 7812) — the server checks it again. */
export function luhnOk(number: string): boolean {
  const digits = digitsOf(number);
  if (digits.length < 12 || digits.length > 19) return false;
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
  }
  return sum % 10 === 0;
}

/** Brand from the leading digits, for the hint next to the number while typing. */
export function cardBrand(number: string): string {
  const n = digitsOf(number);
  if (!n) return '';
  const two = Number(n.slice(0, 2));
  const four = Number(n.slice(0, 4));
  if (n.startsWith('4')) return 'Visa';
  if ((two >= 51 && two <= 55) || (four >= 2221 && four <= 2720)) return 'Mastercard';
  if (two === 34 || two === 37) return 'American Express';
  if (n.startsWith('6011') || n.startsWith('65')) return 'Discover';
  if (four >= 3528 && four <= 3589) return 'JCB';
  if (n.startsWith('62')) return 'UnionPay';
  return 'Card';
}

export function emptyDraft(kind: PaymentMethodKind = 'card'): PaymentMethodDraft {
  return {
    kind,
    label: '',
    holder_name: '',
    is_default: false,
    card_number: '',
    card_cvc: '',
    card_exp_month: '',
    card_exp_year: '',
    billing_postal_code: '',
    wallet_email: '',
    bank_name: '',
    bank_account_type: 'checking',
    bank_routing_number: '',
    bank_account_number: '',
  };
}

/** The form of an existing method: everything but the secret detail, which is never sent back. */
export function draftFrom(m: PaymentMethod): PaymentMethodDraft {
  return {
    ...emptyDraft(m.kind),
    label: m.label ?? '',
    holder_name: m.holder_name ?? '',
    is_default: m.is_default,
    card_exp_month: m.card_exp_month ? String(m.card_exp_month) : '',
    card_exp_year: m.card_exp_year ? String(m.card_exp_year) : '',
    billing_postal_code: m.billing_postal_code ?? '',
    bank_name: m.bank_name ?? '',
    bank_account_type: m.bank_account_type ?? 'checking',
  };
}

/**
 * Checks before the round trip (the server validates everything again). `editing`: the method
 * being changed — then the detail may be left blank to keep what is on file. A message or null.
 */
export function validateDraft(
  d: PaymentMethodDraft,
  editing: PaymentMethod | null = null,
  now = new Date(),
): string | null {
  switch (d.kind) {
    case 'card': {
      if (!d.holder_name.trim()) return 'Please enter the name on the card.';
      const number = digitsOf(d.card_number);
      if (!number && !editing) return 'Please enter the card number.';
      if (number && !luhnOk(number)) return 'This card number does not look right.';
      const cvc = digitsOf(d.card_cvc);
      if (cvc && (cvc.length < 3 || cvc.length > 4))
        return 'The security code has 3 digits (4 on American Express).';
      if (!d.card_exp_month || !d.card_exp_year) return "Please choose the card's expiry date.";
      const y = Number(d.card_exp_year);
      const m = Number(d.card_exp_month);
      if (y < now.getFullYear() || (y === now.getFullYear() && m < now.getMonth() + 1))
        return 'This card has expired.';
      return null;
    }
    case 'stripe':
    case 'paypal': {
      const email = d.wallet_email.trim();
      if (!email && editing) return null;
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
        return `Please enter the e-mail address of your ${METHOD_KIND_LABELS[d.kind]} account.`;
      return null;
    }
    case 'bank': {
      if (!d.holder_name.trim()) return "Please enter the account holder's name.";
      const routing = d.bank_routing_number.replace(/[\s-]/g, '');
      const account = digitsOf(d.bank_account_number);
      if (editing && !routing && !account) return null;
      if (!routing) return 'Please enter the routing number.';
      if (routing.length < 6) return 'The routing number is too short.';
      if (!account)
        return editing
          ? 'Please enter the account number too when changing the routing number.'
          : 'Please enter the account number.';
      if (account.length < 4 || account.length > 17)
        return 'The account number has 4 to 17 digits.';
      return null;
    }
  }
}

const orNull = (v: string): string | null => (v.trim() ? v.trim() : null);

/** The request body from the form — the secret detail only when it was typed. */
export function toInput(d: PaymentMethodDraft): PaymentMethodInput {
  const base: PaymentMethodInput = {
    kind: d.kind,
    label: orNull(d.label),
    holder_name: orNull(d.holder_name),
    is_default: d.is_default,
  };
  switch (d.kind) {
    case 'card': {
      const number = digitsOf(d.card_number);
      const cvc = digitsOf(d.card_cvc);
      return {
        ...base,
        ...(number ? { card_number: number } : {}),
        ...(cvc ? { card_cvc: cvc } : {}),
        card_exp_month: Number(d.card_exp_month),
        card_exp_year: Number(d.card_exp_year),
        billing_postal_code: orNull(d.billing_postal_code),
      };
    }
    case 'stripe':
    case 'paypal': {
      const email = d.wallet_email.trim().toLowerCase();
      return { ...base, ...(email ? { wallet_email: email } : {}) };
    }
    case 'bank': {
      const routing = d.bank_routing_number.replace(/[\s-]/g, '');
      const account = digitsOf(d.bank_account_number);
      return {
        ...base,
        bank_name: orNull(d.bank_name),
        bank_account_type: d.bank_account_type,
        ...(routing ? { bank_routing_number: routing } : {}),
        ...(account ? { bank_account_number: account } : {}),
      };
    }
  }
}

// ---------------------------------------------------------------------------- the API

/**
 * The signed-in patient account's payment methods (/me/payment-methods) and payment history
 * (/me/payments). Every call needs the patient's session.
 */
@Injectable({ providedIn: 'root' })
export class PaymentMethodsService {
  private readonly api = inject(ApiService);

  /** Default first, then oldest first. */
  list(): Promise<PaymentMethod[]> {
    return this.api.get<PaymentMethod[]>('/me/payment-methods');
  }

  /** Tokenized and validated at once (a small authorization, released immediately). */
  add(input: PaymentMethodInput): Promise<PaymentMethod> {
    return this.api.post<PaymentMethod>('/me/payment-methods', input);
  }

  update(id: number, input: PaymentMethodInput): Promise<PaymentMethod> {
    return this.api.put<PaymentMethod>(`/me/payment-methods/${id}`, input);
  }

  remove(id: number): Promise<void> {
    return this.api.delete<void>(`/me/payment-methods/${id}`);
  }

  /** Active methods only; answers the whole list. */
  makeDefault(id: number): Promise<PaymentMethod[]> {
    return this.api.post<PaymentMethod[]>(`/me/payment-methods/${id}/default`);
  }

  validate(id: number): Promise<PaymentMethod> {
    return this.api.post<PaymentMethod>(`/me/payment-methods/${id}/validate`);
  }

  validations(id: number): Promise<PaymentValidation[]> {
    return this.api.get<PaymentValidation[]>(`/me/payment-methods/${id}/validations`);
  }

  /** Every hold, charge, refund and validation on the account, newest first, with totals. */
  history(): Promise<PaymentHistory> {
    return this.api.get<PaymentHistory>('/me/payments');
  }
}
