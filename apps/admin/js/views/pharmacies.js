import { api } from "../api.js";
import { pharmacyProblems, pharmacyReach, pharmacySource } from "../format.js";
import {
  badge,
  button,
  callout,
  describeError,
  field,
  formValues,
  h,
  input,
  orNull,
  render,
  spinner,
  toast,
} from "../ui.js";

/**
 * The pharmacy directory (GET/POST /pharmacies, PATCH /pharmacies/{id} — services/account/prescriptions.py):
 * where prescriptions can be e-mailed. Prescribers pick the patient's pharmacy from it on the chart; a
 * pharmacy a patient names at a visit that is not listed yet is added by the provider ("added at a visit")
 * for the office to review here. Inactive pharmacies stay on record but are no longer offered.
 *
 * Route: #/pharmacies  ·  #/pharmacies?inactive=1 (include the inactive ones)
 */
export function pharmaciesView(params) {
  const root = h("section", { class: "page wide", id: "pharmacies" });
  void renderList(root, { inactive: params.inactive === "1" });
  return root;
}

async function renderList(root, filters) {
  const showInactive = h("input", {
    type: "checkbox",
    id: "pharmacies-inactive",
    checked: filters.inactive,
    onchange: (e) => (location.hash = e.target.checked ? "#/pharmacies?inactive=1" : "#/pharmacies"),
  });
  const list = h("div", { id: "pharmacy-list" }, spinner());
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "Pharmacies"),
        h(
          "p",
          { class: "muted small" },
          "The directory prescribers choose from. A pharmacy with an e-mail address receives prescriptions as a " +
            "PDF attachment; without one the prescription is printed or given to the patient.",
        ),
      ),
      button("Refresh", { class: "btn", id: "btn-pharmacies-refresh", onclick: () => void load() }),
    ),
    h(
      "div",
      { class: "toolbar" },
      h("label", { class: "checkbox", for: "pharmacies-inactive" }, showInactive, h("span", {}, "Show inactive")),
    ),
    list,
    addForm(() => void load()),
  );
  await load();

  async function load() {
    render(list, spinner());
    let rows;
    try {
      rows = await api.get(`/pharmacies${filters.inactive ? "?include_inactive=true" : ""}`);
    } catch (e) {
      render(list, callout("error", "Could not load the pharmacies", h("span", {}, describeError(e))));
      return;
    }
    render(
      list,
      h(
        "p",
        { class: "muted small", id: "pharmacy-count" },
        `${rows.length} pharmac${rows.length === 1 ? "y" : "ies"}${filters.inactive ? " (inactive included)" : ""}`,
      ),
      rows.length
        ? h(
            "ul",
            { class: "rows" },
            rows.map((p) => pharmacyRow(p, () => void load())),
          )
        : h("p", { class: "empty", id: "pharmacy-empty" }, "No pharmacies in the directory yet — add the first below."),
    );
  }
}

function pharmacyRow(p, reload) {
  const editor = h("div", { class: "pharmacy-edit", id: `pharmacy-edit-${p.id}`, hidden: true });
  const source = pharmacySource(p);
  return h(
    "li",
    { class: `row pharmacy ${p.active ? "" : "inactive"}`, id: `pharmacy-${p.id}`, dataset: { id: String(p.id) } },
    h(
      "div",
      { class: "row-main" },
      h("strong", {}, p.name),
      p.active ? null : badge("inactive", "bad"),
      p.email ? badge("e-mail", "ok") : badge("no e-mail", "warn"),
      p.source === "provider" ? badge("added at a visit", "warn") : null,
    ),
    h(
      "div",
      { class: "small" },
      [p.address, p.phone && `phone ${p.phone}`, p.fax && `fax ${p.fax}`].filter(Boolean).join(" · ") || "—",
    ),
    h("div", { class: "muted small" }, pharmacyReach(p), source ? ` · ${source}` : ""),
    h(
      "div",
      { class: "actions" },
      h("span", { class: "spacer" }),
      button("Edit", {
        class: "btn small-btn",
        id: `btn-pharmacy-edit-${p.id}`,
        onclick: () => {
          if (!editor.hidden) {
            editor.hidden = true;
            return;
          }
          render(editor, editForm(p, reload, () => (editor.hidden = true)));
          editor.hidden = false;
        },
      }),
      button(p.active ? "Deactivate" : "Reactivate", {
        class: `btn small-btn ${p.active ? "danger" : ""}`,
        id: `btn-pharmacy-active-${p.id}`,
        title: p.active
          ? "No longer offered to prescribers (kept on record; prescriptions already sent stay as they are)"
          : "Offer it to prescribers again",
        onclick: async (e) => {
          e.target.disabled = true;
          try {
            await api.patch(`/pharmacies/${p.id}`, { active: !p.active });
            toast(p.active ? `${p.name} deactivated` : `${p.name} reactivated`);
            reload();
          } catch (err) {
            e.target.disabled = false;
            toast(describeError(err), { error: true });
          }
        },
      }),
    ),
    editor,
  );
}

/** The fields of a pharmacy (shared by "add" and "edit"). */
function pharmacyFields(prefix, p = {}) {
  return [
    field("Name *", input({ name: "name", required: true, maxlength: 200, value: p.name || "", id: `${prefix}-name` })),
    h(
      "div",
      { class: "field-row" },
      field(
        "E-mail (prescriptions are sent here)",
        input({ name: "email", type: "email", maxlength: 320, value: p.email || "", id: `${prefix}-email` }),
        { optional: true },
      ),
      field("Phone", input({ name: "phone", type: "tel", maxlength: 32, value: p.phone || "", id: `${prefix}-phone` }), {
        optional: true,
      }),
    ),
    h(
      "div",
      { class: "field-row" },
      field("Fax", input({ name: "fax", type: "tel", maxlength: 32, value: p.fax || "", id: `${prefix}-fax` }), {
        optional: true,
      }),
      field(
        "Address",
        input({ name: "address", maxlength: 300, value: p.address || "", id: `${prefix}-address` }),
        { optional: true },
      ),
    ),
  ];
}

function problemsCallout(problems) {
  return callout(
    "error",
    "Please check the form",
    h(
      "ul",
      {},
      problems.map((x) => h("li", {}, x)),
    ),
  );
}

function editForm(p, reload, close) {
  const msg = h("div", { class: "check-live" });
  const form = h(
    "form",
    { class: "form compact", id: `pharmacy-edit-form-${p.id}`, novalidate: true },
    msg,
    ...pharmacyFields(`pharmacy-${p.id}`, p),
    h(
      "div",
      { class: "actions" },
      button("Cancel", { class: "btn small-btn", onclick: close }),
      h("span", { class: "spacer" }),
      button("Save", { type: "submit", class: "btn small-btn primary", id: `btn-pharmacy-save-${p.id}` }),
    ),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    const v = formValues(form);
    const problems = pharmacyProblems(v);
    if (problems.length) return msg.append(problemsCallout(problems));
    try {
      await api.patch(`/pharmacies/${p.id}`, {
        name: v.name,
        email: orNull(v.email),
        phone: orNull(v.phone),
        fax: orNull(v.fax),
        address: orNull(v.address),
      });
      toast("Pharmacy saved");
      reload();
    } catch (err) {
      msg.append(callout("error", "Could not save", h("span", {}, describeError(err))));
    }
  });
  return form;
}

function addForm(reload) {
  const msg = h("div", { class: "check-live", id: "pharmacy-add-msg" });
  const form = h(
    "form",
    { class: "card form pharmacy-add", id: "pharmacy-add-form", novalidate: true },
    h("h3", {}, "Add a pharmacy"),
    msg,
    ...pharmacyFields("pharmacy-add"),
    h(
      "div",
      { class: "actions" },
      h("span", { class: "spacer" }),
      button("Add to directory", { type: "submit", class: "btn primary", id: "btn-pharmacy-add" }),
    ),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    const v = formValues(form);
    const problems = pharmacyProblems(v);
    if (problems.length) return msg.append(problemsCallout(problems));
    try {
      const row = await api.post("/pharmacies", {
        name: v.name,
        email: orNull(v.email),
        phone: orNull(v.phone),
        fax: orNull(v.fax),
        address: orNull(v.address),
      });
      toast(`${row.name} added`);
      form.reset();
      reload();
    } catch (err) {
      msg.append(callout("error", "Could not add", h("span", {}, describeError(err))));
    }
  });
  return form;
}
