import { api } from "../api.js";
import { rxDeliveryText, rxItemText } from "../format.js";
import { badge, button, callout, describeError, field, formatDateTime, h, render, select, toast } from "../ui.js";

/**
 * The pharmacy the patient uses (GET/PUT /patients/{id}/pharmacy): prescriptions go there by default. The
 * provider asks the patient at the visit; the office can set it here too (from the directory, or none).
 */
export function patientPharmacySection(p) {
  const section = h("section", { class: "card", id: "patient-pharmacy" });
  void reload();
  return section;

  async function reload() {
    let current, directory;
    try {
      [current, directory] = await Promise.all([api.get(`/patients/${p.id}/pharmacy`), api.get("/pharmacies")]);
    } catch (e) {
      render(section, h("h3", {}, "Pharmacy"), callout("error", "Could not load", h("span", {}, describeError(e))));
      return;
    }
    const ph = current.pharmacy;
    const choose = select(
      [["", "— none —"], ...directory.map((x) => [String(x.id), `${x.name}${x.email ? "" : " (no e-mail)"}`])],
      { id: "patient-pharmacy-select", value: ph && directory.some((x) => x.id === ph.id) ? String(ph.id) : "" },
    );
    render(
      section,
      h("h3", {}, "Pharmacy"),
      ph
        ? h(
            "div",
            { id: "patient-pharmacy-current" },
            h("strong", {}, ph.name),
            ph.active ? null : badge("inactive", "bad"),
            ph.email ? null : badge("no e-mail", "warn"),
            h(
              "div",
              { class: "small" },
              [ph.address, ph.phone && `phone ${ph.phone}`, ph.fax && `fax ${ph.fax}`, ph.email]
                .filter(Boolean)
                .join(" · "),
            ),
          )
        : h("p", { class: "empty", id: "patient-pharmacy-current" }, "No pharmacy on file."),
      current.set_at
        ? h("p", { class: "muted small" }, `Last set by ${current.set_by || "—"} on ${formatDateTime(current.set_at)}`)
        : null,
      h(
        "div",
        { class: "row-meta" },
        field("Change to", choose),
        button("Save", {
          class: "btn small-btn",
          id: "btn-patient-pharmacy-save",
          onclick: async () => {
            try {
              await api.put(`/patients/${p.id}/pharmacy`, choose.value ? { pharmacy_id: Number(choose.value) } : {});
              toast("Pharmacy saved");
              await reload();
            } catch (e) {
              toast(describeError(e), { error: true });
            }
          },
        }),
      ),
    );
  }
}

/**
 * The patient's prescriptions on the Clinical page of the record (GET /patients/{id}/prescriptions —
 * services/account/prescriptions.py). Read-only: prescriptions are written, e-mailed and voided by the
 * prescriber on the patient's chart; the front desk can print the PDF for the patient (recorded).
 */
export function prescriptionsSection(p) {
  const section = h("section", { class: "card", id: "patient-prescriptions" });
  void reload();
  return section;

  async function reload() {
    let rows;
    try {
      rows = await api.get(`/patients/${p.id}/prescriptions`);
    } catch (e) {
      render(
        section,
        h("h3", {}, "Prescriptions"),
        callout("error", "Could not load", h("span", {}, describeError(e))),
      );
      return;
    }
    render(
      section,
      h("h3", {}, "Prescriptions", h("span", { class: "muted small" }, ` · ${rows.length}`)),
      h(
        "p",
        { class: "muted small" },
        "Written on the patient's chart by a doctor or physician assistant. Print the PDF for a patient who takes it to a pharmacy.",
      ),
      rows.length
        ? h(
            "ul",
            { class: "rows", id: "patient-prescription-list" },
            rows.map((rx) => prescriptionRow(p, rx, reload)),
          )
        : h("p", { class: "empty" }, "No prescriptions on record."),
    );
  }
}

function prescriptionRow(p, rx, reload) {
  return h(
    "li",
    { class: `row prescription ${rx.status}`, dataset: { id: String(rx.id) } },
    h(
      "div",
      { class: "row-main" },
      h("strong", { class: "mono" }, rx.number),
      rx.status === "voided" ? badge("voided", "bad") : badge("issued", "ok"),
      h(
        "span",
        { class: "muted small" },
        ` ${formatDateTime(rx.issued_at)} · ${rx.prescriber_name || ""}${rx.appointment_number ? ` · ${rx.appointment_number}` : ""}`,
      ),
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
            ? h("div", { class: "small muted" }, `For the patient: ${i.patient_instructions}`)
            : null,
        ),
      ),
    ),
    rx.diagnosis
      ? h(
          "div",
          { class: "small" },
          `Indication: ${rx.diagnosis.code ? `${rx.diagnosis.code} — ` : ""}${rx.diagnosis.description}`,
        )
      : null,
    rx.status === "voided" ? h("div", { class: "small bad-text" }, `Voided: ${rx.void_reason}`) : null,
    rx.deliveries.length
      ? h(
          "ul",
          { class: "rx-deliveries small muted" },
          rx.deliveries.map((d) => h("li", { class: d.status }, rxDeliveryText(d, formatDateTime))),
        )
      : null,
    h(
      "div",
      { class: "actions" },
      h("span", { class: "muted small" }, `Verification ${rx.verification}`),
      h("span", { class: "spacer" }),
      button("Print PDF", {
        class: "btn small-btn",
        id: `btn-admin-rx-pdf-${rx.id}`,
        title: "Open the prescription to print it (recorded on the prescription)",
        onclick: async () => {
          const tab = window.open("", "_blank"); // now, in the click — a later window.open would be blocked
          try {
            const url = await api.blobUrl(`/api/v1/patients/${p.id}/prescriptions/${rx.id}/pdf`);
            if (tab) tab.location.href = url;
            else location.href = url;
            setTimeout(() => URL.revokeObjectURL(url), 60_000);
            await reload();
          } catch (e) {
            tab?.close();
            toast(describeError(e), { error: true });
          }
        },
      }),
    ),
  );
}
