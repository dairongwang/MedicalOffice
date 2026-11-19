import { api } from "../api.js";
import {
  append,
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
  statusBadge,
  toast,
} from "../ui.js";
import { roleLabel } from "../validation.js";
import { signaturePad } from "./signature-pad.js";

/**
 * The new hire's onboarding portal — opened from the time-limited link the office sent
 * (#/welcome/<token>). Shows the checklist; the hire fills the forms and signs documents
 * electronically. No account: the token identifies the person; it expires (see invites.py).
 */
export function welcomeView(params) {
  const root = h("section", { class: "page portal" });
  const token = params.id;
  if (!token) {
    render(
      root,
      callout(
        "error",
        "Missing link",
        h("span", {}, "Open the onboarding link you received by e-mail or text message."),
      ),
    );
    return root;
  }
  void load(root, token);
  return root;
}

async function load(root, token, openKey = null) {
  render(root, h("p", { class: "muted loading" }, "Loading your onboarding…"));
  let state;
  try {
    state = await api.get(`/onboarding/portal/${encodeURIComponent(token)}`, { auth: false });
  } catch (e) {
    render(
      root,
      h(
        "section",
        { class: "card result warn", id: "portal-invalid" },
        h("div", { class: "result-icon" }, "!"),
        h("h3", {}, "This link does not work any more"),
        h("p", {}, describeError(e)),
        h(
          "p",
          { class: "small muted" },
          "Links are valid for a limited time. Ask the practice manager to send you a new one.",
        ),
      ),
    );
    return;
  }
  paint(root, token, state, openKey);
}

function paint(root, token, state, openKey) {
  const w = state.worker;
  const pending = state.steps.filter((s) => s.status === "pending" && s.who === "hire" && !s.submission);
  const nextKey = openKey ?? pending[0]?.key ?? null;
  render(
    root,
    h(
      "header",
      { class: "page-header portal-head" },
      h(
        "div",
        {},
        h("p", { class: "muted small" }, "Medic Office · onboarding"),
        h("h2", {}, `Welcome, ${w.first_name}!`),
        h(
          "p",
          { class: "muted small" },
          `${roleLabel(w.role)} · staff number `,
          h("span", { class: "mono" }, w.staff_number),
          w.start_date ? ` · starting ${formatDate(w.start_date)}` : "",
        ),
      ),
    ),
    h(
      "section",
      { class: "card" },
      h(
        "div",
        { class: "progress-row" },
        h(
          "div",
          {
            class: "progress",
            role: "progressbar",
            "aria-valuenow": state.percent,
            "aria-valuemin": 0,
            "aria-valuemax": 100,
          },
          h("div", { class: "progress-bar", style: `width:${state.percent}%` }),
        ),
        h(
          "strong",
          { class: "small", id: "portal-progress" },
          `${state.done}/${state.total} · ${state.percent}%`,
        ),
      ),
      state.completed_at
        ? callout(
            "ok",
            "All done — thank you!",
            h(
              "span",
              {},
              `Your onboarding was approved by the office on ${formatDateTime(state.approved_at || state.completed_at)}. See you on your first day!`,
            ),
          )
        : state.all_steps_done
          ? callout(
              "ok",
              "Everything is in — thank you!",
              h(
                "span",
                {},
                "Every step is done. The practice manager reviews and signs off your onboarding; you will hear from the office.",
              ),
            )
          : pending.length
            ? h(
                "p",
                { class: "small muted" },
                `${pending.length} step${pending.length === 1 ? "" : "s"} left for you. Steps marked "office" are handled by the practice manager. This link is valid until ${formatDateTime(state.expires_at)}.`,
              )
            : callout(
                "info",
                "Your part is done",
                h(
                  "span",
                  {},
                  "The office finishes the remaining steps (background screening, reference check). Thank you!",
                ),
              ),
      state.esign_fake
        ? h(
            "p",
            { class: "hint" },
            "Development mode: electronic signatures are recorded by the built-in fake e-sign provider (FAKE-ESIGN references).",
          )
        : null,
    ),
    h(
      "ol",
      { class: "checklist portal-steps", id: "portal-steps" },
      state.steps.map((s) => stepCard(root, token, state, s, s.key === nextKey)),
    ),
    h(
      "p",
      { class: "hint" },
      "We never ask for government id numbers or full bank account numbers online — those are completed in person at the office.",
    ),
  );
  if (openKey) document.getElementById(`portal-step-${openKey}`)?.scrollIntoView({ block: "start" });
}

function stepCard(root, token, state, s, open) {
  const done = s.status === "done";
  const na = s.status === "not_applicable";
  const office = s.who === "office";
  const body = h("div", { class: "portal-body" });
  const li = h(
    "li",
    { class: `step portal-step ${s.status} ${office ? "office" : ""}`, id: `portal-step-${s.key}` },
    h(
      "div",
      { class: "portal-step-head" },
      h("span", { class: `step-no ${done ? "ok" : ""}` }, done ? "✓" : String(state.steps.indexOf(s) + 1)),
      h(
        "div",
        { class: "portal-step-title" },
        h("strong", {}, s.title),
        h("span", { class: "muted small" }, office ? " · handled by the office" : ""),
      ),
      statusBadge(s.status),
    ),
    body,
  );
  if (office) {
    append(body, h("p", { class: "small muted" }, s.intro || "The office completes this step."));
    return li;
  }
  if (na) {
    append(body, h("p", { class: "small muted" }, "The office marked this step as not applicable for you."));
    return li;
  }
  if (done || (s.submission && s.key === "reference_check")) {
    const sub = s.submission;
    append(
      body,
      h(
        "p",
        { class: "small" },
        sub?.signed_at
          ? `Signed by ${sub.signer_name} on ${formatDateTime(sub.signed_at)}`
          : sub
            ? `Submitted ${formatDateTime(sub.submitted_at)}`
            : s.note || "Done",
      ),
      sub?.esign_reference
        ? h(
            "p",
            { class: "hint" },
            "E-signature reference: ",
            h("span", { class: "mono" }, sub.esign_reference),
          )
        : null,
      s.key === "reference_check" && s.status === "pending"
        ? h("p", { class: "small muted" }, "Thank you — the office will contact your references.")
        : null,
      button(sub?.signed_at ? "View / sign again" : "Edit", {
        class: "btn ghost small-btn",
        onclick: () => {
          body.replaceChildren(stepForm(root, token, state, s));
        },
      }),
    );
    return li;
  }
  if (open) append(body, stepForm(root, token, state, s));
  else
    append(
      body,
      h("p", { class: "small muted" }, s.intro),
      button("Open", { class: "btn small-btn", onclick: () => paint(root, token, state, s.key) }),
    );
  return li;
}

function stepForm(root, token, state, s) {
  const msg = h("div");
  const form = h("form", { class: "portal-form", novalidate: true });
  form.append(h("p", { class: "small muted" }, s.intro));
  if (s.document)
    form.append(h("pre", { class: "document", tabindex: 0, "aria-label": `${s.title} text` }, s.document));
  const inputs = {};
  const prev = s.submission?.data || {};
  for (const f of s.fields) {
    let el;
    if (f.type === "select") {
      el = select([["", "Choose…", { disabled: true }], ...f.options], {
        name: f.name,
        required: f.required,
        id: `pf-${s.key}-${f.name}`,
      });
      el.value = prev[f.name] ?? "";
    } else if (f.type === "checkbox") {
      el = h("input", {
        type: "checkbox",
        name: f.name,
        id: `pf-${s.key}-${f.name}`,
        checked: !!prev[f.name],
      });
    } else if (f.type === "textarea") {
      el = h("textarea", {
        class: "input",
        name: f.name,
        rows: 3,
        maxlength: f.maxlength,
        id: `pf-${s.key}-${f.name}`,
      });
      el.value = prev[f.name] ?? "";
    } else {
      el = input({
        type: f.type === "number" ? "number" : f.type,
        name: f.name,
        maxlength: f.maxlength,
        required: f.required,
        inputmode: f.type === "tel" ? "tel" : f.type === "number" ? "decimal" : null,
        value: prev[f.name] ?? "",
        id: `pf-${s.key}-${f.name}`,
      });
    }
    inputs[f.name] = el;
    form.append(
      f.type === "checkbox"
        ? h(
            "label",
            { class: "checkbox consent", for: el.id },
            el,
            h("span", {}, f.label, f.required ? " *" : ""),
          )
        : field(f.label + (f.required ? " *" : ""), el, { hint: f.hint || undefined }),
    );
  }
  let pad = null;
  let signerName = null;
  let consent = null;
  if (s.signature) {
    pad = signaturePad();
    signerName = input({
      name: "signer_name",
      required: true,
      maxlength: 200,
      autocomplete: "name",
      placeholder: `${state.worker.first_name} ${state.worker.last_name}`,
      value: s.submission?.signer_name || "",
      id: `pf-${s.key}-signer`,
    });
    consent = h("input", { type: "checkbox", id: `pf-${s.key}-esign-consent` });
    form.append(
      h("h4", { class: "section-title" }, "Electronic signature"),
      pad.el,
      field("Type your full name *", signerName),
      h(
        "label",
        { class: "checkbox consent", for: consent.id },
        consent,
        h(
          "span",
          {},
          "I agree to sign this document electronically and understand my electronic signature is as binding as a handwritten one. *",
        ),
      ),
    );
  }
  const submit = button(s.signature ? "Sign & submit" : "Submit", {
    type: "submit",
    class: "btn primary",
    id: `pf-${s.key}-submit`,
  });
  form.append(msg, h("div", { class: "actions" }, h("span", { class: "spacer" }), submit));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    const data = {};
    const problems = [];
    for (const f of s.fields) {
      const el = inputs[f.name];
      const value = f.type === "checkbox" ? el.checked : el.value.trim();
      if (f.required && (value === "" || value === false)) problems.push(`${f.label}: required.`);
      data[f.name] = value === "" ? null : value;
    }
    let signature = null;
    if (s.signature) {
      if (pad.isEmpty()) problems.push("Please draw your signature.");
      if (!signerName.value.trim()) problems.push("Please type your full name.");
      if (!consent.checked) problems.push("Please agree to sign electronically.");
      signature = {
        signer_name: signerName.value.trim(),
        image_png: pad.toDataUrl(),
        consent: consent.checked,
      };
    }
    if (problems.length) {
      msg.append(
        callout(
          "error",
          "Please check",
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
      const next = await api.post(
        `/onboarding/portal/${encodeURIComponent(token)}/steps/${s.key}`,
        { data, signature },
        { auth: false },
      );
      toast(s.signature ? `${s.title}: signed` : `${s.title}: submitted`);
      const stillPending = next.steps.find(
        (x) => x.status === "pending" && x.who === "hire" && !x.submission,
      );
      paint(root, token, next, stillPending ? stillPending.key : null);
      if (!stillPending) window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      msg.append(callout("error", "Could not submit", h("span", {}, describeError(err))));
      submit.disabled = false;
    }
  });
  return form;
}
