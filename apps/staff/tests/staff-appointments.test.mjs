// "My appointments" logic (js/staff-appointments.js): `node --test apps/staff/tests` or `python dev.py test`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
    addDays,
    ageOn,
    answerText,
    checkInLine,
    dayIn,
    groupByDay,
    officeTime,
    quickPeriod,
    splitLocal,
} from "../js/staff-appointments.js";

test("times are shown in the office's zone, with the zone named", () => {
    assert.equal(officeTime("2027-06-07T14:05:00Z", "UTC"), "14:05 UTC");
    assert.equal(dayIn(new Date("2027-06-07T23:30:00Z"), "UTC"), "2027-06-07");
    assert.deepEqual(splitLocal("Monday, June 7, 2027 · 09:30"), { day: "Monday, June 7, 2027", time: "09:30" });
});

test("periods and grouping", () => {
    assert.equal(addDays("2027-06-30", 1), "2027-07-01");
    assert.deepEqual(quickPeriod("7d", "2027-06-07"), { from: "2027-06-07", to: "2027-06-13" });
    assert.deepEqual(quickPeriod("past", "2027-06-07"), { from: "2027-05-31", to: "2027-06-06" });
    const groups = groupByDay([
        { start_local: "Monday, June 7, 2027 · 09:00" },
        { start_local: "Monday, June 7, 2027 · 10:00" },
        { start_local: "Tuesday, June 8, 2027 · 09:00" },
    ]);
    assert.deepEqual(
        groups.map((g) => [g.day, g.items.length]),
        [
            ["Monday, June 7, 2027", 2],
            ["Tuesday, June 8, 2027", 1],
        ],
    );
    assert.equal(ageOn("1985-03-14", new Date(2027, 2, 13)), 41);
});

test("where the patient stands for check-in", () => {
    const now = new Date("2027-06-07T08:00:00Z");
    const base = {
        status: "scheduled",
        timezone: "UTC",
        check_in_opens_at: "2027-06-06T10:00:00Z",
        check_in_closes_at: "2027-06-07T10:30:00Z",
    };
    assert.equal(
        checkInLine({ ...base, checked_in_at: "2027-06-07T07:50:00Z", checked_in_via: "account" }, now).state,
        "checked-in",
    );
    assert.equal(checkInLine(base, now).state, "open");
    assert.equal(checkInLine({ ...base, check_in_opens_at: "2027-06-07T09:00:00Z" }, now).state, "not-yet");
    const sent = { status: "pending", via: "staff", expires_at: "2027-06-07T09:00:00Z" };
    assert.equal(checkInLine({ ...base, check_in: sent }, now).state, "link-sent");
    assert.equal(
        checkInLine({ ...base, check_in: { ...sent, status: "failed", failure_reason: "Declined." } }, now).text,
        "Check-in failed: Declined.",
    );
    assert.equal(checkInLine({ ...base, status: "canceled" }, now).state, "none");
});

test("answers of the questionnaire", () => {
    assert.equal(answerText({ kind: "consent" }, true), "Yes — consented");
    assert.equal(answerText({ kind: "scale" }, 3), "3 / 10");
    assert.equal(answerText({ kind: "yesno", options: [{ value: "yes", label: "Yes" }] }, "yes"), "Yes");
    assert.equal(answerText({ kind: "text" }, ""), "—");
});
