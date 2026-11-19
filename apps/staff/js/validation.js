/**
 * Pure validation rules shared by the application form and the manager views. No DOM, no
 * network — tested with `node --test` (tests/validation.test.mjs). The server applies the same
 * rules (services/account/hiring.py); these only give immediate feedback.
 */

export const MIN_HIRING_AGE = 18;
export const LICENSED_ROLES = ["doctor", "nurse", "physician_assistant", "technician"];

export const ROLES = [
  ["doctor", "Doctor / physician"],
  ["nurse", "Nurse"],
  ["physician_assistant", "Physician assistant"],
  ["medical_assistant", "Medical assistant"],
  ["technician", "Technician (lab, imaging, …)"],
  ["receptionist", "Front desk / receptionist"],
  ["administrator", "Administration / billing"],
  ["other", "Other"],
];

export function roleLabel(role) {
  const found = ROLES.find(([value]) => value === role);
  return found ? found[1] : role || "";
}

/** Whole years between a date of birth (YYYY-MM-DD) and `today`. NaN for an invalid date. */
export function ageOn(dob, today = new Date()) {
  const d = parseDate(dob);
  if (!d) return NaN;
  let years = today.getFullYear() - d.getFullYear();
  const beforeBirthday =
    today.getMonth() < d.getMonth() || (today.getMonth() === d.getMonth() && today.getDate() < d.getDate());
  if (beforeBirthday) years -= 1;
  return years;
}

/** The automatic age validation: { status: 'passed'|'failed'|'invalid', note }. */
export function ageCheck(dob, today = new Date()) {
  const age = ageOn(dob, today);
  if (Number.isNaN(age)) return { status: "invalid", note: "Please enter a valid date of birth." };
  if (age < 0) return { status: "invalid", note: "The date of birth cannot be in the future." };
  if (age > 100) return { status: "invalid", note: "That is not a plausible date of birth." };
  if (age < MIN_HIRING_AGE)
    return { status: "failed", note: `You are ${age}; the minimum hiring age is ${MIN_HIRING_AGE}.` };
  return { status: "passed", note: `Age ${age} — meets the minimum of ${MIN_HIRING_AGE}.` };
}

export function normalizeLicense(value) {
  return (value || "").replace(/[^a-z0-9]/gi, "").toUpperCase();
}

/**
 * The automatic part of the licence validation (format, expiry), mirroring the server:
 * { status: 'not_applicable'|'failed'|'in_progress', note }.
 */
export function licenseCheck(role, number, authority, expires, today = new Date()) {
  if (!LICENSED_ROLES.includes(role))
    return { status: "not_applicable", note: "This role does not require a professional licence." };
  const normalized = normalizeLicense(number);
  if (!normalized)
    return {
      status: "failed",
      note: "A professional licence / registration number is required for this role.",
    };
  if (normalized.length < 4 || normalized.length > 32)
    return { status: "failed", note: "The licence number must be 4–32 letters/digits." };
  const board = authority?.trim() || "the issuing board";
  const exp = parseDate(expires);
  if (!exp)
    return expires
      ? { status: "failed", note: "Please enter a valid expiry date." }
      : { status: "in_progress", note: `Licence recorded; a manager will confirm it with ${board}.` };
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  if (exp < start) return { status: "failed", note: `The licence expired on ${expires}.` };
  const days = Math.round((exp - start) / 86_400_000);
  if (days <= 90)
    return {
      status: "in_progress",
      note: `Licence expires soon (${expires}) — renewal will be confirmed with ${board}.`,
    };
  return {
    status: "in_progress",
    note: `Licence valid until ${expires} — a manager will confirm it with ${board}.`,
  };
}

export function isEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test((value || "").trim());
}

/** 7–15 digits, or empty (optional field). */
export function isPhone(value, { required = false } = {}) {
  const digits = (value || "").replace(/\D/g, "");
  if (!digits) return !required;
  return digits.length >= 7 && digits.length <= 15;
}

/** "9:00" / "09:00:00" → "09:00", or null. */
export function normalizeTime(value) {
  const m = /^(\d{1,2}):(\d{2})/.exec((value || "").trim());
  if (!m) return null;
  const h = Number(m[1]);
  if (h > 23 || Number(m[2]) > 59) return null;
  return `${String(h).padStart(2, "0")}:${m[2]}`;
}

/** Minutes since midnight for "HH:MM" (0 when invalid). */
export function minutesOf(value) {
  const t = normalizeTime(value);
  if (!t) return 0;
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
}

/**
 * Validate a week of { weekday, available, start, end } rows → { ok, errors: [...] , hours }.
 */
export function validateWeek(rows, dayNames = WEEKDAYS) {
  const errors = [];
  let minutes = 0;
  for (const r of rows) {
    if (!r.available) continue;
    const s = normalizeTime(r.start);
    const e = normalizeTime(r.end);
    if (!s || !e) errors.push(`${dayNames[r.weekday]}: please enter a start and an end time.`);
    else if (e <= s) errors.push(`${dayNames[r.weekday]}: the end time must be after the start time.`);
    else minutes += minutesOf(e) - minutesOf(s);
  }
  return { ok: errors.length === 0, errors, hours: minutes / 60 };
}

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** The office's standard week: Monday–Friday, 9 AM – 5 PM. */
export function standardWeek() {
  return WEEKDAYS.map((_, weekday) => ({
    weekday,
    available: weekday < 5,
    start: "09:00",
    end: "17:00",
    note: "",
  }));
}

export function parseDate(value) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [y, m, d] = value.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? date : null;
}

/** Staff / application numbers: "W-000042", "w42", "42" → "W-000042". */
export function formatNumber(prefix, value) {
  const m = /^\s*[A-Za-z]?-?0*(\d{1,9})\s*$/.exec(value || "");
  return m ? `${prefix}-${m[1].padStart(6, "0")}` : (value || "").trim().toUpperCase();
}

/** A staff account's password: at least this long (the server never sees it, so it checks here). */
export const MIN_PASSWORD = 10;

/** The new password's checks before anything is sent. A message or null. */
export function passwordProblem(password, repeat, email) {
  if ((password || "").length < MIN_PASSWORD) return `Please choose a password of at least ${MIN_PASSWORD} characters.`;
  if (password.trim().toLowerCase() === (email || "").trim().toLowerCase())
    return "Please do not use your e-mail address as the password.";
  if (password !== repeat) return "The two passwords are not the same.";
  return null;
}
