import { api } from "../api.js";
import { session } from "../auth.js";
import { officeDateTime } from "../staff-appointments.js";
import { badge, button, callout, describeError, field, h, input, render, spinner, toast } from "../ui.js";
import { openPdf } from "./chart-orders.js";
import { signaturePad } from "./signature-pad.js";

/**
 * The visit summary on the patient's chart (services/account/visit_summaries.py) — after the visit the
 * patient gets a summary of it, **signed by the doctor**:
 *
 *   draft     prepared from the chart (automatically once the visit is over, or here once it has started);
 *             the generated part follows the chart until it is signed ("Update from the chart"); the
 *             assessment and the plan are the visit note's unless rewritten for the patient; instructions
 *             and the follow-up are the doctor's
 *   sign      drawn signature + typed full name + consent → frozen, e-mailed to the patient (PDF attached) /
 *             a text that it is ready, and in the patient's app
 *   signed    PDF, send again, amend (a new version that supersedes it once signed)
 *
 * Only a doctor or a physician assistant signs (the server answers `can_sign` / `sign_blocker`).
 */

const summaryUrl = (chart) => `/staff/me/appointments/${chart.appointment.id}/visit-summary`;

const STATUS = { draft: ["draft — not signed yet", "warn"], signed: ["signed", "ok"], superseded: ["superseded", "muted"] };

/** The section on screen: reloaded after the visit note changes, unless the doctor is working in it. */
let shown = null;

export function summarySection(chart) {
  const box = h("div", { id: "chart-summary-body" }, spinner("Preparing the visit summary…"));
  shown = { box, chart, busy: () => false };
  void load(box, chart);
  return box;
}

/** The visit note was saved ("Start visit", the assessment, the plan): the draft's texts follow it — when the
 *  doctor has not started typing or signing in the summary yet. */
export function refreshSummaryAfterNote() {
  if (!shown || !document.body.contains(shown.box) || shown.busy()) return;
  void load(shown.box, shown.chart);
}

/** After the chart changed (a diagnosis, a prescription, a referral): redraw the generated part of a draft,
 *  keeping what the doctor is typing and drawing. */
export async function refreshSummaryPreview(chart) {
  const preview = document.getElementById("summary-preview");
  if (!preview || preview.dataset.status !== "draft") return;
  try {
    const s = await api.get(summaryUrl(chart));
    preview.replaceWith(previewEl(s));
  } catch {
    /* the next opening shows it */
  }
}

async function load(box, chart) {
  render(box, spinner("Preparing the visit summary…"));
  let s;
  try {
    s = await api.get(summaryUrl(chart));
  } catch (e) {
    const notYet = e.status === 409;
    render(
      box,
      callout(
        notYet ? "info" : "error",
        notYet ? "Not yet" : "Could not load the visit summary",
        h("span", { id: "summary-not-yet" }, describeError(e)),
      ),
      notYet
        ? h(
            "div",
            { class: "actions" },
            button("Prepare it now", {
              class: "btn small-btn",
              id: "btn-summary-prepare",
              title: "After you started the visit",
              onclick: () => void load(box, chart),
            }),
          )
        : null,
    );
    return;
  }
  draw(box, chart, s);
}

function head(s, tz) {
  const [label, kind] = STATUS[s.status] || [s.status, ""];
  const generated = {
    office: "prepared when the office completed the visit",
    job: "prepared automatically after the visit",
    provider: "prepared on the chart",
    seed: "dummy data",
  }[s.generated_by];
  return h(
    "div",
    { class: "row-main", id: "summary-head" },
    h("strong", { class: "mono" }, `${s.number} · version ${s.version}`),
    badge(label, kind),
    s.status === "draft"
      ? h("span", { class: "muted small" }, ` ${generated || ""} · ${officeDateTime(s.generated_at, tz)}`)
      : null,
    s.amendment_reason ? h("div", { class: "small" }, `Amendment: ${s.amendment_reason}`) : null,
  );
}

/** The summary as the patient reads it (the generated part + the texts). */
function previewEl(s) {
  const c = s.content;
  const part = (title, body, empty) => {
    if ((!body || (Array.isArray(body) && !body.length)) && !empty) return null;
    const content = Array.isArray(body)
      ? body.length
        ? h(
            "ul",
            {},
            body.map((x) => h("li", {}, x)),
          )
        : h("p", { class: "muted" }, empty)
      : h("p", { class: "chart-text" }, body || empty);
    return h("div", { class: "summary-part" }, h("h5", {}, title), content);
  };
  return h(
    "div",
    { class: "summary-preview", id: "summary-preview", dataset: { status: s.status } },
    h(
      "p",
      { class: "small muted" },
      `${c.patient_name} (${c.patient_mrn}) · ${c.visit_number} · ${c.visit_date} (${c.timezone}) · ${c.provider_name}`,
    ),
    part("Why the patient came", c.reason),
    part("Vital signs", c.vitals ? `${c.vitals}${c.vitals_note ? ` (${c.vitals_note})` : ""}` : null),
    part("Diagnoses", c.diagnoses, "No diagnosis recorded for this visit."),
    s.status !== "draft" ? part("Assessment", s.assessment) : null,
    s.status !== "draft" ? part("Plan", s.plan) : null,
    part("Medications prescribed at this visit", c.prescriptions),
    part("The patient's medications", c.medications, "None recorded."),
    part("Allergies", c.allergies, "No known allergies recorded."),
    part("Referrals", c.referrals),
    part("Tests", c.tests),
    s.status !== "draft" ? part("Instructions", s.instructions) : null,
    s.status !== "draft" ? part("Follow-up", s.follow_up) : null,
  );
}

function draw(box, chart, s) {
  const tz = chart.appointment.timezone;
  const pdfButtons = (version) => [
    button("Open PDF", {
      class: "btn small-btn",
      id: `btn-summary-pdf${version ? `-v${version}` : ""}`,
      onclick: () =>
        void openPdf(`${summaryUrl(chart)}/pdf${version ? `?version=${version}` : ""}`, `${s.number}.pdf`, {
          download: false,
        }),
    }),
  ];
  const versions = s.previous_versions.length
    ? h(
        "details",
        { class: "chart-inactive", id: "summary-versions" },
        h("summary", { class: "small" }, `Earlier versions (${s.previous_versions.length})`),
        h(
          "ul",
          { class: "chart-items" },
          s.previous_versions.map((v) =>
            h(
              "li",
              {},
              h("strong", {}, `Version ${v.version}`),
              ` · ${v.status}${v.signed_at ? ` · signed ${officeDateTime(v.signed_at, tz)}` : ""} `,
              button("PDF", {
                class: "btn small-btn",
                onclick: () =>
                  void openPdf(`${summaryUrl(chart)}/pdf?version=${v.version}`, `${v.number}-v${v.version}.pdf`, {
                    download: false,
                  }),
              }),
            ),
          ),
        ),
      )
    : null;

  if (s.status !== "draft") {
    if (shown?.box === box) shown.busy = () => false;
    render(
      box,
      head(s, tz),
      previewEl(s),
      signedBlock(s, tz),
      h(
        "div",
        { class: "actions" },
        ...pdfButtons(null),
        button("Send to the patient again", {
          class: "btn small-btn",
          id: "btn-summary-send",
          title: "E-mail it (PDF attached) and text the patient that it is ready",
          onclick: async (e) => {
            e.target.disabled = true;
            try {
              const out = await api.post(`${summaryUrl(chart)}/send`);
              toast(out.sent_channels.length ? `Sent by ${out.sent_channels.join(" + ")}` : "Nothing could be sent");
              draw(box, chart, out);
            } catch (err) {
              toast(describeError(err), { error: true });
              e.target.disabled = false;
            }
          },
        }),
        h("span", { class: "spacer" }),
        s.status === "signed" && isSigner(chart)
          ? button("Amend…", {
              class: "btn small-btn",
              id: "btn-summary-amend",
              title: "A signed summary is never edited: write a new version that replaces it",
              onclick: () => amendForm(box, chart),
            })
          : null,
      ),
      h("div", { id: "summary-amend" }),
      versions,
    );
    return;
  }

  // The draft: the texts, then the signature.
  const c = s.content;
  const area = (id, value, rows, placeholder, max) => {
    const el = h("textarea", { class: "input", id, rows, maxlength: max, placeholder: placeholder || "" });
    el.value = value || "";
    return el;
  };
  const fAssessment = area("summary-assessment", s.assessment, 2, "From your visit note", 5000);
  const fPlan = area("summary-plan", s.plan, 3, "From your visit note", 5000);
  const fInstructions = area(
    "summary-instructions",
    s.instructions,
    4,
    "What the patient should do now — medicines, self-care, when to call",
    5000,
  );
  const fFollowUp = area("summary-follow-up", s.follow_up, 1, "e.g. Recheck in three months", 1000);
  const noteHint = (value, note) =>
    note
      ? value === note
        ? "The visit note's text — rewrite it in plain words for the patient if you like."
        : "Rewritten for the patient (the visit note keeps your clinical text)."
      : "Your visit note has none — write it for the patient, or leave it empty.";
  const texts = () => {
    const orNote = (v, note) => (v.trim() === (note || "").trim() ? null : v.trim() || null);
    return {
      assessment: orNote(fAssessment.value, c.note_assessment),
      plan: orNote(fPlan.value, c.note_plan),
      instructions: fInstructions.value.trim() || null,
      follow_up: fFollowUp.value.trim() || null,
    };
  };
  let dirty = false;
  for (const el of [fAssessment, fPlan, fInstructions, fFollowUp]) el.addEventListener("input", () => (dirty = true));
  const save = async ({ quiet = false } = {}) => {
    const out = await api.put(summaryUrl(chart), texts());
    dirty = false;
    if (!quiet) toast("Draft saved");
    return out;
  };

  const signArea = h("div", { id: "summary-sign" });
  let sign = null;
  if (s.can_sign) {
    sign = signForm(chart, box, () => (dirty ? save({ quiet: true }) : null));
    signArea.append(sign);
  }
  if (shown?.box === box) shown.busy = () => dirty || (sign !== null && sign.dataset.started === "1");
  else
    signArea.append(
      callout("warn", "Not signable here", h("span", { id: "summary-sign-blocker" }, ` ${s.sign_blocker || ""}`)),
    );

  render(
    box,
    head(s, tz),
    s.provider_notified_at
      ? h(
          "p",
          { class: "muted small" },
          `You were asked to sign it on ${officeDateTime(s.provider_notified_at, tz)} (${s.provider_notify_channels.join(" + ") || "—"}).`,
        )
      : null,
    previewEl(s),
    h(
      "div",
      { class: "form compact", id: "summary-texts" },
      field("Assessment — for the patient", fAssessment, { hint: noteHint(s.assessment, c.note_assessment) }),
      field("Plan — for the patient", fPlan, { hint: noteHint(s.plan, c.note_plan) }),
      field("Instructions", fInstructions, { optional: true }),
      field("Follow-up", fFollowUp, { optional: true }),
      h(
        "div",
        { class: "actions" },
        button("Update from the chart", {
          class: "btn small-btn",
          id: "btn-summary-refresh",
          title: "Rebuild the summary from the chart (diagnoses, prescriptions, referrals, the visit note)",
          onclick: async () => {
            if (dirty) await save({ quiet: true }).catch(() => null);
            void load(box, chart);
          },
        }),
        ...pdfButtons(null),
        h("span", { class: "spacer" }),
        button("Save draft", {
          class: "btn small-btn",
          id: "btn-summary-save",
          onclick: async () => {
            try {
              draw(box, chart, await save());
            } catch (e) {
              toast(describeError(e), { error: true });
            }
          },
        }),
      ),
    ),
    signArea,
    versions,
  );
}

function isSigner(chart) {
  return chart.can_prescribe && chart.appointment.provider?.id === session.staff?.worker?.id;
}

function signedBlock(s, tz) {
  const sent = s.sent_at
    ? `Sent to the patient ${officeDateTime(s.sent_at, tz)} by ${s.sent_channels.join(" + ")}${
        s.sent_to.length ? ` (${s.sent_to.join(", ").replace(/email:/g, "").replace(/sms:/g, "")})` : ""
      }`
    : "Not sent to the patient by e-mail / text" + (s.delivery_note ? ` — ${s.delivery_note}` : "");
  return h(
    "div",
    { class: "summary-signature", id: "summary-signature" },
    s.signature_png
      ? h("img", { class: "signature-img", src: s.signature_png, alt: `Signature of ${s.signer_name}` })
      : null,
    h(
      "p",
      { class: "small" },
      h("strong", {}, `Signed by ${s.signer_name}`),
      ` on ${officeDateTime(s.signed_at, tz)} · verification `,
      h("span", { class: "mono" }, s.verification || ""),
    ),
    h("p", { class: "small muted", id: "summary-delivery" }, sent),
    s.patient_viewed_at
      ? h("p", { class: "small muted" }, `The patient opened it ${officeDateTime(s.patient_viewed_at, tz)}.`)
      : null,
    s.status === "superseded"
      ? callout("warn", "Superseded", h("span", {}, " A newer version replaced this one."))
      : null,
  );
}

function signForm(chart, box, beforeSign) {
  const pad = signaturePad();
  const w = session.staff?.worker;
  const name = input({
    placeholder: "Your full name",
    maxlength: 200,
    autocomplete: "name",
    value: w ? `${w.first_name} ${w.last_name}` : "",
    id: "summary-signer",
  });
  const consent = h("input", { type: "checkbox", id: "summary-consent" });
  const sendNow = h("input", { type: "checkbox", id: "summary-send", checked: true });
  const err = h("div", { class: "check-live" });
  const submit = button("Sign visit summary", { type: "submit", class: "btn primary", id: "btn-summary-sign" });
  const form = h(
    "form",
    { class: "portal-form", id: "summary-sign-form", novalidate: true },
    h("h4", {}, "Sign"),
    h(
      "p",
      { class: "small muted" },
      "I have reviewed this visit summary and confirm that it is correct. Once signed it cannot be changed — a " +
        "mistake is corrected by an amendment.",
    ),
    pad.el,
    field("Type your full name *", name),
    h(
      "label",
      { class: "checkbox consent", for: "summary-consent" },
      consent,
      h("span", {}, "I agree to sign this visit summary electronically. *"),
    ),
    h(
      "label",
      { class: "checkbox", for: "summary-send" },
      sendNow,
      h("span", {}, "Send it to the patient now (e-mail with the PDF, a text that it is ready)"),
    ),
    err,
    h("div", { class: "actions" }, h("span", { class: "spacer" }), submit),
  );
  const started = () => (form.dataset.started = "1");
  pad.el.addEventListener("pointerdown", started);
  consent.addEventListener("change", started);
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
      const out = await api.post(`${summaryUrl(chart)}/sign`, {
        signature: { signer_name: name.value.trim(), image_png: pad.toDataUrl(), consent: true },
        send_to_patient: sendNow.checked,
      });
      toast(
        out.sent_channels.length
          ? `Visit summary signed and sent to the patient (${out.sent_channels.join(" + ")})`
          : "Visit summary signed",
      );
      draw(box, chart, out);
      box.scrollIntoView({ block: "start" });
    } catch (e2) {
      err.append(callout("error", "Could not sign", h("span", {}, describeError(e2))));
      submit.disabled = false;
      submit.textContent = "Sign visit summary";
    }
  });
  return form;
}

function amendForm(box, chart) {
  const holder = box.querySelector("#summary-amend");
  if (!holder || holder.firstChild) return;
  const reason = input({ maxlength: 500, placeholder: "What was wrong?", id: "summary-amend-reason" });
  holder.append(
    h(
      "div",
      { class: "inline-form" },
      field("Why is a new version needed? *", reason),
      h(
        "div",
        { class: "actions" },
        button("Cancel", { class: "btn small-btn", onclick: () => holder.replaceChildren() }),
        h("span", { class: "spacer" }),
        button("Write the amendment", {
          class: "btn small-btn primary",
          id: "btn-summary-amend-start",
          onclick: async () => {
            if (reason.value.trim().length < 3) return toast("Please say why (a few words).", { error: true });
            try {
              draw(box, chart, await api.post(`${summaryUrl(chart)}/amend`, { reason: reason.value.trim() }));
              toast("Amendment started — edit it and sign");
            } catch (e) {
              toast(describeError(e), { error: true });
            }
          },
        }),
      ),
    ),
  );
  reason.focus();
}
