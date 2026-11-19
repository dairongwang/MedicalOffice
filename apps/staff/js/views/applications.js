import { api } from "../api.js";
import {
  button,
  callout,
  describeError,
  field,
  formatDate,
  formatDateTime,
  h,
  input,
  record,
  render,
  select,
  spinner,
  statusBadge,
  toast,
} from "../ui.js";
import { roleLabel } from "../validation.js";

/**
 * Managers: the queue of job applications and, per application, the three pre-hire checks
 * (age — automatic · licence — confirm with the board · background screening), then hire / reject.
 * Route: #/applications  or  #/applications/<id>
 */
export function applicationsView(params) {
  const root = h("section", { class: "page wide" });
  if (params.id) void renderDetail(root, Number(params.id));
  else void renderList(root, params);
  return root;
}

const FILTERS = [
  ["open", "Open"],
  ["screening", "Screening"],
  ["submitted", "Blocked"],
  ["hired", "Hired"],
  ["rejected", "Rejected"],
  ["", "All"],
];

async function renderList(root, params) {
  const status = params.status ?? "open";
  const q = params.q ?? "";
  const search = input({
    type: "search",
    placeholder: "Search name, e-mail, role or A-000042",
    value: q,
    id: "app-search",
  });
  search.addEventListener("change", () => navigate({ status, q: search.value.trim() }));
  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "Job applications"),
        h(
          "p",
          { class: "muted small" },
          "Validate age and licences, record the background screening, then hire or reject.",
        ),
      ),
    ),
    h(
      "div",
      { class: "toolbar" },
      h(
        "div",
        { class: "chips", role: "tablist" },
        FILTERS.map(([value, label]) =>
          button(label, {
            class: `chip ${value === status ? "active" : ""}`,
            onclick: () => navigate({ status: value, q }),
            dataset: { filter: value },
          }),
        ),
      ),
      search,
    ),
  );
  const list = h("div", { id: "app-list" }, spinner());
  root.append(list);
  try {
    const qs = new URLSearchParams();
    if (status) qs.set("status", status);
    if (q) qs.set("q", q);
    const page = await api.get(`/applications?${qs}`);
    const counts = page.counts || {};
    root.querySelectorAll(".chip").forEach((chip) => {
      const f = chip.dataset.filter;
      const n =
        f === ""
          ? Object.values(counts).reduce((a, b) => a + b, 0)
          : f === "open"
            ? (counts.submitted || 0) + (counts.screening || 0)
            : counts[f] || 0;
      chip.append(h("span", { class: "count" }, String(n)));
    });
    list.replaceChildren();
    if (!page.items.length) {
      list.append(h("p", { class: "empty" }, "No applications here."));
      return;
    }
    list.append(
      h(
        "ul",
        { class: "rows" },
        page.items.map((a) =>
          h(
            "li",
            {},
            h(
              "a",
              { class: "row", href: `#/applications/${a.id}` },
              h(
                "div",
                { class: "row-main" },
                h("strong", {}, `${a.first_name} ${a.last_name}`),
                h(
                  "span",
                  { class: "muted small" },
                  ` ${roleLabel(a.role)}${a.specialty ? " · " + a.specialty : ""}`,
                ),
              ),
              h(
                "div",
                { class: "row-meta" },
                h("span", { class: "mono small" }, a.number),
                statusBadge(a.status),
                h(
                  "span",
                  { class: "checks small" },
                  check("Age", a.age_check),
                  check("Licence", a.license_check),
                  check("Background", a.background_check),
                ),
              ),
              h("div", { class: "muted small" }, `Applied ${formatDate(a.created_at)}`),
            ),
          ),
        ),
      ),
    );
  } catch (e) {
    render(list, callout("error", "Could not load applications", h("span", {}, describeError(e))));
  }
}

function navigate({ status, q }) {
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  if (q) qs.set("q", q);
  location.hash = `#/applications${qs.toString() ? "?" + qs : ""}`;
}

const DOT = { passed: "ok", not_applicable: "ok", failed: "bad", in_progress: "warn", pending: "" };
function check(label, status) {
  return h("span", { class: `check-dot ${DOT[status] ?? ""}`, title: `${label}: ${status}` }, h("i"), label);
}

async function renderDetail(root, id) {
  render(root, spinner());
  let app;
  try {
    app = await api.get(`/applications/${id}`);
  } catch (e) {
    render(
      root,
      callout("error", "Could not load the application", h("span", {}, describeError(e))),
      button("← Back", { onclick: () => history.back() }),
    );
    return;
  }
  const closed = ["hired", "rejected", "withdrawn"].includes(app.status);
  const msg = h("div", { id: "app-msg" });

  const refresh = (updated) => renderDetail(root, updated?.id ?? id);

  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("a", { href: "#/applications", class: "back" }, "← Applications"),
        h("h2", {}, `${app.first_name} ${app.last_name}`),
        h(
          "p",
          { class: "muted small" },
          h("span", { class: "mono" }, app.number),
          ` · ${roleLabel(app.role)}${app.specialty ? " · " + app.specialty : ""} · applied ${formatDate(app.created_at)} · `,
          statusBadge(app.status),
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
        h("h3", {}, "Application"),
        record([
          ["E-mail", app.email],
          ["Phone", app.phone],
          ["Date of birth", `${formatDate(app.date_of_birth)} (age ${app.age})`],
          ["Employment", app.employment_type],
          ["Available from", formatDate(app.available_from)],
          ["Experience", app.years_experience === null ? "" : `${app.years_experience} years`],
          [
            "Licence",
            app.license_number
              ? `${app.license_number}${app.license_authority ? " · " + app.license_authority : ""}${app.license_expires ? " · expires " + formatDate(app.license_expires) : ""}`
              : "",
          ],
          ["Certifications", app.certifications],
          [
            "Emergency contact",
            app.emergency_contact_name
              ? `${app.emergency_contact_name}${app.emergency_contact_phone ? " · " + app.emergency_contact_phone : ""}`
              : "",
          ],
          ["Background consent", app.background_consent ? "Given" : "Not given"],
          ["Note", app.cover_note, true],
        ]),
        app.status === "hired"
          ? callout(
              "ok",
              "Hired — onboarding started",
              h(
                "span",
                {},
                `Staff number ${app.staff_number}. `,
                h("a", { href: `#/onboarding/${app.onboarding_id}` }, "Open onboarding →"),
              ),
            )
          : null,
        app.status === "hired" && app.last_invite
          ? callout(
              app.last_invite.fake_delivery ? "warn" : "info",
              app.last_invite.fake_delivery
                ? "Onboarding link — where to find it (fake delivery)"
                : "Onboarding link sent",
              h("span", { id: "hire-invite-hint" }, app.last_invite.where_to_find),
              h(
                "span",
                { class: "small" },
                `Valid until ${formatDateTime(app.last_invite.expires_at)} · `,
                h("span", { class: "mono small" }, app.last_invite.link),
              ),
            )
          : null,
        app.status === "rejected"
          ? callout(
              "error",
              "Rejected",
              h(
                "span",
                {},
                app.decision_note || "",
                app.decided_by ? ` — ${app.decided_by}, ${formatDateTime(app.decided_at)}` : "",
              ),
            )
          : null,
      ),
      h(
        "section",
        { class: "card checks-card", id: "app-checks" },
        h("h3", {}, "Pre-hire checks"),
        checkCard(
          "Age validation",
          app.checks.age,
          null,
          "Automatic, from the date of birth: the applicant must be at least 18.",
        ),
        checkCard(
          "Licence / certificate validation",
          app.checks.license,
          closed || app.checks.license.status === "not_applicable" ? null : licenseForm(app, msg, refresh),
          "Format and expiry were checked automatically. Confirm the number with the issuing board, then record the outcome.",
        ),
        checkCard(
          "Background screening",
          app.checks.background,
          closed ? null : backgroundForm(app, msg, refresh),
          "Record what the screening provider reports (reference number, outcome).",
        ),
        closed ? null : decisionForm(app, msg, refresh),
      ),
    ),
  );
}

function checkCard(title, result, form, hint) {
  return h(
    "div",
    { class: `check ${DOT[result.status] ?? ""}` },
    h("div", { class: "check-head" }, h("strong", {}, title), statusBadge(result.status)),
    h("p", { class: "hint" }, hint),
    result.note ? h("p", { class: "small" }, result.note) : null,
    result.reference
      ? h("p", { class: "small" }, "Reference: ", h("span", { class: "mono" }, result.reference))
      : null,
    result.checked_by
      ? h(
          "p",
          { class: "muted small" },
          `Recorded by ${result.checked_by} · ${formatDateTime(result.checked_at)}`,
        )
      : null,
    form,
  );
}

function licenseForm(app, msg, refresh) {
  const fStatus = select(
    [
      ["passed", "Confirmed valid with the board"],
      ["failed", "Invalid / could not be confirmed"],
      ["in_progress", "Still checking"],
    ],
    {
      value: app.checks.license.status === "pending" ? "in_progress" : app.checks.license.status,
      id: "lic-status",
    },
  );
  const fNote = input({
    placeholder: "e.g. verified on the board register on …",
    maxlength: 500,
    id: "lic-note",
  });
  return h(
    "form",
    {
      class: "inline-form",
      id: "license-form",
      novalidate: true,
      onsubmit: (e) =>
        submitCheck(e, app, "license", { status: fStatus.value, note: fNote.value }, msg, refresh),
    },
    field("Outcome", fStatus),
    field("Note", fNote, { optional: true }),
    h(
      "div",
      { class: "actions" },
      button("Record licence check", { type: "submit", class: "btn", id: "btn-license-save" }),
    ),
  );
}

function backgroundForm(app, msg, refresh) {
  const fStatus = select(
    [
      ["in_progress", "Screening requested / in progress"],
      ["passed", "Clear"],
      ["failed", "Flagged — do not hire"],
    ],
    {
      value: app.checks.background.status === "pending" ? "in_progress" : app.checks.background.status,
      id: "bg-status",
    },
  );
  const fRef = input({
    placeholder: "Provider reference",
    maxlength: 120,
    value: app.checks.background.reference || "",
    id: "bg-ref",
  });
  const fNote = input({ placeholder: "Note", maxlength: 500, id: "bg-note" });
  return h(
    "form",
    {
      class: "inline-form",
      id: "background-form",
      novalidate: true,
      onsubmit: (e) =>
        submitCheck(
          e,
          app,
          "background",
          { status: fStatus.value, reference: fRef.value || null, note: fNote.value || null },
          msg,
          refresh,
        ),
    },
    field("Outcome", fStatus),
    h(
      "div",
      { class: "field-row" },
      field("Reference", fRef, { optional: true }),
      field("Note", fNote, { optional: true }),
    ),
    h(
      "div",
      { class: "actions" },
      button("Record screening", { type: "submit", class: "btn", id: "btn-background-save" }),
    ),
  );
}

async function submitCheck(e, app, check, body, msg, refresh) {
  e.preventDefault();
  msg.replaceChildren();
  try {
    const updated = await api.put(`/applications/${app.id}/checks/${check}`, body);
    toast(`${check === "license" ? "Licence check" : "Background screening"} recorded`);
    refresh(updated);
  } catch (err) {
    msg.append(callout("error", "Could not save", h("span", {}, describeError(err))));
  }
}

function decisionForm(app, msg, refresh) {
  const fNote = input({
    placeholder: "Decision note (shown to managers only)",
    maxlength: 1000,
    id: "decision-note",
  });
  const fStart = input({ type: "date", value: app.available_from || "", id: "decision-start" });
  const hireBtn = button("Hire & start onboarding", {
    class: "btn primary",
    id: "btn-hire",
    disabled: !app.can_hire,
  });
  const rejectBtn = button("Reject", { class: "btn danger", id: "btn-reject" });
  hireBtn.addEventListener("click", async () => {
    if (
      !confirm(
        `Hire ${app.first_name} ${app.last_name} as ${roleLabel(app.role)}? This adds them to the roster with the standard week (Mon–Fri 9–5) and starts onboarding.`,
      )
    )
      return;
    await decide(app, "hire", { note: fNote.value || null, start_date: fStart.value || null }, msg, refresh);
  });
  rejectBtn.addEventListener("click", async () => {
    if (!confirm(`Reject the application of ${app.first_name} ${app.last_name}?`)) return;
    await decide(app, "reject", { note: fNote.value || null }, msg, refresh);
  });
  return h(
    "div",
    { class: "decision", id: "decision" },
    h("h4", {}, "Decision"),
    app.can_hire
      ? callout("ok", "All checks passed", h("span", {}, "This applicant can be hired."))
      : callout(
          "info",
          "Not ready to hire",
          h(
            "ul",
            {},
            app.blockers.map((b) => h("li", {}, b)),
          ),
        ),
    h(
      "div",
      { class: "field-row" },
      field("Start date", fStart, { optional: true }),
      field("Note", fNote, { optional: true }),
    ),
    h("div", { class: "actions" }, rejectBtn, h("span", { class: "spacer" }), hireBtn),
  );
}

async function decide(app, action, body, msg, refresh) {
  msg.replaceChildren();
  try {
    const updated = await api.post(`/applications/${app.id}/${action}`, body);
    toast(
      action === "hire"
        ? `${app.first_name} hired — staff number ${updated.staff_number}; onboarding link sent`
        : "Application rejected",
    );
    refresh(updated);
  } catch (err) {
    msg.append(callout("error", "Could not save the decision", h("span", {}, describeError(err))));
  }
}
