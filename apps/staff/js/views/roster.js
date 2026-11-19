import { api } from "../api.js";
import {
  button,
  callout,
  describeError,
  field,
  formatDate,
  formValues,
  h,
  input,
  orNull,
  record,
  render,
  select,
  spinner,
  toast,
} from "../ui.js";
import { ROLES, isEmail, isPhone, roleLabel } from "../validation.js";
import { fromApi, toApi, weekEditor } from "./week-editor.js";

/**
 * Managers: the staff roster — search, one worker's record and week (editable), onboarding
 * link, and "add existing staff" for people who were already employed before this system.
 * Route: #/roster  ·  #/roster/<id>  ·  #/roster/new
 */
export function rosterView(params) {
  const root = h("section", { class: "page wide" });
  if (params.id === "new") renderAdd(root);
  else if (params.id) void renderDetail(root, Number(params.id));
  else void renderList(root, params.q || "");
  return root;
}

async function renderList(root, q) {
  const search = input({
    type: "search",
    placeholder: "Search name, e-mail, role or W-000042",
    value: q,
    id: "roster-search",
  });
  search.addEventListener(
    "change",
    () =>
      (location.hash = `#/roster${search.value.trim() ? "?q=" + encodeURIComponent(search.value.trim()) : ""}`),
  );
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "Staff roster"),
        h(
          "p",
          { class: "muted small" },
          "Everyone who works for the office, with their weekly availability.",
        ),
      ),
      button("+ Add existing staff", {
        class: "btn",
        id: "btn-roster-add",
        onclick: () => (location.hash = "#/roster/new"),
      }),
    ),
    h("div", { class: "toolbar" }, search),
  );
  const list = h("div", { id: "roster-list" }, spinner());
  root.append(list);
  try {
    const page = await api.get(`/workers?q=${encodeURIComponent(q)}&limit=200`);
    render(
      list,
      h("p", { class: "muted small" }, `${page.total} staff member${page.total === 1 ? "" : "s"}`),
    );
    if (!page.items.length) return list.append(h("p", { class: "empty" }, "Nobody on the roster yet."));
    list.append(
      h(
        "ul",
        { class: "rows" },
        page.items.map((w) =>
          h(
            "li",
            {},
            h(
              "a",
              { class: "row", href: `#/roster/${w.id}` },
              h(
                "div",
                { class: "row-main" },
                h("strong", {}, `${w.first_name} ${w.last_name}`),
                h("span", { class: "muted small" }, ` ${roleLabel(w.role)}`),
              ),
              h("div", { class: "row-meta" }, h("span", { class: "mono small" }, w.staff_number)),
              h("div", { class: "muted small" }, `Since ${formatDate(w.created_at)}`),
            ),
          ),
        ),
      ),
    );
  } catch (e) {
    render(list, callout("error", "Could not load the roster", h("span", {}, describeError(e))));
  }
}

async function renderDetail(root, id) {
  render(root, spinner());
  let w;
  try {
    w = await api.get(`/workers/${id}`);
  } catch (e) {
    render(
      root,
      callout("error", "Could not load", h("span", {}, describeError(e))),
      button("← Back", { onclick: () => history.back() }),
    );
    return;
  }
  const msg = h("div", { id: "roster-msg" });
  const saveBtn = button("Saved", { class: "btn primary", id: "btn-roster-save-week", disabled: true });
  const editor = weekEditor(fromApi(w.availability), {
    onChange: () => {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save week";
    },
  });
  saveBtn.addEventListener("click", async () => {
    msg.replaceChildren();
    if (!editor.validate().ok) return;
    saveBtn.disabled = true;
    try {
      const updated = await api.put(`/workers/${id}/availability`, { availability: toApi(editor.rows()) });
      editor.setRows(fromApi(updated.availability));
      saveBtn.textContent = "Saved";
      toast("Week saved");
    } catch (e) {
      msg.append(callout("error", "Could not save", h("span", {}, describeError(e))));
      saveBtn.disabled = false;
    }
  });
  const ob = w.onboarding;
  const onboardingBtn = button(
    ob?.completed_at ? "Onboarding completed ✓" : ob ? "Resend onboarding link" : "Start onboarding",
    {
      class: `btn ${ob ? "" : "primary"}`,
      id: "btn-roster-onboarding",
      disabled: !!ob?.completed_at,
      title: ob?.completed_at
        ? `Completed ${formatDate(ob.completed_at)}`
        : ob
          ? "Send the portal link again by e-mail / text"
          : "Create the onboarding checklist and send the portal link by e-mail / text",
      onclick: async () => {
        onboardingBtn.disabled = true;
        try {
          const o = await api.post(`/workers/${id}/onboarding`);
          const inv = o.invites?.[0];
          toast(
            inv?.channels?.length
              ? `Onboarding link sent via ${inv.channels.join(" + ")}`
              : "Onboarding started — copy the link from the onboarding page",
          );
          if (inv) sessionStorage.setItem("staff.justSentInvite", String(inv.id));
          location.hash = `#/onboarding/${o.id}`;
        } catch (e) {
          msg.replaceChildren(callout("error", "Onboarding", h("span", {}, describeError(e))));
          onboardingBtn.disabled = false;
        }
      },
    },
  );
  const openOnboarding = ob
    ? h(
        "a",
        { class: "btn", href: `#/onboarding/${ob.id}`, id: "link-roster-onboarding" },
        ob.completed_at ? "View onboarding" : `Open onboarding (${ob.percent}%)`,
      )
    : null;
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("a", { href: "#/roster", class: "back" }, "← Roster"),
        h("h2", {}, `${w.first_name} ${w.last_name}`),
        h(
          "p",
          { class: "muted small" },
          `${roleLabel(w.role)}${w.specialty ? " · " + w.specialty : ""} · `,
          h("span", { class: "mono" }, w.staff_number),
          w.active ? "" : " · inactive",
        ),
      ),
    ),
    msg,
    h(
      "div",
      { class: "two-col" },
      h(
        "section",
        { class: "card" },
        h("h3", {}, "Record"),
        record([
          ["Work e-mail", w.email],
          ["Phone", w.phone],
          ["Licence no.", w.license_number],
          ["Employment", w.employment_type],
          ["Start date", formatDate(w.start_date)],
          [
            "Emergency contact",
            w.emergency_contact_name
              ? `${w.emergency_contact_name}${w.emergency_contact_phone ? " · " + w.emergency_contact_phone : ""}`
              : "",
          ],
          ["Notes", w.notes, true],
          ["On roster since", formatDate(w.created_at)],
        ]),
        ob
          ? h(
              "div",
              { class: "progress-row" },
              h(
                "div",
                { class: "progress" },
                h("div", { class: "progress-bar", style: `width:${ob.percent}%` }),
              ),
              h(
                "span",
                { class: "small" },
                `Onboarding ${ob.done}/${ob.total}${ob.completed_at ? " · completed " + formatDate(ob.completed_at) : ""}`,
              ),
            )
          : null,
        h("div", { class: "actions" }, openOnboarding, h("span", { class: "spacer" }), onboardingBtn),
      ),
      h(
        "section",
        { class: "card" },
        h("h3", {}, "Weekly availability"),
        editor.el,
        h("div", { class: "actions" }, h("span", { class: "spacer" }), saveBtn),
      ),
    ),
  );
}

/** Add somebody who already works here (no application / hiring process). */
function renderAdd(root) {
  const msg = h("div", { id: "add-msg" });
  const fRole = select([["", "Choose…", { disabled: true }], ...ROLES], {
    name: "role",
    required: true,
    id: "add-role",
  });
  fRole.value = "";
  const form = h(
    "form",
    { class: "card form", id: "roster-add-form", novalidate: true },
    h(
      "div",
      { class: "field-row" },
      field("First name *", input({ name: "first_name", required: true, maxlength: 100, id: "add-first" })),
      field("Last name *", input({ name: "last_name", required: true, maxlength: 100, id: "add-last" })),
    ),
    h(
      "div",
      { class: "field-row" },
      field("Work e-mail *", input({ name: "email", type: "email", required: true, id: "add-email" })),
      field("Phone", input({ name: "phone", type: "tel", id: "add-phone" }), { optional: true }),
    ),
    h(
      "div",
      { class: "field-row" },
      field("Role *", fRole),
      field("Specialty / department", input({ name: "specialty", maxlength: 120, id: "add-specialty" }), {
        optional: true,
      }),
    ),
    h(
      "div",
      { class: "field-row three" },
      field("Licence no.", input({ name: "license_number", maxlength: 64, id: "add-license" }), {
        optional: true,
      }),
      field(
        "Employment",
        select(
          [
            ["", "Not specified"],
            ["full_time", "Full-time"],
            ["part_time", "Part-time"],
            ["locum", "Locum"],
          ],
          { name: "employment_type", id: "add-employment" },
        ),
      ),
      field("Start date", input({ name: "start_date", type: "date", id: "add-start" }), { optional: true }),
    ),
    h(
      "div",
      { class: "field-row" },
      field(
        "Emergency contact",
        input({ name: "emergency_contact_name", maxlength: 120, id: "add-ec-name" }),
        { optional: true },
      ),
      field(
        "Emergency contact phone",
        input({ name: "emergency_contact_phone", type: "tel", id: "add-ec-phone" }),
        { optional: true },
      ),
    ),
    field("Notes", input({ name: "notes", maxlength: 2000, id: "add-notes" }), { optional: true }),
    h(
      "p",
      { class: "hint" },
      "New staff get the standard week (Mon–Fri 9–5). If the e-mail, licence or name + phone already exist on the roster, nothing is duplicated.",
    ),
    h(
      "div",
      { class: "actions" },
      button("Cancel", { onclick: () => (location.hash = "#/roster") }),
      h("span", { class: "spacer" }),
      button("Add to roster", { type: "submit", class: "btn primary", id: "btn-add-save" }),
    ),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    const v = formValues(form);
    const problems = [];
    if (!v.first_name || !v.last_name) problems.push("First and last name are required.");
    if (!isEmail(v.email)) problems.push("A valid work e-mail is required.");
    if (!isPhone(v.phone)) problems.push("The phone number must have 7–15 digits.");
    if (!v.role) problems.push("Please choose a role.");
    if (problems.length)
      return msg.append(
        callout(
          "error",
          "Please check the form",
          h(
            "ul",
            {},
            problems.map((p) => h("li", {}, p)),
          ),
        ),
      );
    try {
      const res = await api.post("/workers", {
        first_name: v.first_name,
        last_name: v.last_name,
        email: v.email,
        phone: orNull(v.phone),
        license_number: orNull(v.license_number),
        role: v.role,
        specialty: orNull(v.specialty),
        start_date: orNull(v.start_date),
        employment_type: orNull(v.employment_type),
        emergency_contact_name: orNull(v.emergency_contact_name),
        emergency_contact_phone: orNull(v.emergency_contact_phone),
        notes: orNull(v.notes),
        consent: true,
      });
      toast(
        res.status === "new"
          ? `${res.worker.first_name} added — ${res.worker.staff_number}`
          : `${res.worker.first_name} is already on the roster (${res.worker.staff_number})`,
      );
      location.hash = `#/roster/${res.worker.id}`;
    } catch (err) {
      msg.append(callout("error", "Could not add", h("span", {}, describeError(err))));
    }
  });
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("a", { href: "#/roster", class: "back" }, "← Roster"),
        h("h2", {}, "Add existing staff"),
        h(
          "p",
          { class: "muted small" },
          "For people already employed. New hires should come through Applications → Hire.",
        ),
      ),
    ),
    msg,
    form,
  );
}
