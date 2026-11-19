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
  statusBadge,
} from "../ui.js";
import {
  LICENSED_ROLES,
  ROLES,
  ageCheck,
  formatNumber,
  isEmail,
  isPhone,
  licenseCheck,
  parseDate,
  roleLabel,
} from "../validation.js";

/**
 * Public: apply for a position at the office (services/account/hiring.py). Three checks gate a hire:
 *
 *   age         automatic — at least 18 (shown live while typing, run again by the server)
 *   licence     for licensed roles (doctor, nurse, physician assistant, technician): number format and
 *               expiry checked now, then a manager confirms it with the issuing board
 *   background  needs the candidate's consent (ticked here); a manager records the screening
 *
 * After submitting, the candidate gets the application number (A-000042) and can come back to
 * "Check my application" with it and their e-mail.
 *
 * Route: #/apply  ·  #/apply?status=1 (the status lookup)  ·  #/apply?number=A-000001&email=… (prefilled)
 */
export function applyView(params) {
  const root = h("section", { class: "page", id: "apply" });
  if (params.status || params.number) renderStatus(root, params);
  else renderForm(root);
  return root;
}

function header(title, subtitle, ...extra) {
  return h(
    "header",
    { class: "page-header" },
    h("div", {}, h("h2", {}, title), h("p", { class: "muted small" }, subtitle)),
    ...extra,
  );
}

const DOT = { passed: "ok", not_applicable: "ok", failed: "bad", invalid: "bad", in_progress: "warn", pending: "" };

function liveCheck(id, title) {
  const el = h("div", { class: "check", id });
  const set = (status, note) => {
    el.className = `check ${DOT[status] ?? ""}`;
    render(
      el,
      h("div", { class: "check-head" }, h("strong", {}, title), statusBadge(status === "invalid" ? "pending" : status)),
      h("p", { class: "small" }, note),
    );
  };
  return { el, set };
}

function renderForm(root) {
  const msg = h("div", { id: "apply-msg" });
  const fRole = select([["", "Choose…", { disabled: true }], ...ROLES], {
    name: "role",
    required: true,
    id: "apply-role",
  });
  fRole.value = "";
  const fDob = input({ name: "date_of_birth", type: "date", required: true, id: "apply-dob" });
  const fLicense = input({ name: "license_number", maxlength: 64, id: "apply-license", autocomplete: "off" });
  const fAuthority = input({
    name: "license_authority",
    maxlength: 120,
    id: "apply-authority",
    placeholder: "e.g. State Medical Board",
  });
  const fExpires = input({ name: "license_expires", type: "date", id: "apply-license-expires" });

  const ageLive = liveCheck("apply-age-check", "Age");
  const licenseLive = liveCheck("apply-license-check", "Professional licence");
  const licenseGroup = h(
    "div",
    { class: "group", id: "apply-license-group" },
    h("p", { class: "section-title" }, "Professional licence"),
    h("p", { class: "hint", id: "apply-license-hint" }),
    h(
      "div",
      { class: "field-row three" },
      field("Licence / registration no.", fLicense),
      field("Issuing board", fAuthority, { optional: true }),
      field("Expires", fExpires, { optional: true }),
    ),
  );

  const refresh = () => {
    const dob = fDob.value;
    if (!dob) ageLive.set("pending", `You must be at least 18 to be hired. Enter your date of birth.`);
    else {
      const a = ageCheck(dob);
      ageLive.set(a.status, a.note);
    }
    const role = fRole.value;
    const licensed = LICENSED_ROLES.includes(role);
    licenseGroup.classList.toggle("dimmed", !licensed);
    for (const el of [fLicense, fAuthority, fExpires]) el.disabled = !licensed;
    licenseGroup.querySelector("#apply-license-hint").textContent = !role
      ? "Choose the position first — doctors, nurses, physician assistants and technicians need a licence."
      : licensed
        ? `A ${roleLabel(role).toLowerCase()} needs a valid licence. A manager confirms it with the issuing board.`
        : "This position does not need a professional licence.";
    if (!role) licenseLive.set("pending", "Depends on the position.");
    else {
      const l = licenseCheck(role, fLicense.value, fAuthority.value, fExpires.value);
      licenseLive.set(l.status, l.note);
    }
  };
  for (const el of [fRole, fDob, fLicense, fAuthority, fExpires]) {
    el.addEventListener("input", refresh);
    el.addEventListener("change", refresh);
  }

  const submit = button("Submit application", { type: "submit", class: "btn primary", id: "btn-apply-submit" });
  const form = h(
    "form",
    { class: "card form", id: "apply-form", novalidate: true },
    h("p", { class: "section-title" }, "About you"),
    h(
      "div",
      { class: "field-row" },
      field("First name *", input({ name: "first_name", required: true, maxlength: 100, id: "apply-first" })),
      field("Last name *", input({ name: "last_name", required: true, maxlength: 100, id: "apply-last" })),
    ),
    h(
      "div",
      { class: "field-row" },
      field(
        "E-mail *",
        input({ name: "email", type: "email", required: true, autocomplete: "email", id: "apply-email" }),
      ),
      field("Phone", input({ name: "phone", type: "tel", autocomplete: "tel", id: "apply-phone" }), {
        optional: true,
      }),
    ),
    field("Date of birth *", fDob),
    h("p", { class: "section-title" }, "The position"),
    h(
      "div",
      { class: "field-row" },
      field("Position *", fRole),
      field("Specialty / department", input({ name: "specialty", maxlength: 120, id: "apply-specialty" }), {
        optional: true,
      }),
    ),
    h(
      "div",
      { class: "field-row three" },
      field(
        "Employment",
        select(
          [
            ["", "No preference"],
            ["full_time", "Full-time"],
            ["part_time", "Part-time"],
            ["locum", "Locum"],
          ],
          { name: "employment_type", id: "apply-employment" },
        ),
      ),
      field("Available from", input({ name: "available_from", type: "date", id: "apply-available" }), {
        optional: true,
      }),
      field(
        "Years of experience",
        input({ name: "years_experience", type: "number", min: 0, max: 60, id: "apply-years" }),
        { optional: true },
      ),
    ),
    licenseGroup,
    field(
      "Certifications",
      input({ name: "certifications", maxlength: 2000, placeholder: "BLS, ACLS, …", id: "apply-certs" }),
      { optional: true },
    ),
    field(
      "A note to the office",
      h("textarea", { class: "input", name: "cover_note", maxlength: 4000, rows: 4, id: "apply-note" }),
      { optional: true },
    ),
    h("p", { class: "section-title" }, "Emergency contact"),
    h(
      "div",
      { class: "field-row" },
      field("Name", input({ name: "emergency_contact_name", maxlength: 120, id: "apply-ec-name" }), {
        optional: true,
      }),
      field("Phone", input({ name: "emergency_contact_phone", type: "tel", id: "apply-ec-phone" }), {
        optional: true,
      }),
    ),
    h("p", { class: "section-title" }, "Pre-hire checks"),
    h("div", { class: "group", id: "apply-checks" }, ageLive.el, licenseLive.el),
    h(
      "label",
      { class: "checkbox consent", for: "apply-background-consent" },
      h("input", { type: "checkbox", name: "background_consent", id: "apply-background-consent" }),
      h(
        "span",
        {},
        "I agree to a background screening (identity, employment history, relevant records) before I can be hired.",
      ),
    ),
    h(
      "label",
      { class: "checkbox consent", for: "apply-consent" },
      h("input", { type: "checkbox", name: "consent", id: "apply-consent" }),
      h("span", {}, "The office may keep the details of this application to process it."),
    ),
    msg,
    h(
      "div",
      { class: "actions" },
      h("a", { href: "#/apply?status=1", class: "small", id: "link-apply-status" }, "Check an application"),
      h("span", { class: "spacer" }),
      submit,
    ),
  );

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    const v = formValues(form);
    const problems = [];
    if (!v.first_name || !v.last_name) problems.push("Your first and last name.");
    if (!isEmail(v.email)) problems.push("A valid e-mail address.");
    if (!isPhone(v.phone)) problems.push("The phone number must have 7–15 digits.");
    if (!isPhone(v.emergency_contact_phone)) problems.push("The emergency contact's phone must have 7–15 digits.");
    if (!parseDate(v.date_of_birth)) problems.push("Your date of birth.");
    else if (ageCheck(v.date_of_birth).status === "invalid") problems.push(ageCheck(v.date_of_birth).note);
    if (!v.role) problems.push("The position you apply for.");
    if (v.years_experience && !(Number(v.years_experience) >= 0 && Number(v.years_experience) <= 60))
      problems.push("Years of experience: 0–60.");
    if (!v.background_consent) problems.push("Please agree to the background screening.");
    if (!v.consent) problems.push("Please agree that the office may keep your application.");
    if (problems.length) {
      msg.append(
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
      msg.scrollIntoView({ block: "nearest" });
      return;
    }
    const licensed = LICENSED_ROLES.includes(v.role);
    submit.disabled = true;
    submit.textContent = "Submitting…";
    try {
      const receipt = await api.post(
        "/applications",
        {
          first_name: v.first_name,
          last_name: v.last_name,
          email: v.email,
          phone: orNull(v.phone),
          date_of_birth: v.date_of_birth,
          role: v.role,
          specialty: orNull(v.specialty),
          employment_type: orNull(v.employment_type),
          available_from: orNull(v.available_from),
          years_experience: v.years_experience === "" ? null : Number(v.years_experience),
          license_number: licensed ? orNull(v.license_number) : null,
          license_authority: licensed ? orNull(v.license_authority) : null,
          license_expires: licensed ? orNull(v.license_expires) : null,
          certifications: orNull(v.certifications),
          cover_note: orNull(v.cover_note),
          emergency_contact_name: orNull(v.emergency_contact_name),
          emergency_contact_phone: orNull(v.emergency_contact_phone),
          background_consent: v.background_consent,
          consent: v.consent,
        },
        { auth: false },
      );
      renderReceipt(root, receipt, v.email);
    } catch (err) {
      msg.append(callout("error", "Could not submit", h("span", {}, describeError(err))));
      submit.disabled = false;
      submit.textContent = "Submit application";
    }
  });

  render(
    root,
    header(
      "Apply to work with us",
      "Doctors, nurses, physician assistants, medical assistants, technicians and front-desk staff. " +
        "Your age and licence are checked as you type; a manager then verifies your credentials.",
    ),
    form,
  );
  refresh();
}

function receiptChecks(r) {
  return h(
    "div",
    { class: "group", id: "apply-receipt-checks" },
    h(
      "div",
      { class: `check ${DOT[r.age_check] ?? ""}` },
      h("div", { class: "check-head" }, h("strong", {}, "Age"), statusBadge(r.age_check)),
      r.age_note ? h("p", { class: "small" }, r.age_note) : null,
    ),
    h(
      "div",
      { class: `check ${DOT[r.license_check] ?? ""}` },
      h("div", { class: "check-head" }, h("strong", {}, "Professional licence"), statusBadge(r.license_check)),
      r.license_note ? h("p", { class: "small" }, r.license_note) : null,
    ),
    h(
      "div",
      { class: `check ${DOT[r.background_check] ?? ""}` },
      h("div", { class: "check-head" }, h("strong", {}, "Background screening"), statusBadge(r.background_check)),
      h(
        "p",
        { class: "small" },
        r.background_check === "passed"
          ? "Clear."
          : r.background_check === "failed"
            ? "Flagged by the screening."
            : "Runs once a manager has looked at your application.",
      ),
    ),
  );
}

function renderReceipt(root, r, email) {
  const blocked = r.age_check === "failed" || r.license_check === "failed";
  render(
    root,
    header("Application received", "Keep your application number — you need it to check where things stand."),
    h(
      "section",
      { class: "card", id: "apply-receipt" },
      callout(
        blocked ? "warn" : "ok",
        `Application ${r.number}`,
        h("span", { id: "apply-receipt-message" }, r.message),
      ),
      receiptChecks(r),
      h(
        "div",
        { class: "actions" },
        h(
          "a",
          {
            class: "btn",
            href: `#/apply?number=${encodeURIComponent(r.number)}&email=${encodeURIComponent(email)}`,
            id: "link-apply-check",
          },
          "Check its status later",
        ),
        h("span", { class: "spacer" }),
        button("Submit another application", {
          class: "btn",
          id: "btn-apply-another",
          onclick: () => renderForm(root),
        }),
      ),
    ),
  );
  window.scrollTo({ top: 0 });
}

function renderStatus(root, params) {
  const msg = h("div", { id: "apply-status-msg" });
  const result = h("div", { id: "apply-status-result" });
  const fNumber = input({
    name: "number",
    required: true,
    maxlength: 16,
    placeholder: "A-000042",
    value: params.number || "",
    id: "apply-status-number",
  });
  const fEmail = input({
    name: "email",
    type: "email",
    required: true,
    autocomplete: "email",
    value: params.email || "",
    id: "apply-status-email",
  });
  const submit = button("Check", { type: "submit", class: "btn primary", id: "btn-apply-status" });
  const form = h(
    "form",
    { class: "card form narrow", id: "apply-status-form", novalidate: true },
    h("h3", {}, "Check my application"),
    h("p", { class: "muted small" }, "Your application number and the e-mail you applied with."),
    msg,
    field("Application number", fNumber),
    field("E-mail", fEmail),
    h(
      "div",
      { class: "actions" },
      h("a", { href: "#/apply", class: "small", id: "link-apply-form" }, "← New application"),
      h("span", { class: "spacer" }),
      submit,
    ),
  );
  const lookup = async () => {
    msg.replaceChildren();
    result.replaceChildren();
    const number = formatNumber("A", fNumber.value);
    const email = fEmail.value.trim();
    if (!/^A-\d{6}$/.test(number) || !isEmail(email)) {
      msg.append(
        callout("error", "Please check the form", h("span", {}, "Enter the application number and your e-mail.")),
      );
      return;
    }
    submit.disabled = true;
    try {
      const s = await api.post("/applications/status", { number, email }, { auth: false });
      render(
        result,
        h(
          "section",
          { class: "card", id: "apply-status-card" },
          h(
            "div",
            { class: "card-head" },
            h("h3", {}, `${s.number} · ${roleLabel(s.role)}`),
            statusBadge(s.status),
          ),
          h("p", { id: "apply-status-message" }, s.message),
          receiptChecks({
            age_check: s.age_check,
            license_check: s.license_check,
            background_check: s.background_check,
          }),
          record([
            ["Submitted", formatDate(s.submitted_at)],
            ["Last update", formatDate(s.updated_at)],
            ["Staff number", s.staff_number],
          ]),
          s.staff_number
            ? h(
                "p",
                { class: "small" },
                "You are on the roster — ",
                h(
                  "a",
                  {
                    href: `#/create-account?number=${encodeURIComponent(s.staff_number)}`,
                    id: "link-apply-create-account",
                  },
                  "create your staff account",
                ),
                " with your staff number and work e-mail.",
              )
            : null,
        ),
      );
    } catch (err) {
      msg.append(callout("error", "Not found", h("span", {}, describeError(err))));
    } finally {
      submit.disabled = false;
    }
  };
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    void lookup();
  });
  render(root, header("Your application", "Where your job application stands."), form, result);
  if (params.number && params.email) void lookup();
}
