// Check-in on the agenda (views/appointments.js → js/format.js): `node --test apps/admin/tests`.
//
// The viewer of these tests is in New York (UTC−5 in November) while the office runs on UTC —
// the situation in which the agenda once mixed the two zones in one sentence ("sent … 11:40 AM —
// … until 17:40"). Every agenda time must be in the office's zone, with the zone named.
process.env.TZ = "America/New_York";

import assert from "node:assert/strict";
import { test } from "node:test";

import {
    CHECK_IN_VIA,
    answerText,
    checkInSummary,
    officeDateTime,
    officeTime,
    staffNotifiedText,
} from "../js/format.js";

const VISIT = {
    status: "scheduled",
    timezone: "UTC",
    checked_in_at: null,
    checked_in_via: null,
    check_in_opens_at: "2027-06-06T09:30:00Z", // 24 hours before
    check_in_closes_at: "2027-06-07T10:00:00Z", // the end of the visit
    check_in: null,
};
const ATTEMPT = {
    id: 3,
    status: "pending",
    via: "staff",
    started_at: "2027-06-07T08:00:00Z",
    expires_at: "2027-06-07T09:00:00Z",
    link_channels: ["email"],
    link_sent_to: ["email:p•••@example.com"],
    link_sent_at: "2027-06-07T08:00:00Z",
    failure_reason: null,
};
const at = (iso) => new Date(iso);

test("times are in the office's zone and name it — whatever the viewer's zone", () => {
    assert.equal(new Date("2026-11-05T16:40:00Z").getHours(), 11); // the viewer: New York
    assert.equal(officeDateTime("2026-11-05T16:40:00Z", "UTC"), "Nov 5, 2026, 16:40 UTC");
    assert.equal(officeTime("2026-11-05T17:40:00Z", "UTC"), "17:40 UTC");
    assert.equal(officeDateTime("2026-11-05T16:40:00Z", "America/New_York"), "Nov 5, 2026, 11:40 EST");
    assert.equal(officeTime("2026-07-05T16:40:00Z", "Europe/Berlin"), "18:40 GMT+2");
    assert.equal(officeDateTime("", "UTC"), "");
});

test("regression: a link sent at 16:40 UTC reads '16:40 UTC … until 17:40 UTC (60 minutes)'", () => {
    const visit = {
        ...VISIT,
        check_in: {
            ...ATTEMPT,
            link_sent_at: "2026-11-05T16:40:00Z",
            started_at: "2026-11-05T16:40:00Z",
            expires_at: "2026-11-05T17:40:00Z",
            link_sent_to: ["email:d•••@example.com", "sms:+•••••••0301"],
        },
    };
    assert.equal(
        checkInSummary(visit, at("2026-11-05T17:00:00Z")).text,
        "Check-in link sent by e-mail d•••@example.com, text +•••••••0301 on Nov 5, 2026, 16:40 UTC — " +
            "waiting for the patient's answers and signatures until 17:40 UTC (60 minutes)",
    );
    // An office in New York: both times in EST, still one hour apart.
    assert.match(
        checkInSummary({ ...visit, timezone: "America/New_York" }, at("2026-11-05T17:00:00Z")).text,
        /on Nov 5, 2026, 11:40 EST — .* until 12:40 EST \(60 minutes\)$/,
    );
});

test("the patient's own window, before any check-in", () => {
    assert.deepEqual(checkInSummary(VISIT, at("2027-06-05T12:00:00Z")), {
        state: "not-yet",
        text: "Not checked in — the patient can check in from the app from Jun 6, 2027, 09:30 UTC",
    });
    assert.equal(checkInSummary(VISIT, at("2027-06-06T09:30:00Z")).state, "open");
    const over = checkInSummary(VISIT, at("2027-06-07T10:00:00Z"));
    assert.equal(over.state, "closed");
    assert.match(over.text, /send the check-in link/);
});

test("a link the office sent: waiting for the patient until it expires", () => {
    const s = checkInSummary({ ...VISIT, check_in: ATTEMPT }, at("2027-06-07T08:30:00Z"));
    assert.equal(s.state, "link-sent");
    assert.equal(
        s.text,
        "Check-in link sent by e-mail p•••@example.com on Jun 7, 2027, 08:00 UTC — waiting for the patient's answers and signatures until 09:00 UTC (60 minutes)",
    );
    const filling = checkInSummary({ ...VISIT, check_in: { ...ATTEMPT, via: "account" } }, at("2027-06-07T08:30:00Z"));
    assert.deepEqual(filling, {
        state: "filling-in",
        text: "The patient opened the check-in page in the app (until 09:00 UTC (60 minutes))",
    });
    // Past its hour without an answer → failed (the server records it within a minute).
    assert.equal(checkInSummary({ ...VISIT, check_in: ATTEMPT }, at("2027-06-07T09:01:00Z")).state, "failed");
});

test("a failed check-in says why", () => {
    const failed = { ...ATTEMPT, status: "failed", failure_reason: "No validated payment method on file." };
    assert.deepEqual(checkInSummary({ ...VISIT, check_in: failed }, at("2027-06-07T08:30:00Z")), {
        state: "failed",
        text: "Check-in failed: No validated payment method on file.",
    });
});

test("checked in — how and when (office time)", () => {
    for (const [via, label] of Object.entries(CHECK_IN_VIA)) {
        const s = checkInSummary(
            { ...VISIT, checked_in_at: "2027-06-07T09:05:00Z", checked_in_via: via },
            at("2027-06-08T00:00:00Z"),
        );
        assert.equal(s.state, "checked-in");
        assert.equal(
            s.text,
            `Checked in ${label} on Jun 7, 2027, 09:05 UTC — questionnaire and financial responsibility signed`,
        );
    }
    assert.equal(CHECK_IN_VIA.staff, "with the link the front desk sent");
});

test("the provider's notification after the check-in", () => {
    const done = {
        ...ATTEMPT,
        status: "completed",
        staff_name: "Dr. Alex Example",
        staff_notified_at: "2027-06-07T09:05:00Z",
        staff_notified_to: ["email:d•••@example.com"],
    };
    assert.equal(
        staffNotifiedText(done, "UTC"),
        "Dr. Alex Example notified by e-mail d•••@example.com on Jun 7, 2027, 09:05 UTC",
    );
    assert.equal(
        staffNotifiedText(
            { ...done, staff_notified_at: null, staff_notify_note: "E-mail: not set up on this server" },
            "UTC",
        ),
        "Dr. Alex Example could not be notified — E-mail: not set up on this server",
    );
    assert.equal(staffNotifiedText(ATTEMPT, "UTC"), ""); // not before completion
    assert.equal(staffNotifiedText(null, "UTC"), "");
});

test("nothing to say for visits that are not scheduled", () => {
    for (const status of ["canceled", "completed", "no_show"]) {
        assert.deepEqual(checkInSummary({ ...VISIT, status }, at("2027-06-07T09:00:00Z")), { state: "none", text: "" });
    }
    assert.equal(
        checkInSummary({ ...VISIT, status: "completed", checked_in_at: "2027-06-07T09:05:00Z", checked_in_via: "link" })
            .state,
        "checked-in",
    );
});

test("answers of the questionnaire as text", () => {
    const yesno = {
        kind: "yesno",
        options: [
            { value: "yes", label: "Yes" },
            { value: "no", label: "No" },
        ],
    };
    assert.equal(answerText(yesno, "no"), "No");
    assert.equal(answerText({ kind: "scale" }, 3), "3 / 10");
    assert.equal(answerText({ kind: "consent" }, true), "Yes — consented");
    assert.equal(
        answerText({ kind: "choice", options: [{ value: "days", label: "Less than a week" }] }, "days"),
        "Less than a week",
    );
    assert.equal(answerText({ kind: "text" }, "none"), "none");
    assert.equal(answerText({ kind: "text" }, ""), "—");
});
