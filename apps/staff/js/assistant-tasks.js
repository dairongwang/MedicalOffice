/**
 * Pure logic of "My tasks" (views/my-tasks.js): what a signed-in medical assistant sees of the
 * tasks the office assigned them — one per appointment to prepare (room the patient, take the
 * vitals, review medications / allergies and the check-in answers, tell the provider). No DOM, no
 * network — tested with `node --test` (tests/assistant-tasks.test.mjs).
 *
 * The life of a task (services/account/assignments.py):
 *   assigned ─take─▶ accepted ─complete (every duty confirmed)─▶ completed
 *       └────────────┴─"I can't do it"─▶ reported  (the office assigns someone else)
 *   canceled = withdrawn by the office (reassigned / taken off / the visit was canceled).
 */
import { officeDateTime, officeTime } from "./staff-appointments.js";

/** status → [label, badge kind] */
export const TASK_STATUS = {
  assigned: ["New", "warn"],
  accepted: ["Taken", "ok"],
  completed: ["Completed", "muted"],
  reported: ["Reported — can't do it", "bad"],
  canceled: ["Withdrawn", "muted"],
};

/** The status filter of the page: [value, label] (value = the API's `status`). */
export const TASK_FILTERS = [
  ["", "Every status"],
  ["open", "To do (new or taken)"],
  ["assigned", "New"],
  ["accepted", "Taken"],
  ["completed", "Completed"],
  ["reported", "Reported"],
  ["canceled", "Withdrawn"],
];

export function isOpen(task) {
  return task?.status === "assigned" || task?.status === "accepted";
}

/**
 * One line on where the task stands, for the assistant: `{ state, text }` with state
 * new · taken · ready · done · reported · withdrawn.
 */
export function taskLine(task, now = new Date()) {
  const tz = task?.appointment?.timezone || "UTC";
  switch (task?.status) {
    case "assigned":
      return { state: "new", text: "New task — take it, or tell the office if you cannot do it." };
    case "accepted": {
      if (task.can_complete) return { state: "ready", text: "Taken — confirm every duty when you are done." };
      const opens = Date.parse(task.complete_opens_at);
      if (!Number.isNaN(opens) && now.getTime() < opens)
        return {
          state: "taken",
          text: `Taken — it can be completed from ${officeDateTime(task.complete_opens_at, tz)}.`,
        };
      return { state: "taken", text: `Taken — the visit is ${(task.appointment?.status || "").replace("_", " ")}.` };
    }
    case "completed":
      return {
        state: "done",
        text: `Completed ${task.completed_at ? officeDateTime(task.completed_at, tz) : ""}`.trim(),
      };
    case "reported": {
      const why = task.report_reason_label || task.report_reason || "";
      const note = task.report_note ? ` — ${task.report_note}` : "";
      return { state: "reported", text: `You reported: ${why}${note}. The office assigns someone else.` };
    }
    case "canceled":
      return { state: "withdrawn", text: `Withdrawn${task.cancel_reason ? `: ${task.cancel_reason}` : ""}` };
    default:
      return { state: "", text: "" };
  }
}

/** Client-side check of the report form — the server applies the same rule. null = fine. */
export function reportProblem(reason, note) {
  if (!reason) return "Please choose why you cannot do this task.";
  if (reason === "other" && !(note || "").trim()) return "Please say why you cannot do this task.";
  return null;
}

/** "Monday, June 7, 2027 · 09:30" → { day, time } (the appointment's office-local start). */
function splitStart(task) {
  const [day, time] = String(task?.appointment?.start_local || "").split(" · ");
  return { day: day || "", time: time || "" };
}

/** The tasks grouped by the visit's office-local day, in order: [{ day, items }]. */
export function groupTasksByDay(items) {
  const groups = [];
  for (const t of Array.isArray(items) ? items : []) {
    const { day } = splitStart(t);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.items.push(t);
    else groups.push({ day, items: [t] });
  }
  return groups;
}

/** The visit's start time for the row ("10:00 UTC"). */
export function taskTime(task) {
  return officeTime(task?.appointment?.start_at, task?.appointment?.timezone) || splitStart(task).time;
}

/** How many tasks are in each state: { new, taken, completed, reported, withdrawn, open }. */
export function taskCounts(items) {
  const out = { new: 0, taken: 0, completed: 0, reported: 0, withdrawn: 0, open: 0 };
  for (const t of Array.isArray(items) ? items : []) {
    if (t.status === "assigned") out.new += 1;
    else if (t.status === "accepted") out.taken += 1;
    else if (t.status === "completed") out.completed += 1;
    else if (t.status === "reported") out.reported += 1;
    else if (t.status === "canceled") out.withdrawn += 1;
    if (isOpen(t)) out.open += 1;
  }
  return out;
}

/** "Medical assistant: Jamie Helper (taken)" — the provider's view of who prepares the visit. */
export function assistantText(assistant) {
  if (!assistant) return "";
  const state = {
    assigned: "assigned",
    accepted: "taken",
    completed: "done",
    reported: "cannot do it — the office is reassigning",
    canceled: "withdrawn",
  }[assistant.status];
  return `Medical assistant: ${assistant.name}${state ? ` (${state})` : ""}`;
}

/* ---------- The review form ("Review & complete…"): services/account/assistant_review.py ---------- */

/** The hint on a medication / allergy item → [badge text, badge kind]. */
export function flagBadge(item) {
  switch (item?.flag) {
    case "changed":
      return ["Check-in differs", "warn"];
    case "new":
      return ["New from check-in", "warn"];
    case "not_mentioned":
      return ["Not in check-in list", "warn"];
    case "said_none":
      return ["Patient wrote “none”", "bad"];
    case "match":
      return ["Matches check-in", "ok"];
    default:
      return item?.origin === "assistant" ? ["Added by you", ""] : ["On record", ""];
  }
}

/** The two ways to clear an item, worded for its list: [[value, label], …]. */
export function decisionOptions(kind) {
  return kind === "allergy"
    ? [
        ["confirmed", "Confirmed"],
        ["removed", "Not an allergy / resolved — remove"],
      ]
    : [
        ["confirmed", "Taking — confirmed"],
        ["removed", "Not taking — remove"],
      ];
}

const RANGES = {
  bp_systolic: [50, 260, "Blood pressure (systolic)"],
  bp_diastolic: [30, 160, "Blood pressure (diastolic)"],
  pulse: [20, 250, "Pulse"],
};

/** A number typed into a field → number, or null when empty / not a number. */
export function toNumber(value) {
  const text = String(value ?? "")
    .trim()
    .replace(",", ".");
  if (!text) return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : NaN;
}

/**
 * Plausibility of the vital signs as typed (the server applies the same limits) → messages.
 * `v`: { bp_systolic, bp_diastolic, pulse, temperature, temperature_unit, weight, weight_unit }.
 */
export function vitalsProblems(v) {
  const out = [];
  for (const [key, [low, high, label]] of Object.entries(RANGES)) {
    const n = v?.[key];
    if (n === null || n === undefined) continue;
    if (Number.isNaN(n) || !Number.isInteger(n)) out.push(`${label}: a whole number, please.`);
    else if (n < low || n > high) out.push(`${label}: ${n} is not plausible (${low}–${high}).`);
  }
  if (Number.isInteger(v?.bp_systolic) && Number.isInteger(v?.bp_diastolic) && v.bp_systolic <= v.bp_diastolic)
    out.push("Blood pressure: the systolic (upper) value must be above the diastolic (lower) one.");
  if (v?.temperature !== null && v?.temperature !== undefined) {
    const [low, high] = v.temperature_unit === "C" ? [32, 43.5] : [90, 110];
    if (Number.isNaN(v.temperature) || v.temperature < low || v.temperature > high)
      out.push(
        `Temperature: not plausible in °${v.temperature_unit || "F"} (${low}–${high}) — check the value and the unit.`,
      );
  }
  if (v?.weight !== null && v?.weight !== undefined) {
    const [low, high] = v.weight_unit === "kg" ? [0.5, 450] : [1, 1000];
    if (Number.isNaN(v.weight) || v.weight < low || v.weight > high)
      out.push(`Weight: not plausible in ${v.weight_unit || "lb"} — check the value and the unit.`);
  }
  return out;
}

/**
 * What is still missing before the task can be completed — the same rules as the server
 * (assistant_review.progress), for instant feedback while typing. `state`:
 * { room_patient, vitals, medications: [{name, decision}], allergies: [...], no_medications, no_allergies,
 *   has_check_in, check_in_reviewed, check_in_note, provider_ready } → { done: [duty keys], problems: [...] }.
 */
export function reviewProgress(state) {
  const done = [];
  const problems = [];
  if (state.room_patient) done.push("room_patient");
  else problems.push("Room the patient and confirm their identity (name and date of birth).");
  const v = state.vitals || {};
  const missing = [
    ["bp_systolic", "blood pressure (systolic)"],
    ["bp_diastolic", "blood pressure (diastolic)"],
    ["pulse", "pulse"],
    ["temperature", "temperature"],
    ["weight", "weight"],
  ]
    .filter(([key]) => v[key] === null || v[key] === undefined)
    .map(([, label]) => label);
  const implausible = vitalsProblems(v);
  if (missing.length) problems.push(`Vital signs: enter the ${missing.join(", ")}.`);
  problems.push(...implausible);
  if (!missing.length && !implausible.length) done.push("vitals");
  for (const [list, duty, none, noneLabel, label] of [
    ["medications", "medications", "no_medications", "the patient takes no medications", "Medications"],
    ["allergies", "allergies", "no_allergies", "no known allergies", "Allergies"],
  ]) {
    const items = (state[list] || []).filter((i) => (i.name || "").trim());
    const open = items.filter((i) => !i.decision).map((i) => i.name);
    const confirmed = items.filter((i) => i.decision === "confirmed").map((i) => i.name);
    const before = problems.length;
    if (open.length) problems.push(`${label}: clear every item — still open: ${open.join(", ")}.`);
    if (!open.length && !confirmed.length && !state[none])
      problems.push(`${label}: nothing is confirmed — confirm that ${noneLabel}, or add what the patient says.`);
    if (confirmed.length && state[none])
      problems.push(`${label}: “${noneLabel}” is ticked, but ${confirmed.join(", ")} is confirmed.`);
    const needNote = items.filter((i) => i.decision === "removed" && i.fromChart && !(i.note || "").trim());
    if (needNote.length)
      problems.push(`${label}: add a note saying why ${needNote.map((i) => i.name).join(", ")} is removed.`);
    if (problems.length === before) done.push(duty);
  }
  const before = problems.length;
  if (!state.check_in_reviewed) problems.push("Check-in answers: review them with the patient and tick “reviewed”.");
  if (!state.has_check_in && !(state.check_in_note || "").trim())
    problems.push("Check-in answers: the patient has not checked in — add a note on how the questions were covered.");
  if (problems.length === before) done.push("check_in_review");
  if (state.provider_ready) done.push("provider_ready");
  else problems.push("Tell the provider the patient is ready, then tick it.");
  return { done, problems };
}
