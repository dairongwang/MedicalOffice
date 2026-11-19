import { session } from "../auth.js";
import { button, callout, describeError, field, h, input, passwordInput, toast } from "../ui.js";

/**
 * Sign-in to the staff dashboard (an account of the Medic Office API): managers with their
 * administrator account; members of staff with the staff account they created from their staff
 * number and work e-mail (#/create-account). Accounts with two-factor authentication are asked
 * for a code after the password. `params.next` = where to go afterwards (a locked tab's address).
 */
export function loginView(params, { onSignedIn }) {
  const msg = h("div", { id: "login-msg" });
  const fEmail = input({
    name: "email",
    type: "email",
    required: true,
    autocomplete: "username",
    inputmode: "email",
    id: "login-email",
  });
  const fPassword = passwordInput({
    name: "password",
    required: true,
    autocomplete: "current-password",
    id: "login-password",
  });
  const submit = button("Sign in", { type: "submit", class: "btn primary", id: "btn-login" });
  const form = h(
    "form",
    { class: "card form narrow", id: "login-form", novalidate: true },
    h("h3", {}, "Sign in"),
    h(
      "p",
      { class: "muted small" },
      "Managers sign in with their administrator account. Doctors, nurses and medical assistants sign in with " +
        "their staff account — ",
      h("a", { href: "#/create-account", id: "link-login-create" }, "create one"),
      " with your staff number and work e-mail if you have not yet.",
    ),
    msg,
    field("E-mail", fEmail),
    field("Password", fPassword),
    h("div", { class: "actions" }, h("span", { class: "spacer" }), submit),
    window.STAFF_CONFIG?.showDummyHint
      ? h(
          "div",
          { class: "hint", id: "login-dummy-hint" },
          h("p", {}, "Development — dummy accounts created by the seed:"),
          h(
            "ul",
            {},
            h("li", {}, "Manager: dummy-admin@example.com / Dummy-Admin-pass-123"),
            h("li", {}, "Doctor: dummy-doctor@example.com / Dummy-Doctor-pass-123"),
            h("li", {}, "Nurse: dummy-nurse@example.com / Dummy-Nurse-pass-123"),
            h("li", {}, "Medical assistant: dummy-assistant@example.com / Dummy-Assistant-pass-123"),
          ),
        )
      : null,
  );

  let challenge = null;
  const fCode = input({
    name: "code",
    inputmode: "numeric",
    autocomplete: "one-time-code",
    placeholder: "6-digit code or recovery code",
    id: "login-code",
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.replaceChildren();
    if (!challenge && (!fEmail.value.trim() || !fPassword.value)) {
      msg.append(callout("error", "Sign-in failed", h("span", {}, "Please enter your e-mail and password.")));
      return;
    }
    submit.disabled = true;
    submit.textContent = challenge ? "Verifying…" : "Signing in…";
    try {
      let result;
      if (challenge) result = await session.loginCode(challenge.challenge, fCode.value.trim());
      else result = await session.login(fEmail.value.trim(), fPassword.value);
      if (result.twoFactor) {
        challenge = result.twoFactor;
        fEmail.disabled = true;
        fPassword.disabled = true;
        form.insertBefore(
          field("Verification code", fCode, {
            hint: challenge.sent
              ? `A code was sent to ${challenge.sent.destination}.`
              : "Enter the code from your authenticator app (or a recovery code).",
          }),
          form.querySelector(".actions"),
        );
        fCode.focus();
        return;
      }
      toast(`Signed in as ${session.displayName}`);
      onSignedIn(params.next && !params.next.startsWith("#/login") ? params.next : `#/${session.home}`);
    } catch (err) {
      msg.append(callout("error", "Sign-in failed", h("span", {}, describeError(err))));
    } finally {
      submit.disabled = false;
      submit.textContent = challenge ? "Verify" : "Sign in";
    }
  });
  queueMicrotask(() => fEmail.focus());
  return h("section", { class: "page" }, form);
}
