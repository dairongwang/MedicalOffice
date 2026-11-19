/**
 * Pure helpers of the administration dashboard (no DOM, no network) — tested with
 * `node --test` (tests/format.test.mjs).
 */

export const ROLE_LABELS = {
  doctor: "Doctor",
  nurse: "Nurse",
  physician_assistant: "Physician assistant",
  medical_assistant: "Medical assistant",
  technician: "Technician",
  receptionist: "Receptionist",
  administrator: "Administrator",
  other: "Staff",
};

export function roleLabel(role) {
  return ROLE_LABELS[role] || role || "";
}

export const STATUS_LABELS = {
  scheduled: ["Scheduled", "ok"],
  completed: ["Completed", "muted"],
  no_show: ["No-show", "bad"],
  canceled: ["Canceled", "muted"],
};

/** Local YYYY-MM-DD of a Date (no UTC shift). */
export function isoDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function addDays(iso, days) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + days);
  return isoDate(d);
}

/** "Monday, June 7, 2027 · 09:30" (what the API formats in office time) → { day, time }. */
export function splitLocal(startLocal) {
  const [day, time] = (startLocal || "").split(" · ");
  return { day: day || startLocal || "", time: time || "" };
}

/** `#/appointments?from=…&to=…` from a filter object (empty values left out). */
export function hashFor(tab, filters) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v) q.set(k, v);
  const s = q.toString();
  return `#/${tab}${s ? "?" + s : ""}`;
}

/** "sent by e-mail p•••@example.com, text +•••0301" from the API's masked destinations. */
export function describeSentTo(sentTo) {
  return (sentTo || [])
    .map((entry) => {
      const [channel, ...rest] = entry.split(":");
      return `${channel === "sms" ? "text" : "e-mail"} ${rest.join(":")}`;
    })
    .join(", ");
}

/** Record numbers typed loosely: "p42", "P-000042", "42" → "P-000042". */
export function formatNumber(prefix, value) {
  const m = /^\s*[A-Za-z]*-?0*(\d{1,9})\s*$/.exec(value || "");
  return m ? `${prefix}-${m[1].padStart(6, "0")}` : (value || "").trim().toUpperCase();
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

/** How a patient was checked in (appointment.checked_in_via): who started the signed check-in page. */
export const CHECK_IN_VIA = {
  account: "in the app (patient account)",
  link: "in the app (appointment link)",
  identity: "in the app (lookup page)",
  staff: "with the link the front desk sent",
};

/**
 * Times of the agenda are shown in the **office's** time zone (the appointment's `timezone`, like
 * its `start_local`) and always name the zone — never the browser's own zone, so that two times in
 * one sentence can be compared ("sent … 16:40 UTC — … until 17:40 UTC"), whoever looks at them.
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

/** "Nov 5, 2026, 16:40 UTC" in the office's time zone. */
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

/** "17:40 UTC" in the office's time zone. */
export function officeTime(iso, timezone) {
  return inZone(iso, timezone, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

/** Whole minutes from one timestamp to another (the life of a check-in link). */
function minutesBetween(from, to) {
  const m = Math.round((Date.parse(to) - Date.parse(from)) / 60_000);
  return Number.isFinite(m) ? m : null;
}

/**
 * Where a visit stands for check-in, for the agenda: `{ state, text }`. States:
 *   checked-in · link-sent (the office sent the check-in page, waiting for the patient's answers
 *   and signatures until it expires) · filling-in (the patient opened the page in the app) ·
 *   failed (the reason) · open / not-yet / closed (the patient's own window) · none.
 * Every time is in the office's time zone with the zone named (officeDateTime / officeTime).
 */
export function checkInSummary(a, now = new Date()) {
  const tz = a.timezone || "UTC";
  const fmt = (iso) => officeDateTime(iso, tz);
  if (a.checked_in_at) {
    const via = CHECK_IN_VIA[a.checked_in_via];
    return {
      state: "checked-in",
      text: `Checked in${via ? " " + via : ""} on ${fmt(a.checked_in_at)} — questionnaire and financial responsibility signed`,
    };
  }
  if (a.status !== "scheduled") return { state: "none", text: "" };
  const c = a.check_in;
  const t = now.getTime();
  if (c && c.status === "pending" && t < Date.parse(c.expires_at)) {
    const until = officeTime(c.expires_at, tz);
    const started = c.link_sent_at || c.started_at;
    const minutes = started ? minutesBetween(started, c.expires_at) : null;
    const valid = minutes ? ` (${minutes} minutes)` : "";
    if (c.via === "staff") {
      const to = (c.link_sent_to || [])
        .map((d) => d.replace(/^sms:/, "text ").replace(/^email:/, "e-mail "))
        .join(", ");
      return {
        state: "link-sent",
        text: `Check-in link sent${to ? " by " + to : ""}${c.link_sent_at ? " on " + fmt(c.link_sent_at) : ""} — waiting for the patient's answers and signatures until ${until}${valid}`,
      };
    }
    return { state: "filling-in", text: `The patient opened the check-in page in the app (until ${until}${valid})` };
  }
  if (c && (c.status === "failed" || (c.status === "pending" && t >= Date.parse(c.expires_at)))) {
    return {
      state: "failed",
      text: `Check-in failed: ${c.failure_reason || "the check-in page was not completed in time."}`,
    };
  }
  const opens = a.check_in_opens_at ? Date.parse(a.check_in_opens_at) : NaN;
  const closes = a.check_in_closes_at ? Date.parse(a.check_in_closes_at) : NaN;
  if (Number.isFinite(opens) && t < opens)
    return {
      state: "not-yet",
      text: `Not checked in — the patient can check in from the app from ${fmt(a.check_in_opens_at)}`,
    };
  if (Number.isFinite(closes) && t >= closes)
    return {
      state: "closed",
      text: "Not checked in — the visit time has passed; send the check-in link or help at the desk",
    };
  return { state: "open", text: "Not checked in yet — the patient can check in from the app now" };
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

/**
 * After a check-in: was the appointment's provider told? "Dr. Alex Example notified by e-mail
 * d•••@example.com on Nov 5, 2026, 16:41 UTC" — or why not. "" before completion.
 */
export function staffNotifiedText(c, timezone) {
  if (!c || c.status !== "completed") return "";
  const who = c.staff_name || "The provider";
  if (c.staff_notified_at) {
    const to = (c.staff_notified_to || [])
      .map((d) => d.replace(/^sms:/, "text ").replace(/^email:/, "e-mail "))
      .join(", ");
    return `${who} notified${to ? " by " + to : ""} on ${officeDateTime(c.staff_notified_at, timezone)}`;
  }
  return `${who} could not be notified${c.staff_notify_note ? ` — ${c.staff_notify_note}` : ""}`;
}

/* ---------- Medical assistants (the agenda's "Assistant" column; services/account/assignments.py) ---------- */

/** The agenda's "Assistant" filter: [value, label] (value = the API's `assistant=`). */
export const ASSISTANT_FILTERS = [
  ["", "Any"],
  ["none", "No assistant yet"],
  ["open", "Assigned / taken"],
  ["reported", "Can't do it — reassign"],
  ["completed", "Task completed"],
];

/**
 * The medical assistant on a visit, for the agenda: `{ state, text, kind }` — kind is the badge
 * colour. States: none · assigned (waiting for the assistant to take it) · accepted · completed ·
 * reported (the assistant cannot do it: the office must assign someone else).
 */
export function assistantSummary(a) {
  const x = a?.assistant;
  if (!x) {
    const open = a?.status === "scheduled";
    return { state: "none", text: open ? "No medical assistant assigned" : "", kind: "" };
  }
  const tz = a.timezone || "UTC";
  const when = (iso) => (iso ? ` on ${officeDateTime(iso, tz)}` : "");
  switch (x.status) {
    case "assigned":
      return { state: "assigned", text: `${x.name} assigned${when(x.assigned_at)} — not taken yet`, kind: "warn" };
    case "accepted":
      return { state: "accepted", text: `${x.name} took the task${when(x.accepted_at)}`, kind: "ok" };
    case "completed": {
      const vitals = x.vitals?.text ? ` · ${x.vitals.text}` : "";
      return { state: "completed", text: `${x.name} completed the task${when(x.completed_at)}${vitals}`, kind: "ok" };
    }
    case "reported": {
      const why = x.report_reason_label || x.report_reason || "no reason given";
      const note = x.report_note ? ` — “${x.report_note}”` : "";
      return {
        state: "reported",
        text: `${x.name} cannot do it (${why}${note})${when(x.reported_at)} — assign someone else`,
        kind: "bad",
      };
    }
    default:
      return { state: x.status, text: `${x.name}: ${x.status}`, kind: "" };
  }
}

/** The assistant badge text on an agenda row ("MA: Jamie Helper ✓"). */
export function assistantBadge(a) {
  const x = a?.assistant;
  if (!x) return "";
  const mark = { assigned: "…", accepted: "✓", completed: "✓✓", reported: "✗" }[x.status] || "";
  return `MA: ${x.name}${mark ? " " + mark : ""}`;
}

/** A decision of the assistant's review on a medication / allergy item → [label, badge kind]. */
export function reviewDecision(item) {
  if (item?.decision === "confirmed") return ["confirmed", "ok"];
  if (item?.decision === "removed") return ["removed", "muted"];
  return ["not cleared", "warn"];
}

/** Where an entry of the patient's chart came from, as shown on the record. */
export const HEALTH_SOURCES = {
  office: "entered by the office",
  check_in: "from the patient's check-in",
  assistant: "added by a medical assistant",
  provider: "added by the provider (diagnosis / prescription)",
  seed: "dummy data",
};

/* ---------- Prescriptions and the pharmacy directory (services/account/prescriptions.py) ---------- */

/** "Lisinopril 20 mg tablet — Take 1 tablet … — 30 tablets, 3 refills, substitution permitted" */
export function rxItemText(i) {
  const refills = `${i.refills} refill${i.refills === 1 ? "" : "s"}`;
  return `${i.line} — ${i.sig} — ${i.quantity} ${i.quantity_unit}, ${refills}, ${
    i.substitution_allowed ? "substitution permitted" : "dispense as written"
  }`;
}

/** One hand-over of a prescription (e-mail to a pharmacy / PDF downloaded), as a line; `when` formats the time. */
export function rxDeliveryText(d, when = (iso) => iso) {
  const at = when(d.created_at);
  if (d.method === "pdf") return `PDF downloaded${d.by ? ` by ${d.by}` : ""} on ${at}`;
  const to = `${d.pharmacy_name || "a pharmacy"}${d.destination ? ` (${d.destination})` : ""}`;
  const notice = (d.note || "").includes("void notice") ? "“Do not fill” notice e-mailed" : "E-mailed";
  if (d.status === "sent") return `${notice} to ${to} on ${at}`;
  return `E-mail to ${to} failed on ${at}${d.note ? `: ${d.note}` : ""}`;
}

/** What is missing / wrong in the pharmacy form (the server checks the same) — messages. */
export function pharmacyProblems(p) {
  const out = [];
  if ((p.name || "").trim().length < 2) out.push("The pharmacy's name.");
  const email = (p.email || "").trim();
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) out.push("That e-mail address does not look right.");
  return out;
}

/** How prescriptions reach this pharmacy. */
export function pharmacyReach(p) {
  if (!p.active) return "not offered to prescribers (inactive)";
  return p.email
    ? `prescriptions can be e-mailed to ${p.email}`
    : "no e-mail — the PDF is printed / given to the patient";
}

/** One hand-over of a referral letter (e-mail to the specialist / the patient, the office told, PDF); `when` formats the time. */
export function referralDeliveryText(d, when = (iso) => iso) {
  const at = when(d.created_at);
  if (d.method === "pdf") return `Letter printed / downloaded${d.by ? ` by ${d.by}` : ""} on ${at}`;
  const withdrawal = (d.note || "").includes("withdrawal notice");
  const dest = (d.destination || "").replace(/email:/g, "e-mail ").replace(/sms:/g, "text ");
  if (d.recipient === "office")
    return `${withdrawal ? "Office told of the withdrawal" : "Office told"}${dest ? ` (${dest})` : ""} on ${at}`;
  const who = d.recipient === "patient" ? "the patient" : "the specialist";
  if (d.status !== "sent")
    return `E-mail to ${who}${dest ? ` (${dest})` : ""} failed on ${at}${d.note ? `: ${d.note}` : ""}`;
  return `${withdrawal ? "Withdrawal notice e-mailed" : "Letter e-mailed"} to ${who}${dest ? ` (${dest})` : ""} on ${at}`;
}

/** Where a pharmacy of the directory came from. */
export function pharmacySource(p) {
  if (p.source === "provider") return `added at a visit${p.added_by ? ` by ${p.added_by}` : ""}`;
  if (p.source === "seed") return "dummy data";
  return "";
}

/* ---------- Reports on request and immunizations (services/account/patient_reports.py, immunizations.py) ---------- */

export const REPORT_KINDS = [
  ["immunization", "Immunization record"],
  ["annual_exam", "Annual health examination report"],
];

export const REPORT_PURPOSES = [
  ["personal", "Personal records"],
  ["school", "School"],
  ["work", "Work / employment"],
  ["sports", "Sports"],
  ["travel", "Travel"],
  ["camp", "Camp"],
  ["other", "Other"],
];

/** The queue's filters: [key, label] — the counts come from the server (`counts`). */
export const REPORT_FILTERS = [
  ["open", "Open"],
  ["requested", "To prepare"],
  ["draft", "With the doctor"],
  ["declined", "Returned"],
  ["signed", "Signed"],
  ["canceled", "Canceled"],
  ["all", "All"],
];

/** status → [badge text, badge kind] */
export function reportStatusBadge(status) {
  return (
    {
      requested: ["to prepare", "warn"],
      draft: ["with the doctor", ""],
      declined: ["returned by the doctor", "bad"],
      signed: ["signed", "ok"],
      canceled: ["canceled", "muted"],
      superseded: ["superseded", "muted"],
    }[status] || [status, ""]
  );
}

/** "needed by Dec 2" (+ urgency) for an open report; null when there is no date or it is done. */
export function neededBy(report, today = new Date()) {
  if (!report.needed_by || !["requested", "draft", "declined"].includes(report.status)) return null;
  const [y, m, d] = report.needed_by.split("-").map(Number);
  const due = new Date(y, m - 1, d);
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const days = Math.round((due - start) / 86_400_000);
  const label = due.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  if (days < 0) return { text: `overdue — was needed by ${label}`, kind: "bad" };
  if (days <= 3) return { text: `needed by ${label} (${days === 0 ? "today" : `in ${days} day${days === 1 ? "" : "s"}`})`, kind: "warn" };
  return { text: `needed by ${label}`, kind: "" };
}

/** What is wrong with the immunization form (the server checks the same) — messages. */
export function immunizationProblems(v, today = new Date()) {
  const out = [];
  if ((v.vaccine || "").trim().length < 2) out.push("The vaccine.");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(v.date_given || "")) out.push("The date it was given.");
  else {
    const [y, m, d] = v.date_given.split("-").map(Number);
    if (new Date(y, m - 1, d) > today) out.push("The date cannot be in the future.");
  }
  const dose = Number(v.dose_number) || null;
  const of = Number(v.series_doses) || null;
  if (dose && of && dose > of) out.push("The dose number cannot be more than the doses of the series.");
  return out;
}
