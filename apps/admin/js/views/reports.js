import { api } from "../api.js";
import { REPORT_FILTERS, REPORT_KINDS, REPORT_PURPOSES, neededBy, reportStatusBadge } from "../format.js";
import {
  badge,
  button,
  callout,
  describeError,
  field,
  formatDateTime,
  formValues,
  h,
  input,
  orNull,
  render,
  select,
  spinner,
  toast,
} from "../ui.js";

/**
 * Reports on request (services/account/patient_reports.py): the office prepares a patient's
 * **immunization record** or **annual health examination report** and a **doctor signs** it.
 *
 *   the queue (tab Reports)      requests from the patients' app and the desk → "Prepare…" (choose the doctor
 *                                and, for the annual exam, the exam visit) → the doctor is told, completes and
 *                                signs it on the staff dashboard (or returns it with a reason) → print / send
 *   the patient's page           (#/patients/<id>/reports) the patient's reports + "New request" for a request
 *                                made at the desk / on the phone (prepared at once when a doctor is chosen)
 *
 * Route: #/reports  ·  #/reports?status=open|requested|draft|declined|signed|canceled|all&kind=&q=
 */
export function reportsView(params) {
  const root = h("section", { class: "page wide", id: "reports" });
  void renderQueue(root, {
    status: REPORT_FILTERS.some(([k]) => k === params.status) ? params.status : "open",
    kind: ["immunization", "annual_exam"].includes(params.kind) ? params.kind : "",
    q: params.q || "",
  });
  return root;
}

const hashFor = (f) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v && !(k === "status" && v === "open")) q.set(k, v);
  const s = q.toString();
  return `#/reports${s ? "?" + s : ""}`;
};

async function renderQueue(root, filters) {
  const search = input({ type: "search", placeholder: "Patient name, P-000001 or RPT-000001", value: filters.q, id: "reports-search" });
  search.addEventListener("change", () => (location.hash = hashFor({ ...filters, q: search.value.trim() })));
  const kind = select([["", "Both kinds"], ...REPORT_KINDS], { id: "reports-kind", value: filters.kind });
  kind.addEventListener("change", () => (location.hash = hashFor({ ...filters, kind: kind.value })));
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Status", id: "reports-filters" });
  const list = h("div", { id: "reports-list" }, spinner());
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "Reports"),
        h(
          "p",
          { class: "muted small" },
          "Immunization records and annual health examination reports patients ask for. Prepare each from the " +
            "record and choose the doctor who signs it; the patient gets it by e-mail and in the app once signed.",
        ),
      ),
      button("Refresh", { class: "btn", id: "btn-reports-refresh", onclick: () => void load() }),
    ),
    chips,
    h("div", { class: "toolbar" }, search, kind),
    list,
  );
  await load();

  async function load() {
    render(list, spinner());
    const q = new URLSearchParams({ status: filters.status });
    if (filters.kind) q.set("kind", filters.kind);
    if (filters.q) q.set("q", filters.q);
    let page;
    try {
      page = await api.get(`/patient-reports?${q}`);
    } catch (e) {
      render(list, callout("error", "Could not load the reports", h("span", {}, describeError(e))));
      return;
    }
    render(
      chips,
      REPORT_FILTERS.map(([key, label]) =>
        button([label, h("span", { class: "count" }, ` ${page.counts[key] ?? 0}`)], {
          class: `btn chip ${filters.status === key ? "active" : ""}`,
          id: `reports-filter-${key}`,
          "aria-pressed": String(filters.status === key),
          onclick: () => (location.hash = hashFor({ ...filters, status: key })),
        }),
      ),
    );
    render(
      list,
      h("p", { class: "muted small", id: "reports-count" }, `${page.total} report${page.total === 1 ? "" : "s"}`),
      page.items.length
        ? h(
            "ul",
            { class: "rows" },
            page.items.map((r) => reportRow(r, () => void load(), { showPatient: true })),
          )
        : h("p", { class: "empty", id: "reports-empty" }, "Nothing here."),
    );
  }
}

/** One report with what the office can do with it. */
export function reportRow(r, reload, { showPatient = false } = {}) {
  const [label, kind] = reportStatusBadge(r.status);
  const due = neededBy(r);
  const panel = h("div", { class: "report-panel", id: `report-panel-${r.id}`, hidden: true });
  const open = ["requested", "draft", "declined"].includes(r.status);
  const pdfButton = (text) =>
    button(text, {
      class: "btn small-btn",
      id: `btn-report-pdf-${r.id}`,
      onclick: async () => {
        const tab = window.open("", "_blank"); // now, in the click — a later window.open would be blocked
        try {
          const url = await api.blobUrl(`/api/v1/patient-reports/${r.id}/pdf`);
          if (tab) tab.location.href = url;
          else location.href = url;
          setTimeout(() => URL.revokeObjectURL(url), 60_000);
        } catch (e) {
          tab?.close();
          toast(describeError(e), { error: true });
        }
      },
    });
  const actions = [];
  if (open)
    actions.push(
      button(r.status === "requested" ? "Prepare…" : r.status === "declined" ? "Prepare again…" : "Change doctor / visit…", {
        class: `btn small-btn ${r.status === "draft" ? "" : "primary"}`,
        id: `btn-report-prepare-${r.id}`,
        title: "Build it from the record and choose the doctor who signs it (they are told)",
        onclick: () => void togglePrepare(r, panel, reload),
      }),
    );
  if (r.status === "draft" || r.status === "declined") actions.push(pdfButton("Draft PDF"));
  if (r.status === "signed" || r.status === "superseded") actions.push(pdfButton("Print"));
  if (r.status === "signed")
    actions.push(
      button("Send to patient", {
        class: "btn small-btn",
        id: `btn-report-send-${r.id}`,
        title: "E-mail it again (PDF attached) and text the patient that it is ready",
        onclick: async () => {
          try {
            const out = await api.post(`/patient-reports/${r.id}/send`);
            toast(
              out.sent_channels.length
                ? `Sent by ${out.sent_channels.map((x) => (x === "sms" ? "text" : "e-mail")).join(" and ")}`
                : `Not sent: ${out.delivery_note || "no e-mail address or phone number"} — print it instead`,
              { error: !out.sent_channels.length },
            );
            reload();
          } catch (e) {
            toast(describeError(e), { error: true });
          }
        },
      }),
    );
  if (open)
    actions.push(
      button("Cancel…", {
        class: "btn small-btn danger",
        id: `btn-report-cancel-${r.id}`,
        title: "Withdraw the request — the patient is told, with the reason",
        onclick: async () => {
          const reason = prompt(`Why is ${r.number} canceled? (the patient is told)`);
          if (!reason || reason.trim().length < 3) return;
          try {
            await api.post(`/patient-reports/${r.id}/cancel`, { reason: reason.trim() });
            toast(`${r.number} canceled`);
            reload();
          } catch (e) {
            toast(describeError(e), { error: true });
          }
        },
      }),
    );
  const warnings = r.status === "signed" ? [] : r.content?.warnings || [];
  return h(
    "li",
    { class: `row report ${r.status}`, id: `report-${r.id}`, dataset: { id: String(r.id), status: r.status } },
    h(
      "div",
      { class: "row-main" },
      h("strong", { class: "mono" }, r.number),
      h("strong", {}, ` ${r.kind_label}`),
      r.version > 1 ? h("span", { class: "muted small" }, ` v${r.version}`) : null,
      showPatient
        ? h("a", { href: `#/patients/${r.patient_id}/reports`, class: "report-patient" }, ` ${r.patient_name} `)
        : null,
      showPatient ? h("span", { class: "muted small mono" }, r.patient_mrn) : null,
      badge(label, kind),
      due ? badge(due.text, due.kind) : null,
    ),
    h(
      "div",
      { class: "small" },
      `For: ${r.purpose_label}`,
      ` · requested ${r.requested_via === "patient" ? "in the patients' app" : "at the office"} ${formatDateTime(r.requested_at)}`,
      r.doctor_name ? ` · doctor: ${r.doctor_name}` : "",
      r.appointment_number ? ` · exam visit ${r.appointment_number}` : "",
    ),
    r.request_note ? h("div", { class: "muted small" }, `Note: ${r.request_note}`) : null,
    r.status === "declined"
      ? h("div", { class: "small bad-text", id: `report-declined-${r.id}` }, `Returned by the doctor: ${r.decline_reason}`)
      : null,
    r.status === "draft"
      ? h(
          "div",
          { class: "muted small" },
          r.doctor_notified_at
            ? `The doctor was told ${formatDateTime(r.doctor_notified_at)} (${r.doctor_notify_channels.join(" + ")})`
            : "The doctor sees it under Reports on the staff dashboard",
        )
      : null,
    r.status === "signed"
      ? h(
          "div",
          { class: "small", id: `report-signed-${r.id}` },
          `Signed by ${r.signer_name} on ${formatDateTime(r.signed_at)}`,
          r.sent_at ? ` · sent to the patient ${formatDateTime(r.sent_at)}` : " · not sent by e-mail / text",
          r.patient_viewed_at ? " · opened by the patient" : "",
        )
      : null,
    r.status === "canceled" ? h("div", { class: "muted small" }, `Canceled: ${r.cancel_reason}`) : null,
    warnings.length ? callout("warn", "Check before the doctor signs", h("ul", {}, warnings.map((w) => h("li", {}, w)))) : null,
    h("div", { class: "actions" }, ...actions),
    panel,
  );
}

/** Choose the doctor (and, for the annual exam, the exam visit) and prepare it. */
async function togglePrepare(r, panel, reload) {
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  render(panel, spinner());
  let opts;
  try {
    opts = await api.get(`/patients/${r.patient_id}/reports/options`);
  } catch (e) {
    render(panel, callout("error", "Could not load the choices", h("span", {}, describeError(e))));
    return;
  }
  if (!opts.doctors.length) {
    render(panel, callout("warn", "No doctor on the roster", h("span", {}, " Only a doctor signs these reports.")));
    return;
  }
  const doctor = select(
    opts.doctors.map((d) => [String(d.id), `${d.name}${d.specialty ? ` — ${d.specialty}` : ""}`]),
    { id: `report-doctor-${r.id}`, value: String(r.worker_id || opts.suggested_worker_id || opts.doctors[0].id) },
  );
  const visit =
    r.kind === "annual_exam"
      ? select(
          [
            ["", "— no exam visit (the doctor documents the examination) —"],
            ...opts.visits.map((v) => [String(v.id), `${v.start_local} · ${v.provider_name} · ${v.number}${v.reason ? ` · ${v.reason}` : ""}`]),
          ],
          { id: `report-visit-${r.id}`, value: String(r.appointment_id || opts.suggested_appointment_id || "") },
        )
      : null;
  render(
    panel,
    h(
      "div",
      { class: "inline-form" },
      field("Doctor who signs", doctor),
      visit ? field("Exam visit it is based on", visit, { hint: "Suggested: the latest annual / physical exam." }) : null,
      h(
        "div",
        { class: "actions" },
        button("Close", { class: "btn small-btn", onclick: () => (panel.hidden = true) }),
        h("span", { class: "spacer" }),
        button("Prepare & send to the doctor", {
          class: "btn small-btn primary",
          id: `btn-report-generate-${r.id}`,
          onclick: async () => {
            try {
              const out = await api.post(`/patient-reports/${r.id}/generate`, {
                worker_id: Number(doctor.value),
                appointment_id: visit && visit.value ? Number(visit.value) : null,
              });
              toast(`${out.number} prepared — ${out.doctor_name} ${out.doctor_notified_at ? "was told" : "sees it on the staff dashboard"}`);
              reload();
            } catch (e) {
              toast(describeError(e), { error: true });
            }
          },
        }),
      ),
    ),
  );
}

/** The patient's reports on their record page + a request made at the desk / on the phone. */
export function patientReportsSection(p) {
  const section = h("section", { class: "card", id: "patient-reports" });
  void reload();
  return section;

  async function reload() {
    let rows, opts;
    try {
      [rows, opts] = await Promise.all([api.get(`/patients/${p.id}/reports`), api.get(`/patients/${p.id}/reports/options`)]);
    } catch (e) {
      render(section, h("h3", {}, "Reports"), callout("error", "Could not load", h("span", {}, describeError(e))));
      return;
    }
    render(
      section,
      h("h3", {}, "Reports", h("span", { class: "muted small" }, ` · ${rows.length}`)),
      rows.length
        ? h(
            "ul",
            { class: "rows", id: "patient-report-list" },
            rows.map((r) => reportRow(r, () => void reload())),
          )
        : h("p", { class: "empty" }, "No reports yet."),
      requestForm(opts),
    );
  }

  function requestForm(opts) {
    const msg = h("div", { class: "check-live", id: "report-request-msg" });
    const kind = select(REPORT_KINDS, { name: "kind", id: "report-request-kind" });
    const doctor = select(
      [["", "— prepare later (from the Reports tab) —"], ...opts.doctors.map((d) => [String(d.id), d.name])],
      { name: "worker_id", id: "report-request-doctor", value: String(opts.suggested_worker_id || "") },
    );
    const visit = select(
      [["", "— none —"], ...opts.visits.map((v) => [String(v.id), `${v.start_local} · ${v.number}${v.reason ? ` · ${v.reason}` : ""}`])],
      { name: "appointment_id", id: "report-request-visit", value: String(opts.suggested_appointment_id || "") },
    );
    const visitField = field("Exam visit (annual exam)", visit, { optional: true });
    const sync = () => (visitField.hidden = kind.value !== "annual_exam");
    kind.addEventListener("change", sync);
    sync();
    const form = h(
      "form",
      { class: "form compact inline-form", id: "report-request-form", novalidate: true },
      h("h4", {}, "New request (at the desk / on the phone)"),
      msg,
      h(
        "div",
        { class: "field-row" },
        field("Report", kind),
        field("What for", select(REPORT_PURPOSES, { name: "purpose", id: "report-request-purpose" })),
      ),
      h(
        "div",
        { class: "field-row" },
        field("For (school, employer, club…)", input({ name: "purpose_detail", maxlength: 200, id: "report-request-detail" }), {
          optional: true,
        }),
        field("Needed by", input({ name: "needed_by", type: "date", id: "report-request-needed" }), { optional: true }),
      ),
      field("Note", input({ name: "note", maxlength: 1000, id: "report-request-note" }), { optional: true }),
      h("div", { class: "field-row" }, field("Doctor who signs", doctor), visitField),
      h(
        "div",
        { class: "actions" },
        h("span", { class: "spacer" }),
        button("Record the request", { type: "submit", class: "btn primary", id: "btn-report-request" }),
      ),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      msg.replaceChildren();
      const v = formValues(form);
      try {
        const r = await api.post(`/patients/${p.id}/reports`, {
          kind: v.kind,
          purpose: v.purpose,
          purpose_detail: orNull(v.purpose_detail),
          needed_by: orNull(v.needed_by),
          note: orNull(v.note),
          worker_id: v.worker_id ? Number(v.worker_id) : null,
          appointment_id: v.kind === "annual_exam" && v.appointment_id ? Number(v.appointment_id) : null,
        });
        toast(r.status === "draft" ? `${r.number} prepared — ${r.doctor_name} was asked to sign it` : `${r.number} recorded`);
        await reload();
      } catch (err) {
        msg.append(callout("error", "Could not record it", h("span", {}, describeError(err))));
      }
    });
    return form;
  }
}

