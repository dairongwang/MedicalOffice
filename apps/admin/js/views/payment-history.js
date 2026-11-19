import { api } from "../api.js";
import { money } from "../payment.js";
import {
  FILTERS,
  KIND,
  actionsFor,
  amountText,
  emptyTotals,
  filterCounts,
  matches,
  normalizeHistory,
  normalizeMethods,
  parseAmountCents,
  refundableCents,
  statusBadge,
  validateCharge,
  validateRefund,
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
 * "Payment history" of one patient record (administrators, #/patients/<id>/payments): every
 * hold, charge and refund on the record across all the payment methods of the patient's
 * account — plus the validations of those methods — newest first, with totals and filters.
 *
 * The history is **independent of a payment method's lifetime**: every row carries the masked
 * description of the method it was made with, so lines of a card / wallet / bank account that
 * was removed since stay, marked "removed".
 *
 * Money moves here too: **Charge…** (a statement or fee against an active method, charged at
 * once), **Capture** / **Release** a hold (normally done when the office marks the visit
 * completed / canceled), **Refund…** (part of) a charge. Every action is audited.
 *
 * What the API answered is normalised before rendering (payment-ledger.js): a half-loaded or
 * unexpected payload shows an empty history or the error, never a broken page.
 */
export function paymentHistorySection(p) {
  const section = h(
    "section",
    { class: "card", id: "patient-payment-history" },
    h("h3", {}, "Payment history"),
    spinner(),
  );
  const base = `/patients/${p.id}/payments`;
  let data = normalizeHistory(null);
  let account = normalizeMethods(null);
  let loadError = null;
  let methodsError = null;
  let filter = "all";

  const load = async () => {
    try {
      const [history, methods] = await Promise.allSettled([
        api.get(base),
        api.get(`/patients/${p.id}/payment-methods`),
      ]);
      loadError = history.status === "rejected" ? describeError(history.reason) : null;
      data = normalizeHistory(history.status === "fulfilled" ? history.value : null);
      // The methods are only needed for "Charge…": a failure there must not hide the history.
      methodsError = methods.status === "rejected" ? describeError(methods.reason) : null;
      account = normalizeMethods(methods.status === "fulfilled" ? methods.value : null);
      show();
    } catch (e) {
      // Never leave the section on "Loading…": whatever went wrong is shown with a way back.
      render(
        section,
        h("h3", {}, "Payment history"),
        callout(
          "error",
          "Could not show the payment history",
          h("span", { id: "payments-error" }, describeError(e)),
          button("Try again", { class: "btn", id: "btn-payment-retry", onclick: () => void load() }),
        ),
      );
    }
  };

  const show = (form = null) => {
    const t = data.totals || emptyTotals();
    const counts = filterCounts(data.entries);
    render(
      section,
      h(
        "div",
        { class: "card-head" },
        h("h3", {}, "Payment history ", h("span", { class: "muted small" }, `· ${data.entries.length} entries`)),
        h(
          "div",
          { class: "actions" },
          button("Charge…", {
            class: "btn primary",
            id: "btn-payment-charge",
            title: account.active.length
              ? "Charge the patient now against one of the validated payment methods (a statement, a fee)"
              : "No validated payment method to charge — see Payment methods",
            disabled: !account.active.length || !!form || !!loadError,
            onclick: () => show(chargeForm()),
          }),
          button("Refresh", { class: "btn", id: "btn-payment-refresh", onclick: () => void load() }),
        ),
      ),
      h(
        "p",
        { class: "muted small" },
        "Every hold, charge and refund on this record across all the payment methods of the patient's account, and the validations of those methods — including methods removed since (marked “removed”: the history is independent of a method's lifetime). A hold is placed when a visit is booked (the copay the coverage check named, or the self-pay fee), charged when the visit is marked completed and released when it is canceled.",
      ),
      loadError
        ? callout(
            "error",
            "Could not load the payment history",
            h("span", { id: "payments-error" }, loadError),
            button("Try again", { class: "btn", id: "btn-payment-retry", onclick: () => void load() }),
          )
        : null,
      methodsError && !loadError
        ? callout("warn", "Could not load the payment methods", h("span", {}, methodsError))
        : null,
      h(
        "div",
        { class: "pay-totals", id: "payment-totals" },
        total("Charged", money(t.charged_cents, t.currency), "payment-total-charged"),
        total("On hold", money(t.held_cents, t.currency), "payment-total-held"),
        total("Refunded", money(t.refunded_cents, t.currency), "payment-total-refunded"),
        total("Payments", String(t.count), "payment-total-count"),
      ),
      form,
      h(
        "div",
        { class: "chips", id: "payment-filters", role: "tablist" },
        FILTERS.map(([key, label]) =>
          button(`${label} `, {
            class: `btn chip ${filter === key ? "active" : ""}`,
            id: `payment-filter-${key}`,
            role: "tab",
            "aria-selected": String(filter === key),
            onclick: () => {
              filter = key;
              show(form);
            },
          }),
        ),
      ),
      list(),
      !data.entries.length && !account.hasAccount && !loadError
        ? h(
            "p",
            { class: "muted small", id: "payments-no-account" },
            "This patient has no patient account yet — payment methods and payments live on the account.",
          )
        : null,
    );
    for (const [key] of FILTERS) {
      section.querySelector(`#payment-filter-${key}`).append(h("span", { class: "count" }, String(counts[key])));
    }
  };

  const total = (label, value, id) =>
    h("div", { class: "pay-total" }, h("span", { class: "muted small" }, label), h("strong", { id }, value));

  const list = () => {
    const rows = data.entries.filter((e) => matches(e, filter));
    if (!rows.length) {
      return h(
        "p",
        { class: "empty", id: "payments-empty" },
        loadError ? "Nothing to show." : data.entries.length ? "Nothing of this kind." : "No payments yet.",
      );
    }
    return h(
      "ul",
      { class: "rows", id: "payments-list" },
      rows.map((e) => entryRow(e)),
    );
  };

  const entryRow = (e) => {
    const [label, kind] = statusBadge(e);
    const pay = e.payment;
    const actions = actionsFor(e);
    const act = async (btn, working, fn) => {
      const text = btn.textContent;
      btn.disabled = true;
      btn.textContent = working;
      try {
        await fn();
        await load();
      } catch (err) {
        toast(describeError(err), { error: true });
        btn.disabled = false;
        btn.textContent = text;
      }
    };
    return h(
      "li",
      {
        class: `row payment-entry ${e.entry}`,
        id: `payment-entry-${e.id}`,
        dataset: { entry: e.entry, status: e.status, kind: e.kind },
      },
      h(
        "div",
        { class: "row-main" },
        h("strong", {}, e.description),
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
        h("span", {}, KIND[e.kind] || e.kind),
        h("span", { class: "mono" }, e.method.display),
        e.method.removed
          ? h(
              "span",
              {
                class: "badge muted payment-method-removed",
                title:
                  "This payment method has since been removed from the account; the payment stays on record.",
              },
              "removed",
            )
          : null,
        e.appointment
          ? h(
              "a",
              { href: `#/appointments?patient=${p.id}`, title: "This visit on the agenda" },
              `${e.appointment.number} · ${e.appointment.start_local} · ${e.appointment.provider}` +
                ` (${String(e.appointment.status || "").replace("_", " ")})`,
            )
          : null,
        h("span", {}, e.by === "staff" ? "by the office" : e.by === "patient" ? "by the patient" : "automatic"),
        e.provider === "fake" ? h("span", {}, "built-in fake") : null,
        e.reference ? h("span", {}, `ref ${e.reference}`) : null,
      ),
      e.message ? h("div", { class: "muted small" }, e.message) : null,
      actions.length
        ? h(
            "div",
            { class: "actions" },
            actions.includes("capture")
              ? button("Capture", {
                  class: "btn",
                  id: `btn-payment-capture-${pay.id}`,
                  title: "Charge this hold now (normally done when the visit is marked completed)",
                  onclick: (ev) =>
                    act(ev.currentTarget, "Charging…", async () => {
                      const r = await api.post(`${base}/${pay.id}/capture`);
                      toast(r.message);
                    }),
                })
              : null,
            actions.includes("release")
              ? button("Release", {
                  class: "btn",
                  id: `btn-payment-release-${pay.id}`,
                  title: "Let this hold go without charging it",
                  onclick: (ev) =>
                    act(ev.currentTarget, "Releasing…", async () => {
                      const r = await api.post(`${base}/${pay.id}/release`);
                      toast(r.message);
                    }),
                })
              : null,
            actions.includes("refund")
              ? button("Refund…", {
                  class: "btn",
                  id: `btn-payment-refund-${pay.id}`,
                  title: "Give (part of) this payment back to the method it came from",
                  onclick: () => show(refundForm(pay)),
                })
              : null,
          )
        : null,
    );
  };

  /** The office charges the patient now. */
  const chargeForm = () => {
    const active = account.active;
    const msg = h("div", { class: "form-msg" });
    const method = select(
      active.map((m) => [
        String(m.id),
        `${m.label ? m.label + " — " : ""}${m.display}${m.is_default ? " (default)" : ""}`,
      ]),
      { name: "method_id", id: "charge-method", value: String((active.find((m) => m.is_default) || active[0]).id) },
    );
    const amount = input({
      name: "amount",
      id: "charge-amount",
      inputmode: "decimal",
      placeholder: "25.00",
      autocomplete: "off",
    });
    const description = input({
      name: "description",
      id: "charge-description",
      maxlength: 200,
      placeholder: "Statement June 2027",
    });
    const kind = select(
      [
        ["statement", "Statement"],
        ["other", "Other charge"],
      ],
      { name: "kind", id: "charge-kind", value: "statement" },
    );
    const save = button("Charge now", { type: "submit", class: "btn primary", id: "btn-charge-save" });
    const form = h(
      "form",
      { class: "form compact payment-form", id: "charge-form", novalidate: true },
      h("h4", {}, "Charge the patient"),
      h(
        "p",
        { class: "muted small" },
        "Charged at once against the chosen payment method (the vault's token — nothing is stored here). The patient sees it in their payment history.",
      ),
      msg,
      h("div", { class: "field-row" }, field("Payment method", method), field("Kind", kind)),
      h("div", { class: "field-row" }, field(`Amount (${data.totals.currency})`, amount), field("Description", description)),
      h(
        "div",
        { class: "actions" },
        button("Cancel", { class: "btn", id: "btn-charge-cancel", onclick: () => show() }),
        h("span", { class: "spacer" }),
        save,
      ),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      msg.replaceChildren();
      const problem = validateCharge({
        methodId: method.value,
        amount: amount.value,
        description: description.value,
      });
      if (problem) {
        msg.append(callout("error", "Please check the form", h("span", {}, problem)));
        return;
      }
      save.disabled = true;
      save.textContent = "Charging…";
      try {
        const r = await api.post(base, {
          method_id: Number(method.value),
          amount_cents: parseAmountCents(amount.value),
          description: description.value.trim(),
          kind: kind.value,
        });
        toast(r.message);
        await load();
      } catch (err) {
        msg.append(callout("error", "Could not charge", h("span", {}, describeError(err))));
        save.disabled = false;
        save.textContent = "Charge now";
      }
    });
    return form;
  };

  /** (Part of) a captured payment back to the method. */
  const refundForm = (pay) => {
    const msg = h("div", { class: "form-msg" });
    const left = refundableCents(pay);
    const amount = input({
      name: "amount",
      id: "refund-amount",
      inputmode: "decimal",
      value: (left / 100).toFixed(2),
      autocomplete: "off",
    });
    const reason = input({ name: "reason", id: "refund-reason", maxlength: 200, placeholder: "e.g. overpaid" });
    const save = button("Refund", { type: "submit", class: "btn primary", id: "btn-refund-save" });
    const form = h(
      "form",
      { class: "form compact payment-form", id: "refund-form", novalidate: true },
      h("h4", {}, `Refund ${pay.description}`),
      h(
        "p",
        { class: "muted small" },
        `${money(left, pay.currency)} of ${money(pay.amount_cents, pay.currency)} can still be refunded to ${pay.method?.display || "the payment method"}.`,
      ),
      msg,
      h("div", { class: "field-row" }, field(`Amount (${pay.currency})`, amount), field("Reason", reason, { optional: true })),
      h(
        "div",
        { class: "actions" },
        button("Cancel", { class: "btn", id: "btn-refund-cancel", onclick: () => show() }),
        h("span", { class: "spacer" }),
        save,
      ),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      msg.replaceChildren();
      const problem = validateRefund({ amount: amount.value, left, currency: pay.currency }, money);
      if (problem) {
        msg.append(callout("error", "Please check the amount", h("span", {}, problem)));
        return;
      }
      save.disabled = true;
      save.textContent = "Refunding…";
      try {
        const r = await api.post(`${base}/${pay.id}/refund`, {
          amount_cents: parseAmountCents(amount.value),
          reason: reason.value.trim() || null,
        });
        toast(r.message);
        await load();
      } catch (err) {
        msg.append(callout("error", "Could not refund", h("span", {}, describeError(err))));
        save.disabled = false;
        save.textContent = "Refund";
      }
    });
    return form;
  };

  void load();
  return section;
}
