import { api } from "../api.js";
import { isoDate, referralDeliveryText } from "../format.js";
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
  toast,
} from "../ui.js";

/**
 * The patient's lab / imaging results and referral notes on the Clinical page of the record
 * (GET/POST/PATCH /patients/{id}/results and /referrals — services/account/clinical.py). The office
 * enters them as they arrive; the provider reads them in the patient's chart on the staff dashboard.
 * Referrals the provider wrote on the chart come with a signed letter (referrals.py): the office helps the
 * patient book them (status scheduled → completed) and prints the letter (recorded).
 */

const RESULT_STATUSES = [
  ["final", "Final"],
  ["preliminary", "Preliminary"],
  ["pending", "Pending"],
  ["amended", "Amended"],
];
const FLAGS = [
  ["", "—"],
  ["H", "High"],
  ["L", "Low"],
  ["A", "Abnormal"],
];
const REFERRAL_STATUSES = [
  ["open", "Open"],
  ["scheduled", "Scheduled"],
  ["completed", "Completed"],
  ["closed", "Closed"],
];

export function resultsSection(p) {
  const section = h("section", { class: "card", id: "patient-results" });
  void reload();
  return section;

  async function reload() {
    let rows;
    try {
      rows = await api.get(`/patients/${p.id}/results`);
    } catch (e) {
      render(
        section,
        h("h3", {}, "Lab & imaging results"),
        callout("error", "Could not load", h("span", {}, describeError(e))),
      );
      return;
    }
    render(
      section,
      h("h3", {}, "Lab & imaging results", h("span", { class: "muted small" }, ` · ${rows.length}`)),
      rows.length
        ? h(
            "ul",
            { class: "rows", id: "patient-results-list" },
            rows.map((r) => resultRow(r)),
          )
        : h("p", { class: "empty" }, "No results on record."),
      resultForm(p, reload),
    );
  }

  function resultRow(r) {
    const status = select(RESULT_STATUSES, {
      id: `result-status-${r.id}`,
      value: r.status,
      class: "input small-select",
    });
    status.addEventListener("change", async () => {
      try {
        await api.patch(`/patients/${p.id}/results/${r.id}`, { status: status.value });
        toast(`${r.name}: ${status.value}`);
        await reload();
      } catch (e) {
        toast(describeError(e), { error: true });
      }
    });
    return h(
      "li",
      { class: `row lab-result ${r.abnormal ? "abnormal" : ""}`, dataset: { id: String(r.id) } },
      h(
        "div",
        { class: "row-main" },
        badge(r.kind === "lab" ? "Lab" : "Imaging", ""),
        h("strong", {}, r.name),
        r.abnormal ? badge("out of range", "bad") : null,
      ),
      h(
        "div",
        { class: "muted small" },
        [
          r.resulted_on
            ? `resulted ${formatDate(r.resulted_on)}`
            : r.ordered_on
              ? `ordered ${formatDate(r.ordered_on)}`
              : "",
          r.performed_by,
          r.ordered_by ? `ordered by ${r.ordered_by}${r.appointment_number ? ` (${r.appointment_number})` : ""}` : "",
        ]
          .filter(Boolean)
          .join(" · "),
      ),
      r.values.length
        ? h(
            "ul",
            { class: "result-values-list small" },
            r.values.map((v) =>
              h(
                "li",
                { class: v.flag ? "flagged" : "" },
                `${v.name}: ${v.value}${v.unit ? ` ${v.unit}` : ""}${v.range ? ` (ref. ${v.range})` : ""}`,
                v.flag ? ` ${v.flag}` : "",
              ),
            ),
          )
        : null,
      r.summary ? h("p", { class: "small result-summary" }, r.summary) : null,
      h("div", { class: "row-meta" }, field("Status", status)),
    );
  }
}

function resultForm(p, onSaved) {
  const kind = select(
    [
      ["lab", "Lab result"],
      ["imaging", "Imaging report"],
    ],
    { id: "result-kind" },
  );
  const name = input({ id: "result-name", maxlength: 200, placeholder: "e.g. Lipid panel / Chest X-ray" });
  const status = select(RESULT_STATUSES, { id: "result-status" });
  const ordered = input({ id: "result-ordered", type: "date" });
  const resulted = input({ id: "result-resulted", type: "date", value: isoDate(new Date()) });
  const by = input({ id: "result-by", maxlength: 200, placeholder: "The lab / imaging center" });
  const summary = h("textarea", { class: "input", id: "result-summary", rows: 2, maxlength: 5000 });
  const summaryField = field("Comment", summary, { optional: true });
  const values = h("div", { class: "result-value-rows", id: "result-values" });
  const addValue = button("+ Add value", { class: "btn small-btn", id: "btn-result-add-value" });
  const valueRow = () => {
    const row = h(
      "div",
      { class: "result-value-row" },
      input({ class: "input v-name", placeholder: "Test", maxlength: 120, "aria-label": "Test" }),
      input({ class: "input v-value", placeholder: "Value", maxlength: 60, "aria-label": "Value" }),
      input({ class: "input v-unit", placeholder: "Unit", maxlength: 30, "aria-label": "Unit" }),
      input({ class: "input v-range", placeholder: "Reference range", maxlength: 60, "aria-label": "Reference range" }),
      select(FLAGS, { class: "input v-flag", "aria-label": "Flag" }),
      button("✕", { class: "btn small-btn", title: "Remove this value", onclick: () => row.remove() }),
    );
    values.append(row);
  };
  addValue.addEventListener("click", valueRow);
  valueRow();
  const valuesPart = h("div", { class: "field" }, h("label", {}, "Values"), values, addValue);
  kind.addEventListener("change", () => {
    const lab = kind.value === "lab";
    valuesPart.hidden = !lab;
    summaryField.querySelector("label").firstChild.textContent = lab ? "Comment" : "Impression";
  });
  const msg = h("div", { class: "check-live" });
  return h(
    "form",
    {
      class: "inline-form clinical-add",
      id: "result-form",
      onsubmit: async (e) => {
        e.preventDefault();
        msg.replaceChildren();
        const rows =
          kind.value === "lab"
            ? [...values.querySelectorAll(".result-value-row")]
                .map((r) => ({
                  name: r.querySelector(".v-name").value.trim(),
                  value: r.querySelector(".v-value").value.trim(),
                  unit: r.querySelector(".v-unit").value.trim() || null,
                  range: r.querySelector(".v-range").value.trim() || null,
                  flag: r.querySelector(".v-flag").value,
                }))
                .filter((v) => v.name || v.value)
            : [];
        try {
          await api.post(`/patients/${p.id}/results`, {
            kind: kind.value,
            name: name.value.trim(),
            status: status.value,
            ordered_on: ordered.value || null,
            resulted_on: resulted.value || null,
            performed_by: by.value.trim() || null,
            summary: summary.value.trim() || null,
            values: rows,
          });
          toast(`${name.value.trim()} added`);
          await onSaved();
        } catch (err) {
          msg.append(callout("error", "Could not add the result", h("span", {}, describeError(err))));
        }
      },
    },
    h("h4", {}, "Add a result"),
    h(
      "div",
      { class: "health-add-fields" },
      field("Kind", kind),
      field("Name", name),
      field("Status", status),
      field("Ordered", ordered, { optional: true }),
      field("Resulted", resulted, { optional: true }),
      field("Performed by", by, { optional: true }),
    ),
    valuesPart,
    summaryField,
    h(
      "div",
      { class: "actions" },
      button("Add result", { class: "btn primary", type: "submit", id: "btn-result-add" }),
    ),
    msg,
  );
}

export function referralsSection(p) {
  const section = h("section", { class: "card", id: "patient-referrals" });
  void reload();
  return section;

  async function reload() {
    let rows;
    try {
      rows = await api.get(`/patients/${p.id}/referrals`);
    } catch (e) {
      render(
        section,
        h("h3", {}, "Referrals"),
        callout("error", "Could not load", h("span", {}, describeError(e))),
      );
      return;
    }
    render(
      section,
      h("h3", {}, "Referrals", h("span", { class: "muted small" }, ` · ${rows.length}`)),
      rows.length
        ? h(
            "ul",
            { class: "rows", id: "patient-referrals-list" },
            rows.map((r) => referralRow(r)),
          )
        : h("p", { class: "empty" }, "No referral notes."),
      referralForm(p, reload),
    );
  }

  function referralRow(r) {
    const status = select(REFERRAL_STATUSES, {
      id: `referral-status-${r.id}`,
      value: r.status,
      class: "input small-select",
    });
    status.addEventListener("change", async () => {
      try {
        await api.patch(`/patients/${p.id}/referrals/${r.id}`, { status: status.value });
        toast(`Referral: ${status.value}`);
        await reload();
      } catch (e) {
        toast(describeError(e), { error: true });
      }
    });
    const contact = [
      r.counterpart_address,
      r.counterpart_phone && `phone ${r.counterpart_phone}`,
      r.counterpart_fax && `fax ${r.counterpart_fax}`,
      r.counterpart_email,
    ]
      .filter(Boolean)
      .join(" · ");
    return h(
      "li",
      {
        class: `row referral ${r.expired ? "expired" : ""} ${r.status} ${r.letter ? "letter" : ""}`,
        dataset: { id: String(r.id) },
      },
      h(
        "div",
        { class: "row-main" },
        badge(r.direction === "outgoing" ? "Outgoing" : "Incoming", ""),
        r.letter ? h("strong", { class: "mono" }, r.number) : null,
        h("strong", {}, r.reason),
        r.urgency === "urgent" ? badge("urgent", "bad") : null,
        r.expired ? badge("expired", "warn") : null,
        r.status === "withdrawn" ? badge("withdrawn", "bad") : null,
      ),
      h(
        "div",
        { class: "muted small" },
        `${r.direction === "outgoing" ? "To" : "From"} ${r.counterpart}${r.specialty ? ` (${r.specialty})` : ""} · ` +
          `${formatDate(r.referral_date)}${r.valid_until ? ` · valid until ${formatDate(r.valid_until)}` : ""}`,
      ),
      r.letter
        ? h(
            "div",
            { class: "small" },
            `Written by ${r.provider_name || "the provider"}${r.appointment_number ? ` at ${r.appointment_number}` : ""}` +
              ` · signed ${formatDateTime(r.signed_at)}`,
          )
        : null,
      contact ? h("div", { class: "small" }, `Contact: ${contact}`) : null,
      r.diagnosis
        ? h(
            "div",
            { class: "small" },
            `Diagnosis: ${r.diagnosis.code ? `${r.diagnosis.code} — ` : ""}${r.diagnosis.description}`,
          )
        : null,
      r.status === "withdrawn" ? h("div", { class: "small bad-text" }, `Withdrawn: ${r.withdraw_reason}`) : null,
      r.note ? h("p", { class: "small result-summary" }, r.note) : null,
      r.deliveries?.length
        ? h(
            "ul",
            { class: "rx-deliveries small muted" },
            r.deliveries.map((d) => h("li", { class: d.status }, referralDeliveryText(d, formatDateTime))),
          )
        : null,
      h(
        "div",
        { class: "row-meta" },
        r.status === "withdrawn" ? null : field("Status", status),
        h("span", { class: "spacer" }),
        r.letter
          ? button("Print letter", {
              class: "btn small-btn",
              id: `btn-admin-ref-pdf-${r.id}`,
              title: "Open the referral letter to print it for the patient (recorded on the referral)",
              onclick: async () => {
                const tab = window.open("", "_blank"); // now, in the click — a later window.open would be blocked
                try {
                  const url = await api.blobUrl(`/api/v1/patients/${p.id}/referrals/${r.id}/pdf`);
                  if (tab) tab.location.href = url;
                  else location.href = url;
                  setTimeout(() => URL.revokeObjectURL(url), 60_000);
                  await reload();
                } catch (e) {
                  tab?.close();
                  toast(describeError(e), { error: true });
                }
              },
            })
          : null,
      ),
    );
  }
}

function referralForm(p, onSaved) {
  const direction = select(
    [
      ["outgoing", "Outgoing — to a specialist"],
      ["incoming", "Incoming — from another practice"],
    ],
    { id: "referral-direction" },
  );
  const counterpart = input({ id: "referral-counterpart", maxlength: 200, placeholder: "Provider / practice" });
  const specialty = input({ id: "referral-specialty", maxlength: 120, placeholder: "e.g. Cardiology" });
  const date = input({ id: "referral-date", type: "date", value: isoDate(new Date()) });
  const until = input({ id: "referral-until", type: "date" });
  const reason = input({ id: "referral-reason", maxlength: 500, placeholder: "Why" });
  const note = h("textarea", { class: "input", id: "referral-note", rows: 2, maxlength: 5000 });
  const status = select(REFERRAL_STATUSES, { id: "referral-status" });
  const msg = h("div", { class: "check-live" });
  return h(
    "form",
    {
      class: "inline-form clinical-add",
      id: "referral-form",
      onsubmit: async (e) => {
        e.preventDefault();
        msg.replaceChildren();
        try {
          await api.post(`/patients/${p.id}/referrals`, {
            direction: direction.value,
            counterpart: counterpart.value.trim(),
            specialty: specialty.value.trim() || null,
            referral_date: date.value,
            valid_until: until.value || null,
            reason: reason.value.trim(),
            note: note.value.trim() || null,
            status: status.value,
          });
          toast("Referral added");
          await onSaved();
        } catch (err) {
          msg.append(callout("error", "Could not add the referral", h("span", {}, describeError(err))));
        }
      },
    },
    h("h4", {}, "Add a referral note"),
    h(
      "div",
      { class: "health-add-fields" },
      field("Direction", direction),
      field("Provider / practice", counterpart),
      field("Specialty", specialty, { optional: true }),
      field("Date", date),
      field("Valid until", until, { optional: true }),
      field("Status", status),
      field("Reason", reason),
    ),
    field("Note / letter", note, { optional: true }),
    h(
      "div",
      { class: "actions" },
      button("Add referral", { class: "btn primary", type: "submit", id: "btn-referral-add" }),
    ),
    msg,
  );
}
