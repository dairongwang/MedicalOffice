/**
 * Office administration dashboard shell: hash router + tab bar. Everything here is for the
 * office's administrators (Patients roster, Appointments agenda, Reports — immunization records and
 * annual health examination reports patients ask for, signed by a doctor, Payments — the office-wide
 * payment history, Pharmacies — where prescriptions can be e-mailed) and needs an administrator sign-in: signed out, every tab shows the sign-in
 * form instead of its view, so no office data is requested. Independent of the main app and the
 * staff dashboard: talks to the API across origins.
 */
import { api } from "./api.js";
import { session } from "./auth.js";
import { button, h, render, toast } from "./ui.js";
import { appointmentsView } from "./views/appointments.js";
import { loginView } from "./views/login.js";
import { patientsView } from "./views/patients.js";
import { paymentsView } from "./views/payments.js";
import { pharmaciesView } from "./views/pharmacies.js";
import { reportsView } from "./views/reports.js";

const TABS = [
  { id: "patients", label: "Patients", icon: "☺", view: patientsView },
  { id: "appointments", label: "Appointments", icon: "▦", view: appointmentsView },
  { id: "reports", label: "Reports", icon: "✍", view: reportsView },
  { id: "payments", label: "Payments", icon: "$", view: paymentsView },
  { id: "pharmacies", label: "Pharmacies", icon: "℞", view: pharmaciesView },
];

const outlet = document.getElementById("view");
const nav = document.getElementById("tabs");
const account = document.getElementById("account");

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "") || "patients";
  const [path, query = ""] = raw.split("?");
  const [tab, id, sub] = path.split("/");
  const params = Object.fromEntries(new URLSearchParams(query));
  if (id) params.id = id;
  if (sub) params.sub = sub; // #/patients/3/payments — a sub-page of one record
  return { tab, params };
}

function renderNav(active) {
  render(
    nav,
    ...TABS.map((t) =>
      h(
        "a",
        {
          class: `tab ${t.id === active ? "active" : ""} ${session.isAdmin ? "" : "locked"}`,
          href: `#/${t.id}`,
          id: `tab-${t.id}`,
          "aria-current": t.id === active ? "page" : null,
          title: session.isAdmin ? t.label : `${t.label} (administrators — sign in)`,
        },
        h("span", { class: "tab-icon", "aria-hidden": "true" }, t.icon),
        h("span", { class: "tab-label" }, t.label),
        session.isAdmin ? null : h("span", { class: "tab-lock", "aria-hidden": "true" }, "🔒"),
      ),
    ),
  );
}

function renderAccount() {
  render(
    account,
    session.signedIn
      ? h(
          "div",
          { class: "who" },
          h(
            "span",
            { class: "who-email", title: session.me.email },
            h("span", { class: "muted" }, "Administrator · "),
            session.me.email,
          ),
          button("Sign out", {
            class: "btn small-btn",
            id: "btn-logout",
            onclick: async () => {
              await session.logout();
              toast("Signed out");
              route();
            },
          }),
        )
      : button("Sign in", {
          class: "btn small-btn",
          id: "btn-signin",
          onclick: () => (location.hash = `#/login?next=${encodeURIComponent(location.hash || "#/patients")}`),
        }),
  );
}

/** Go to a hash, re-rendering even when it is the current one (hashchange would not fire). */
function go(next) {
  if (location.hash === next) route();
  else location.hash = next;
}

function route() {
  const { tab, params } = parseHash();
  renderAccount();
  if (tab === "login") {
    renderNav(null);
    render(outlet, loginView(params, { onSignedIn: go }));
    return;
  }
  const def = TABS.find((t) => t.id === tab) || TABS[0];
  renderNav(def.id);
  if (!session.isAdmin) {
    // Signed out: the sign-in instead of the section — and back to exactly this address after it
    // (a deep link such as #/payments?q=… or #/patients/3/payments keeps its record / search).
    const next = def.id === tab ? location.hash : `#/${def.id}`;
    render(outlet, loginView({ next }, { onSignedIn: go }));
    return;
  }
  render(outlet, def.view(params));
  window.scrollTo({ top: 0 });
}

document.addEventListener("admin:signed-out", () => {
  session.me = null;
  toast("Your session expired — please sign in again.", { error: true });
  route();
});
window.addEventListener("hashchange", route);

(async function start() {
  document.getElementById("api-origin").textContent = api.base.replace(/\/api\/v1$/, "") || location.origin;
  await session.restore();
  route();
})();
