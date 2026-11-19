/**
 * Pure logic of the patient's chart (views/patient-chart.js) and the provider's "ready" line on
 * My appointments. No DOM, no network — tested with `node --test` (tests/chart-format.test.mjs).
 */
import { officeDateTime, officeTime } from "./staff-appointments.js";

/** The medical history, in the order the chart shows it. */
export const HISTORY_GROUPS = [
  ["condition", "Conditions"],
  ["surgery", "Surgeries & procedures"],
  ["family_history", "Family history"],
  ["social_history", "Social history"],
];

/** The history items grouped by kind (empty groups left out): [{ kind, label, items }]. */
export function groupHistory(items) {
  const list = Array.isArray(items) ? items : [];
  return HISTORY_GROUPS.map(([kind, label]) => ({ kind, label, items: list.filter((i) => i.kind === kind) })).filter(
    (g) => g.items.length,
  );
}

/** A lab value's flag as a word. */
export function flagLabel(flag) {
  return { H: "High", L: "Low", A: "Abnormal" }[flag] || "";
}

/** "Penicillin (rash) · Latex" — or a clear "none on record" for the allergy banner. */
export function allergyBanner(allergies) {
  const current = (allergies || []).filter((a) => a.status === "active");
  if (!current.length) return { known: false, text: "No known allergies on record" };
  return {
    known: true,
    text: current.map((a) => (a.detail ? `${a.name} (${a.detail})` : a.name)).join(" · "),
  };
}

/** How many values of the results are flagged (the "results" heading shows it). */
export function abnormalCount(results) {
  return (results || []).filter((r) => r.abnormal).length;
}

/** "Resulted Nov 3, 2026 · Example Labs" — the line under a result's name. */
export function resultMeta(r) {
  const parts = [];
  const day = r.resulted_on || r.ordered_on;
  if (day) parts.push(`${r.resulted_on ? "Resulted" : "Ordered"} ${formatDay(day)}`);
  if (r.performed_by) parts.push(r.performed_by);
  if (r.ordered_by) parts.push(`ordered by ${r.ordered_by}${r.appointment_number ? ` (${r.appointment_number})` : ""}`);
  return parts.join(" · ");
}

export function formatDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  if (!m) return iso || "";
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}

/** "Outgoing to Dummy Cardiology (Cardiology) · Nov 3, 2026 · valid until … · scheduled". */
export function referralLine(r) {
  const where = r.direction === "outgoing" ? `To ${r.counterpart}` : `From ${r.counterpart}`;
  const parts = [`${where}${r.specialty ? ` (${r.specialty})` : ""}`, formatDay(r.referral_date)];
  if (r.valid_until) parts.push(`${r.expired ? "expired" : "valid until"} ${formatDay(r.valid_until)}`);
  parts.push(r.status);
  return parts.join(" · ");
}

/**
 * Where the patient stands for the provider, on My appointments: `{ state, text }` with state
 * ready (the assistant finished — the provider was told) · preparing · assigned · reassigning · none.
 */
export function readiness(a) {
  const x = a?.assistant;
  const tz = a?.timezone || "UTC";
  if (!x || a.status === "canceled") return { state: "none", text: "" };
  if (x.status === "completed") {
    const how = (x.provider_notify_channels || []).map((c) => (c === "sms" ? "text" : "e-mail")).join(" and ");
    return {
      state: "ready",
      text:
        `Ready — ${x.name} finished preparing the patient at ${officeTime(x.completed_at, tz)}` +
        (how ? ` (you were told by ${how})` : ""),
    };
  }
  if (x.status === "accepted") return { state: "preparing", text: `${x.name} is preparing the patient` };
  if (x.status === "assigned") return { state: "assigned", text: `${x.name} will prepare the patient` };
  if (x.status === "reported") return { state: "reassigning", text: "The office is assigning another assistant" };
  return { state: "none", text: "" };
}

/** "Seen from 10:04 UTC" once the provider started the visit. */
export function seenText(a) {
  return a?.provider_seen_at ? `Seen from ${officeTime(a.provider_seen_at, a.timezone)}` : "";
}

/** The previous visit's heading line: "Aug 19, 2026, 10:00 UTC · Dr. Alex Example · completed". */
export function visitHeading(v, timezone) {
  return [officeDateTime(v.start_at, timezone), v.provider_name, v.status.replace("_", " ")].join(" · ");
}

/* ---------- Diagnoses and prescriptions (services/account/prescriptions.py) ---------- */

/** ICD-10-CM as the server accepts it: "I10", "E11.9", "R73.03" (empty = no code). */
export function icd10Ok(code) {
  const c = String(code || "")
    .trim()
    .toUpperCase();
  return !c || /^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$/.test(c);
}

/** "I10 — Essential hypertension" */
export function diagnosisLine(d) {
  return d.code ? `${d.code} — ${d.description}` : d.description;
}

export const DOSAGE_FORMS = [
  ["tablet", "tablet", "tablets"],
  ["capsule", "capsule", "capsules"],
  ["oral solution", "oral solution", "mL"],
  ["oral suspension", "oral suspension", "mL"],
  ["inhaler", "inhaler", "inhalers"],
  ["cream", "cream", "g"],
  ["ointment", "ointment", "g"],
  ["drops", "drops", "mL"],
  ["patch", "patch", "patches"],
  ["injection", "injection", "doses"],
  ["other", "other", "units"],
];

/** The usual unit a quantity of this form is counted in. */
export function unitFor(form) {
  return (DOSAGE_FORMS.find(([value]) => value === form) || [null, null, "units"])[2];
}

/** What is wrong with one medication line before it is sent (the server checks the same) — messages. */
export function rxItemProblems(item, n = 1) {
  const out = [];
  const label = (item.drug || "").trim() || `Medication ${n}`;
  if ((item.drug || "").trim().length < 2) out.push(`Medication ${n}: the drug's name.`);
  if ((item.sig || "").trim().length < 3) out.push(`${label}: the directions (how to take it).`);
  const q = Number(item.quantity);
  if (!Number.isInteger(q) || q < 1 || q > 1000) out.push(`${label}: a quantity from 1 to 1000.`);
  const r = Number(item.refills ?? 0);
  if (!Number.isInteger(r) || r < 0 || r > 11) out.push(`${label}: refills from 0 to 11.`);
  return out;
}

/** "Lisinopril 20 mg tablet — Take 1 tablet … — 30 tablets, 3 refills, substitution permitted" */
export function rxItemText(i) {
  const refills = `${i.refills} refill${i.refills === 1 ? "" : "s"}`;
  return `${i.line} — ${i.sig} — ${i.quantity} ${i.quantity_unit}, ${refills}, ${
    i.substitution_allowed ? "substitution permitted" : "dispense as written"
  }`;
}

/** One hand-over of a prescription, as a line. */
export function deliveryText(d, timezone) {
  const when = officeDateTime(d.created_at, timezone);
  if (d.method === "pdf") return `PDF downloaded${d.by ? ` by ${d.by}` : ""} on ${when}`;
  const to = `${d.pharmacy_name || "a pharmacy"}${d.destination ? ` (${d.destination})` : ""}`;
  const notice = (d.note || "").includes("void notice") ? "“Do not fill” notice e-mailed" : "E-mailed";
  if (d.status === "sent") return `${notice} to ${to} on ${when}`;
  return `E-mail to ${to} failed on ${when}${d.note ? `: ${d.note}` : ""}`;
}

/* ---------- The patient's pharmacy and referrals (prescriptions.py, referrals.py) ---------- */

/** "10 Main Street · phone +1 555 010 0401 · fax +1 555 010 0402" */
export function pharmacyContact(p) {
  if (!p) return "";
  return [p.address, p.phone ? `phone ${p.phone}` : "", p.fax ? `fax ${p.fax}` : ""].filter(Boolean).join(" · ");
}

/** The pharmacy a new prescription goes to by default: the patient's, when it takes e-mail. */
export function defaultPharmacyId(chart) {
  const p = chart?.patient_pharmacy?.pharmacy;
  const offered = (chart?.pharmacies || []).some((x) => x.id === p?.id);
  return p && p.email && p.active && offered ? String(p.id) : "";
}

export const SPECIALTIES = [
  "Allergy & immunology",
  "Cardiology",
  "Dermatology",
  "Endocrinology",
  "ENT (otolaryngology)",
  "Gastroenterology",
  "General surgery",
  "Hematology",
  "Nephrology",
  "Neurology",
  "Obstetrics & gynecology",
  "Oncology",
  "Ophthalmology",
  "Orthopedics",
  "Physical therapy",
  "Psychiatry",
  "Pulmonology",
  "Rheumatology",
  "Urology",
];

/** YYYY-MM-DD `days` after `iso` (calendar days, no time-zone shift). */
export function plusDays(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return date.toISOString().slice(0, 10);
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** What is missing / wrong in a referral before it is signed (the server checks the same) — messages. */
export function referralProblems(r, { signed = false, signer = "the provider", today = "" } = {}) {
  const out = [];
  if ((r.counterpart || "").trim().length < 2) out.push("Who the patient is referred to (the specialist / practice).");
  if ((r.specialty || "").trim().length < 2) out.push("The specialty.");
  if ((r.reason || "").trim().length < 3) out.push("The reason for the referral.");
  const email = (r.counterpart_email || "").trim();
  if (email && !EMAIL.test(email)) out.push("The specialist's e-mail address does not look right.");
  if (r.send_to_specialist && !email) out.push("The specialist's e-mail address — to e-mail the letter.");
  if (r.valid_until && today && r.valid_until < today) out.push("“Valid until” is in the past.");
  if (!signed) out.push(`Tick the signature: “I, ${signer}, sign this referral”.`);
  return out;
}

/** One hand-over of a referral letter, as a line. */
export function referralDeliveryText(d, timezone) {
  const when = officeDateTime(d.created_at, timezone);
  if (d.method === "pdf") return `Letter downloaded${d.by ? ` by ${d.by}` : ""} on ${when}`;
  const who =
    { specialist: "the specialist", patient: "the patient", office: "the office" }[d.recipient] || d.recipient;
  const to = `${who}${d.destination ? ` (${d.destination})` : ""}`;
  const withdrawal = (d.note || "").includes("withdrawal notice");
  if (d.status !== "sent") return `E-mail to ${to} failed on ${when}${d.note ? `: ${d.note}` : ""}`;
  if (d.recipient === "office") {
    const how = (d.destination || "").replace(/email:/g, "e-mail ").replace(/sms:/g, "text ");
    return `${withdrawal ? "The office told of the withdrawal" : "The office told"}${how ? ` (${how})` : ""} on ${when}`;
  }
  return `${withdrawal ? "Withdrawal notice e-mailed" : "Letter e-mailed"} to ${to} on ${when}`;
}
