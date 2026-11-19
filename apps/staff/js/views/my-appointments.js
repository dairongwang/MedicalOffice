import { api } from "../api.js";
import { assistantText } from "../assistant-tasks.js";
import { readiness, seenText } from "../chart-format.js";
import { session } from "../auth.js";
import {
  STATUS,
  addDays,
  ageOn,
  answerText,
  checkInLine,
  dayIn,
  groupByDay,
  officeDateTime,
  officeTime,
  quickPeriod,
  splitLocal,
} from "../staff-appointments.js";
import { badge, button, callout, describeError, field, h, input, render, select, spinner } from "../ui.js";

/**
 * Signed-in member of staff: the appointments where they are the provider, grouped by day —
 * the patient (name, record number, age), the reason, the status, whether the patient confirmed
 * and where the check-in stands; "Check-in answers" shows what the patient answered on the
 * check-in page. Once the medical assistant has prepared the patient the row says "Ready" (the provider
 * is told by e-mail / text as well); "Open chart" shows everything about the patient (patient-chart.js).
 * Times are in the office's time zone (named on the page).
 *
 * Route: #/my-appointments  ·  #/my-appointments?from=YYYY-MM-DD&to=YYYY-MM-DD&status=
 */
export function myAppointmentsView(params) {
  const root = h("section", { class: "page wide", id: "my-appointments" });
  void renderList(root, params);
  return root;
}

const hashFor = (f) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) q.set(k, v);
  const s = q.toString();
  return `#/my-appointments${s ? "?" + s : ""}`;
};

async function renderList(root, params) {
  const staff = session.staff;
  const tz = staff?.timezone || "UTC";
  const today = dayIn(new Date(), tz);
  const iso = (v) => (/^\d{4}-\d{2}-\d{2}$/.test(v || "") ? v : "");
  const filters = {
    from: iso(params.from) || today,
    to: iso(params.to) || addDays(iso(params.from) || today, 13),
    status: ["scheduled", "completed", "no_show", "canceled"].includes(params.status) ? params.status : "",
  };
  const w = staff?.worker;

  const from = input({ type: "date", value: filters.from, id: "mine-from" });
  const to = input({ type: "date", value: filters.to, id: "mine-to" });
  const status = select(
    [
      ["", "Every status"],
      ["scheduled", "Scheduled"],
      ["completed", "Completed"],
      ["no_show", "No-show"],
      ["canceled", "Canceled"],
    ],
    { id: "mine-status", value: filters.status },
  );
  const apply = () => (location.hash = hashFor({ from: from.value, to: to.value, status: status.value }));
  for (const el of [from, to, status]) el.addEventListener("change", apply);
  const quick = (label, kind) => {
    const p = quickPeriod(kind, today);
    const active = p.from === filters.from && p.to === filters.to;
    return button(label, {
      class: `btn chip ${active ? "active" : ""}`,
      id: `mine-period-${kind}`,
      "aria-pressed": String(active),
      onclick: () => (location.hash = hashFor({ ...filters, ...p })),
    });
  };

  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "My appointments"),
        h(
          "p",
          { class: "muted small", id: "mine-who" },
          w ? `${session.displayName} · ${w.staff_number} · ` : "",
          `the visits patients booked with you. Times are in the office's time zone (${tz}).`,
        ),
      ),
      button("Refresh", { class: "btn", id: "btn-mine-refresh", onclick: () => void load() }),
    ),
    h(
      "div",
      { class: "chips", role: "group", "aria-label": "Period" },
      quick("Today", "today"),
      quick("Next 7 days", "7d"),
      quick("Next 14 days", "14d"),
      quick("Past week", "past"),
    ),
    h(
      "form",
      {
        class: "agenda-filters",
        id: "mine-filters",
        onsubmit: (e) => {
          e.preventDefault();
          apply();
        },
      },
      field("From", from),
      field("To", to),
      field("Status", status),
    ),
  );
  const list = h("div", { id: "mine-list" }, spinner());
  root.append(list);
  await load();

  async function load() {
    render(list, spinner());
    const q = new URLSearchParams({ from: filters.from, to: filters.to });
    if (filters.status) q.set("status", filters.status);
    let page;
    try {
      page = await api.get(`/staff/me/appointments?${q}`);
    } catch (e) {
      render(
        list,
        callout("error", "Could not load your appointments", h("span", { id: "mine-error" }, describeError(e))),
      );
      return;
    }
    render(
      list,
      h(
        "p",
        { class: "muted small", id: "mine-count" },
        `${page.total} appointment${page.total === 1 ? "" : "s"} from ${page.date_from} to ${page.date_to}`,
      ),
    );
    if (!page.items.length) {
      list.append(
        h(
          "p",
          { class: "empty", id: "mine-empty" },
          ["doctor", "nurse", "physician_assistant"].includes(w?.role)
            ? "No appointments with you in this period."
            : "Patients book doctors, nurses and physician assistants — your role has no appointments of its own.",
        ),
      );
      return;
    }
    for (const group of groupByDay(page.items)) {
      list.append(
        h("h3", { class: "agenda-day" }, group.day, h("span", { class: "muted small" }, ` · ${group.items.length}`)),
        h(
          "ul",
          { class: "rows" },
          group.items.map((a) => appointmentRow(a)),
        ),
      );
    }
  }
}

function appointmentRow(a) {
  const { time } = splitLocal(a.start_local);
  const [label, kind] = STATUS[a.status] || [a.status, ""];
  const line = checkInLine(a);
  const ready = readiness(a);
  const seen = seenText(a);
  const answers = h("div", { class: "checkin-answers-panel", id: `mine-answers-${a.id}`, hidden: true });
  const age = ageOn(a.patient.date_of_birth);
  return h(
    "li",
    {
      class: `row appointment ${a.status}`,
      id: `mine-appt-${a.id}`,
      dataset: { id: String(a.id), checkin: line.state, ready: ready.state },
    },
    h(
      "div",
      { class: "row-main" },
      h("strong", { class: "mono mine-time" }, officeTime(a.start_at, a.timezone) || time),
      h("strong", {}, `${a.patient.first_name} ${a.patient.last_name}`),
      h("span", { class: "muted small" }, ` ${a.patient.mrn}${age !== null ? ` · ${age} y` : ""}`),
      badge(label, kind),
      a.checked_in_at ? badge("✓ checked in", "ok") : null,
      !a.checked_in_at && a.status === "scheduled" && a.confirmed_at ? badge("confirmed", "ok") : null,
      a.payment === "self_pay" ? badge("self-pay", "warn") : null,
      ready.state === "ready" && a.status === "scheduled" ? badge("ready to be seen", "ok") : null,
      seen ? badge(seen, "muted") : null,
      a.visit_summary
        ? badge(
            a.visit_summary.status === "signed" ? "summary signed" : "summary to sign",
            a.visit_summary.status === "signed" ? "ok" : "warn",
          )
        : null,
    ),
    h("div", { class: "muted small" }, h("span", { class: "mono" }, a.number), a.reason ? ` · ${a.reason}` : ""),
    line.text ? h("div", { class: `small mine-checkin ${line.state}`, id: `mine-checkin-${a.id}` }, line.text) : null,
    a.assistant && a.status === "scheduled"
      ? h(
          "div",
          { class: `small my-assistant ${a.assistant.status} state-${ready.state}`, id: `mine-assistant-${a.id}` },
          ready.text || assistantText(a.assistant),
        )
      : null,
    a.assistant?.vitals?.text
      ? h(
          "div",
          { class: "small my-vitals", id: `mine-vitals-${a.id}` },
          h("strong", {}, "Vital signs: "),
          a.assistant.vitals.text,
          a.assistant.vitals.note ? ` (${a.assistant.vitals.note})` : "",
        )
      : null,
    a.status !== "canceled"
      ? h(
          "div",
          { class: "actions" },
          h(
            "a",
            {
              class: `btn small-btn ${ready.state === "ready" ? "primary" : ""}`,
              id: `btn-mine-chart-${a.id}`,
              href: `#/patient-chart/${a.id}`,
              title:
                "The patient's chart: previous visits, medical history, medications, allergies, results, referrals, " +
                "the check-in and the medical assistant's work",
            },
            "Open chart",
          ),
          a.checked_in_at
            ? button("Check-in answers", {
                class: "btn small-btn",
                id: `btn-mine-answers-${a.id}`,
                title: "What the patient answered on the check-in page",
                onclick: () => void toggleAnswers(a, answers),
              })
            : null,
        )
      : null,
    answers,
  );
}

async function toggleAnswers(a, panel) {
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  render(panel, spinner());
  let out;
  try {
    out = await api.get(`/staff/me/appointments/${a.id}/check-in`);
  } catch (e) {
    render(panel, callout("error", "Could not load the answers", h("span", {}, describeError(e))));
    return;
  }
  render(
    panel,
    h(
      "dl",
      { class: "record" },
      out.questions
        .filter((q) => q.id in out.answers)
        .map((q) => h("div", { class: "wide" }, h("dt", {}, q.label), h("dd", {}, answerText(q, out.answers[q.id])))),
    ),
    h(
      "p",
      { class: "muted small" },
      `Signed by ${out.signer_name || "the patient"} on ${officeDateTime(out.signed_at || out.completed_at, a.timezone)}`,
    ),
  );
}
