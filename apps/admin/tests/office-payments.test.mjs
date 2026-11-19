// Logic of the office-wide Payments tab (views/payments.js → js/payment-ledger.js):
// `node --test apps/admin/tests` or `python dev.py test`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
    OFFICE_FILTERS,
    normalizeOfficePage,
    officeFilters,
    officeQuery,
    pageInfo,
    quickRange,
} from "../js/payment-ledger.js";

const ENTRY = {
    id: "payment-7",
    at: "2027-06-03T10:00:00Z",
    entry: "payment",
    kind: "copay",
    status: "captured",
    amount_cents: 4000,
    refunded_cents: 0,
    currency: "USD",
    description: "Copay · visit with Alex Example",
    message: "40.00 USD charged to Visa •••• 4242 · 12/2031.",
    method: { id: 1, kind: "card", display: "Visa •••• 4242 · 12/2031", removed: false },
    appointment: null,
    patient: { id: 1, mrn: "P-000001", first_name: "Pat", last_name: "Dummy" },
    reference: "ch_fake_dummy0001",
    provider: "fake",
    by: "system",
    payment: { id: 7, status: "captured", amount_cents: 4000, refunded_cents: 0, currency: "USD" },
};

test("the groups of the office ledger (no validations: those stay per record)", () => {
    assert.deepEqual(
        OFFICE_FILTERS.map(([key]) => key),
        ["all", "charges", "holds", "refunds", "released", "declined"],
    );
});

test("route parameters are cleaned: unknown groups / kinds / dates fall back to the defaults", () => {
    assert.deepEqual(officeFilters({}), { q: "", show: "all", kind: "", since: "", until: "", offset: 0 });
    assert.deepEqual(
        officeFilters({
            q: "  Pat ",
            show: "holds",
            kind: "statement",
            since: "2027-06-01",
            until: "2027-06-30",
            offset: "50",
        }),
        { q: "Pat", show: "holds", kind: "statement", since: "2027-06-01", until: "2027-06-30", offset: 50 },
    );
    assert.deepEqual(
        officeFilters({ show: "validations", kind: "subscription", since: "June", until: "2027-6-1", offset: "-3" }),
        { q: "", show: "all", kind: "", since: "", until: "", offset: 0 },
    );
    assert.deepEqual(officeFilters(null), officeFilters({}));
});

test("the API query leaves the defaults out; the export is not paged", () => {
    assert.equal(officeQuery({}), "limit=50");
    assert.equal(
        officeQuery({ q: "P-000001", show: "refunds", since: "2027-06-01", offset: 100 }),
        "q=P-000001&show=refunds&since=2027-06-01&offset=100&limit=50",
    );
    assert.equal(
        officeQuery({ q: "lab work", kind: "statement", offset: 50 }, { paged: false }),
        "q=lab+work&kind=statement",
    );
});

test("a page is normalised: totals, counts and entries are always complete", () => {
    const page = normalizeOfficePage({
        items: [ENTRY, null],
        total: 12,
        offset: 0,
        limit: 50,
        totals: { currency: "USD", charged_cents: 5550, refunded_cents: 1000, held_cents: 4000, count: 12 },
        counts: { all: 12, charges: 5, holds: 2, refunds: 1, released: 3, declined: 1 },
    });
    assert.equal(page.items.length, 2);
    assert.equal(page.items[0].patient.mrn, "P-000001");
    assert.equal(page.items[1].method.display, "(payment method)"); // a broken line renders, never throws
    assert.equal(page.totals.charged_cents, 5550);
    assert.deepEqual(page.counts, { all: 12, charges: 5, holds: 2, refunds: 1, released: 3, declined: 1 });

    const empty = normalizeOfficePage(undefined);
    assert.deepEqual(empty.items, []);
    assert.equal(empty.total, 0);
    assert.equal(empty.limit, 50);
    assert.deepEqual(empty.totals, { currency: "USD", charged_cents: 0, refunded_cents: 0, held_cents: 0, count: 0 });
    assert.deepEqual(empty.counts, { all: 0, charges: 0, holds: 0, refunds: 0, released: 0, declined: 0 });
});

test("the pager", () => {
    const items = (n) => Array.from({ length: n }, () => ENTRY);
    assert.deepEqual(pageInfo({ offset: 0, limit: 50, total: 120, items: items(50) }), {
        from: 1,
        to: 50,
        hasPrev: false,
        hasNext: true,
        prevOffset: 0,
        nextOffset: 50,
    });
    const last = pageInfo({ offset: 100, limit: 50, total: 120, items: items(20) });
    assert.equal(last.from, 101);
    assert.equal(last.to, 120);
    assert.equal(last.hasNext, false);
    assert.equal(last.prevOffset, 50);
    assert.equal(pageInfo({ offset: 0, limit: 50, total: 0, items: [] }).from, 0);
});

test("quick periods are office-local days", () => {
    const today = new Date(2027, 5, 15, 23, 30); // 15 June 2027, late evening
    assert.deepEqual(quickRange("7d", today), { since: "2027-06-09", until: "2027-06-15" });
    assert.deepEqual(quickRange("30d", today), { since: "2027-05-17", until: "2027-06-15" });
    assert.deepEqual(quickRange("month", today), { since: "2027-06-01", until: "2027-06-15" });
    assert.deepEqual(quickRange("all", today), { since: "", until: "" });
    assert.deepEqual(quickRange("7d", new Date(2027, 0, 3)), { since: "2026-12-28", until: "2027-01-03" });
});
