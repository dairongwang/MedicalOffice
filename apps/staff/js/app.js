/**
 * Staff dashboard shell: hash router + tab bar. Four kinds of tab:
 *   public    — Apply, My availability (staff number + work e-mail, or the signed-in worker's week)
 *   staff     — My appointments, Reports (immunization records / annual health examination reports the office
 *               prepared for a doctor to sign): a signed-in member of staff (staff account linked to the roster)
 *   assistant — My tasks: a signed-in medical assistant (the visits the office assigned them)
 *   manager   — Applications, Onboarding, Roster: an administrator account
 * Signing in (#/login) serves both; members of staff create their account at #/create-account.
 * Independent of the main app: talks to the API across origins.
 */
import { api } from "./api.js";
import { session } from "./auth.js";
import { button, h, render, toast } from "./ui.js";
import { applicationsView } from "./views/applications.js";
import { applyView } from "./views/apply.js";
import { availabilityView } from "./views/availability.js";
import { createAccountView } from "./views/create-account.js";
import { loginView } from "./views/login.js";
import { myAppointmentsView } from "./views/my-appointments.js";
import { myTasksView } from "./views/my-tasks.js";
import { onboardingView } from "./views/onboarding.js";
import { patientChartView } from "./views/patient-chart.js";
import { reportsView } from "./views/reports.js";
import { rosterView } from "./views/roster.js";
import { welcomeView } from "./views/welcome.js";

const TABS = [
  { id: "apply", label: "Apply", icon: "✎", view: applyView, access: "public" },
  { id: "my-tasks", label: "My tasks", icon: "✚", view: myTasksView, access: "assistant" },
  { id: "my-appointments", label: "My appointments", icon: "▦", view: myAppointmentsView, access: "provider" },
  { id: "reports", label: "Reports", icon: "✍", view: reportsView, access: "provider" },
  { id: "availability", label: "My availability", icon: "◷", view: availabilityView, access: "public" },
  { id: "applications", label: "Applications", icon: "☰", view: applicationsView, access: "manager" },
  { id: "onboarding", label: "Onboarding", icon: "✓", view: onboardingView, access: "manager" },
  { id: "roster", label: "Roster", icon: "☺", view: rosterView, access: "manager" },
];

/** May the current session open this tab? */
function canOpen(t) {
  if (t.access === "public") return true;
  if (t.access === "assistant") return session.isAssistant;
  if (t.access === "provider") return session.isStaff && !session.isAssistant;
  return session.isAdmin;
}

/** Is the tab shown at all? Signed out: every tab (locked). Signed in: the staff tabs only to the
 *  members of staff they are for (an assistant has tasks, not appointments of their own). */
function isListed(t) {
  if (!session.signedIn || t.access === "public" || t.access === "manager") return true;
  return canOpen(t);
}

/** Where a session lands by default (auth.js → session.home). */
function home() {
  return session.home;
}

const LOCKED_HINT = {
  assistant: "medical assistants — sign in",
  provider: "staff — sign in",
  manager: "managers — sign in",
};

const outlet = document.getElementById("view");
const nav = document.getElementById("tabs");
const account = document.getElementById("account");

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "") || home();
  const [path, query = ""] = raw.split("?");
  const [tab, id] = path.split("/");
  const params = Object.fromEntries(new URLSearchParams(query));
  if (id) params.id = id;
  return { tab, params };
}

function renderNav(active) {
  render(
    nav,
    ...TABS.filter(isListed).map((t) =>
      h(
        "a",
        {
          class: `tab ${t.id === active ? "active" : ""} ${canOpen(t) ? "" : "locked"} ${t.access}`,
          href: `#/${t.id}`,
          id: `tab-${t.id}`,
          "aria-current": t.id === active ? "page" : null,
          title: canOpen(t) ? t.label : `${t.label} (${LOCKED_HINT[t.access] || "sign in"})`,
        },
        h("span", { class: "tab-icon", "aria-hidden": "true" }, t.icon),
        h("span", { class: "tab-label" }, t.label),
        !canOpen(t) ? h("span", { class: "tab-lock", "aria-hidden": "true" }, "🔒") : null,
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
            { class: "who-email", title: session.me.email, id: "who" },
            h("span", { class: "muted" }, session.isAdmin ? "Manager · " : "Staff · "),
            session.displayName,
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
      : h(
          "div",
          { class: "who" },
          h("a", { href: "#/create-account", class: "small", id: "link-signup" }, "Create staff account"),
          button("Sign in", {
            class: "btn small-btn",
            id: "btn-signin",
            onclick: () =>
              (location.hash = `#/login${location.hash ? `?next=${encodeURIComponent(location.hash)}` : ""}`),
          }),
        ),
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
  if (tab === "welcome") {
    // The new hire's portal, opened from the invite link — not a tab, no sign-in.
    renderNav(null);
    document.body.classList.add("portal-mode");
    render(outlet, welcomeView(params));
    return;
  }
  document.body.classList.remove("portal-mode");
  if (tab === "login") {
    renderNav(null);
    render(outlet, loginView(params, { onSignedIn: go }));
    return;
  }
  if (tab === "patient-chart") {
    // The patient's chart of one of the provider's visits (from My appointments / the "ready" message).
    renderNav("my-appointments");
    if (!session.isStaff || session.isAssistant) {
      render(outlet, loginView({ next: location.hash }, { onSignedIn: go }));
      return;
    }
    render(outlet, patientChartView(params));
    window.scrollTo({ top: 0 });
    return;
  }
  if (tab === "create-account") {
    renderNav(null);
    render(outlet, createAccountView(params, { onSignedIn: go }));
    return;
  }
  const def = TABS.find((t) => t.id === tab) || TABS.find((t) => t.id === home());
  renderNav(def.id);
  if (!canOpen(def)) {
    if (session.signedIn) {
      // Signed in, but this tab is not for this account (a member of staff on a manager tab, …).
      render(
        outlet,
        h(
          "section",
          { class: "page" },
          h(
            "div",
            { class: "callout warn", id: "tab-not-allowed" },
            h(
              "strong",
              {},
              def.access === "manager"
                ? "For managers only"
                : def.access === "assistant"
                  ? "For medical assistants"
                  : "For members of staff",
            ),
            h(
              "span",
              {},
              def.access === "manager"
                ? "This section needs an administrator account of the office."
                : def.access === "assistant"
                  ? "My tasks lists the visits the office assigned to a medical assistant."
                  : "This section needs a staff account linked to your staff record.",
            ),
          ),
        ),
      );
      return;
    }
    const next = def.id === tab ? location.hash : `#/${def.id}`;
    render(outlet, loginView({ next }, { onSignedIn: go }));
    return;
  }
  render(outlet, def.view(params));
  window.scrollTo({ top: 0 });
}

document.addEventListener("staff:signed-out", () => {
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
