/**
 * Pure helpers for the patients' payment methods (views/payment-methods.js): card-number checks
 * before the round trip, labels for the kinds and validation states, the request body built
 * from the form. No DOM, no network — tested with `node --test` (tests/payment.test.mjs). The
 * server validates everything again; the detail itself (card number, account number, wallet
 * e-mail) is tokenized there and never stored.
 */

export const KIND_LABELS = { card: "Credit / debit card", stripe: "Stripe", paypal: "PayPal", bank: "Bank account" };
export const KINDS = Object.keys(KIND_LABELS);

/** Method status → [label, badge kind]. Only "active" methods can be charged / be the default. */
export const STATUS = {
  active: ["Active", "ok"],
  pending: ["Not validated", "warn"],
  failed: ["Declined", "bad"],
};

/** Validation result → [label, badge kind]. */
export const RESULT = {
  authorized: ["Authorized", "ok"],
  declined: ["Declined", "bad"],
  error: ["Not possible", "warn"],
};

export const digitsOf = (value) => String(value ?? "").replace(/\D+/g, "");

/** The check digit of a card number (ISO/IEC 7812). */
export function luhnOk(number) {
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
export function cardBrand(number) {
  const n = digitsOf(number);
  if (!n) return "";
  const two = Number(n.slice(0, 2));
  const four = Number(n.slice(0, 4));
  if (n.startsWith("4")) return "Visa";
  if ((two >= 51 && two <= 55) || (four >= 2221 && four <= 2720)) return "Mastercard";
  if (two === 34 || two === 37) return "American Express";
  if (n.startsWith("6011") || n.startsWith("65")) return "Discover";
  if (four >= 3528 && four <= 3589) return "JCB";
  if (n.startsWith("62")) return "UnionPay";
  return "Card";
}

/** Amount in minor units → "$1.00" (falls back to "1.00 USD"). */
export function money(cents, currency = "USD") {
  if (cents === null || cents === undefined) return "";
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

/**
 * Client-side checks of the form (strings as typed; `editing` = the method being updated, if
 * any — then the detail may be left blank to keep what is on file). Returns a message or null.
 */
export function validateForm(d, editing = null) {
  const now = new Date();
  switch (d.kind) {
    case "card": {
      if (!d.holder_name) return "Please enter the name on the card.";
      const number = digitsOf(d.card_number);
      if (!number && !editing) return "Please enter the card number.";
      if (number && !luhnOk(number)) return "This card number does not look right.";
      const cvc = digitsOf(d.card_cvc);
      if (cvc && (cvc.length < 3 || cvc.length > 4)) return "The security code has 3 digits (4 on American Express).";
      if (!d.card_exp_month || !d.card_exp_year) return "Please choose the card's expiry date.";
      const y = Number(d.card_exp_year);
      const m = Number(d.card_exp_month);
      if (y < now.getFullYear() || (y === now.getFullYear() && m < now.getMonth() + 1)) return "This card has expired.";
      return null;
    }
    case "stripe":
    case "paypal": {
      const email = (d.wallet_email || "").trim();
      if (!email && editing) return null;
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
        return `Please enter the e-mail address of the patient's ${KIND_LABELS[d.kind]} account.`;
      return null;
    }
    case "bank": {
      if (!d.holder_name) return "Please enter the account holder's name.";
      if (!d.bank_account_type) return "Please choose checking or savings.";
      const routing = (d.bank_routing_number || "").replace(/[\s-]/g, "");
      const account = digitsOf(d.bank_account_number);
      if (editing && !routing && !account) return null;
      if (!routing) return "Please enter the routing number.";
      if (routing.length < 6) return "The routing number is too short.";
      if (!account)
        return editing
          ? "Please enter the account number too when changing the routing number."
          : "Please enter the account number.";
      if (account.length < 4 || account.length > 17) return "The account number has 4 to 17 digits.";
      return null;
    }
    default:
      return "Please choose the kind of payment method.";
  }
}

const orNull = (v) => (v && String(v).trim() ? String(v).trim() : null);

/** The request body (schemas.PatientPaymentMethodIn) — the detail only when typed. */
export function toBody(d) {
  const base = { kind: d.kind, label: orNull(d.label), holder_name: orNull(d.holder_name), is_default: !!d.is_default };
  switch (d.kind) {
    case "card": {
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
    case "stripe":
    case "paypal": {
      const email = (d.wallet_email || "").trim().toLowerCase();
      return { ...base, ...(email ? { wallet_email: email } : {}) };
    }
    case "bank": {
      const routing = (d.bank_routing_number || "").replace(/[\s-]/g, "");
      const account = digitsOf(d.bank_account_number);
      return {
        ...base,
        bank_name: orNull(d.bank_name),
        bank_account_type: d.bank_account_type || "checking",
        ...(routing ? { bank_routing_number: routing } : {}),
        ...(account ? { bank_account_number: account } : {}),
      };
    }
    default:
      return base;
  }
}
