/**
 * Pure logic of "My appointments" (views/my-appointments.js): the list a signed-in member of
 * staff sees of the visits where they are the provider. No DOM, no network — tested with
 * `node --test` (tests/staff-appointments.test.mjs).
 *
 * Every time is shown in the office's time zone (the list's `timezone`, like each visit's
 * `start_local`) with the zone named — never the browser's zone.
 */

function inZone(iso, timezone, options) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(+d)) return String(iso);
  try {
    return d.toLocaleString("en-US", { ...options, timeZone: timezone || "UTC", timeZoneName: "short" });
  } catch {
    return d.toLocaleString("en-US", { ...options, timeZone: "UTC", timeZoneName: "short" });
  }
}

/** "Nov 5, 2026, 16:40 UTC" */
export function officeDateTime(iso, timezone) {
  return inZone(iso, timezone, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

/** "16:40 UTC" */
export function officeTime(iso, timezone) {
  return inZone(iso, timezone, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

export const STATUS = {
  scheduled: ["Scheduled", "ok"],
  completed: ["Completed", "muted"],
  no_show: ["No-show", "bad"],
  canceled: ["Canceled", "muted"],
};

/** "Monday, June 7, 2027 · 09:30" (what the API formats in office time) → { day, time }. */
export function splitLocal(startLocal) {
  const [day, time] = String(startLocal || "").split(" · ");
  return { day: day || String(startLocal || ""), time: time || "" };
}

/** The visits grouped by office-local day, in order: [{ day, items }]. */
export function groupByDay(items) {
  const groups = [];
  for (const a of Array.isArray(items) ? items : []) {
    const { day } = splitLocal(a.start_local);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.items.push(a);
    else groups.push({ day, items: [a] });
  }
  return groups;
}

/** Whole years between a YYYY-MM-DD date of birth and `today`. */
export function ageOn(dob, today = new Date()) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dob || "");
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]) - 1, Number(m[3])];
  let years = today.getFullYear() - y;
  if (today.getMonth() < mo || (today.getMonth() === mo && today.getDate() < d)) years -= 1;
  return years;
}

const VIA = {
  account: "in the app",
  link: "in the app (appointment link)",
  identity: "in the app",
  staff: "with the front desk's link",
};

/**
 * Where the patient stands for check-in, for the provider: `{ state, text }` with state
 * checked-in · link-sent · filling-in · failed · not-yet · open · closed · none.
 */
export function checkInLine(a, now = new Date()) {
  const tz = a.timezone || "UTC";
  if (a.checked_in_at) {
    const via = VIA[a.checked_in_via] || "";
    return { state: "checked-in", text: `Checked in at ${officeTime(a.checked_in_at, tz)}${via ? " " + via : ""}` };
  }
  if (a.status !== "scheduled") return { state: "none", text: "" };
  const c = a.check_in;
  const t = now.getTime();
  if (c && c.status === "pending" && t < Date.parse(c.expires_at)) {
    return c.via === "staff"
      ? {
          state: "link-sent",
          text: `Check-in link sent — waiting for the patient until ${officeTime(c.expires_at, tz)}`,
        }
      : { state: "filling-in", text: `The patient is checking in (until ${officeTime(c.expires_at, tz)})` };
  }
  if (c && (c.status === "failed" || (c.status === "pending" && t >= Date.parse(c.expires_at)))) {
    return { state: "failed", text: `Check-in failed: ${c.failure_reason || "not completed in time."}` };
  }
  if (a.check_in_opens_at && t < Date.parse(a.check_in_opens_at))
    return { state: "not-yet", text: `Not checked in — check-in opens ${officeDateTime(a.check_in_opens_at, tz)}` };
  if (a.check_in_closes_at && t >= Date.parse(a.check_in_closes_at)) return { state: "closed", text: "Not checked in" };
  return { state: "open", text: "Not checked in yet" };
}

/** YYYY-MM-DD of a Date in a time zone (the office's "today"). */
export function dayIn(date, timezone) {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone || "UTC",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(date);
    return parts; // en-CA formats as YYYY-MM-DD
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

export function addDays(iso, days) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** The quick periods, as office-local days: { from, to }. */
export function quickPeriod(kind, today) {
  switch (kind) {
    case "today":
      return { from: today, to: today };
    case "7d":
      return { from: today, to: addDays(today, 6) };
    case "past":
      return { from: addDays(today, -7), to: addDays(today, -1) };
    default: // "14d" — the list's default
      return { from: today, to: addDays(today, 13) };
  }
}

/** An answer of the check-in questionnaire as text ("Yes", "3 / 10", the option's label, …). */
export function answerText(question, value) {
  if (value === null || value === undefined || value === "") return "—";
  switch (question.kind) {
    case "consent":
      return value === true ? "Yes — consented" : "No";
    case "scale":
      return `${value} / 10`;
    case "choice":
    case "yesno":
      return (question.options || []).find((o) => o.value === value)?.label || String(value);
    default:
      return String(value);
  }
}
