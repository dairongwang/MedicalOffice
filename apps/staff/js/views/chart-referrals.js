import { api } from "../api.js";
import { session } from "../auth.js";
import {
  SPECIALTIES,
  diagnosisLine,
  pharmacyContact,
  plusDays,
  referralDeliveryText,
  referralLine,
  referralProblems,
} from "../chart-format.js";
import { officeDateTime } from "../staff-appointments.js";
import { badge, button, callout, describeError, field, h, input, select, toast } from "../ui.js";
import { openPdf } from "./chart-orders.js";

/**
 * Referrals on the patient's chart (services/account/referrals.py):
 *
 *   referralsSection   "Write a referral" — the specialist (practice, specialty, contact), the urgency, the
 *                      reason, today's diagnosis, the question for the specialist, a clinical summary (from
 *                      today's note in one click), the recent results, valid until; e-mailed to the specialist
 *                      and / or a copy to the patient; signed & sent (the office is told) — and every referral
 *                      of the patient: the ones the office entered and the letters (PDF, e-mail, withdraw)
 *
 * Only doctors and physician assistants write referrals (chart.can_refer); everyone sees them.
 */

const visitUrl = (chart) => `/staff/me/appointments/${chart.appointment.id}`;

/** Today in the office's time zone (YYYY-MM-DD). */
function officeToday(timezone) {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: timezone || "UTC" }).format(new Date());
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

export function referralsSection(chart, reload) {
  const parts = [];
  if (chart.can_refer && chart.appointment.status !== "canceled") parts.push(referralForm(chart, reload));
  else if (!chart.can_refer)
    parts.push(h("p", { class: "small muted" }, "Referrals are written by a doctor or physician assistant."));
  parts.push(referralList(chart, reload));
  return parts;
}

/** Every referral of the patient, newest first. */
export function referralList(chart, reload) {
  return h(
    "div",
    { id: "chart-ref-list" },
    chart.referrals.length
      ? h(
          "ul",
          { class: "rows", id: "chart-referral-list" },
          chart.referrals.map((r) => referralRow(chart, r, reload)),
        )
      : h("p", { class: "empty" }, "No referrals."),
  );
}

const diagnosisOptions = (chart) => [["", "— none —"], ...chart.diagnoses.map((d) => [String(d.id), diagnosisLine(d)])];

/** The referral's diagnosis follows today's primary diagnosis — unless the provider chose one (kept while it
 *  still exists). */
export function updateReferralDiagnoses(chart) {
  const el = document.getElementById("ref-diagnosis");
  if (!el) return;
  const primary = chart.diagnoses.find((d) => d.primary);
  const keep = el.dataset.touched ? el.value : primary ? String(primary.id) : "";
  el.replaceChildren(...select(diagnosisOptions(chart), {}).children);
  el.value = [...el.options].some((o) => o.value === keep) ? keep : primary ? String(primary.id) : "";
}

function referralRow(chart, r, reload) {
  const tz = chart.appointment.timezone;
  const mine = r.provider_id === session.staff?.worker?.id;
  const today = r.appointment_id === chart.appointment.id;
  const url = `${visitUrl(chart)}/referrals/${r.id}`;
  const actions = h("div", { class: "actions ref-actions" });
  const act = async (path, body, done) => {
    try {
      await api.post(`${url}/${path}`, body);
      toast(done);
      await reload("chart-ref-list");
    } catch (e) {
      toast(describeError(e), { error: true });
    }
  };
  if (r.letter) {
    actions.append(
      button("Download letter", {
        class: "btn small-btn",
        id: `btn-ref-pdf-${r.id}`,
        onclick: () => void openPdf(`${url}/pdf`, `${r.number}.pdf`, { download: true }),
      }),
      button("Open / print", {
        class: "btn small-btn",
        id: `btn-ref-open-${r.id}`,
        onclick: () => void openPdf(`${url}/pdf`, `${r.number}.pdf`, { download: false }),
      }),
    );
    if (chart.can_refer && r.status !== "withdrawn") {
      if (r.counterpart_email)
        actions.append(
          button("E-mail to the specialist", {
            class: "btn small-btn",
            id: `btn-ref-send-specialist-${r.id}`,
            title: r.counterpart_email,
            onclick: () => void act("send", { to: "specialist" }, `${r.number} e-mailed to ${r.counterpart}`),
          }),
        );
      if (chart.patient.email_masked)
        actions.append(
          button("E-mail a copy to the patient", {
            class: "btn small-btn",
            id: `btn-ref-send-patient-${r.id}`,
            title: chart.patient.email_masked,
            onclick: () => void act("send", { to: "patient" }, `A copy of ${r.number} e-mailed to the patient`),
          }),
        );
    }
    if (mine && (r.status === "open" || r.status === "scheduled"))
      actions.append(
        button("Withdraw", {
          class: "btn small-btn danger",
          id: `btn-ref-withdraw-${r.id}`,
          title: "Cancel this referral (whoever got the letter by e-mail is told)",
          onclick: () => {
            const reason = prompt(`Why is ${r.number} withdrawn?`, "");
            if (reason === null) return;
            void act("withdraw", { reason: reason.trim() }, `${r.number} withdrawn`);
          },
        }),
      );
  }
  const contact = [
    pharmacyContact({ address: r.counterpart_address, phone: r.counterpart_phone, fax: r.counterpart_fax }),
  ]
    .concat(r.counterpart_email ? [r.counterpart_email] : [])
    .filter(Boolean)
    .join(" · ");
  return h(
    "li",
    {
      class: `row referral ${r.expired ? "expired" : ""} ${r.status} ${r.letter ? "letter" : ""} ${today ? "today" : ""}`,
      dataset: { id: String(r.id) },
    },
    h(
      "div",
      { class: "row-main" },
      badge(r.direction === "outgoing" ? "Outgoing" : "Incoming", ""),
      r.letter ? h("strong", { class: "mono" }, r.number) : null,
      h("strong", {}, r.reason),
      r.urgency === "urgent" ? badge("urgent", "bad") : null,
      r.status === "withdrawn" ? badge("withdrawn", "bad") : null,
      today ? badge("this visit", "") : null,
    ),
    h("div", { class: "small muted" }, referralLine(r)),
    r.letter
      ? h(
          "div",
          { class: "small muted" },
          `Written by ${r.provider_name || "the provider"} · signed ${officeDateTime(r.signed_at, tz)}`,
        )
      : null,
    contact ? h("div", { class: "small" }, `Specialist: ${contact}`) : null,
    r.diagnosis ? h("div", { class: "small" }, `Diagnosis: ${diagnosisLine(r.diagnosis)}`) : null,
    r.question ? h("div", { class: "small" }, `Question: ${r.question}`) : null,
    r.clinical_summary
      ? h(
          "details",
          { class: "small" },
          h("summary", {}, "Clinical summary"),
          h("p", { class: "chart-text" }, r.clinical_summary),
        )
      : null,
    r.expired ? h("div", { class: "small chart-warn" }, "This referral has expired.") : null,
    r.status === "withdrawn" ? h("div", { class: "small chart-warn" }, `Withdrawn: ${r.withdraw_reason}`) : null,
    r.note ? h("div", { class: "small chart-text" }, r.note) : null,
    r.deliveries?.length
      ? h(
          "ul",
          { class: "rx-deliveries small muted" },
          r.deliveries.map((d) => h("li", { class: d.status }, referralDeliveryText(d, tz))),
        )
      : null,
    r.verification ? h("div", { class: "small muted" }, `Verification ${r.verification}`) : null,
    actions.childElementCount ? actions : null,
  );
}

function referralForm(chart, reload) {
  const tz = chart.appointment.timezone;
  const today = officeToday(tz);
  const signer = session.displayName || "the provider";
  const primary = chart.diagnoses.find((d) => d.primary);
  const f = {
    counterpart: input({ id: "ref-counterpart", maxlength: 200, placeholder: "e.g. Dr. Casey Heart — Heart Clinic" }),
    specialty: input({ id: "ref-specialty", maxlength: 120, list: "ref-specialties", placeholder: "e.g. Cardiology" }),
    urgency: select(
      [
        ["routine", "Routine"],
        ["urgent", "Urgent — within days"],
      ],
      { id: "ref-urgency", value: "routine" },
    ),
    counterpart_email: input({ id: "ref-email", type: "email", maxlength: 254, placeholder: "for the letter" }),
    counterpart_phone: input({ id: "ref-phone", type: "tel", maxlength: 32 }),
    counterpart_fax: input({ id: "ref-fax", type: "tel", maxlength: 32 }),
    counterpart_address: input({ id: "ref-address", maxlength: 300 }),
    reason: input({ id: "ref-reason", maxlength: 500, placeholder: "e.g. Exertional chest pain — evaluation" }),
    diagnosis: select(diagnosisOptions(chart), { id: "ref-diagnosis", value: primary ? String(primary.id) : "" }),
    question: h("textarea", {
      class: "input",
      id: "ref-question",
      rows: 2,
      maxlength: 2000,
      placeholder: "What would you like the specialist to do? (evaluate, treat, procedure, opinion)",
    }),
    summary: h("textarea", { class: "input", id: "ref-summary", rows: 5, maxlength: 5000 }),
    valid: input({ id: "ref-valid", type: "date", min: today, value: plusDays(today, 90) }),
  };
  const results = h("input", { type: "checkbox", id: "ref-results", checked: chart.results.length > 0 });
  const toSpecialist = h("input", { type: "checkbox", id: "ref-send-specialist" });
  const toPatient = h("input", {
    type: "checkbox",
    id: "ref-send-patient",
    disabled: !chart.patient.email_masked,
  });
  f.diagnosis.addEventListener("change", () => (f.diagnosis.dataset.touched = "1"));
  const sign = h("input", { type: "checkbox", id: "ref-sign" });
  const msg = h("div", { class: "check-live", id: "ref-msg" });
  const submit = button("Sign & send referral", { class: "btn primary", type: "submit", id: "btn-ref-write" });
  // Typing the specialist's e-mail offers to e-mail the letter (until the box is ticked / unticked by hand).
  toSpecialist.addEventListener("change", () => (toSpecialist.dataset.touched = "1"));
  f.counterpart_email.addEventListener("input", () => {
    if (!toSpecialist.dataset.touched) toSpecialist.checked = !!f.counterpart_email.value.trim();
  });

  const fromNote = button("Use today's note", {
    class: "btn small-btn",
    id: "btn-ref-from-note",
    title: "Fill the clinical summary from today's visit note and assessment",
    onclick: () => {
      const value = (key) => document.getElementById(`visit-${key}`)?.value?.trim() || chart.visit_note?.[key] || "";
      const text = [value("note"), value("assessment") && `Assessment: ${value("assessment")}`]
        .filter(Boolean)
        .join("\n\n");
      if (!text) {
        toast("Today's note is still empty", { error: true });
        return;
      }
      if (f.summary.value.trim() && !confirm("Replace the clinical summary with today's note?")) return;
      f.summary.value = text;
    },
  });

  const form = h(
    "form",
    {
      class: "form orders-form ref-new",
      id: "ref-form",
      novalidate: true,
      onsubmit: async (e) => {
        e.preventDefault();
        msg.replaceChildren();
        const body = {
          counterpart: f.counterpart.value.trim(),
          specialty: f.specialty.value.trim(),
          urgency: f.urgency.value,
          reason: f.reason.value.trim(),
          diagnosis_id: f.diagnosis.value ? Number(f.diagnosis.value) : null,
          question: f.question.value.trim() || null,
          clinical_summary: f.summary.value.trim() || null,
          include_results: results.checked,
          counterpart_email: f.counterpart_email.value.trim() || null,
          counterpart_phone: f.counterpart_phone.value.trim() || null,
          counterpart_fax: f.counterpart_fax.value.trim() || null,
          counterpart_address: f.counterpart_address.value.trim() || null,
          valid_until: f.valid.value || null,
          send_to_specialist: toSpecialist.checked,
          send_to_patient: toPatient.checked,
        };
        const problems = referralProblems(body, { signed: sign.checked, signer, today });
        if (problems.length) {
          msg.append(
            callout(
              "error",
              "Not yet",
              h(
                "ul",
                {},
                problems.map((p) => h("li", {}, p)),
              ),
            ),
          );
          return;
        }
        submit.disabled = true;
        try {
          const ref = await api.post(`${visitUrl(chart)}/referrals`, body);
          const sent = ref.deliveries
            .filter((d) => d.recipient !== "office" && d.status === "sent")
            .map((d) => d.recipient);
          const failed = ref.deliveries.some((d) => d.status === "failed" && d.recipient !== "office");
          toast(
            `${ref.number} signed` +
              (sent.length ? ` and e-mailed to the ${sent.join(" and the ")}` : " — download the letter to print it") +
              (failed ? " (an e-mail failed — see the referral)" : ""),
            { error: failed },
          );
          await reload("chart-ref-list", { resetReferral: true });
        } catch (err) {
          submit.disabled = false;
          msg.append(callout("error", "Not sent", h("span", {}, ` ${describeError(err)}`)));
        }
      },
    },
    h(
      "datalist",
      { id: "ref-specialties" },
      SPECIALTIES.map((s) => h("option", { value: s })),
    ),
    chart.patient.insurance_verification?.referral_required
      ? callout(
          "info",
          "Referral required",
          " The patient's plan needs a referral for specialist visits — this letter is it (the insurance details are printed on it).",
        )
      : null,
    h(
      "div",
      { class: "orders-grid" },
      field("Referred to (specialist / practice)", f.counterpart),
      field("Specialty", f.specialty),
      field("Urgency", f.urgency),
    ),
    h(
      "details",
      { class: "ref-contact", open: false },
      h("summary", { class: "small" }, "The specialist's contact — e-mail for the letter, phone, fax, address"),
      h(
        "div",
        { class: "orders-grid" },
        field("E-mail", f.counterpart_email, { optional: true }),
        field("Phone", f.counterpart_phone, { optional: true }),
        field("Fax", f.counterpart_fax, { optional: true }),
        field("Address", f.counterpart_address, { optional: true }),
      ),
    ),
    h(
      "div",
      { class: "orders-grid" },
      field("Reason for the referral", f.reason),
      field("Diagnosis", f.diagnosis, { optional: true }),
    ),
    field("Question for the specialist", f.question, { optional: true }),
    h(
      "div",
      { class: "field" },
      h(
        "label",
        { for: f.summary.id },
        "Clinical summary",
        h("span", { class: "muted" }, " (optional)"),
        " ",
        fromNote,
      ),
      f.summary,
      h(
        "p",
        { class: "hint" },
        "The letter adds the current medications, allergies, active conditions and today's vital signs from the chart.",
      ),
    ),
    h(
      "div",
      { class: "actions" },
      field("Valid until", f.valid, { optional: true }),
      h(
        "label",
        { class: "checkbox inline", for: results.id },
        results,
        h("span", {}, `Include the recent results (${Math.min(chart.results.length, 3)} of ${chart.results.length})`),
      ),
    ),
    h(
      "div",
      { class: "ref-send" },
      h(
        "label",
        { class: "checkbox inline", for: toSpecialist.id },
        toSpecialist,
        h("span", {}, "E-mail the letter to the specialist"),
      ),
      h(
        "label",
        { class: "checkbox inline", for: toPatient.id },
        toPatient,
        h(
          "span",
          {},
          chart.patient.email_masked
            ? `E-mail a copy to the patient (${chart.patient.email_masked})`
            : "E-mail a copy to the patient — no e-mail address on file (print the letter)",
        ),
      ),
      h("span", { class: "small muted" }, "The office is told about the referral to help the patient book it."),
    ),
    h(
      "label",
      { class: "checkbox consent", for: sign.id },
      sign,
      h("span", {}, `I, ${signer}, sign this referral electronically.`),
    ),
    h("div", { class: "actions" }, h("span", { class: "spacer" }), submit),
    msg,
  );
  return h(
    "details",
    { class: "ref-write", id: "ref-write" },
    h("summary", { class: "btn", id: "btn-ref-new" }, "Write a referral"),
    form,
  );
}
