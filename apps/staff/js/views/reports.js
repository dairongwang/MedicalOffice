import { api } from "../api.js";
import { session } from "../auth.js";
import {
  badge,
  button,
  callout,
  describeError,
  field,
  formatDate,
  formatDateTime,
  h,
  input,
  render,
  select,
  spinner,
  toast,
} from "../ui.js";
import { openPdf } from "./chart-orders.js";
import { signaturePad } from "./signature-pad.js";

/**
 * Reports the office prepared on a patient's request for the signed-in **doctor** to sign
 * (services/account/patient_reports.py): the immunization record and the annual health examination report.
 *
 *   #/reports                 waiting for my signature · signed · all
 *   #/reports/<id>            what the record says (the office prepared it — it follows the record until
 *                             signed), my part — the annual exam: measurements, vision, hearing, the
 *                             examination by system, the decision for the purpose (+ restrictions),
 *                             recommendations, the next exam; the immunization record: contraindications and
 *                             the vaccines due — then sign (drawn signature + name + consent) or return it to
 *                             the office with the reason. Signed: PDF, send again, amend.
 */

export const SYSTEMS = [
  ["general", "General appearance"],
  ["heent", "Head, eyes, ears, nose, throat"],
  ["neck", "Neck and thyroid"],
  ["cardiovascular", "Heart and circulation"],
  ["respiratory", "Lungs and breathing"],
  ["abdomen", "Abdomen"],
  ["musculoskeletal", "Muscles, bones and joints"],
  ["neurological", "Nervous system"],
  ["skin", "Skin"],
];
export const FITNESS = [
  ["", "Choose…"],
  ["fit", "Fit for the stated purpose without restrictions"],
  ["fit_with_restrictions", "Fit with restrictions"],
  ["not_fit", "Not fit at this time"],
  ["further_evaluation", "Further evaluation needed"],
];
const FINDINGS = [
  ["not_examined", "Not examined"],
  ["normal", "Normal"],
  ["abnormal", "Abnormal"],
];
const STATUS = {
  draft: ["to sign", "warn"],
  declined: ["returned to the office", "muted"],
  signed: ["signed", "ok"],
  superseded: ["superseded", "muted"],
  canceled: ["canceled", "muted"],
};

export function reportsView(params) {
  const root = h("section", { class: "page wide", id: "staff-reports" });
  if (params.id) void renderReport(root, Number(params.id));
  else void renderList(root, ["todo", "signed", "all"].includes(params.status) ? params.status : "todo");
  return root;
}

async function renderList(root, status) {
  const chip = (key, label) =>
    button(label, {
      class: `btn chip ${status === key ? "active" : ""}`,
      id: `reports-${key}`,
      "aria-pressed": String(status === key),
      onclick: () => (location.hash = key === "todo" ? "#/reports" : `#/reports?status=${key}`),
    });
  const list = h("div", { id: "reports-list" }, spinner());
  const doctor = session.staff?.worker?.role === "doctor";
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
          "Immunization records and annual health examination reports patients asked for. The office prepares " +
            "them from the record; you complete your part and sign.",
        ),
      ),
    ),
    doctor
      ? null
      : callout("info", "Signed by doctors", h("span", {}, " These reports are assigned to and signed by a doctor.")),
    h("div", { class: "chips", role: "group", "aria-label": "Show" }, chip("todo", "To sign"), chip("signed", "Signed"), chip("all", "All")),
    list,
  );
  let rows;
  try {
    rows = await api.get(`/staff/me/reports?status=${status}`);
  } catch (e) {
    render(list, callout("error", "Could not load the reports", h("span", {}, describeError(e))));
    return;
  }
  if (!rows.length) {
    render(list, h("p", { class: "empty", id: "reports-empty" }, status === "todo" ? "Nothing waits for your signature." : "No reports."));
    return;
  }
  render(
    list,
    h(
      "ul",
      { class: "rows" },
      rows.map((r) => {
        const [label, kind] = STATUS[r.status] || [r.status, ""];
        return h(
          "li",
          {},
          h(
            "a",
            { class: `row report ${r.status}`, href: `#/reports/${r.id}`, id: `report-link-${r.id}` },
            h(
              "div",
              { class: "row-main" },
              h("strong", {}, r.kind_label),
              h("span", {}, ` · ${r.patient_name} `),
              h("span", { class: "muted small mono" }, r.patient_mrn),
              badge(label, kind),
              r.needed_by && r.status === "draft" ? badge(`needed by ${formatDate(r.needed_by)}`, "warn") : null,
            ),
            h(
              "div",
              { class: "muted small" },
              `${r.number}${r.version > 1 ? ` v${r.version}` : ""} · for ${r.purpose_label} · requested ${formatDate(r.requested_at)}`,
              r.signed_at ? ` · signed ${formatDateTime(r.signed_at)}` : "",
            ),
          ),
        );
      }),
    ),
  );
}

const url = (id) => `/staff/me/reports/${id}`;

async function renderReport(root, id) {
  render(root, spinner("Opening the report…"));
  let r;
  try {
    r = await api.get(url(id));
  } catch (e) {
    render(
      root,
      callout("error", "Could not open the report", h("span", { id: "report-error" }, describeError(e))),
      h("p", {}, h("a", { href: "#/reports" }, "← Reports")),
    );
    return;
  }
  draw(root, r);
}

function draw(root, r) {
  const [label, kind] = STATUS[r.status] || [r.status, ""];
  const c = r.content;
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("a", { href: "#/reports", class: "back" }, "← Reports"),
        h("h2", { id: "report-title" }, `${r.kind_label} · ${r.patient_name}`),
        h(
          "p",
          { class: "muted small" },
          h("span", { class: "mono" }, `${r.number} · version ${r.version}`),
          " ",
          badge(label, kind),
          ` · for ${r.purpose_label}`,
          r.needed_by ? ` · needed by ${formatDate(r.needed_by)}` : "",
          ` · ${r.patient_mrn}, born ${c ? formatDate(c.patient_date_of_birth) : ""}`,
        ),
        r.request_note ? h("p", { class: "small" }, `Request: ${r.request_note}`) : null,
        r.amendment_reason ? h("p", { class: "small" }, `Amendment: ${r.amendment_reason}`) : null,
      ),
    ),
    r.status === "draft" && c?.warnings?.length
      ? callout(
          "warn",
          "Please note",
          h(
            "ul",
            { id: "report-warnings" },
            c.warnings.map((w) => h("li", {}, w)),
          ),
        )
      : null,
    r.status === "declined"
      ? callout("info", "Returned to the office", h("span", {}, ` ${r.decline_reason}`))
      : null,
    c ? recordCard(r, c) : null,
    r.status === "draft" ? doctorForm(root, r) : doneCard(root, r),
  );
}

function list(items, empty) {
  return items.length
    ? h(
        "ul",
        { class: "chart-items" },
        items.map((x) => h("li", {}, x)),
      )
    : h("p", { class: "muted small" }, empty);
}

/** What the record says (the office's part). */
function recordCard(r, c) {
  const part = (title, body) => h("div", { class: "summary-part" }, h("h5", {}, title), body);
  const shots = c.immunizations.length
    ? h(
        "table",
        { class: "result-values", id: "report-immunizations" },
        h(
          "thead",
          {},
          h(
            "tr",
            {},
            ["Vaccine", "Date", "Dose", "Product · lot", "Site · route", "Given by"].map((t) => h("th", {}, t)),
          ),
        ),
        h(
          "tbody",
          {},
          c.immunizations.map((s) =>
            h(
              "tr",
              {},
              h("td", {}, s.vaccine),
              h("td", { class: "mono" }, formatDate(s.date_given)),
              h("td", {}, s.dose || ""),
              h("td", {}, s.product || ""),
              h("td", {}, s.site_route || ""),
              h("td", {}, s.given_by || ""),
            ),
          ),
        ),
      )
    : h("p", { class: "muted small" }, "No immunizations on record.");
  const body =
    r.kind === "immunization"
      ? [part("Immunizations", shots), part("Allergies", list(c.allergies, "No known allergies recorded."))]
      : [
          part(
            "Exam visit",
            h(
              "p",
              { class: "small", id: "report-exam-visit" },
              c.exam_date ? `${c.exam_date} (${c.exam_visit_number}) · ${c.exam_provider || ""}` : "No exam visit chosen.",
            ),
          ),
          c.vitals ? part("Vital signs at the visit", h("p", { class: "small" }, c.vitals)) : null,
          c.visit_diagnoses.length ? part("Diagnoses at the visit", list(c.visit_diagnoses, "")) : null,
          c.visit_assessment || c.visit_plan
            ? part(
                "Your visit note",
                h(
                  "p",
                  { class: "small chart-text" },
                  [c.visit_assessment && `Assessment: ${c.visit_assessment}`, c.visit_plan && `Plan: ${c.visit_plan}`]
                    .filter(Boolean)
                    .join("\n"),
                ),
              )
            : null,
          part("Medical history", list([...c.conditions, ...c.surgeries.map((s) => `Surgery: ${s}`)], "None recorded.")),
          c.family_history.length ? part("Family history", list(c.family_history, "")) : null,
          c.social_history.length ? part("Social history", list(c.social_history, "")) : null,
          part("Medications", list(c.medications, "None.")),
          part("Allergies", list(c.allergies, "No known allergies recorded.")),
          part("Immunizations", shots),
          c.recent_results.length ? part("Results (last 12 months)", list(c.recent_results, "")) : null,
        ];
  return h(
    "section",
    { class: "card", id: "report-record" },
    h("h3", {}, "From the record"),
    h("div", { class: "summary-preview", dataset: { status: r.status } }, ...body),
  );
}

/** The doctor's part of a draft + the signature. */
function doctorForm(root, r) {
  const f = r.findings || {};
  const c = r.content || {};
  const text = (id, value, attrs = {}) => input({ id, value: value || "", maxlength: 100, ...attrs });
  const area = (id, value, rows = 2, max = 2000) => {
    const el = h("textarea", { class: "input", id, rows, maxlength: max });
    el.value = value || "";
    return el;
  };
  const els = {};
  const parts = [];
  if (r.kind === "annual_exam") {
    els.height = text("report-height", f.height, { placeholder: "e.g. 165 cm" });
    els.weight = text("report-weight", f.weight, { placeholder: c.weight ? `${c.weight} (at the visit)` : "" });
    els.blood_pressure = text("report-bp", f.blood_pressure, {
      placeholder: c.blood_pressure ? `${c.blood_pressure} (at the visit)` : "",
    });
    els.vision = text("report-vision", f.vision, { placeholder: "e.g. 20/20 both eyes" });
    els.hearing = text("report-hearing", f.hearing, { placeholder: "e.g. normal" });
    els.systems = SYSTEMS.map(([key, label]) => {
      const current = f.systems?.[key] || {};
      const status = select(FINDINGS, { id: `report-system-${key}`, value: current.status || "not_examined" });
      const note = input({ id: `report-system-${key}-note`, value: current.note || "", maxlength: 500, placeholder: "Details" });
      return { key, label, status, note };
    });
    els.fitness = select(FITNESS, { id: "report-fitness", value: f.fitness || "" });
    els.restrictions = area("report-restrictions", f.restrictions);
    els.recommendations = area("report-recommendations", f.recommendations);
    const nextYear = new Date();
    nextYear.setFullYear(nextYear.getFullYear() + 1);
    els.next_exam_due = input({
      type: "date",
      id: "report-next-due",
      value: f.next_exam_due || nextYear.toISOString().slice(0, 10),
    });
    const restrictionsField = field("Restrictions", els.restrictions);
    const syncRestrictions = () => (restrictionsField.hidden = els.fitness.value !== "fit_with_restrictions");
    els.fitness.addEventListener("change", syncRestrictions);
    syncRestrictions();
    parts.push(
      h("p", { class: "section-title" }, "Measurements"),
      h(
        "div",
        { class: "field-row three" },
        field("Height", els.height, { optional: true }),
        field("Weight", els.weight, { optional: true }),
        field("Blood pressure", els.blood_pressure, { optional: true }),
      ),
      h("div", { class: "field-row" }, field("Vision", els.vision, { optional: true }), field("Hearing", els.hearing, { optional: true })),
      h("p", { class: "section-title" }, "Examination"),
      h(
        "table",
        { class: "exam-systems", id: "report-systems" },
        h("tbody", {}, els.systems.map((s) => h("tr", {}, h("th", { scope: "row" }, s.label), h("td", {}, s.status), h("td", {}, s.note)))),
      ),
      h("p", { class: "section-title" }, "Assessment"),
      field(`Decision for: ${r.purpose_label}`, els.fitness),
      restrictionsField,
      field("Recommendations", els.recommendations, { optional: true }),
      field("Next examination due", els.next_exam_due, { optional: true }),
    );
  } else {
    els.contraindications = area("report-contraindications", f.contraindications);
    els.vaccines_due = area("report-vaccines-due", f.vaccines_due);
    parts.push(
      field("Contraindications / exemptions", els.contraindications, { optional: true }),
      field("Vaccines due or recommended", els.vaccines_due, { optional: true }),
    );
  }
  els.note = area("report-note", r.doctor_note, 2, 5000);
  parts.push(field("Comments", els.note, { optional: true }));

  const body = () => {
    const v = (el) => el.value.trim() || null;
    const findings =
      r.kind === "annual_exam"
        ? {
            height: v(els.height),
            weight: v(els.weight),
            blood_pressure: v(els.blood_pressure),
            vision: v(els.vision),
            hearing: v(els.hearing),
            systems: Object.fromEntries(els.systems.map((s) => [s.key, { status: s.status.value, note: v(s.note) }])),
            fitness: els.fitness.value || null,
            restrictions: els.fitness.value === "fit_with_restrictions" ? v(els.restrictions) : null,
            recommendations: v(els.recommendations),
            next_exam_due: els.next_exam_due.value || null,
          }
        : { contraindications: v(els.contraindications), vaccines_due: v(els.vaccines_due) };
    return { findings, doctor_note: v(els.note) };
  };
  const missing = h("div", { class: "check-live", id: "report-missing" });
  const showMissing = (items) =>
    render(
      missing,
      items.length
        ? callout(
            "warn",
            "Still to do before you sign",
            h(
              "ul",
              {},
              items.map((m) => h("li", {}, m)),
            ),
          )
        : null,
    );
  showMissing(r.missing || []);
  const save = async (quiet) => {
    const out = await api.put(url(r.id), body());
    showMissing(out.missing);
    if (!quiet) toast("Saved");
    return out;
  };
  const pdfBtn = button("Open PDF (draft)", {
    class: "btn small-btn",
    id: "btn-report-pdf",
    onclick: async () => {
      await save(true).catch(() => null);
      void openPdf(`${url(r.id)}/pdf`, `${r.number}.pdf`, { download: false });
    },
  });

  const form = h(
    "section",
    { class: "card form", id: "report-doctor-form" },
    h("h3", {}, "Your part"),
    ...parts,
    missing,
    h(
      "div",
      { class: "actions" },
      pdfBtn,
      h("span", { class: "spacer" }),
      button("Save", {
        class: "btn",
        id: "btn-report-save",
        onclick: () => void save(false).catch((e) => toast(describeError(e), { error: true })),
      }),
    ),
  );
  return [
    form,
    r.can_sign ? signCard(root, r, () => save(true)) : callout("warn", "Not signable", h("span", {}, ` ${r.sign_blocker}`)),
    returnCard(root, r),
  ];
}

function signCard(root, r, beforeSign) {
  const pad = signaturePad();
  const w = session.staff?.worker;
  const name = input({ maxlength: 200, autocomplete: "name", value: w ? `${w.first_name} ${w.last_name}` : "", id: "report-signer" });
  const consent = h("input", { type: "checkbox", id: "report-consent" });
  const sendNow = h("input", { type: "checkbox", id: "report-send", checked: true });
  const err = h("div", { class: "check-live", id: "report-sign-msg" });
  const submit = button("Sign report", { type: "submit", class: "btn primary", id: "btn-report-sign" });
  const statement =
    r.kind === "annual_exam"
      ? "I examined the patient and certify that this report is correct to the best of my knowledge."
      : "I certify that the immunizations listed are those recorded in the patient's medical record.";
  const form = h(
    "form",
    { class: "card portal-form", id: "report-sign-form", novalidate: true },
    h("h3", {}, "Sign"),
    h("p", { class: "small muted" }, `${statement} Once signed it cannot be changed — a mistake is corrected by an amendment.`),
    pad.el,
    field("Type your full name *", name),
    h(
      "label",
      { class: "checkbox consent", for: "report-consent" },
      consent,
      h("span", {}, "I agree to sign this report electronically. *"),
    ),
    h(
      "label",
      { class: "checkbox", for: "report-send" },
      sendNow,
      h("span", {}, "Send it to the patient now (e-mail with the PDF, a text that it is ready)"),
    ),
    err,
    h("div", { class: "actions" }, h("span", { class: "spacer" }), submit),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.replaceChildren();
    const problems = [];
    if (pad.isEmpty()) problems.push("Please draw your signature.");
    if (name.value.trim().length < 2) problems.push("Please type your full name.");
    if (!consent.checked) problems.push("Please agree to sign electronically.");
    if (problems.length)
      return err.append(
        callout(
          "error",
          "Please check",
          h(
            "ul",
            {},
            problems.map((p) => h("li", {}, p)),
          ),
        ),
      );
    submit.disabled = true;
    submit.textContent = "Signing…";
    try {
      await beforeSign();
      const out = await api.post(`${url(r.id)}/sign`, {
        signature: { signer_name: name.value.trim(), image_png: pad.toDataUrl(), consent: true },
        send_to_patient: sendNow.checked,
      });
      toast(out.sent_channels.length ? `Signed and sent to the patient (${out.sent_channels.join(" + ")})` : "Report signed");
      draw(root, out);
      window.scrollTo({ top: 0 });
    } catch (e2) {
      err.append(callout("error", "Could not sign", h("span", {}, describeError(e2))));
      submit.disabled = false;
      submit.textContent = "Sign report";
    }
  });
  return form;
}

function returnCard(root, r) {
  const reason = input({ maxlength: 500, placeholder: "e.g. No exam within the last year — please book an annual physical", id: "report-decline-reason" });
  return h(
    "section",
    { class: "card", id: "report-return" },
    h("h3", {}, "Cannot sign it?"),
    h("p", { class: "small muted" }, "Return it to the office with the reason — they prepare it again or tell the patient."),
    field("Reason", reason),
    h(
      "div",
      { class: "actions" },
      h("span", { class: "spacer" }),
      button("Return to the office", {
        class: "btn danger",
        id: "btn-report-decline",
        onclick: async () => {
          if (reason.value.trim().length < 3) return toast("Please give the reason.", { error: true });
          try {
            const out = await api.post(`${url(r.id)}/decline`, { reason: reason.value.trim() });
            toast("Returned to the office");
            draw(root, out);
          } catch (e) {
            toast(describeError(e), { error: true });
          }
        },
      }),
    ),
  );
}

/** A signed (or returned / replaced) report: the doctor's part as signed, the signature, PDF, send, amend. */
function doneCard(root, r) {
  const f = r.findings || {};
  const rows = [];
  const add = (label, value) => value && rows.push(h("div", { class: "wide" }, h("dt", {}, label), h("dd", {}, value)));
  if (r.kind === "annual_exam") {
    add("Measurements", [f.height && `Height ${f.height}`, f.weight && `Weight ${f.weight}`, f.blood_pressure && `BP ${f.blood_pressure}`].filter(Boolean).join(" · "));
    add("Vision / hearing", [f.vision, f.hearing].filter(Boolean).join(" · "));
    add(
      "Examination",
      SYSTEMS.map(([key, label]) => {
        const s = f.systems?.[key];
        return s && s.status !== "not_examined" ? `${label}: ${s.status}${s.note ? ` — ${s.note}` : ""}` : null;
      })
        .filter(Boolean)
        .join("\n"),
    );
    add("Decision", (FITNESS.find(([k]) => k === f.fitness) || [, ""])[1]);
    add("Restrictions", f.restrictions);
    add("Recommendations", f.recommendations);
    add("Next examination due", f.next_exam_due ? formatDate(f.next_exam_due) : "");
  } else {
    add("Contraindications / exemptions", f.contraindications);
    add("Vaccines due or recommended", f.vaccines_due);
  }
  add("Comments", r.doctor_note);
  const signed = r.status === "signed" || r.status === "superseded";
  return h(
    "section",
    { class: "card", id: "report-done" },
    h("h3", {}, signed ? "Signed" : "Your part"),
    rows.length ? h("dl", { class: "record" }, rows) : null,
    signed
      ? h(
          "div",
          { class: "summary-signature", id: "report-signature" },
          r.signature_png ? h("img", { class: "signature-img", src: r.signature_png, alt: `Signature of ${r.signer_name}` }) : null,
          h(
            "p",
            { class: "small" },
            h("strong", {}, `Signed by ${r.signer_name}`),
            ` on ${formatDateTime(r.signed_at)} · verification `,
            h("span", { class: "mono" }, r.verification || ""),
          ),
          h(
            "p",
            { class: "small muted", id: "report-delivery" },
            r.sent_at ? `Sent to the patient ${formatDateTime(r.sent_at)} by ${r.sent_channels.join(" + ")}` : "Not sent by e-mail / text",
          ),
        )
      : null,
    h(
      "div",
      { class: "actions" },
      signed
        ? button("Open PDF", {
            class: "btn small-btn",
            id: "btn-report-pdf",
            onclick: () => void openPdf(`${url(r.id)}/pdf`, `${r.number}.pdf`, { download: false }),
          })
        : null,
      r.status === "signed"
        ? button("Send to the patient again", {
            class: "btn small-btn",
            id: "btn-report-send",
            onclick: async () => {
              try {
                const out = await api.post(`${url(r.id)}/send`);
                toast(out.sent_channels.length ? `Sent by ${out.sent_channels.join(" + ")}` : "Nothing could be sent");
                draw(root, out);
              } catch (e) {
                toast(describeError(e), { error: true });
              }
            },
          })
        : null,
      h("span", { class: "spacer" }),
      r.status === "signed"
        ? button("Amend…", {
            class: "btn small-btn",
            id: "btn-report-amend",
            title: "A signed report is never edited: write a new version that replaces it",
            onclick: async () => {
              const reason = prompt("Why is a new version needed?");
              if (!reason || reason.trim().length < 3) return;
              try {
                const out = await api.post(`${url(r.id)}/amend`, { reason: reason.trim() });
                toast("Amendment started — complete it and sign");
                location.hash = `#/reports/${out.id}`;
              } catch (e) {
                toast(describeError(e), { error: true });
              }
            },
          })
        : null,
    ),
  );
}
