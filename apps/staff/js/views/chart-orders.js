import { api } from "../api.js";
import { session } from "../auth.js";
import {
  DOSAGE_FORMS,
  defaultPharmacyId,
  deliveryText,
  diagnosisLine,
  icd10Ok,
  pharmacyContact,
  rxItemProblems,
  rxItemText,
  unitFor,
} from "../chart-format.js";
import { officeDateTime } from "../staff-appointments.js";
import { badge, button, callout, describeError, field, h, input, render, select, toast } from "../ui.js";

/**
 * The provider's orders on the patient's chart (services/account/prescriptions.py):
 *
 *   diagnosesCard     today's diagnoses — description, ICD-10 code, primary, note, "add to the medical
 *                     history"; taken back with ✕
 *   prescriptionsSection   a new prescription (one or more medications: drug, strength, form, directions,
 *                     instructions for the patient (shown in the patients' app, "My medicines"),
 *                     quantity, refills, substitution; the indication; a note to the pharmacist; e-mail it
 *                     to a pharmacy or not) signed & issued — and every prescription of the patient: the PDF
 *                     (download / open to print), e-mail it to a pharmacy, void it (the prescriber)
 *
 * Only doctors and physician assistants may diagnose and prescribe (chart.can_prescribe).
 */

const visitUrl = (chart) => `/staff/me/appointments/${chart.appointment.id}`;

export function diagnosesCard(chart, reload) {
  const box = h("div", { class: "orders-card", id: "chart-diagnoses" }, h("h4", {}, "Diagnoses"));
  if (!chart.can_prescribe) {
    box.append(h("p", { class: "small muted" }, "Diagnoses are recorded by a doctor or physician assistant."));
  }
  box.append(
    chart.diagnoses.length
      ? h(
          "ul",
          { class: "diagnosis-list" },
          chart.diagnoses.map((d) =>
            h(
              "li",
              { dataset: { id: String(d.id) } },
              d.primary ? badge("primary", "ok") : badge("secondary", ""),
              " ",
              h("strong", {}, diagnosisLine(d)),
              d.in_history ? badge("in medical history", "muted") : null,
              d.note ? h("span", { class: "muted small" }, ` — ${d.note}`) : null,
              chart.can_prescribe
                ? button("✕", {
                    class: "btn small-btn",
                    title: "Take this diagnosis back",
                    id: `btn-diagnosis-remove-${d.id}`,
                    onclick: async () => {
                      if (!confirm(`Remove the diagnosis “${diagnosisLine(d)}”?`)) return;
                      try {
                        await api.delete(`${visitUrl(chart)}/diagnoses/${d.id}`);
                        toast("Diagnosis removed");
                        await reload("chart-diagnoses");
                      } catch (e) {
                        toast(describeError(e), { error: true });
                      }
                    },
                  })
                : null,
            ),
          ),
        )
      : h("p", { class: "empty small" }, "No diagnosis recorded for this visit yet."),
  );
  if (!chart.can_prescribe) return box;
  const description = input({ id: "dx-description", maxlength: 300, placeholder: "e.g. Essential hypertension" });
  const code = input({ id: "dx-code", maxlength: 10, placeholder: "e.g. I10", class: "input dx-code" });
  const primary = h("input", { type: "checkbox", id: "dx-primary", checked: !chart.diagnoses.length });
  const history = h("input", { type: "checkbox", id: "dx-history" });
  const note = input({ id: "dx-note", maxlength: 1000, placeholder: "Optional" });
  const msg = h("div", { class: "check-live" });
  box.append(
    h(
      "form",
      {
        class: "form orders-form",
        id: "dx-form",
        onsubmit: async (e) => {
          e.preventDefault();
          msg.replaceChildren();
          if (description.value.trim().length < 2) {
            msg.append(callout("error", "The diagnosis", " What is the diagnosis?"));
            return;
          }
          if (!icd10Ok(code.value)) {
            msg.append(callout("error", "ICD-10 code", " That is not an ICD-10 code (e.g. I10, E11.9, R73.03)."));
            return;
          }
          try {
            await api.post(`${visitUrl(chart)}/diagnoses`, {
              description: description.value.trim(),
              code: code.value.trim() || null,
              primary: primary.checked,
              note: note.value.trim() || null,
              add_to_history: history.checked,
            });
            toast("Diagnosis recorded");
            await reload("chart-diagnoses");
          } catch (err) {
            msg.append(callout("error", "Could not record the diagnosis", h("span", {}, describeError(err))));
          }
        },
      },
      h(
        "div",
        { class: "orders-grid" },
        field("Diagnosis", description),
        field("ICD-10 code", code, { optional: true }),
        field("Note", note, { optional: true }),
      ),
      h(
        "div",
        { class: "actions" },
        h("label", { class: "checkbox", for: primary.id }, primary, h("span", {}, "Primary diagnosis")),
        h(
          "label",
          { class: "checkbox", for: history.id },
          history,
          h("span", {}, "Add to the medical history (a lasting condition)"),
        ),
        h("span", { class: "spacer" }),
        button("Add diagnosis", { class: "btn primary", type: "submit", id: "btn-dx-add" }),
      ),
      msg,
    ),
  );
  return box;
}

/** Download a PDF the API serves behind the sign-in (a file), or open it in a new tab to print. */
export async function openPdf(path, filename, { download }) {
  const tab = download ? null : window.open("", "_blank"); // opened now: a later window.open would be blocked
  try {
    const blob = await api.blob(path);
    const url = URL.createObjectURL(blob);
    if (tab) tab.location.href = url;
    else {
      const a = h("a", { href: url, download: filename });
      document.body.append(a);
      a.click();
      a.remove();
    }
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (e) {
    if (tab) tab.close();
    toast(describeError(e), { error: true });
  }
}

const rxPdf = (chart, rx, opts) => openPdf(`${visitUrl(chart)}/prescriptions/${rx.id}/pdf`, `${rx.number}.pdf`, opts);

const PDF_ONLY = "Don't e-mail — PDF only (print / give to the patient)";

function pharmacyOptions(chart, { none }) {
  const theirs = chart.patient_pharmacy?.pharmacy?.id;
  const mark = (p) => (p.id === theirs ? " — the patient's pharmacy" : "");
  return [
    ...(none ? [["", none]] : [["", "Choose a pharmacy…", { disabled: true }]]),
    ...chart.pharmacies.map((p) =>
      p.email
        ? [String(p.id), `${p.name}${mark(p)}`]
        : [String(p.id), `${p.name}${mark(p)} — no e-mail (print the PDF)`, { disabled: true }],
    ),
  ];
}

/**
 * The pharmacy the patient uses — asked at the visit: confirmed ("Still the same"), changed to another one
 * of the directory, a new one the patient names (added to the directory), its details corrected, or none.
 * New prescriptions go there by default (when it takes e-mail).
 */
export function pharmacyCard(chart, reload) {
  const url = `${visitUrl(chart)}/pharmacy`;
  const pp = chart.patient_pharmacy || {};
  const p = pp.pharmacy;
  const tz = chart.appointment.timezone;
  const panel = h("div", { class: "pharmacy-panel", id: "chart-pharmacy-panel" });
  const save = async (method, body, done, msg = null) => {
    try {
      await api[method](url, body);
      toast(done);
      await reload("chart-pharmacy");
    } catch (e) {
      if (msg) msg.replaceChildren(callout("error", "Not saved", h("span", {}, ` ${describeError(e)}`)));
      else toast(describeError(e), { error: true });
    }
  };

  const changeForm = () => {
    const offered = chart.pharmacies.some((x) => x.id === p?.id);
    const choose = select(
      [
        ["", "Choose from the directory…", { disabled: true }],
        ...chart.pharmacies.map((x) => [String(x.id), `${x.name}${x.email ? "" : " (no e-mail)"}`]),
      ],
      { id: "pharmacy-choose", value: offered ? String(p.id) : "" },
    );
    const f = {
      name: input({ id: "pharmacy-new-name", maxlength: 200, placeholder: "e.g. Riverside Pharmacy" }),
      email: input({ id: "pharmacy-new-email", type: "email", maxlength: 254, placeholder: "where prescriptions go" }),
      phone: input({ id: "pharmacy-new-phone", type: "tel", maxlength: 32 }),
      fax: input({ id: "pharmacy-new-fax", type: "tel", maxlength: 32 }),
      address: input({ id: "pharmacy-new-address", maxlength: 300, placeholder: "Street, town" }),
    };
    const msg = h("div", { class: "check-live", id: "pharmacy-msg" });
    return [
      h(
        "form",
        {
          class: "orders-form",
          id: "pharmacy-choose-form",
          onsubmit: (e) => {
            e.preventDefault();
            if (!choose.value) toast("Choose a pharmacy first", { error: true });
            else void save("put", { pharmacy_id: Number(choose.value) }, "The patient's pharmacy is saved", msg);
          },
        },
        field("From the directory", choose),
        h(
          "div",
          { class: "actions" },
          button("Use this pharmacy", { class: "btn small-btn primary", type: "submit", id: "btn-pharmacy-use" }),
          button("Cancel", { class: "btn small-btn", onclick: () => panel.replaceChildren() }),
        ),
      ),
      h(
        "form",
        {
          class: "orders-form pharmacy-add",
          id: "pharmacy-add-form",
          novalidate: true,
          onsubmit: (e) => {
            e.preventDefault();
            const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim() || null]));
            const problems = [];
            if (!body.name || body.name.length < 2) problems.push("The pharmacy's name.");
            if (body.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.email))
              problems.push("That e-mail address does not look right.");
            if (problems.length) {
              msg.replaceChildren(
                callout(
                  "error",
                  "Not yet",
                  h(
                    "ul",
                    {},
                    problems.map((x) => h("li", {}, x)),
                  ),
                ),
              );
              return;
            }
            void save("put", { pharmacy: body }, `${body.name} is the patient's pharmacy`, msg);
          },
        },
        h("h5", {}, "Not in the list? Add the pharmacy the patient names"),
        h(
          "div",
          { class: "orders-grid" },
          field("Name", f.name),
          field("E-mail for prescriptions", f.email, { optional: true }),
          field("Phone", f.phone, { optional: true }),
          field("Fax", f.fax, { optional: true }),
        ),
        field("Address", f.address, { optional: true }),
        h(
          "div",
          { class: "actions" },
          h("span", { class: "small muted" }, "It is added to the office's pharmacy directory."),
          h("span", { class: "spacer" }),
          button("Add & use", { class: "btn small-btn primary", type: "submit", id: "btn-pharmacy-add" }),
        ),
        msg,
      ),
    ];
  };

  const detailsForm = () => {
    const f = {
      email: input({ id: "pharmacy-edit-email", type: "email", maxlength: 254, value: p.email || "" }),
      phone: input({ id: "pharmacy-edit-phone", type: "tel", maxlength: 32, value: p.phone || "" }),
      fax: input({ id: "pharmacy-edit-fax", type: "tel", maxlength: 32, value: p.fax || "" }),
      address: input({ id: "pharmacy-edit-address", maxlength: 300, value: p.address || "" }),
    };
    const msg = h("div", { class: "check-live" });
    return h(
      "form",
      {
        class: "orders-form",
        id: "pharmacy-details-form",
        novalidate: true,
        onsubmit: (e) => {
          e.preventDefault();
          const changed = Object.fromEntries(
            Object.entries(f)
              .map(([k, el]) => [k, el.value.trim() || null])
              .filter(([k, v]) => v !== (p[k] || null)),
          );
          if (!Object.keys(changed).length) {
            panel.replaceChildren();
            return;
          }
          if (changed.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(changed.email)) {
            msg.replaceChildren(callout("error", "E-mail", " That e-mail address does not look right."));
            return;
          }
          void save("patch", changed, `${p.name}: details updated`, msg);
        },
      },
      h("h5", {}, `${p.name} — as the patient tells it`),
      h(
        "div",
        { class: "orders-grid" },
        field("E-mail for prescriptions", f.email, { optional: true }),
        field("Phone", f.phone, { optional: true }),
        field("Fax", f.fax, { optional: true }),
        field("Address", f.address, { optional: true }),
      ),
      h(
        "div",
        { class: "actions" },
        h("span", { class: "small muted" }, "The office's directory is updated too."),
        h("span", { class: "spacer" }),
        button("Cancel", { class: "btn small-btn", onclick: () => panel.replaceChildren() }),
        button("Save details", { class: "btn small-btn primary", type: "submit", id: "btn-pharmacy-details-save" }),
      ),
      msg,
    );
  };

  return h(
    "div",
    { class: "orders-card pharmacy-card", id: "chart-pharmacy" },
    h(
      "div",
      { class: "row-main" },
      h("h4", {}, "The patient's pharmacy"),
      p && !p.email ? badge("no e-mail — prescriptions are printed", "warn") : null,
      p && !p.active ? badge("inactive in the directory", "bad") : null,
    ),
    p
      ? h(
          "div",
          { class: "pharmacy-current" },
          h("strong", { id: "chart-pharmacy-name" }, p.name),
          pharmacyContact(p) ? h("div", { class: "small" }, pharmacyContact(p)) : null,
          p.email ? h("div", { class: "small muted" }, `Prescriptions are e-mailed to ${p.email}`) : null,
        )
      : h(
          "p",
          { class: "small", id: "chart-pharmacy-name" },
          "No pharmacy on file — ask the patient which pharmacy they use.",
        ),
    pp.set_at
      ? h(
          "div",
          { class: "small muted", id: "chart-pharmacy-set" },
          `${p ? "Last confirmed" : "Set to none"} by ${pp.set_by || "—"} on ${officeDateTime(pp.set_at, tz)}`,
        )
      : null,
    h(
      "div",
      { class: "actions" },
      p
        ? button("Still the same ✓", {
            class: "btn small-btn",
            id: "btn-pharmacy-confirm",
            title: "The patient confirmed they still use this pharmacy",
            onclick: () => void save("put", { pharmacy_id: p.id }, "Pharmacy confirmed"),
          })
        : null,
      button(p ? "Change…" : "Choose or add…", {
        class: "btn small-btn",
        id: "btn-pharmacy-change",
        onclick: () => render(panel, changeForm()),
      }),
      p
        ? button("Update details…", {
            class: "btn small-btn",
            id: "btn-pharmacy-details",
            onclick: () => render(panel, detailsForm()),
          })
        : null,
      p
        ? button("None", {
            class: "btn small-btn",
            id: "btn-pharmacy-none",
            title: "The patient has no preferred pharmacy",
            onclick: () => void save("put", {}, "No preferred pharmacy on file"),
          })
        : null,
    ),
    panel,
  );
}

/** The new prescription's "Send to" follows the patient's pharmacy — unless the prescriber chose one already. */
export function updateSendTo(chart) {
  const el = document.getElementById("rx-pharmacy");
  if (!el) return;
  const keep = el.dataset.touched ? el.value : defaultPharmacyId(chart);
  const fresh = select(pharmacyOptions(chart, { none: PDF_ONLY }), {});
  el.replaceChildren(...fresh.children);
  el.value = [...el.options].some((o) => o.value === keep && !o.disabled) ? keep : "";
}

export function prescriptionsSection(chart, reload) {
  const parts = [pharmacyCard(chart, reload)];
  if (chart.can_prescribe && chart.appointment.status !== "canceled") parts.push(newPrescriptionForm(chart, reload));
  else if (!chart.can_prescribe)
    parts.push(h("p", { class: "small muted" }, "Prescriptions are written by a doctor or physician assistant."));
  parts.push(prescriptionList(chart, reload));
  return parts;
}

/** Every prescription of the patient, newest first. */
export function prescriptionList(chart, reload) {
  return h(
    "div",
    { id: "chart-rx-list" },
    chart.prescriptions.length
      ? h(
          "ul",
          { class: "rows", id: "chart-prescription-list" },
          chart.prescriptions.map((rx) => prescriptionRow(chart, rx, reload)),
        )
      : h("p", { class: "empty" }, "No prescriptions on record."),
  );
}

const indicationOptions = (chart) => [
  ["", "— none —"],
  ...chart.diagnoses.map((d) => [String(d.id), diagnosisLine(d)]),
];

/** The new prescription's "Indication" follows today's primary diagnosis — unless the prescriber chose one
 *  (kept while it still exists). */
export function updateIndications(chart) {
  const el = document.getElementById("rx-diagnosis");
  if (!el) return;
  const primary = chart.diagnoses.find((d) => d.primary);
  const keep = el.dataset.touched ? el.value : primary ? String(primary.id) : "";
  el.replaceChildren(...select(indicationOptions(chart), {}).children);
  el.value = [...el.options].some((o) => o.value === keep) ? keep : primary ? String(primary.id) : "";
}

function prescriptionRow(chart, rx, reload) {
  const tz = chart.appointment.timezone;
  const mine = rx.prescriber_id === session.staff?.worker?.id;
  const today = rx.appointment_id === chart.appointment.id;
  const actions = h("div", { class: "actions rx-actions" });
  actions.append(
    button("Download PDF", {
      class: "btn small-btn",
      id: `btn-rx-pdf-${rx.id}`,
      onclick: () => void rxPdf(chart, rx, { download: true }),
    }),
    button("Open / print", {
      class: "btn small-btn",
      id: `btn-rx-open-${rx.id}`,
      onclick: () => void rxPdf(chart, rx, { download: false }),
    }),
  );
  if (rx.status === "issued" && chart.can_prescribe) {
    const pharmacy = select(pharmacyOptions(chart, { none: null }), {
      id: `rx-pharmacy-${rx.id}`,
      value: defaultPharmacyId(chart),
    });
    actions.append(
      pharmacy,
      button("E-mail to pharmacy", {
        class: "btn small-btn",
        id: `btn-rx-send-${rx.id}`,
        onclick: async () => {
          if (!pharmacy.value) {
            toast("Choose a pharmacy first", { error: true });
            return;
          }
          try {
            await api.post(`${visitUrl(chart)}/prescriptions/${rx.id}/send`, { pharmacy_id: Number(pharmacy.value) });
            toast(`${rx.number} e-mailed to the pharmacy`);
            await reload("chart-rx-list");
          } catch (e) {
            toast(describeError(e), { error: true });
          }
        },
      }),
    );
    if (mine)
      actions.append(
        button("Void", {
          class: "btn small-btn danger",
          id: `btn-rx-void-${rx.id}`,
          title: "Cancel this prescription (a pharmacy it was e-mailed to is told not to fill it)",
          onclick: async () => {
            const reason = prompt(`Why is ${rx.number} voided?`, "");
            if (reason === null) return;
            try {
              await api.post(`${visitUrl(chart)}/prescriptions/${rx.id}/void`, { reason: reason.trim() });
              toast(`${rx.number} voided`);
              await reload("chart-rx-list");
            } catch (e) {
              toast(describeError(e), { error: true });
            }
          },
        }),
      );
  }
  return h(
    "li",
    { class: `row prescription ${rx.status} ${today ? "today" : ""}`, dataset: { id: String(rx.id) } },
    h(
      "div",
      { class: "row-main" },
      h("strong", { class: "mono" }, rx.number),
      rx.status === "voided" ? badge("voided", "bad") : badge("issued", "ok"),
      today ? badge("this visit", "") : null,
      h("span", { class: "muted small" }, ` ${officeDateTime(rx.issued_at, tz)} · ${rx.prescriber_name || ""}`),
    ),
    h(
      "ul",
      { class: "rx-items" },
      rx.items.map((i) =>
        h(
          "li",
          {},
          rxItemText(i),
          i.patient_instructions
            ? h("div", { class: "small muted rx-patient-instructions" }, `For the patient: ${i.patient_instructions}`)
            : null,
        ),
      ),
    ),
    rx.diagnosis ? h("div", { class: "small" }, `Indication: ${diagnosisLine(rx.diagnosis)}`) : null,
    rx.note_to_pharmacist ? h("div", { class: "small" }, `Note to the pharmacist: ${rx.note_to_pharmacist}`) : null,
    rx.allergy_override ? h("div", { class: "small chart-warn" }, `Allergy override: ${rx.allergy_override}`) : null,
    rx.status === "voided" ? h("div", { class: "small chart-warn" }, `Voided: ${rx.void_reason}`) : null,
    rx.deliveries.length
      ? h(
          "ul",
          { class: "rx-deliveries small muted" },
          rx.deliveries.map((d) => h("li", { class: d.status }, deliveryText(d, tz))),
        )
      : h("div", { class: "small muted" }, "Not handed over yet — download the PDF or e-mail it to a pharmacy."),
    h("div", { class: "small muted" }, `Verification ${rx.verification}`),
    actions,
  );
}

/** "Allergy warning: X — … To prescribe anyway, give the reason (allergy_override_reason)." → the sentence for people. */
function allergyWarningText(message) {
  return message
    .replace(/^Allergy warning:\s*/, "")
    .replace(
      /To prescribe anyway, give the reason \(allergy_override_reason\)\.?/,
      "To prescribe anyway, give the reason below and sign again.",
    )
    .trim();
}

function newPrescriptionForm(chart, reload) {
  const rows = h("div", { class: "rx-rows", id: "rx-rows" });
  const addRow = () => {
    const n = rows.childElementCount + 1;
    const form = select(
      DOSAGE_FORMS.map(([value, label]) => [value, label]),
      { class: "input rx-form", value: "tablet", "aria-label": "Form" },
    );
    const quantity = input({
      class: "input rx-quantity",
      type: "number",
      min: 1,
      max: 1000,
      placeholder: "30",
      "aria-label": "Quantity",
    });
    const unit = h("span", { class: "muted small rx-unit" }, unitFor("tablet"));
    form.addEventListener("change", () => (unit.textContent = unitFor(form.value)));
    const sub = h("input", { type: "checkbox", class: "rx-sub", checked: true, id: `rx-sub-${n}-${Date.now()}` });
    const row = h(
      "div",
      { class: "rx-row", dataset: { n: String(n) } },
      h(
        "div",
        { class: "rx-line" },
        input({ class: "input rx-drug", maxlength: 200, placeholder: "Drug, e.g. Lisinopril", "aria-label": "Drug" }),
        input({
          class: "input rx-strength",
          maxlength: 60,
          placeholder: "Strength, e.g. 20 mg",
          "aria-label": "Strength",
        }),
        form,
      ),
      input({
        class: "input rx-sig",
        maxlength: 500,
        placeholder: "Directions, e.g. Take 1 tablet by mouth once daily",
        "aria-label": "Directions",
      }),
      input({
        class: "input rx-instructions",
        maxlength: 1000,
        placeholder: "Instructions for the patient (optional), e.g. Take it with food. Stand up slowly if dizzy.",
        "aria-label": "Instructions for the patient",
        title: "Plain-language advice — shown to the patient in the app (My medicines) and printed on the prescription",
      }),
      h(
        "div",
        { class: "rx-line" },
        h("label", { class: "small" }, "Quantity "),
        quantity,
        unit,
        h("label", { class: "small" }, " Refills "),
        input({ class: "input rx-refills", type: "number", min: 0, max: 11, value: "0", "aria-label": "Refills" }),
        h("label", { class: "checkbox inline", for: sub.id }, sub, h("span", {}, "Substitution permitted")),
        n > 1
          ? button("✕", { class: "btn small-btn", title: "Remove this medication", onclick: () => row.remove() })
          : null,
      ),
    );
    rows.append(row);
  };
  addRow();

  const indication = select(indicationOptions(chart), {
    id: "rx-diagnosis",
    value: chart.diagnoses.find((d) => d.primary) ? String(chart.diagnoses.find((d) => d.primary).id) : "",
  });
  indication.addEventListener("change", () => (indication.dataset.touched = "1"));
  const note = h("textarea", { class: "input", id: "rx-note", rows: 2, maxlength: 1000 });
  const pharmacy = select(pharmacyOptions(chart, { none: PDF_ONLY }), {
    id: "rx-pharmacy",
    value: defaultPharmacyId(chart),
  });
  pharmacy.addEventListener("change", () => (pharmacy.dataset.touched = "1"));
  const override = h("textarea", {
    class: "input",
    id: "rx-override",
    rows: 2,
    maxlength: 500,
    placeholder: "Why prescribe despite the allergy (e.g. tolerated before, benefit outweighs the risk)",
  });
  const overrideField = field("Allergy override — reason", override);
  overrideField.hidden = true;
  const signer = session.displayName || "the prescriber";
  const sign = h("input", { type: "checkbox", id: "rx-sign" });
  const msg = h("div", { class: "check-live", id: "rx-msg" });
  const submit = button("Sign & issue", { class: "btn primary", type: "submit", id: "btn-rx-issue" });

  const collect = () =>
    [...rows.querySelectorAll(".rx-row")].map((r) => ({
      drug: r.querySelector(".rx-drug").value.trim(),
      strength: r.querySelector(".rx-strength").value.trim() || null,
      form: r.querySelector(".rx-form").value,
      sig: r.querySelector(".rx-sig").value.trim(),
      quantity: Number(r.querySelector(".rx-quantity").value),
      refills: Number(r.querySelector(".rx-refills").value || 0),
      substitution_allowed: r.querySelector(".rx-sub").checked,
      patient_instructions: r.querySelector(".rx-instructions").value.trim() || null,
    }));

  return h(
    "form",
    {
      class: "form orders-form rx-new",
      id: "rx-form",
      onsubmit: async (e) => {
        e.preventDefault();
        msg.replaceChildren();
        const items = collect();
        const problems = items.flatMap((item, i) => rxItemProblems(item, i + 1));
        if (!sign.checked) problems.push(`Tick the signature: “I, ${signer}, sign this prescription”.`);
        if (!overrideField.hidden && override.value.trim().length < 3)
          problems.push("Give the reason for the allergy override.");
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
          const rx = await api.post(`${visitUrl(chart)}/prescriptions`, {
            items,
            diagnosis_id: indication.value ? Number(indication.value) : null,
            note_to_pharmacist: note.value.trim() || null,
            pharmacy_id: pharmacy.value ? Number(pharmacy.value) : null,
            allergy_override_reason: overrideField.hidden ? null : override.value.trim(),
          });
          const sent = rx.deliveries.find((d) => d.method === "email");
          toast(
            sent
              ? sent.status === "sent"
                ? `${rx.number} issued and e-mailed to ${sent.pharmacy_name}`
                : `${rx.number} issued — the e-mail failed: ${sent.note}`
              : `${rx.number} issued — download the PDF to print it`,
            { error: sent?.status === "failed" },
          );
          await reload("chart-rx-list", { resetForm: true });
        } catch (err) {
          submit.disabled = false;
          if (err.status === 409 && /Allergy warning/.test(err.message)) {
            overrideField.hidden = false;
            msg.append(
              callout(
                "warn",
                "Allergy warning",
                h("span", { id: "rx-allergy-warning" }, ` ${allergyWarningText(err.message)}`),
              ),
            );
            override.focus();
            return;
          }
          msg.append(callout("error", "Not issued", h("span", {}, describeError(err))));
        }
      },
    },
    h("h4", {}, "New prescription"),
    rows,
    button("+ Add medication", { class: "btn small-btn", id: "btn-rx-add-row", onclick: addRow }),
    h("div", { class: "orders-grid" }, field("Indication", indication, { optional: true }), field("Send to", pharmacy)),
    field("Note to the pharmacist", note, { optional: true }),
    overrideField,
    h(
      "label",
      { class: "checkbox consent", for: sign.id },
      sign,
      h("span", {}, `I, ${signer}, sign this prescription electronically.`),
    ),
    h("div", { class: "actions" }, h("span", { class: "spacer" }), submit),
    msg,
  );
}
