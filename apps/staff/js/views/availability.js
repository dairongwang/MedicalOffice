import { api } from "../api.js";
import { session } from "../auth.js";
import { button, callout, describeError, field, h, input, render, spinner, toast } from "../ui.js";
import { formatNumber, isEmail, roleLabel } from "../validation.js";
import { fromApi, toApi, weekEditor } from "./week-editor.js";

/**
 * "My availability" — the weekly hours a member of staff works (services/account/workers.py). Patients
 * book providers inside these hours and the office assigns medical assistants by them.
 *
 *   signed in with a staff account   GET/PUT /staff/me/availability (no need to identify again)
 *   everybody else                   staff number + work e-mail → POST /workers/availability/lookup,
 *                                    saved with PUT /workers/availability (both must match)
 *
 * Route: #/availability
 */
export function availabilityView() {
  const root = h("section", { class: "page", id: "availability" });
  if (session.isStaff) void renderSignedIn(root);
  else renderLookup(root);
  return root;
}

function header(subtitle) {
  return h(
    "header",
    { class: "page-header" },
    h(
      "div",
      {},
      h("h2", {}, "My availability"),
      h(
        "p",
        { class: "muted small" },
        subtitle ||
          "The days and hours you work. Patients can book you only inside them; new staff start with the " +
            "office's standard week (Monday–Friday, 9 AM – 5 PM).",
      ),
    ),
  );
}

function workerLine(w) {
  return h(
    "p",
    { class: "small", id: "availability-worker" },
    h("strong", {}, `${w.first_name} ${w.last_name}`),
    ` · ${roleLabel(w.role)} · `,
    h("span", { class: "mono" }, w.staff_number),
  );
}

/** The editor card: week + save. `save(rows)` returns the API's AvailabilityOut. */
function editorCard(out, save) {
  const msg = h("div", { id: "availability-msg" });
  const saveBtn = button("Saved", { class: "btn primary", id: "btn-availability-save", disabled: true });
  const editor = weekEditor(fromApi(out.availability), {
    onChange: () => {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save week";
    },
  });
  saveBtn.addEventListener("click", async () => {
    msg.replaceChildren();
    const v = editor.validate();
    if (!v.ok) return;
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      const updated = await save(toApi(editor.rows()));
      editor.setRows(fromApi(updated.availability));
      saveBtn.textContent = "Saved";
      toast("Your week was saved");
    } catch (e) {
      msg.append(callout("error", "Could not save", h("span", {}, describeError(e))));
      saveBtn.disabled = false;
      saveBtn.textContent = "Save week";
    }
  });
  return h(
    "section",
    { class: "card", id: "availability-card" },
    workerLine(out.worker),
    msg,
    editor.el,
    h("div", { class: "actions" }, h("span", { class: "spacer" }), saveBtn),
  );
}

async function renderSignedIn(root) {
  render(root, header(), spinner());
  let out;
  try {
    out = await api.get("/staff/me/availability");
  } catch (e) {
    render(root, header(), callout("error", "Could not load your week", h("span", {}, describeError(e))));
    return;
  }
  render(
    root,
    header(`Signed in as ${session.displayName}. The days and hours you work — patients book you only inside them.`),
    editorCard(out, (availability) => api.put("/staff/me/availability", { availability })),
  );
}

function renderLookup(root, preset = {}) {
  const msg = h("div", { id: "availability-lookup-msg" });
  const fNumber = input({
    name: "staff_number",
    required: true,
    maxlength: 16,
    placeholder: "W-000042",
    autocomplete: "off",
    value: preset.number || "",
    id: "availability-number",
  });
  const fEmail = input({
    name: "email",
    type: "email",
    required: true,
    autocomplete: "email",
    inputmode: "email",
    value: preset.email || "",
    id: "availability-email",
  });
  const submit = button("Show my week", { type: "submit", class: "btn primary", id: "btn-availability-lookup" });
  const form = h(
    "form",
    { class: "card form narrow", id: "availability-lookup", novalidate: true },
    h("h3", {}, "Who are you?"),
    h(
      "p",
      { class: "muted small" },
      "Your staff number and the work e-mail on your staff record. Have a staff account? ",
      h("a", { href: `#/login?next=${encodeURIComponent("#/availability")}`, id: "link-availability-login" }, "Sign in"),
      " instead.",
    ),
    msg,
    field("Staff number", fNumber),
    field("Work e-mail", fEmail),
    h("div", { class: "actions" }, h("span", { class: "spacer" }), submit),
    window.STAFF_CONFIG?.showDummyHint
      ? h("p", { class: "hint" }, "Development: W-000001 / dummy-doctor@example.com (dummy staff record).")
      : null,
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    const number = formatNumber("W", fNumber.value);
    const email = fEmail.value.trim();
    if (!/^W-\d{6}$/.test(number) || !isEmail(email)) {
      msg.append(
        callout("error", "Please check the form", h("span", {}, "Enter your staff number and your work e-mail.")),
      );
      return;
    }
    submit.disabled = true;
    submit.textContent = "Looking up…";
    try {
      const out = await api.post("/workers/availability/lookup", { staff_number: number, email }, { auth: false });
      render(
        root,
        header(),
        editorCard(out, (availability) =>
          api.put("/workers/availability", { staff_number: number, email, availability }, { auth: false }),
        ),
        h(
          "p",
          { class: "small" },
          button("Someone else", {
            class: "btn small-btn",
            id: "btn-availability-other",
            onclick: () => renderLookup(root),
          }),
        ),
      );
    } catch (err) {
      msg.append(callout("error", "Not found", h("span", {}, describeError(err))));
      submit.disabled = false;
      submit.textContent = "Show my week";
    }
  });
  render(root, header(), form);
}
