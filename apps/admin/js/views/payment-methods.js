import { api } from "../api.js";
import { KINDS, KIND_LABELS, RESULT, STATUS, cardBrand, money, toBody, validateForm } from "../payment.js";
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
 * "Payment methods" on a patient's detail page (administrators): the cards, Stripe / PayPal
 * wallets and bank accounts on the patient's *account*, masked the way the patient sees them
 * (the API keeps only the vault's token — the detail itself is never stored or shown).
 *
 * Each method carries its validation state: a small amount (PAYMENT_VALIDATION_AMOUNT_CENTS)
 * is authorized against the token and released at once when a method is added or its detail
 * changes; the method is usable ("Active") only after that succeeded. The office can
 * **Validate** any method again at any time, add one on the patient's behalf (read out at the
 * desk), edit, make default (active methods only) and remove. Every action is audited.
 */
export function paymentMethodsSection(p) {
  const section = h("section", { class: "card", id: "patient-payments" }, h("h3", {}, "Payment methods"), spinner());
  const base = `/patients/${p.id}/payment-methods`;
  let data = null;

  const load = async () => {
    try {
      data = await api.get(base);
    } catch (e) {
      render(section, h("h3", {}, "Payment methods"), callout("error", "Could not load", h("span", {}, describeError(e))));
      return;
    }
    show();
  };

  const show = (form = null) => {
    const head = h(
      "div",
      { class: "card-head" },
      h(
        "h3",
        {},
        "Payment methods ",
        h("span", { class: "muted small" }, `· ${data.methods.length}`),
      ),
      data.has_account
        ? h(
            "div",
            { class: "actions" },
            button("+ Add", {
              class: "btn primary",
              id: "btn-payment-add",
              title: "Add a card, Stripe / PayPal account or bank account on the patient's behalf",
              disabled: !!form,
              onclick: () => show(methodForm(null)),
            }),
          )
        : null,
    );
    const intro = h(
      "p",
      { class: "muted small" },
      "What the patient keeps on file to pay copays and self-pay visits. Everything is tokenized — only the last four digits / a masked e-mail address are kept. A method is usable once a small authorization (released at once, nothing charged) succeeded; validate it again at any time.",
    );
    if (!data.has_account) {
      render(
        section,
        head,
        intro,
        h(
          "p",
          { class: "empty", id: "payments-no-account" },
          "This patient has no patient account yet. Payment methods live on the account — once the patient creates one in the app (same e-mail) or signs in and attaches this record, they appear here.",
        ),
      );
      return;
    }
    render(
      section,
      head,
      intro,
      data.methods.length
        ? h(
            "ul",
            { class: "rows", id: "payments-list" },
            data.methods.map((m) => methodRow(m)),
          )
        : h("p", { class: "empty", id: "payments-empty" }, "No payment method on file."),
      form,
      data.methods.some((m) => m.provider === "fake")
        ? h(
            "p",
            { class: "muted small" },
            "Built-in fake vault (development): nothing left this machine and nothing is ever charged. It declines cards ending in 0002, bank accounts ending in 0000 and entries labelled “decline”.",
          )
        : null,
    );
    if (form) form.scrollIntoView({ block: "nearest" });
  };

  const methodRow = (m) => {
    const [statusLabel, statusKind] = STATUS[m.status] || [m.status, ""];
    const v = m.validation;
    const act = async (btn, working, fn) => {
      const label = btn.textContent;
      btn.disabled = true;
      btn.textContent = working;
      try {
        await fn();
      } catch (e) {
        toast(describeError(e), { error: true });
        btn.disabled = false;
        btn.textContent = label;
      }
    };
    return h(
      "li",
      { class: "row payment-method", id: `payment-${m.id}`, dataset: { status: m.status, kind: m.kind } },
      h(
        "div",
        { class: "row-main" },
        h("strong", {}, m.label || KIND_LABELS[m.kind]),
        m.is_default ? badge("Default", "ok") : null,
        m.expired ? badge("Expired", "bad") : null,
        badge(statusLabel, statusKind),
      ),
      h(
        "div",
        { class: "row-meta" },
        h("span", { class: "mono small" }, m.display),
        m.kind === "bank" ? h("span", { class: "muted small" }, `routing ${m.bank_routing_masked}`) : null,
        m.holder_name ? h("span", { class: "muted small" }, m.holder_name) : null,
        m.billing_postal_code ? h("span", { class: "muted small" }, `ZIP ${m.billing_postal_code}`) : null,
      ),
      h(
        "div",
        { class: "muted small payment-validation", id: `payment-${m.id}-validation` },
        v
          ? [
              `Last validation: ${(RESULT[v.result] || [v.result])[0].toLowerCase()} · ${money(v.amount_cents, v.currency)} · ` +
                `${formatDateTime(v.created_at)} · ${v.triggered_by === "staff" ? "by the office" : v.triggered_by === "patient" ? "by the patient" : "automatic"}` +
                (v.provider === "fake" ? " · built-in fake" : "") +
                (v.reference ? ` · ref ${v.reference}` : ""),
              v.message ? h("div", {}, v.message) : null,
            ]
          : "Not validated yet.",
      ),
      h(
        "div",
        { class: "actions" },
        button(m.status === "active" ? "Validate again" : "Validate", {
          class: "btn",
          id: `btn-payment-validate-${m.id}`,
          title: `Authorize a small amount against this method and release it at once — ${
            m.status === "active" ? "confirms it still works" : "makes it usable once it passes"
          }`,
          onclick: (e) =>
            act(e.currentTarget, "Validating…", async () => {
              const fresh = await api.post(`${base}/${m.id}/validate`);
              const r = fresh.validation;
              toast(`${fresh.display}: ${(RESULT[r.result] || [r.result])[0]} — ${r.message}`, {
                error: r.result !== "authorized",
              });
              await load();
            }),
        }),
        button("History", {
          class: "btn",
          id: `btn-payment-history-${m.id}`,
          title: "Every validation attempt of this method",
          onclick: () => void showHistory(m),
        }),
        !m.is_default && m.status === "active"
          ? button("Make default", {
              class: "btn",
              id: `btn-payment-default-${m.id}`,
              title: "The method the office charges first",
              onclick: (e) =>
                act(e.currentTarget, "Saving…", async () => {
                  data = await api.post(`${base}/${m.id}/default`);
                  toast(`${m.display} is now the default.`);
                  show();
                }),
            })
          : null,
        h("span", { class: "spacer" }),
        button("Edit", {
          class: "btn",
          id: `btn-payment-edit-${m.id}`,
          title: "Change the label, name, expiry, bank details — or replace the card / account / address",
          onclick: () => show(methodForm(m)),
        }),
        button("Remove", {
          class: "btn danger",
          id: `btn-payment-remove-${m.id}`,
          title: "Remove this payment method from the patient's account",
          onclick: (e) => {
            if (!confirm(`Remove ${m.display} from ${p.first_name} ${p.last_name}'s account?`)) return;
            void act(e.currentTarget, "Removing…", async () => {
              await api.delete(`${base}/${m.id}`);
              toast("Payment method removed.");
              await load();
            });
          },
        }),
      ),
    );
  };

  const showHistory = async (m) => {
    let rows;
    try {
      rows = await api.get(`${base}/${m.id}/validations`);
    } catch (e) {
      toast(describeError(e), { error: true });
      return;
    }
    const row = section.querySelector(`#payment-${m.id}`);
    const old = row.querySelector(".payment-history");
    if (old) old.remove();
    row.append(
      h(
        "div",
        { class: "payment-history" },
        h("h4", {}, `Validations (${rows.length})`),
        rows.length
          ? h(
              "ul",
              { class: "rows" },
              rows.map((v) => {
                const [label, kind] = RESULT[v.result] || [v.result, ""];
                return h(
                  "li",
                  { class: "row" },
                  h("div", { class: "row-main" }, badge(label, kind), h("span", { class: "small" }, v.message)),
                  h(
                    "div",
                    { class: "muted small" },
                    `${money(v.amount_cents, v.currency)} · ${formatDateTime(v.created_at)} · ${v.triggered_by} · ${v.provider}` +
                      (v.reference ? ` · ref ${v.reference}` : ""),
                  ),
                );
              }),
            )
          : h("p", { class: "empty" }, "No validation yet."),
      ),
    );
  };

  /** Add (editing = null) or update one method — the same form, fields by kind. */
  const methodForm = (editing) => {
    const msg = h("div", { class: "form-msg" });
    let kind = editing ? editing.kind : "card";
    const f = {};
    const text = (name, attrs = {}) => (f[name] = input({ name, id: `payment-${name.replace(/_/g, "-")}`, ...attrs }));
    text("label", { maxlength: 60, placeholder: "e.g. Family card", value: editing?.label ?? "" });
    text("holder_name", { maxlength: 120, autocomplete: "off", value: editing?.holder_name ?? "" });
    text("card_number", { inputmode: "numeric", autocomplete: "off", placeholder: "4242 4242 4242 4242" });
    text("card_cvc", { inputmode: "numeric", autocomplete: "off", maxlength: 4, placeholder: "123" });
    const now = new Date().getFullYear();
    f.card_exp_month = select(
      [["", "MM"], ...Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, "0"))],
      { name: "card_exp_month", id: "payment-card-exp-month", value: editing?.card_exp_month ? String(editing.card_exp_month).padStart(2, "0") : "" },
    );
    f.card_exp_year = select([["", "YYYY"], ...Array.from({ length: 16 }, (_, i) => String(now + i))], {
      name: "card_exp_year",
      id: "payment-card-exp-year",
      value: editing?.card_exp_year ? String(editing.card_exp_year) : "",
    });
    text("billing_postal_code", { maxlength: 20, value: editing?.billing_postal_code ?? "" });
    text("wallet_email", { type: "email", autocomplete: "off", placeholder: "  " });
    text("bank_name", { maxlength: 120, placeholder: "Example Bank", value: editing?.bank_name ?? "" });
    f.bank_account_type = select(
      [
        ["checking", "Checking"],
        ["savings", "Savings"],
      ],
      { name: "bank_account_type", id: "payment-bank-account-type", value: editing?.bank_account_type ?? "checking" },
    );
    text("bank_routing_number", { inputmode: "numeric", autocomplete: "off", placeholder: "9 digits (US)" });
    text("bank_account_number", { inputmode: "numeric", autocomplete: "off" });
    const isDefault = h("input", {
      type: "checkbox",
      name: "is_default",
      id: "payment-is-default",
      checked: editing ? editing.is_default : false,
      disabled: !!editing?.is_default,
    });
    const brand = h("span", { class: "badge muted", id: "payment-card-brand" });
    f.card_number.addEventListener("input", () => {
      const b = cardBrand(f.card_number.value);
      brand.textContent = b;
      brand.hidden = !b;
    });
    brand.hidden = true;

    const keep = (what) => (editing ? h("span", { class: "muted" }, ` (leave blank to keep ${what})`) : null);
    const fieldsFor = () => {
      switch (kind) {
        case "card":
          return [
            field("Name on the card", f.holder_name),
            h("div", { class: "field" }, h("label", { for: f.card_number.id }, "Card number", keep(`•••• ${editing?.card_last4}`)), h("div", { class: "row-meta" }, f.card_number, brand)),
            h(
              "div",
              { class: "field-row three" },
              field("Expiry month", f.card_exp_month),
              field("Expiry year", f.card_exp_year),
              field("Security code", f.card_cvc, { hint: "Checked, never stored." }),
            ),
            field("Billing postal code", f.billing_postal_code, { optional: true }),
          ];
        case "stripe":
        case "paypal":
          return [
            h(
              "div",
              { class: "field" },
              h("label", { for: f.wallet_email.id }, `E-mail address of the patient's ${KIND_LABELS[kind]} account`, keep(editing?.wallet_email_masked)),
              f.wallet_email,
            ),
            field("Name", f.holder_name, { optional: true }),
          ];
        case "bank":
          return [
            field("Account holder", f.holder_name),
            h("div", { class: "field-row" }, field("Bank", f.bank_name), field("Account type", f.bank_account_type)),
            h(
              "div",
              { class: "field-row" },
              h("div", { class: "field" }, h("label", { for: f.bank_routing_number.id }, "Routing number", editing ? h("span", { class: "muted" }, ` (on file: ${editing.bank_routing_masked})`) : null), f.bank_routing_number),
              h("div", { class: "field" }, h("label", { for: f.bank_account_number.id }, "Account number", keep(`•••• ${editing?.bank_account_last4}`)), f.bank_account_number),
            ),
          ];
        default:
          return [];
      }
    };
    const body = h("div", { id: "payment-form-fields" });
    const renderFields = () => render(body, fieldsFor());
    renderFields();

    const kinds = editing
      ? null
      : h(
          "div",
          { class: "chips", role: "radiogroup", "aria-label": "Kind of payment method", id: "payment-kinds" },
          KINDS.map((k) =>
            button(KIND_LABELS[k], {
              class: `btn chip ${k === kind ? "active" : ""}`,
              id: `payment-kind-${k}`,
              role: "radio",
              "aria-checked": String(k === kind),
              onclick: (e) => {
                kind = k;
                for (const c of e.currentTarget.parentElement.children) {
                  c.classList.toggle("active", c === e.currentTarget);
                  c.setAttribute("aria-checked", String(c === e.currentTarget));
                }
                renderFields();
              },
            }),
          ),
        );

    const save = button(editing ? "Save changes" : "Add payment method", {
      type: "submit",
      class: "btn primary",
      id: "btn-payment-save",
    });
    const cancel = button("Cancel", { class: "btn", id: "btn-payment-cancel", onclick: () => show() });
    const form = h(
      "form",
      { class: "form compact payment-form", id: "payment-form", novalidate: true },
      h("h4", {}, editing ? `Update ${editing.display}` : "Add a payment method"),
      h(
        "p",
        { class: "muted small" },
        editing
          ? "A new card number, e-mail address or account number replaces what is on file and is validated again; the kind cannot change."
          : "Tokenized on arrival and validated by a small authorization that is released at once — nothing is charged, nothing is stored in full.",
      ),
      kinds,
      msg,
      body,
      h(
        "div",
        { class: "field-row" },
        field("Label", f.label, { optional: true }),
        h("label", { class: "checkbox", for: isDefault.id }, isDefault, h("span", {}, "Default payment method")),
      ),
      h("div", { class: "actions" }, cancel, h("span", { class: "spacer" }), save),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      msg.replaceChildren();
      const draft = { kind, is_default: isDefault.checked };
      for (const [name, el] of Object.entries(f)) draft[name] = el.value;
      const problem = validateForm(draft, editing);
      if (problem) {
        msg.append(callout("error", "Please check the form", h("span", {}, problem)));
        return;
      }
      save.disabled = true;
      save.textContent = "Saving…";
      try {
        const saved = editing
          ? await api.put(`${base}/${editing.id}`, toBody(draft))
          : await api.post(base, toBody(draft));
        const r = saved.validation;
        toast(
          `${saved.display} saved — ${r ? `${(RESULT[r.result] || [r.result])[0].toLowerCase()}: ${r.message}` : "not validated"}`,
          { error: saved.status !== "active" },
        );
        await load();
      } catch (err) {
        msg.append(callout("error", "Could not save", h("span", {}, describeError(err))));
        save.disabled = false;
        save.textContent = editing ? "Save changes" : "Add payment method";
      }
    });
    return form;
  };

  void load();
  return section;
}
