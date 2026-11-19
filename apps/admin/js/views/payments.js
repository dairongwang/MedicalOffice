import { api } from "../api.js";
import { hashFor } from "../format.js";
import { money } from "../payment.js";
import {
  KIND,
  OFFICE_FILTERS,
  OFFICE_KINDS,
  amountText,
  normalizeOfficePage,
  officeFilters,
  officeQuery,
  pageInfo,
  quickRange,
  statusBadge,
} from "../payment-ledger.js";
import {
  badge,
  button,
  callout,
  describeError,
  field,
  formatDateTime,
  h,
  input,
  render,
  select,
  spinner,
  toast,
} from "../ui.js";

/**
 * Administrators: the office's **payment history** — every hold, charge, release and refund on
 * every patient's payment methods in one list (GET /patient-payments), newest first, with the
 * totals of the search (charged net of refunds · on hold · refunded · count), status groups with
 * their counts, a date range, the kind of payment and a search over patient name, record number
 * (P-000042), appointment number (APT-000007), description and reference. Rows link to the
 * record's own payment history, where a hold is captured / released and a charge refunded.
 * "Export CSV" downloads the same search for accounting (audited by the API).
 *
 * Route: #/payments  ·  #/payments?q=&show=&kind=&since=&until=&offset=
 */
export function paymentsView(params) {
  const root = h("section", { class: "page wide", id: "office-payments" });
  void renderPayments(root, officeFilters(params));
  return root;
}

const go = (filters) => (location.hash = hashFor("payments", { ...filters, offset: filters.offset || "" }));

async function renderPayments(root, filters) {
  const search = input({
    type: "search",
    placeholder: "Patient name, P-000042, APT-000007, description or reference",
    value: filters.q,
    id: "office-payments-search",
  });
  const since = input({ type: "date", value: filters.since, id: "office-payments-since" });
  const until = input({ type: "date", value: filters.until, id: "office-payments-until" });
  const kind = select(OFFICE_KINDS, { id: "office-payments-kind", value: filters.kind });
  const apply = () =>
    go({ ...filters, q: search.value.trim(), since: since.value, until: until.value, kind: kind.value, offset: 0 });
  for (const el of [search, since, until, kind]) el.addEventListener("change", apply);

  const today = new Date();
  const quick = (label, key) => {
    const range = quickRange(key, today);
    const active = filters.since === range.since && filters.until === range.until;
    return button(label, {
      class: `btn chip ${active ? "active" : ""}`,
      id: `office-payments-period-${key}`,
      "aria-pressed": String(active),
      onclick: () => go({ ...filters, ...range, offset: 0 }),
    });
  };

  const exportButton = button("Export CSV", {
    class: "btn",
    id: "btn-office-payments-export",
    title: "Download this search as a CSV file (for accounting). Recorded in the audit log.",
    onclick: (e) => void exportCsv(e.currentTarget, filters),
  });

  render(
    root,
    h(
      "header",
      { class: "page-header" },
      h(
        "div",
        {},
        h("h2", {}, "Payments"),
        h(
          "p",
          { class: "muted small" },
          "Payment history of the whole office: every copay and self-pay hold placed when a visit is booked (charged when it is completed, released when it is canceled), the statements the office charged and the refunds — across every patient and payment method, including methods removed since.",
        ),
      ),
      h(
        "div",
        { class: "actions" },
        exportButton,
        button("Refresh", { class: "btn", id: "btn-office-payments-refresh", onclick: () => void load() }),
      ),
    ),
    h(
      "div",
      { class: "chips", role: "group", "aria-label": "Period" },
      quick("Last 7 days", "7d"),
      quick("Last 30 days", "30d"),
      quick("This month", "month"),
      quick("All time", "all"),
    ),
    h(
      "form",
      {
        class: "agenda-filters",
        id: "office-payments-filters",
        onsubmit: (e) => {
          e.preventDefault();
          apply();
        },
      },
      field("From", since),
      field("To", until),
      field("Kind", kind),
      field("Search", search),
    ),
  );
  const body = h("div", { id: "office-payments-body" }, spinner());
  root.append(body);
  await load();

  async function load() {
    render(body, spinner());
    let page;
    try {
      page = normalizeOfficePage(await api.get(`/patient-payments?${officeQuery(filters)}`));
    } catch (e) {
      render(
        body,
        callout(
          "error",
          "Could not load the payments",
          h("span", { id: "office-payments-error" }, describeError(e)),
          button("Try again", { class: "btn", id: "btn-office-payments-retry", onclick: () => void load() }),
        ),
      );
      return;
    }
    const t = page.totals;
    const info = pageInfo(page);
    render(
      body,
      h(
        "div",
        { class: "pay-totals", id: "office-payments-totals" },
        total("Charged", money(t.charged_cents, t.currency), "office-total-charged", "Captured, net of refunds"),
        total("On hold", money(t.held_cents, t.currency), "office-total-held", "Authorized, not charged yet"),
        total("Refunded", money(t.refunded_cents, t.currency), "office-total-refunded", "Given back to patients"),
        total("Payments", String(t.count), "office-total-count", "Every movement in this search"),
      ),
      h(
        "div",
        { class: "chips", id: "office-payments-groups", role: "tablist", "aria-label": "Show" },
        OFFICE_FILTERS.map(([key, label]) =>
          button([label, h("span", { class: "count" }, String(page.counts[key]))], {
            class: `btn chip ${filters.show === key ? "active" : ""}`,
            id: `office-payments-filter-${key}`,
            role: "tab",
            "aria-selected": String(filters.show === key),
            onclick: () => go({ ...filters, show: key, offset: 0 }),
          }),
        ),
      ),
      h(
        "p",
        { class: "muted small", id: "office-payments-count" },
        page.total ? `Showing ${info.from}–${info.to} of ${page.total} payment${page.total === 1 ? "" : "s"}` : "",
      ),
      page.items.length
        ? h(
            "ul",
            { class: "rows", id: "office-payments-list" },
            page.items.map((e) => entryRow(e)),
          )
        : h(
            "p",
            { class: "empty", id: "office-payments-empty" },
            page.counts.all ? "No payment of this kind in this search." : "No payments match this search.",
          ),
      page.total > page.limit
        ? h(
            "div",
            { class: "actions pager", id: "office-payments-pager" },
            button("← Newer", {
              class: "btn",
              id: "btn-office-payments-prev",
              disabled: !info.hasPrev,
              onclick: () => go({ ...filters, offset: info.prevOffset }),
            }),
            h("span", { class: "spacer" }),
            button("Older →", {
              class: "btn",
              id: "btn-office-payments-next",
              disabled: !info.hasNext,
              onclick: () => go({ ...filters, offset: info.nextOffset }),
            }),
          )
        : null,
      h(
        "p",
        { class: "muted small" },
        "Validations of payment methods (the small authorizations released at once) are not money movements; each record's own payment history lists them.",
      ),
    );
  }
}

function total(label, value, id, title) {
  return h(
    "div",
    { class: "pay-total", title },
    h("span", { class: "muted small" }, label),
    h("strong", { id }, value),
  );
}

function entryRow(e) {
  const [label, kind] = statusBadge(e);
  const patient = e.patient;
  const manage = patient ? `#/patients/${patient.id}/payments` : null;
  return h(
    "li",
    {
      class: `row payment-entry ${e.entry}`,
      id: `office-${e.id}`,
      dataset: { entry: e.entry, status: e.status, kind: e.kind },
    },
    h(
      "div",
      { class: "row-main" },
      h("strong", {}, e.description || KIND[e.kind] || e.kind),
      badge(label, kind),
      h(
        "span",
        { class: "payment-amount mono" },
        amountText(e, money),
        e.refunded_cents
          ? h("span", { class: "muted small" }, ` −${money(e.refunded_cents, e.currency)} refunded`)
          : null,
      ),
    ),
    h(
      "div",
      { class: "row-meta muted small" },
      e.at ? h("span", {}, formatDateTime(e.at)) : null,
      patient
        ? h(
            "a",
            {
              href: `#/patients/${patient.id}`,
              class: "office-payment-patient",
              title: "Open the patient record",
            },
            `${patient.first_name} ${patient.last_name} · ${patient.mrn}`,
          )
        : h("span", {}, "(record removed)"),
      h("span", {}, KIND[e.kind] || e.kind),
      h("span", { class: "mono" }, e.method.display),
      e.method.removed
        ? h(
            "span",
            {
              class: "badge muted payment-method-removed",
              title: "This payment method has since been removed from the account; the payment stays on record.",
            },
            "removed",
          )
        : null,
      e.appointment
        ? h(
            "span",
            { class: "office-payment-visit", title: "The visit this payment belongs to (see the patient record)" },
            `${e.appointment.number} · ${e.appointment.start_local} · ${e.appointment.provider}` +
              ` (${String(e.appointment.status || "").replace("_", " ")})`,
          )
        : null,
      h("span", {}, e.by === "staff" ? "by the office" : e.by === "patient" ? "by the patient" : "automatic"),
      e.provider === "fake" ? h("span", {}, "built-in fake") : null,
      e.reference ? h("span", {}, `ref ${e.reference}`) : null,
    ),
    e.message ? h("div", { class: "muted small" }, e.message) : null,
    manage
      ? h(
          "div",
          { class: "actions" },
          h(
            "a",
            {
              class: "btn small-btn",
              href: manage,
              id: `office-manage-${e.id}`,
              title: "The record's payment history: capture or release a hold, refund a charge, charge a statement",
            },
            "Manage →",
          ),
        )
      : null,
  );
}

/** The same search as CSV, downloaded with the administrator's token (an <a href> cannot send it). */
async function exportCsv(btn, filters) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Exporting…";
  try {
    const url = await api.blobUrl(`${api.base}/patient-payments/export.csv?${officeQuery(filters, { paged: false })}`);
    const a = h("a", { href: url, download: `patient-payments-${new Date().toISOString().slice(0, 10)}.csv` });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
    toast("CSV exported");
  } catch (e) {
    toast(describeError(e), { error: true });
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}
