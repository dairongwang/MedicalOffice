// Medical assistants on the agenda (js/format.js): `node --test apps/admin/tests` or `python dev.py test`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ASSISTANT_FILTERS, assistantBadge, assistantSummary, roleLabel } from "../js/format.js";

const visit = (assistant, status = "scheduled") => ({ status, timezone: "UTC", assistant });
const jamie = (status, extra = {}) => ({
    name: "Jamie Helper",
    status,
    assigned_at: "2027-06-07T08:00:00Z",
    accepted_at: "2027-06-07T08:30:00Z",
    completed_at: "2027-06-07T10:25:00Z",
    reported_at: "2027-06-07T08:40:00Z",
    ...extra,
});

test("the role and the filter", () => {
    assert.equal(roleLabel("medical_assistant"), "Medical assistant");
    assert.deepEqual(
        ASSISTANT_FILTERS.map(([v]) => v),
        ["", "none", "open", "reported", "completed"],
    );
});

test("where the visit's assistant stands", () => {
    assert.deepEqual(assistantSummary(visit(null)), { state: "none", text: "No medical assistant assigned", kind: "" });
    assert.equal(assistantSummary(visit(null, "canceled")).text, "");
    assert.equal(assistantSummary(visit(jamie("assigned"))).kind, "warn");
    assert.equal(
        assistantSummary(visit(jamie("accepted"))).text,
        "Jamie Helper took the task on Jun 7, 2027, 08:30 UTC",
    );
    assert.equal(assistantSummary(visit(jamie("completed"))).state, "completed");
    const reported = assistantSummary(
        visit(jamie("reported", { report_reason_label: "Sick / unwell", report_note: "Fever" })),
    );
    assert.equal(reported.kind, "bad");
    assert.equal(
        reported.text,
        "Jamie Helper cannot do it (Sick / unwell — “Fever”) on Jun 7, 2027, 08:40 UTC — assign someone else",
    );
});

test("the badge", () => {
    assert.equal(assistantBadge(visit(null)), "");
    assert.equal(assistantBadge(visit(jamie("accepted"))), "MA: Jamie Helper ✓");
    assert.equal(assistantBadge(visit(jamie("reported"))), "MA: Jamie Helper ✗");
});

test("the completed task shows the vital signs; review decisions read plainly", async () => {
    const { assistantSummary, reviewDecision, HEALTH_SOURCES } = await import("../js/format.js");
    const done = assistantSummary(visit(jamie("completed", { vitals: { text: "BP 120/80 mmHg · pulse 72/min" } })));
    assert.equal(
        done.text,
        "Jamie Helper completed the task on Jun 7, 2027, 10:25 UTC · BP 120/80 mmHg · pulse 72/min",
    );
    assert.deepEqual(reviewDecision({ decision: "removed" }), ["removed", "muted"]);
    assert.deepEqual(reviewDecision({ decision: null }), ["not cleared", "warn"]);
    assert.equal(HEALTH_SOURCES.check_in, "from the patient's check-in");
});
