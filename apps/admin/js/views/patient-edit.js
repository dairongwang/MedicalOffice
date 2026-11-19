import { api } from "../api.js";
import { cardCapture } from "../card-photo.js";
import { button, callout, describeError, field, h, input, orNull, select, toast } from "../ui.js";

const RELATIONSHIPS = [
  ["self", "The patient"],
  ["spouse", "Spouse / partner"],
  ["parent", "Parent / guardian"],
  ["child", "Child"],
  ["other", "Someone else"],
];

/**
 * The office edits a patient record (PUT /patients/{id}): every field including the date of
 * birth, the insurance section, and the card photos — upload a file or take one with the
 * camera (card-photo.js), keep / replace / remove what is on file. A changed insurance is
 * checked again by the API. `onSaved(record)` gets the updated record; `onCancel()` leaves.
 */
export function patientEditForm(p, { onSaved, onCancel }) {
  const msg = h("div", { id: "edit-msg" });
  const f = {};
  const text = (name, attrs = {}) => (f[name] = input({ name, value: p[name] ?? "", ...attrs }));
  text("first_name", { required: true, maxlength: 100, autocomplete: "off" });
  text("last_name", { required: true, maxlength: 100, autocomplete: "off" });
  text("date_of_birth", { type: "date", required: true });
  f.sex = select(
    [
      ["", "Not given"],
      ["female", "Female"],
      ["male", "Male"],
      ["other", "Other"],
      ["unspecified", "Unspecified"],
    ],
    { name: "sex", value: p.sex ?? "" },
  );
  text("email", { type: "email", maxlength: 320 });
  text("phone", { type: "tel", maxlength: 32 });
  text("address_line", { maxlength: 200 });
  text("city", { maxlength: 100 });
  text("state", { maxlength: 100 });
  text("postal_code", { maxlength: 20 });
  text("country", { maxlength: 100 });
  text("insurance_provider", { maxlength: 120 });
  text("insurance_plan_name", { maxlength: 120 });
  text("insurance_member_id", { maxlength: 64 });
  text("insurance_group_number", { maxlength: 64 });
  f.insurance_policyholder_relationship = select(RELATIONSHIPS, {
    name: "insurance_policyholder_relationship",
    value: p.insurance_policyholder_relationship ?? "self",
  });
  text("insurance_policyholder_name", { maxlength: 120 });
  text("emergency_contact_name", { maxlength: 120 });
  text("emergency_contact_phone", { type: "tel", maxlength: 32 });
  f.reason_for_visit = h("textarea", { class: "input", name: "reason_for_visit", maxlength: 2000, rows: 3 });
  f.reason_for_visit.value = p.reason_for_visit ?? "";
  for (const [name, el] of Object.entries(f)) el.id = `edit-${name.replace(/_/g, "-")}`;

  const stored = (side) => (p.insurance_cards || []).find((c) => c.side === side) || null;
  const front = cardCapture({ side: "front", stored: stored("front") });
  const back = cardCapture({ side: "back", stored: stored("back") });

  const save = button("Save changes", { type: "submit", class: "btn primary", id: "btn-edit-save" });
  const cancel = button("Cancel", { class: "btn", id: "btn-edit-cancel", onclick: () => onCancel() });
  const form = h(
    "form",
    { class: "card form", id: "patient-edit", novalidate: true },
    h("h3", {}, `Edit ${p.first_name} ${p.last_name} · ${p.mrn}`),
    h(
      "p",
      { class: "muted small" },
      "Everything can be corrected here, including the date of birth. A changed insurance is checked again when you save.",
    ),
    msg,
    h("h4", { class: "section-title" }, "Identity"),
    h("div", { class: "field-row" }, field("First name", f.first_name), field("Last name", f.last_name)),
    h("div", { class: "field-row" }, field("Date of birth", f.date_of_birth), field("Sex", f.sex)),
    h("h4", { class: "section-title" }, "Contact"),
    h(
      "div",
      { class: "field-row" },
      field("E-mail", f.email, { optional: true }),
      field("Phone", f.phone, { optional: true }),
    ),
    field("Street address", f.address_line, { optional: true }),
    h(
      "div",
      { class: "field-row three" },
      field("City", f.city, { optional: true }),
      field("State / province", f.state, { optional: true }),
      field("Postal code", f.postal_code, { optional: true }),
    ),
    field("Country", f.country, { optional: true }),
    h("h4", { class: "section-title" }, "Insurance"),
    h(
      "div",
      { class: "field-row" },
      field("Insurance company", f.insurance_provider, { optional: true }),
      field("Plan name / type", f.insurance_plan_name, { optional: true }),
    ),
    h(
      "div",
      { class: "field-row" },
      field("Member ID", f.insurance_member_id, { optional: true }),
      field("Group number", f.insurance_group_number, { optional: true }),
    ),
    h(
      "div",
      { class: "field-row" },
      field("Policy in the name of", f.insurance_policyholder_relationship),
      field("Policyholder's name", f.insurance_policyholder_name, { optional: true }),
    ),
    h("div", { class: "card-photos", id: "edit-card-photos" }, front.el, back.el),
    h("h4", { class: "section-title" }, "Emergency contact"),
    h(
      "div",
      { class: "field-row" },
      field("Name", f.emergency_contact_name, { optional: true }),
      field("Phone", f.emergency_contact_phone, { optional: true }),
    ),
    h("h4", { class: "section-title" }, "Visit"),
    field("Reason for first visit", f.reason_for_visit, { optional: true }),
    h("div", { class: "actions" }, cancel, h("span", { class: "spacer" }), save),
  );

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    const body = {};
    for (const [name, el] of Object.entries(f)) body[name] = orNull(el.value.trim());
    if (!body.first_name || !body.last_name || !body.date_of_birth) {
      msg.append(
        callout("error", "Please check the form", h("span", {}, "First name, last name and date of birth are needed.")),
      );
      return;
    }
    if (body.insurance_policyholder_relationship === "self") body.insurance_policyholder_name = null;
    const hasInsurance =
      body.insurance_provider || body.insurance_member_id || body.insurance_plan_name || body.insurance_group_number;
    if (!hasInsurance && !front.photo() && !back.photo()) body.insurance_policyholder_relationship = null;
    body.insurance_card_front = front.photo();
    body.insurance_card_back = back.photo();
    body.remove_insurance_card_front = front.removed();
    body.remove_insurance_card_back = back.removed();
    save.disabled = true;
    save.textContent = "Saving…";
    try {
      const record = await api.put(`/patients/${p.id}`, body);
      toast(`Saved ${record.mrn}`);
      onSaved(record);
    } catch (err) {
      msg.append(callout("error", "Could not save", h("span", {}, describeError(err))));
      msg.scrollIntoView({ block: "nearest" });
    } finally {
      save.disabled = false;
      save.textContent = "Save changes";
    }
  });
  return form;
}
