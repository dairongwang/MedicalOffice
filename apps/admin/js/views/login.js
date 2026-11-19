import { session } from "../auth.js";
import { button, callout, describeError, field, h, input, passwordInput, toast } from "../ui.js";

/** Administrator sign-in (administrator account of the Medic Office API). */
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
    h("h3", {}, "Administrator sign-in"),
    h(
      "p",
      { class: "muted small" },
      "The office administration dashboard — the patient roster, the appointment agenda and the payment history — needs an administrator account of the office.",
    ),
    msg,
    field("E-mail", fEmail),
    field("Password", fPassword),
    h("div", { class: "actions" }, h("span", { class: "spacer" }), submit),
    window.ADMIN_CONFIG?.showDummyHint
      ? h(
          "p",
          { class: "hint" },
          "Development: dummy-admin@example.com / Dummy-Admin-pass-123 (dummy account created by the seed).",
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
    submit.disabled = true;
    submit.textContent = "Signing in…";
    try {
      let result;
      if (challenge) result = await session.loginCode(challenge.challenge, fCode.value.trim());
      else result = await session.login(fEmail.value.trim(), fPassword.value);
      if (result.twoFactor) {
        challenge = result.twoFactor;
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
      toast(`Signed in as ${session.me.email}`);
      onSignedIn(params.next || "#/patients");
    } catch (err) {
      msg.append(callout("error", "Sign-in failed", h("span", {}, describeError(err))));
    } finally {
      submit.disabled = false;
      submit.textContent = challenge ? "Verify" : "Sign in";
    }
  });
  return h("section", { class: "page" }, form);
}
