import { api } from "../api.js";
import { session } from "../auth.js";
import { button, callout, describeError, field, h, input, passwordInput, render, toast } from "../ui.js";
import { MIN_PASSWORD, formatNumber, isEmail, passwordProblem } from "../validation.js";

/**
 * A member of staff creates their staff dashboard account (services/account/staff_accounts.py):
 *
 *   1. staff number (W-000042, handed out when they were put on the roster) + work e-mail — both
 *      must match an active staff record; a 6-digit code goes to the work e-mail on that record;
 *   2. the code + a new password → the account (role staff), linked to the record, signed in.
 *
 * The password never leaves the browser: only the keys derived from it are sent (crypto.js).
 * Route: #/create-account  ·  #/create-account?number=W-000001&email=…
 */
export function createAccountView(params, { onSignedIn }) {
  const root = h("section", { class: "page", id: "create-account" });
  if (session.signedIn) {
    render(
      root,
      h(
        "div",
        { class: "card form narrow" },
        h("h3", {}, "Create staff account"),
        callout(
          "info",
          "You are signed in",
          h("span", {}, `as ${session.displayName}. Sign out first to create another account.`),
        ),
        h(
          "div",
          { class: "actions" },
          h("span", { class: "spacer" }),
          h("a", { class: "btn primary", href: `#/${session.home}` }, "Continue"),
        ),
      ),
    );
    return root;
  }
  stepOne(root, params, onSignedIn);
  return root;
}

function outboxLink(sent) {
  if (!sent.fake) return null;
  const origin = api.base.replace(/\/api\/v1$/, "");
  const href = sent.outbox_url ? `${origin}${sent.outbox_url}` : `${api.base}/messages/fake/outbox`;
  return h(
    "p",
    { class: "hint", id: "enroll-outbox" },
    "Development: no e-mail server is configured — the code is in the ",
    h("a", { href, target: "_blank", rel: "noopener", id: "link-enroll-outbox" }, "fake outbox"),
    ".",
  );
}

function stepOne(root, params, onSignedIn) {
  const msg = h("div", { id: "enroll-msg" });
  const fNumber = input({
    name: "staff_number",
    required: true,
    maxlength: 16,
    autocomplete: "off",
    placeholder: "W-000042",
    value: params.number || "",
    id: "enroll-number",
  });
  const fEmail = input({
    name: "email",
    type: "email",
    required: true,
    autocomplete: "email",
    inputmode: "email",
    value: params.email || "",
    id: "enroll-email",
  });
  const submit = button("Send me a code", { type: "submit", class: "btn primary", id: "btn-enroll-send" });
  const form = h(
    "form",
    { class: "card form narrow", id: "enroll-form", novalidate: true },
    h("h3", {}, "Create your staff account"),
    h(
      "p",
      { class: "muted small" },
      "For doctors, nurses, medical assistants and the rest of the team who are on the office's roster. " +
        "With the account you see your appointments or tasks and keep your weekly availability up to date.",
    ),
    msg,
    field("Staff number", fNumber, { hint: "From your registration / onboarding, e.g. W-000042." }),
    field("Work e-mail", fEmail, { hint: "The address on your staff record — the code is sent there." }),
    h(
      "div",
      { class: "actions" },
      h("a", { href: "#/login", class: "small", id: "link-enroll-login" }, "I already have an account"),
      h("span", { class: "spacer" }),
      submit,
    ),
    window.STAFF_CONFIG?.showDummyHint
      ? h(
          "p",
          { class: "hint" },
          "Development: the dummy receptionist W-000003 / dummy-frontdesk@example.com has no account yet.",
        )
      : null,
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    const number = formatNumber("W", fNumber.value);
    const email = fEmail.value.trim();
    const problems = [];
    if (!/^W-\d{6}$/.test(number)) problems.push("Please enter your staff number (e.g. W-000042).");
    if (!isEmail(email)) problems.push("Please enter your work e-mail address.");
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
      return;
    }
    submit.disabled = true;
    submit.textContent = "Sending…";
    try {
      const sent = await session.startAccount(number, email);
      stepTwo(root, { number, email, sent }, onSignedIn);
    } catch (err) {
      msg.append(callout("error", "Could not start", h("span", {}, describeError(err))));
      submit.disabled = false;
      submit.textContent = "Send me a code";
    }
  });
  render(root, form);
  queueMicrotask(() => (fNumber.value ? fEmail : fNumber).focus());
}

function stepTwo(root, state, onSignedIn) {
  const { email, sent } = state;
  const msg = h("div", { id: "enroll-code-msg" });
  const fCode = input({
    name: "code",
    required: true,
    inputmode: "numeric",
    autocomplete: "one-time-code",
    maxlength: 16,
    placeholder: "123456",
    id: "enroll-code",
  });
  const fPassword = passwordInput({
    name: "password",
    required: true,
    autocomplete: "new-password",
    minlength: MIN_PASSWORD,
    id: "enroll-password",
  });
  const fRepeat = passwordInput({
    name: "repeat",
    required: true,
    autocomplete: "new-password",
    id: "enroll-repeat",
  });
  const submit = button("Create account", { type: "submit", class: "btn primary", id: "btn-enroll-create" });
  const minutes = Math.max(1, Math.round((sent.expires_in || 600) / 60));
  const form = h(
    "form",
    { class: "card form narrow", id: "enroll-code-form", novalidate: true },
    h("h3", {}, "Check your e-mail"),
    callout(
      "ok",
      "Code sent",
      h("span", { id: "enroll-sent-to" }, `to ${sent.sent_to || "your work e-mail"} — valid for ${minutes} minutes.`),
    ),
    outboxLink(sent),
    msg,
    field("Code from the e-mail", fCode),
    field("New password", fPassword, {
      hint: `At least ${MIN_PASSWORD} characters. It never leaves this browser — only keys derived from it are sent.`,
    }),
    field("Repeat the password", fRepeat),
    h(
      "div",
      { class: "actions" },
      button("Start again", {
        class: "btn",
        id: "btn-enroll-restart",
        onclick: () => stepOne(root, { number: state.number, email }, onSignedIn),
      }),
      h("span", { class: "spacer" }),
      submit,
    ),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    const code = fCode.value.replace(/\s/g, "");
    const problem = !/^\d{4,16}$/.test(code)
      ? "Please enter the code from the e-mail."
      : passwordProblem(fPassword.value, fRepeat.value, email);
    if (problem) {
      msg.append(callout("error", "Please check the form", h("span", {}, problem)));
      return;
    }
    submit.disabled = true;
    submit.textContent = "Creating…";
    try {
      await session.createAccount(sent.enrollment, code, email.toLowerCase(), fPassword.value);
      toast(`Welcome, ${session.displayName} — your staff account is ready`);
      onSignedIn(`#/${session.home}`);
    } catch (err) {
      msg.append(callout("error", "Could not create the account", h("span", {}, describeError(err))));
      submit.disabled = false;
      submit.textContent = "Create account";
    }
  });
  render(root, form);
  queueMicrotask(() => fCode.focus());
}
