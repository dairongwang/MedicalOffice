/**
 * Pure logic of the patient's payment history (views/payment-history.js): normalising what the
 * API answered, the filters and their counts, the labels, and the checks of the charge /
 * refund forms. No DOM, no network — tested with `node --test` (tests/payment-ledger.test.mjs).
 *
 * `normalizeHistory` / `normalizeMethods` are deliberately forgiving: a missing, half-loaded or
 * unexpected payload yields an empty-but-complete shape instead of throwing while rendering
 * (the view used to read `data.entries.length` straight from the response).
 */

export const FILTERS = [
  ["all", "All"],
  ["charges", "Charges"],
  ["holds", "On hold"],
  ["refunds", "Refunds"],
  ["validations", "Validations"],
];

/** Payment / validation kind → label. */
export const KIND = {
  copay: "Copay",
  self_pay: "Self-pay visit",
  statement: "Statement",
  other: "Charge",
  check_in: "Check-in pre-authorization",
  validation: "Validation",
};

/** Status → [label, badge kind]. */
export const STATUS = {
  authorized: ["On hold", "warn"],
  captured: ["Charged", "ok"],
  released: ["Released", "muted"],
  declined: ["Declined", "bad"],
  refunded: ["Refunded", ""],
  partially_refunded: ["Partly refunded", ""],
  error: ["Not possible", "warn"],
};

const isObject = (v) => !!v && typeof v === "object" && !Array.isArray(v);
const int = (v) => (Number.isFinite(Number(v)) ? Math.round(Number(v)) : 0);

export function emptyTotals(currency = "USD") {
  return { currency, charged_cents: 0, refunded_cents: 0, held_cents: 0, count: 0 };
}

/** One history line, with every field the view touches present. */
function normalizeEntry(raw, index, currency) {
  const e = isObject(raw) ? raw : {};
  const method = isObject(e.method) ? e.method : {};
  const entry = e.entry === "validation" ? "validation" : "payment";
  return {
    id: typeof e.id === "string" && e.id ? e.id : `entry-${index}`,
    at: e.at || null,
    entry,
    kind: e.kind || (entry === "validation" ? "validation" : "other"),
    status: e.status || "",
    amount_cents: int(e.amount_cents),
    refunded_cents: int(e.refunded_cents),
    currency: e.currency || currency,
    description: e.description || "",
    message: e.message || "",
    method: {
      id: method.id ?? null,
      kind: method.kind || "",
      display: method.display || "(payment method)",
      // The history outlives the method: a removed one keeps its masked description.
      removed: method.removed === true || method.id === null || method.id === undefined,
    },
    appointment: isObject(e.appointment) ? e.appointment : null,
    patient: isObject(e.patient) ? e.patient : null,
    reference: e.reference || null,
    provider: e.provider || "",
    by: e.by || "",
    payment: isObject(e.payment) ? e.payment : null,
  };
}

/** Whatever GET /patients/{id}/payments answered → `{entries, totals}` that is safe to render. */
export function normalizeHistory(raw, currency = "USD") {
  const data = isObject(raw) ? raw : {};
  const list = Array.isArray(data.entries) ? data.entries : [];
  const totals = isObject(data.totals) ? data.totals : {};
  const entries = list.map((e, i) => normalizeEntry(e, i, totals.currency || currency));
  return {
    entries,
    totals: {
      currency: totals.currency || currency,
      charged_cents: int(totals.charged_cents),
      refunded_cents: int(totals.refunded_cents),
      held_cents: int(totals.held_cents),
      count: Number.isFinite(Number(totals.count))
        ? int(totals.count)
        : entries.filter((e) => e.entry === "payment").length,
    },
  };
}

/** Whatever GET /patients/{id}/payment-methods answered → `{hasAccount, methods, active}`. */
export function normalizeMethods(raw) {
  const data = isObject(raw) ? raw : {};
  const methods = Array.isArray(data.methods) ? data.methods.filter(isObject) : [];
  return {
    hasAccount: data.has_account === true || methods.length > 0,
    methods,
    active: methods.filter((m) => m.status === "active"),
  };
}

export function matches(entry, filter) {
  switch (filter) {
    case "validations":
      return entry.entry === "validation";
    case "holds":
      return entry.entry === "payment" && entry.status === "authorized";
    case "refunds":
      return entry.entry === "payment" && (entry.status === "refunded" || entry.status === "partially_refunded");
    case "charges":
      return entry.entry === "payment" && ["captured", "partially_refunded", "refunded"].includes(entry.status);
    default:
      return true;
  }
}

/** `{ all: 7, charges: 2, … }` for the filter chips. */
export function filterCounts(entries) {
  return Object.fromEntries(FILTERS.map(([key]) => [key, entries.filter((e) => matches(e, key)).length]));
}

export function statusBadge(entry) {
  if (entry.entry === "validation" && entry.status === "authorized") return ["Validated", "ok"];
  return STATUS[entry.status] || [entry.status || "—", ""];
}

/** Released holds and validations are shown in brackets: no money changed hands. */
export function amountText(entry, money) {
  const text = money(entry.amount_cents, entry.currency);
  return entry.entry === "validation" || entry.status === "released" ? `(${text})` : text;
}

/** Which actions a line offers: capture/release a hold, refund a charge. */
export function actionsFor(entry) {
  const pay = entry.payment;
  if (!pay) return [];
  if (pay.status === "authorized") return ["capture", "release"];
  if (pay.status === "captured" || pay.status === "partially_refunded") return ["refund"];
  return [];
}

export function refundableCents(payment) {
  return Math.max(0, int(payment?.amount_cents) - int(payment?.refunded_cents));
}

/** "$25.50" / "25,50" / " 25.5 " → 2550; null when it is not an amount. */
export function parseAmountCents(text) {
  const cleaned = String(text ?? "")
    .replace(/[^\d.,-]/g, "")
    .replace(",", ".");
  if (!cleaned || !/^-?\d*\.?\d+$/.test(cleaned)) return null;
  const cents = Math.round(Number(cleaned) * 100);
  return Number.isFinite(cents) ? cents : null;
}

/** The charge form; returns a message or null. */
export function validateCharge({ methodId, amount, description }) {
  if (!methodId) return "Choose the payment method to charge.";
  const cents = parseAmountCents(amount);
  if (cents === null || cents <= 0) return "Enter the amount to charge.";
  if (cents > 1000000) return "That amount is too large.";
  if (!String(description ?? "").trim()) return "Say what the charge is for.";
  return null;
}

/** The refund form; `left` is what is still refundable (cents). */
export function validateRefund({ amount, left, currency = "USD" }, money) {
  const cents = parseAmountCents(amount);
  if (cents === null || cents <= 0) return "Enter the amount to refund.";
  if (cents > left) return `Only ${money(left, currency)} is left to refund.`;
  return null;
}

// ---------------------------------------------------------------------------- the office-wide ledger

/**
 * The Payments tab (views/payments.js, GET /patient-payments): every patient's holds, charges,
 * releases and refunds in one list. Validations are not money movements — they stay on each
 * record's own payment history — so the groups differ a little from FILTERS above.
 */
export const OFFICE_FILTERS = [
  ["all", "All"],
  ["charges", "Charges"],
  ["holds", "On hold"],
  ["refunds", "Refunds"],
  ["released", "Released"],
  ["declined", "Declined"],
];
const OFFICE_KEYS = OFFICE_FILTERS.map(([key]) => key);
export const OFFICE_KINDS = [
  ["", "Every kind"],
  ["copay", "Copay"],
  ["self_pay", "Self-pay visit"],
  ["statement", "Statement"],
  ["other", "Other charge"],
  ["check_in", "Check-in pre-authorization"],
];
export const OFFICE_PAGE_SIZE = 50;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The tab's filters from the route (#/payments?q=&show=&kind=&since=&until=&offset=), cleaned. */
export function officeFilters(params = {}) {
  const p = isObject(params) ? params : {};
  const kinds = OFFICE_KINDS.map(([k]) => k);
  return {
    q: String(p.q ?? "").trim(),
    show: OFFICE_KEYS.includes(p.show) ? p.show : "all",
    kind: kinds.includes(p.kind) ? p.kind : "",
    since: ISO_DAY.test(p.since ?? "") ? p.since : "",
    until: ISO_DAY.test(p.until ?? "") ? p.until : "",
    offset: Math.max(0, int(p.offset)),
  };
}

/** The API query (and the CSV export's) — empty filters and the defaults are left out. */
export function officeQuery(filters, { limit = OFFICE_PAGE_SIZE, paged = true } = {}) {
  const f = officeFilters(filters);
  const q = new URLSearchParams();
  if (f.q) q.set("q", f.q);
  if (f.show !== "all") q.set("show", f.show);
  if (f.kind) q.set("kind", f.kind);
  if (f.since) q.set("since", f.since);
  if (f.until) q.set("until", f.until);
  if (paged) {
    if (f.offset) q.set("offset", String(f.offset));
    q.set("limit", String(limit));
  }
  return q.toString();
}

/** Whatever GET /patient-payments answered → a page that is safe to render. */
export function normalizeOfficePage(raw, currency = "USD") {
  const data = isObject(raw) ? raw : {};
  const history = normalizeHistory({ entries: data.items, totals: data.totals }, currency);
  const counts = isObject(data.counts) ? data.counts : {};
  const total = Number.isFinite(Number(data.total)) ? int(data.total) : history.entries.length;
  return {
    items: history.entries,
    totals: history.totals,
    total,
    offset: Math.max(0, int(data.offset)),
    limit: int(data.limit) > 0 ? int(data.limit) : OFFICE_PAGE_SIZE,
    counts: Object.fromEntries(
      OFFICE_KEYS.map((key) => [key, Number.isFinite(Number(counts[key])) ? int(counts[key]) : key === "all" ? total : 0]),
    ),
  };
}

/** "Showing 51–100 of 230" and where the pager's buttons go. */
export function pageInfo({ offset, limit, total, items }) {
  const shown = Array.isArray(items) ? items.length : 0;
  return {
    from: shown ? offset + 1 : 0,
    to: offset + shown,
    hasPrev: offset > 0,
    hasNext: offset + shown < total,
    prevOffset: Math.max(0, offset - limit),
    nextOffset: offset + limit,
  };
}

/** Quick periods for the date filter, as office-local YYYY-MM-DD (today is passed in for tests). */
export function quickRange(kind, today) {
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const back = (days) => {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    d.setDate(d.getDate() - days);
    return iso(d);
  };
  switch (kind) {
    case "7d":
      return { since: back(6), until: iso(today) };
    case "30d":
      return { since: back(29), until: iso(today) };
    case "month":
      return { since: `${today.getFullYear()}-${pad(today.getMonth() + 1)}-01`, until: iso(today) };
    default:
      return { since: "", until: "" };
  }
}
