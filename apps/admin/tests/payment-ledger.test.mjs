// Logic of the patient payment history (views/payment-history.js → js/payment-ledger.js):
// `node --test apps/admin/tests` or `python dev.py test`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { money } from "../js/payment.js";
import {
  FILTERS,
  actionsFor,
  amountText,
  filterCounts,
  matches,
  normalizeHistory,
  normalizeMethods,
  parseAmountCents,
  refundableCents,
  statusBadge,
  validateCharge,
  validateRefund,
} from "../js/payment-ledger.js";

const HOLD = {
  id: "payment-3",
  at: "2027-06-03T10:00:00Z",
  entry: "payment",
  kind: "copay",
  status: "authorized",
  amount_cents: 4000,
  refunded_cents: 0,
  currency: "USD",
  description: "Copay · visit with Alex Example",
  message: "40.00 USD on hold on Visa •••• 4242 · 12/2031.",
  method: { id: 1, kind: "card", display: "Visa •••• 4242 · 12/2031", removed: false },
  appointment: { id: 9, number: "APT-000009", start_local: "Mon · 09:30", provider: "Alex Example", status: "scheduled" },
  patient: { id: 1, mrn: "P-000001", first_name: "Pat", last_name: "Dummy" },
  reference: "auth_fake_1",
  provider: "fake",
  by: "patient",
  payment: { id: 3, status: "authorized", amount_cents: 4000, refunded_cents: 0, currency: "USD", description: "Copay" },
};
const CHARGE = {
  ...HOLD,
  id: "payment-2",
  kind: "statement",
  status: "partially_refunded",
  amount_cents: 2550,
  refunded_cents: 1000,
  description: "Statement June",
  appointment: null,
  payment: { id: 2, status: "partially_refunded", amount_cents: 2550, refunded_cents: 1000, currency: "USD" },
};
const RELEASED = { ...HOLD, id: "payment-1", kind: "self_pay", status: "released", amount_cents: 15000, payment: { id: 1, status: "released" } };
const VALIDATION = {
  ...HOLD,
  id: "validation-1",
  entry: "validation",
  kind: "validation",
  status: "authorized",
  amount_cents: 100,
  description: "Validation of Visa •••• 4242 · 12/2031",
  appointment: null,
  payment: null,
};
const HISTORY = {
  entries: [HOLD, CHARGE, RELEASED, VALIDATION],
  totals: { currency: "USD", charged_cents: 1550, refunded_cents: 1000, held_cents: 4000, count: 3 },
};

test("normalizes what the API answered", () => {
  const d = normalizeHistory(HISTORY);
  assert.equal(d.entries.length, 4);
  assert.deepEqual(d.totals, HISTORY.totals);
  assert.equal(d.entries[0].method.display, "Visa •••• 4242 · 12/2031");
  assert.equal(d.entries[0].method.removed, false);
});

test("never throws on a missing, partial or unexpected payload (the loading bug)", () => {
  for (const bad of [undefined, null, "", 0, [], "nope", { detail: "Not found" }]) {
    const d = normalizeHistory(bad);
    assert.deepEqual(d.entries, []);
    assert.equal(d.entries.length, 0); // what the view reads while rendering
    assert.deepEqual(d.totals, { currency: "USD", charged_cents: 0, refunded_cents: 0, held_cents: 0, count: 0 });
    assert.deepEqual(filterCounts(d.entries), { all: 0, charges: 0, holds: 0, refunds: 0, validations: 0 });
  }
  // A payment-methods payload (the shape the view crashed on) yields an empty history, not a throw.
  const swapped = normalizeHistory({ patient_id: 1, has_account: true, methods: [{ id: 1 }] });
  assert.equal(swapped.entries.length, 0);
  assert.equal(swapped.totals.count, 0);
  // Half-filled entries get every field the view touches.
  const partial = normalizeHistory({ entries: [{}, { entry: "validation" }, null], totals: { currency: "EUR" } });
  assert.equal(partial.entries.length, 3);
  assert.deepEqual(
    partial.entries.map((e) => [e.id, e.entry, e.currency, e.method.display, e.method.removed, e.amount_cents]),
    [
      ["entry-0", "payment", "EUR", "(payment method)", true, 0],
      ["entry-1", "validation", "EUR", "(payment method)", true, 0],
      ["entry-2", "payment", "EUR", "(payment method)", true, 0],
    ],
  );
  assert.equal(partial.totals.count, 2); // counted from the entries when the totals say nothing
});

test("normalizes the payment methods and finds the ones that can be charged", () => {
  assert.deepEqual(normalizeMethods(null), { hasAccount: false, methods: [], active: [] });
  assert.deepEqual(normalizeMethods({ has_account: false, methods: [] }).active, []);
  const a = normalizeMethods({
    has_account: true,
    methods: [{ id: 1, status: "active" }, { id: 2, status: "failed" }, null, { id: 3, status: "active" }],
  });
  assert.equal(a.hasAccount, true);
  assert.deepEqual(
    a.active.map((m) => m.id),
    [1, 3],
  );
});

test("filters and their counts", () => {
  const entries = normalizeHistory(HISTORY).entries;
  assert.deepEqual(filterCounts(entries), { all: 4, charges: 1, holds: 1, refunds: 1, validations: 1 });
  assert.deepEqual(
    entries.filter((e) => matches(e, "charges")).map((e) => e.id),
    ["payment-2"],
  );
  assert.deepEqual(
    entries.filter((e) => matches(e, "holds")).map((e) => e.id),
    ["payment-3"],
  );
  assert.deepEqual(
    entries.filter((e) => matches(e, "validations")).map((e) => e.id),
    ["validation-1"],
  );
  assert.deepEqual(FILTERS.map(([k]) => k), ["all", "charges", "holds", "refunds", "validations"]);
});

test("labels and amounts", () => {
  const [hold, charge, released, validation] = normalizeHistory(HISTORY).entries;
  assert.deepEqual(statusBadge(hold), ["On hold", "warn"]);
  assert.deepEqual(statusBadge(charge), ["Partly refunded", ""]);
  assert.deepEqual(statusBadge(validation), ["Validated", "ok"]);
  assert.deepEqual(statusBadge(normalizeHistory({ entries: [{ entry: "validation", status: "declined" }] }).entries[0]), [
    "Declined",
    "bad",
  ]);
  assert.equal(amountText(hold, money), "$40.00");
  assert.equal(amountText(released, money), "($150.00)"); // released: no money changed hands
  assert.equal(amountText(validation, money), "($1.00)");
});

test("the actions a line offers", () => {
  const [hold, charge, released, validation] = normalizeHistory(HISTORY).entries;
  assert.deepEqual(actionsFor(hold), ["capture", "release"]);
  assert.deepEqual(actionsFor(charge), ["refund"]);
  assert.deepEqual(actionsFor(released), []);
  assert.deepEqual(actionsFor(validation), []);
  assert.equal(refundableCents(charge.payment), 1550);
  assert.equal(refundableCents(undefined), 0);
});

test("payments of a removed method keep their description and are marked removed", () => {
  const d = normalizeHistory({
    entries: [
      { ...CHARGE, method: { id: null, kind: "card", display: "Visa •••• 4242 · 12/2031", removed: true } },
      { ...VALIDATION, method: { id: null, kind: "paypal", display: "PayPal · pa•••@example.com", removed: true } },
    ],
    totals: HISTORY.totals,
  });
  assert.deepEqual(
    d.entries.map((e) => [e.method.display, e.method.removed]),
    [
      ["Visa •••• 4242 · 12/2031", true],
      ["PayPal · pa•••@example.com", true],
    ],
  );
  assert.deepEqual(actionsFor(d.entries[0]), ["refund"]); // still refundable to the removed card
});

test("amounts typed into the charge / refund forms", () => {
  assert.equal(parseAmountCents("25.50"), 2550);
  assert.equal(parseAmountCents(" $25,50 "), 2550);
  assert.equal(parseAmountCents("25"), 2500);
  assert.equal(parseAmountCents("0.07"), 7);
  assert.equal(parseAmountCents(""), null);
  assert.equal(parseAmountCents("abc"), null);
  assert.equal(parseAmountCents(undefined), null);

  assert.equal(validateCharge({ methodId: "1", amount: "25.50", description: "Statement" }), null);
  assert.match(validateCharge({ methodId: "", amount: "1", description: "x" }), /payment method/);
  assert.match(validateCharge({ methodId: "1", amount: "", description: "x" }), /amount/);
  assert.match(validateCharge({ methodId: "1", amount: "0", description: "x" }), /amount/);
  assert.match(validateCharge({ methodId: "1", amount: "99999999", description: "x" }), /too large/);
  assert.match(validateCharge({ methodId: "1", amount: "10", description: "  " }), /what the charge is for/);

  assert.equal(validateRefund({ amount: "5.00", left: 1550, currency: "USD" }, money), null);
  assert.equal(validateRefund({ amount: "15.50", left: 1550, currency: "USD" }, money), null);
  assert.match(validateRefund({ amount: "20.00", left: 1550, currency: "USD" }, money), /Only \$15\.50 is left/);
  assert.match(validateRefund({ amount: "", left: 1550 }, money), /amount/);
});
