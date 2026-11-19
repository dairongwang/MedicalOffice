/** Tiny DOM helpers — everything user-provided goes through textContent, never innerHTML. */

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key in el && typeof value !== "string" && key !== "style") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

export function append(el, ...children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** Replace `el`'s content; null/false/undefined children are skipped (unlike Node.replaceChildren). */
export function render(el, ...children) {
  clear(el);
  return append(el, children);
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

/** <div class="field"><label>…</label>{input}</div> */
export function field(label, input, { hint, optional } = {}) {
  if (!input.id) input.id = "f-" + Math.random().toString(36).slice(2, 9);
  return h(
    "div",
    { class: "field" },
    h("label", { for: input.id }, label, optional ? h("span", { class: "muted" }, " (optional)") : null),
    input.passwordField || input,
    hint ? h("p", { class: "hint" }, hint) : null,
  );
}

export function input(attrs = {}) {
  return h("input", { class: "input", ...attrs });
}

/**
 * A password input with a Show / Hide button, so a typo can be seen before it fails the
 * sign-in. Returns the <input>; `.passwordField` on it is the wrapper to put in a `field()`.
 */
export function passwordInput(attrs = {}) {
  const el = input({ type: "password", ...attrs });
  const toggle = h("button", {
    type: "button",
    class: "password-toggle",
    "aria-pressed": "false",
    "aria-label": "Show password",
    title: "Show or hide the password you typed",
  });
  toggle.textContent = "Show";
  toggle.addEventListener("click", () => {
    const show = el.type === "password";
    el.type = show ? "text" : "password";
    toggle.textContent = show ? "Hide" : "Show";
    toggle.setAttribute("aria-pressed", String(show));
    toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
    el.focus();
  });
  el.passwordField = h("span", { class: "password-field" }, el, toggle);
  return el;
}

export function select(options, attrs = {}) {
  const el = h("select", { class: "input", ...attrs });
  for (const opt of options) {
    const [value, label, extra] = Array.isArray(opt) ? opt : [opt, opt];
    el.append(h("option", { value, ...(extra || {}) }, label));
  }
  if (attrs.value !== undefined) el.value = attrs.value;
  return el;
}

export function button(label, attrs = {}) {
  return h("button", { type: "button", class: "btn", ...attrs }, label);
}

export function callout(kind, title, ...body) {
  return h(
    "div",
    { class: `callout ${kind}`, role: kind === "error" ? "alert" : "status" },
    h("strong", {}, title),
    ...body,
  );
}

export function badge(text, kind = "") {
  return h("span", { class: `badge ${kind}` }, text);
}

/** Status → badge (shared vocabulary of checks, applications and onboarding steps). */
export function statusBadge(status) {
  const map = {
    passed: ["Passed", "ok"],
    done: ["Done", "ok"],
    hired: ["Hired", "ok"],
    clear: ["Clear", "ok"],
    failed: ["Failed", "bad"],
    rejected: ["Rejected", "bad"],
    in_progress: ["In progress", "warn"],
    screening: ["Screening", "warn"],
    submitted: ["Submitted", "warn"],
    pending: ["Pending", ""],
    not_applicable: ["N/A", "muted"],
    withdrawn: ["Withdrawn", "muted"],
  };
  const [label, kind] = map[status] || [status, ""];
  return badge(label, kind);
}

let toastTimer = null;
export function toast(message, { error = false } = {}) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.toggle("error", error);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), error ? 6000 : 3500);
}

export function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return Number.isNaN(+d)
    ? iso
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** A timestamp in *this browser's* time zone, with the zone named ("Nov 5, 2026, 11:40 AM EST").
 *  Agenda / check-in times use format.officeDateTime (the office's zone) instead. */
export function formatDateTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(+d)
    ? iso
    : d.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      });
}

export function formatTime(hhmm) {
  const m = /^(\d{1,2}):(\d{2})/.exec(hhmm || "");
  if (!m) return hhmm || "";
  const d = new Date(2000, 0, 1, Number(m[1]), Number(m[2]));
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** Read a form's fields into a plain object (trimmed strings; checkboxes → boolean). */
export function formValues(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.value.trim();
  }
  return out;
}

export const orNull = (v) => (v === "" || v === undefined ? null : v);

/** A <dl class="record"> of [label, value] pairs (empty values skipped). */
export function record(pairs) {
  return h(
    "dl",
    { class: "record" },
    pairs
      .filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v, wide]) => h("div", { class: wide ? "wide" : null }, h("dt", {}, k), h("dd", {}, v))),
  );
}

export function spinner(text = "Loading…") {
  return h("p", { class: "muted loading" }, text);
}

export function describeError(e) {
  return e instanceof Error ? e.message : String(e);
}
