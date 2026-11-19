import { api } from "../api.js";
import {
  abnormalCount,
  allergyBanner,
  diagnosisLine,
  flagLabel,
  formatDay,
  groupHistory,
  readiness,
  referralLine,
  resultMeta,
  visitHeading,
} from "../chart-format.js";
import { answerText, officeDateTime, officeTime } from "../staff-appointments.js";
import { badge, button, callout, describeError, field, h, render, spinner, toast } from "../ui.js";
import {
  diagnosesCard,
  pharmacyCard,
  prescriptionList,
  prescriptionsSection,
  updateIndications,
  updateSendTo,
} from "./chart-orders.js";
import { referralList, referralsSection, updateReferralDiagnoses } from "./chart-referrals.js";
import { refreshSummaryAfterNote, refreshSummaryPreview, summarySection } from "./chart-summary.js";

/**
 * The patient's chart for one of the provider's visits (GET /staff/me/appointments/{id}/chart) —
 * opened from My appointments or from the "ready" message once the medical assistant is done:
 *
 *   today's visit   the provider's note ("Start visit", note, assessment, plan), today's diagnoses, the
 *                   assistant's work (vital signs, the reviewed medications / allergies with decisions and
 *                   notes), the patient's check-in answers
 *   prescriptions   the patient's pharmacy (asked at the visit); write, sign & issue; PDF; e-mail to a
 *                   pharmacy; void (views/chart-orders.js)
 *   referrals       write a referral — a signed letter for the specialist (PDF, e-mail to the specialist /
 *                   the patient, withdraw) — and the referral notes of the office (views/chart-referrals.js)
 *   visit summary   what the patient gets after the visit: prepared from the chart, edited and signed by the
 *                   doctor, sent to the patient (views/chart-summary.js)
 *   the record      medications, allergies, medical history, immunizations, lab and imaging results, referral notes,
 *                   previous visits (with their notes)
 *
 * Route: #/patient-chart/<appointment id>
 */
export function patientChartView(params) {
  const root = h("section", { class: "page wide", id: "patient-chart" }, spinner("Opening the chart…"));
  void load(root, params.id);
  return root;
}

/**
 * After a diagnosis, prescription, referral or the patient's pharmacy changes, the chart is fetched again
 * and only the parts that can change are redrawn (diagnoses, the pharmacy, the prescription and referral
 * lists, medications, medical history) — a visit note, a prescription or a referral being typed is kept.
 * `resetForm` starts a fresh prescription (after issuing), `resetReferral` a fresh referral (after sending).
 */
function refresher(root, id) {
  const refresh = async (focusId, { resetForm = false, resetReferral = false } = {}) => {
    let chart;
    try {
      chart = await api.get(`/staff/me/appointments/${encodeURIComponent(id)}/chart`);
    } catch (e) {
      toast(describeError(e), { error: true });
      return;
    }
    const swap = (selector, el) => root.querySelector(selector)?.replaceWith(el);
    const body = (key, ...children) => {
      const section = root.querySelector(`#chart-${key}`);
      if (!section) return;
      const title = section.querySelector("h3");
      render(section, title, ...children);
    };
    swap("#chart-diagnoses", diagnosesCard(chart, refresh));
    if (resetForm || !root.querySelector("#rx-form")) body("prescriptions", prescriptionsSection(chart, refresh));
    else {
      swap("#chart-pharmacy", pharmacyCard(chart, refresh));
      swap("#chart-rx-list", prescriptionList(chart, refresh));
      updateIndications(chart);
      updateSendTo(chart);
    }
    if (resetReferral || !root.querySelector("#ref-form")) body("referrals", referralsSection(chart, refresh));
    else {
      swap("#chart-ref-list", referralList(chart, refresh));
      updateReferralDiagnoses(chart);
    }
    body("medications", itemList(chart.medications, "No medications on record."));
    body("history", historyList(chart.medical_history));
    void refreshSummaryPreview(chart); // a draft visit summary follows the chart
    if (focusId) root.querySelector(`#${focusId}`)?.scrollIntoView({ block: "nearest" });
  };
  return refresh;
}

async function load(root, id) {
  const reload = refresher(root, id);
  let chart;
  try {
    chart = await api.get(`/staff/me/appointments/${encodeURIComponent(id)}/chart`);
  } catch (e) {
    render(
      root,
      callout("error", "Could not open the chart", h("span", { id: "chart-error" }, describeError(e))),
      h("p", {}, h("a", { href: "#/my-appointments" }, "← My appointments")),
    );
    return;
  }
  const a = chart.appointment;
  const p = chart.patient;
  const tz = a.timezone;
  const allergies = allergyBanner(chart.allergies);
  const ready = readiness(a);
  const sections = [];
  const sectionEl = (key, title, ...body) => {
    const el = h("section", { class: "card chart-section", id: `chart-${key}` }, h("h3", {}, title), ...body);
    sections.push([key, title, el]);
    return el;
  };

  const today = sectionEl(
    "today",
    "Today's visit",
    visitNoteCard(a, chart.visit_note),
    diagnosesCard(chart, reload),
    assistantWork(chart.assistant_work, tz),
    h("h4", {}, "Check-in answers"),
    checkInAnswers(chart.check_in, tz),
  );
  const prescriptions = sectionEl("prescriptions", "Prescriptions", prescriptionsSection(chart, reload));
  const medications = sectionEl("medications", "Medications", itemList(chart.medications, "No medications on record."));
  const allergySection = sectionEl(
    "allergies",
    "Allergies",
    itemList(chart.allergies, "No known allergies on record."),
  );
  const history = sectionEl("history", "Medical history", historyList(chart.medical_history));
  const shots = (chart.immunizations || []).filter((i) => i.status === "active");
  const immunizations = sectionEl(
    "immunizations",
    `Immunizations · ${shots.length}`,
    shots.length
      ? h(
          "ul",
          { class: "chart-items", id: "chart-immunization-list" },
          shots.map((i) => h("li", { dataset: { id: String(i.id) } }, i.line)),
        )
      : h("p", { class: "empty" }, "No immunizations on record."),
  );
  const abnormal = abnormalCount(chart.results);
  const results = sectionEl(
    "results",
    `Lab & imaging results${abnormal ? ` · ${abnormal} with values out of range` : ""}`,
    chart.results.length
      ? h(
          "ul",
          { class: "rows" },
          chart.results.map((r) => resultCard(r)),
        )
      : h("p", { class: "empty" }, "No results on record."),
  );
  const referrals = sectionEl("referrals", "Referrals", referralsSection(chart, reload));
  const summary = sectionEl("summary", "Visit summary", summarySection(chart));
  const previous = sectionEl(
    "previous",
    `Previous visits · ${chart.previous_visits.length}`,
    chart.previous_visits.length
      ? h(
          "ul",
          { class: "rows" },
          chart.previous_visits.map((v) => previousVisit(v, tz)),
        )
      : h("p", { class: "empty" }, "No earlier visits at the office."),
  );

  const v = chart.assistant_work?.vitals;
  render(
    root,
    h(
      "header",
      { class: "page-header chart-header" },
      h(
        "div",
        {},
        h("h2", { id: "chart-patient" }, `${p.first_name} ${p.last_name}`),
        h(
          "p",
          { class: "muted small" },
          h("span", { class: "mono" }, p.mrn),
          ` · ${p.age} y · born ${formatDay(p.date_of_birth)}${p.sex ? ` · ${p.sex}` : ""}`,
          p.insurance_provider
            ? ` · ${p.insurance_provider}${p.insurance_plan_name ? ` — ${p.insurance_plan_name}` : ""}`
            : " · self-pay",
        ),
        h(
          "p",
          { class: "small" },
          h("span", { class: "mono" }, a.number),
          ` · ${a.start_local} (${tz})${a.reason ? ` · ${a.reason}` : ""} `,
          a.checked_in_at
            ? badge(`checked in ${officeTime(a.checked_in_at, tz)}`, "ok")
            : badge("not checked in", "warn"),
          ready.state === "ready" ? badge("ready", "ok") : null,
          a.status !== "scheduled" ? badge(a.status.replace("_", " "), "muted") : null,
        ),
        ready.text ? h("p", { class: `small chart-ready ${ready.state}`, id: "chart-ready" }, ready.text) : null,
      ),
      h("a", { class: "btn", href: "#/my-appointments", id: "chart-back" }, "← My appointments"),
    ),
    h(
      "div",
      { class: `callout ${allergies.known ? "error" : "info"} chart-allergy-banner`, id: "chart-allergy-banner" },
      h("strong", {}, "Allergies"),
      h("span", {}, ` ${allergies.text}`),
    ),
    v?.text
      ? h("p", { class: "chart-vitals-line", id: "chart-vitals" }, h("strong", {}, "Vital signs today: "), v.text)
      : null,
    p.insurance_verification?.referral_required
      ? callout("warn", "Referral required", " The patient's plan needs a referral for specialist visits.")
      : null,
    h(
      "nav",
      { class: "chips chart-nav", "aria-label": "Sections of the chart" },
      sections.map(([key, title, el]) =>
        button(title.split(" · ")[0], {
          class: "btn chip",
          id: `chart-go-${key}`,
          onclick: () => el.scrollIntoView({ behavior: "smooth", block: "start" }),
        }),
      ),
    ),
    today,
    prescriptions,
    medications,
    allergySection,
    history,
    immunizations,
    results,
    referrals,
    summary,
    previous,
    h("p", { class: "muted small" }, `Chart as of ${officeDateTime(chart.generated_at, tz)} · opening it is recorded.`),
  );
}

/** The provider's own note: "Start visit" (once), then the note, assessment and plan. */
function visitNoteCard(a, note) {
  const box = h("div", { class: "visit-note", id: "chart-visit-note" });
  const draw = (n) => {
    const text = (key, label, rows) => {
      const el = h("textarea", { class: "input", id: `visit-${key}`, rows, maxlength: key === "note" ? 10000 : 5000 });
      el.value = n?.[key] || "";
      return field(label, el, { optional: true });
    };
    const fields = [
      text("note", "Note (history, examination)", 4),
      text("assessment", "Assessment", 2),
      text("plan", "Plan", 3),
    ];
    const save = async (seen) => {
      const body = { seen: seen || undefined };
      for (const key of ["note", "assessment", "plan"]) body[key] = box.querySelector(`#visit-${key}`)?.value ?? null;
      try {
        const out = await api.put(`/staff/me/appointments/${a.id}/visit-note`, body);
        toast(seen && !n?.seen_at ? "Visit started" : "Note saved");
        draw(out);
        refreshSummaryAfterNote(); // the summary's assessment / plan come from the note
      } catch (e) {
        toast(describeError(e), { error: true });
      }
    };
    render(
      box,
      h(
        "div",
        { class: "row-main" },
        h("h4", {}, "Your visit note"),
        n?.seen_at
          ? badge(`seen from ${officeTime(n.seen_at, a.timezone)}`, "ok")
          : button("Start visit", {
              class: "btn small-btn primary",
              id: "btn-visit-start",
              title: "Record that you are seeing the patient now",
              onclick: () => void save(true),
            }),
      ),
      ...fields,
      h(
        "div",
        { class: "actions" },
        button("Save note", { class: "btn primary", id: "btn-visit-save", onclick: () => void save(false) }),
        n?.updated_at ? h("span", { class: "muted small" }, `Saved ${officeDateTime(n.updated_at, a.timezone)}`) : null,
      ),
    );
  };
  draw(note);
  return box;
}

const DECISION = { confirmed: ["confirmed", "ok"], removed: ["removed", "muted"] };

/** What the medical assistant did for this visit. */
function assistantWork(w, tz) {
  if (!w)
    return h(
      "div",
      { class: "assistant-work", id: "chart-assistant" },
      h("h4", {}, "Medical assistant"),
      h("p", { class: "empty" }, "No medical assistant on this visit."),
    );
  const done = w.status === "completed";
  return h(
    "div",
    { class: "assistant-work", id: "chart-assistant" },
    h(
      "h4",
      {},
      `Prepared by ${w.name} `,
      badge(done ? "completed" : w.status, done ? "ok" : "warn"),
      done && w.completed_at ? h("span", { class: "muted small" }, ` ${officeDateTime(w.completed_at, tz)}`) : null,
    ),
    w.vitals?.text
      ? h(
          "p",
          { class: "small" },
          h("strong", {}, "Vital signs: "),
          w.vitals.text,
          w.vitals.note ? ` (${w.vitals.note})` : "",
        )
      : h("p", { class: "small muted" }, "No vital signs recorded yet."),
    w.review_items?.length
      ? h(
          "ul",
          { class: "review-summary" },
          w.review_items.map((i) => {
            const [label, kind] = DECISION[i.decision] || ["not cleared", "warn"];
            return h(
              "li",
              { dataset: { kind: i.kind } },
              h("span", { class: "muted" }, i.kind === "medication" ? "Medication " : "Allergy "),
              h("strong", {}, i.name),
              i.detail ? ` · ${i.detail} ` : " ",
              badge(label, kind),
              i.flag && i.flag !== "match" && i.flag_text ? h("span", { class: "muted" }, ` — ${i.flag_text}`) : null,
              i.note ? h("div", { class: "muted" }, `Note: ${i.note}`) : null,
            );
          }),
        )
      : null,
    w.check_in_note ? h("p", { class: "small" }, h("strong", {}, "On the check-in answers: "), w.check_in_note) : null,
    w.completion_note ? h("p", { class: "small" }, h("strong", {}, "Note: "), w.completion_note) : null,
  );
}

function checkInAnswers(c, tz) {
  if (!c) return h("p", { class: "empty", id: "chart-check-in" }, "The patient has not checked in for this visit.");
  return h(
    "div",
    { id: "chart-check-in" },
    h(
      "dl",
      { class: "record check-in-answers" },
      c.questions
        .filter((q) => q.id in c.answers)
        .map((q) => h("div", { class: "wide" }, h("dt", {}, q.label), h("dd", {}, answerText(q, c.answers[q.id])))),
    ),
    h(
      "p",
      { class: "muted small" },
      `Signed by ${c.signer_name || "the patient"} on ${officeDateTime(c.signed_at || c.completed_at, tz)}`,
    ),
  );
}

function itemRow(i) {
  const reviewed = i.last_reviewed_at
    ? `reviewed ${formatDay(i.last_reviewed_at)}${i.last_reviewed_by ? ` by ${i.last_reviewed_by}` : ""}`
    : "";
  return h(
    "li",
    { class: `chart-item ${i.status}`, dataset: { id: String(i.id) } },
    h("strong", {}, i.name),
    i.detail ? ` · ${i.detail}` : "",
    reviewed ? h("span", { class: "muted small" }, ` — ${reviewed}`) : null,
    i.note ? h("div", { class: "muted small" }, `Note: ${i.note}`) : null,
  );
}

function itemList(items, empty) {
  const current = items.filter((i) => i.status === "active");
  const old = items.filter((i) => i.status !== "active");
  return [
    current.length ? h("ul", { class: "chart-items" }, current.map(itemRow)) : h("p", { class: "empty" }, empty),
    old.length
      ? h(
          "details",
          { class: "chart-inactive" },
          h("summary", { class: "small" }, `No longer current (${old.length})`),
          h("ul", { class: "chart-items" }, old.map(itemRow)),
        )
      : null,
  ];
}

function historyList(items) {
  const groups = groupHistory(items.filter((i) => i.status === "active"));
  if (!groups.length) return h("p", { class: "empty" }, "No medical history on record.");
  return h(
    "div",
    { class: "history-groups" },
    groups.map((g) =>
      h(
        "div",
        { class: "history-group", dataset: { kind: g.kind } },
        h("h4", {}, g.label),
        h("ul", { class: "chart-items" }, g.items.map(itemRow)),
      ),
    ),
  );
}

function resultCard(r) {
  return h(
    "li",
    { class: `row lab-result ${r.abnormal ? "abnormal" : ""}`, dataset: { id: String(r.id), kind: r.kind } },
    h(
      "div",
      { class: "row-main" },
      badge(r.kind === "lab" ? "Lab" : "Imaging", ""),
      h("strong", {}, r.name),
      r.status !== "final" ? badge(r.status, "warn") : null,
      r.abnormal ? badge("out of range", "bad") : null,
    ),
    h("div", { class: "small muted" }, resultMeta(r)),
    r.values.length
      ? h(
          "table",
          { class: "result-values" },
          h(
            "thead",
            {},
            h("tr", {}, h("th", {}, "Test"), h("th", {}, "Value"), h("th", {}, "Reference"), h("th", {}, "")),
          ),
          h(
            "tbody",
            {},
            r.values.map((x) =>
              h(
                "tr",
                { class: x.flag ? "flagged" : "" },
                h("td", {}, x.name),
                h("td", { class: "mono" }, `${x.value}${x.unit ? ` ${x.unit}` : ""}`),
                h("td", { class: "muted" }, x.range || ""),
                h("td", {}, x.flag ? badge(flagLabel(x.flag), "bad") : ""),
              ),
            ),
          ),
        )
      : null,
    r.summary ? h("p", { class: "small chart-text" }, r.summary) : null,
  );
}

function previousVisit(v, tz) {
  const n = v.visit_note;
  return h(
    "li",
    { class: `row previous-visit ${v.status}`, dataset: { id: String(v.id) } },
    h(
      "div",
      { class: "row-main" },
      h("strong", {}, visitHeading(v, tz)),
      h("span", { class: "muted small mono" }, ` ${v.number}`),
    ),
    v.reason || v.check_in_reason ? h("div", { class: "small" }, `Reason: ${v.check_in_reason || v.reason}`) : null,
    v.vitals?.text
      ? h("div", { class: "small" }, `Vital signs${v.assistant_name ? ` (${v.assistant_name})` : ""}: ${v.vitals.text}`)
      : null,
    v.assistant_note ? h("div", { class: "small muted" }, `Assistant's note: ${v.assistant_note}`) : null,
    v.diagnoses?.length
      ? h("div", { class: "small previous-diagnoses" }, `Diagnoses: ${v.diagnoses.map(diagnosisLine).join("; ")}`)
      : null,
    v.prescriptions?.length
      ? h("div", { class: "small previous-prescriptions" }, `Prescriptions: ${v.prescriptions.join("; ")}`)
      : null,
    v.visit_summary
      ? h(
          "div",
          { class: "small previous-summary" },
          v.visit_summary.status === "signed"
            ? `Visit summary ${v.visit_summary.number} signed by ${v.visit_summary.signer_name || "the provider"} on ${officeDateTime(v.visit_summary.signed_at, tz)}`
            : `Visit summary ${v.visit_summary.number}: draft, not signed yet`,
        )
      : null,
    n
      ? h(
          "dl",
          { class: "record visit-note-record" },
          n.note
            ? h("div", { class: "wide" }, h("dt", {}, `Note — ${n.provider_name || "provider"}`), h("dd", {}, n.note))
            : null,
          n.assessment ? h("div", { class: "wide" }, h("dt", {}, "Assessment"), h("dd", {}, n.assessment)) : null,
          n.plan ? h("div", { class: "wide" }, h("dt", {}, "Plan"), h("dd", {}, n.plan)) : null,
        )
      : h("div", { class: "small muted" }, "No visit note."),
  );
}
