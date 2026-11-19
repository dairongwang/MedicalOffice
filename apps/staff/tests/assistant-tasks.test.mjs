// "My tasks" logic for medical assistants (js/assistant-tasks.js): `node --test apps/staff/tests`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
    TASK_STATUS,
    assistantText,
    decisionOptions,
    flagBadge,
    groupTasksByDay,
    isOpen,
    reportProblem,
    reviewProgress,
    taskCounts,
    taskLine,
    taskTime,
    toNumber,
    vitalsProblems,
} from "../js/assistant-tasks.js";

const task = (status, extra = {}) => ({
    id: 1,
    status,
    complete_opens_at: "2027-06-06T10:00:00Z",
    can_complete: false,
    appointment: {
        status: "scheduled",
        timezone: "UTC",
        start_at: "2027-06-07T10:00:00Z",
        start_local: "Monday, June 7, 2027 · 10:00",
    },
    ...extra,
});

test("every status has a label", () => {
    for (const s of ["assigned", "accepted", "completed", "reported", "canceled"]) assert.ok(TASK_STATUS[s]);
    assert.ok(isOpen(task("assigned")) && isOpen(task("accepted")));
    assert.ok(!isOpen(task("completed")) && !isOpen(task("reported")));
});

test("the task line says what to do next", () => {
    const early = new Date("2027-06-05T10:00:00Z");
    assert.equal(taskLine(task("assigned")).state, "new");
    const taken = taskLine(task("accepted"), early);
    assert.equal(taken.state, "taken");
    assert.match(taken.text, /can be completed from Jun 6, 2027, 10:00 UTC/);
    assert.equal(taskLine(task("accepted", { can_complete: true })).state, "ready");
    const reported = taskLine(task("reported", { report_reason_label: "Sick / unwell", report_note: "Fever" }));
    assert.equal(reported.text, "You reported: Sick / unwell — Fever. The office assigns someone else.");
    assert.equal(
        taskLine(task("canceled", { cancel_reason: "The visit was canceled." })).text,
        "Withdrawn: The visit was canceled.",
    );
    assert.equal(
        taskLine(task("completed", { completed_at: "2027-06-07T10:20:00Z" })).text,
        "Completed Jun 7, 2027, 10:20 UTC",
    );
});

test("reporting 'other' needs a note", () => {
    assert.equal(reportProblem("", ""), "Please choose why you cannot do this task.");
    assert.equal(reportProblem("other", "  "), "Please say why you cannot do this task.");
    assert.equal(reportProblem("other", "Car broke down"), null);
    assert.equal(reportProblem("sick", ""), null);
});

test("grouping, counting and times", () => {
    const items = [
        task("assigned"),
        task("accepted"),
        task("completed", { appointment: { ...task("x").appointment, start_local: "Tuesday, June 8, 2027 · 09:00" } }),
        task("reported"),
    ];
    assert.deepEqual(
        groupTasksByDay(items).map((g) => [g.day, g.items.length]),
        [
            ["Monday, June 7, 2027", 2],
            ["Tuesday, June 8, 2027", 1],
            ["Monday, June 7, 2027", 1],
        ],
    );
    assert.deepEqual(taskCounts(items), { new: 1, taken: 1, completed: 1, reported: 1, withdrawn: 0, open: 2 });
    assert.equal(taskTime(task("assigned")), "10:00 UTC");
});

test("the provider's line about the assistant", () => {
    assert.equal(assistantText(null), "");
    assert.equal(
        assistantText({ name: "Jamie Helper", status: "accepted" }),
        "Medical assistant: Jamie Helper (taken)",
    );
    assert.match(assistantText({ name: "Jamie Helper", status: "reported" }), /the office is reassigning/);
});

test("item hints and the ways to clear an item", () => {
    assert.deepEqual(flagBadge({ flag: "changed" }), ["Check-in differs", "warn"]);
    assert.deepEqual(flagBadge({ flag: "said_none" }), ["Patient wrote “none”", "bad"]);
    assert.deepEqual(flagBadge({ flag: null, origin: "record" }), ["On record", ""]);
    assert.deepEqual(flagBadge({ flag: null, origin: "assistant" }), ["Added by you", ""]);
    assert.deepEqual(
        decisionOptions("medication").map(([v]) => v),
        ["confirmed", "removed"],
    );
    assert.match(decisionOptions("allergy")[1][1], /Not an allergy/);
});

test("vital signs as typed", () => {
    assert.equal(toNumber(""), null);
    assert.equal(toNumber("98,6"), 98.6);
    assert.ok(Number.isNaN(toNumber("abc")));
    const fine = {
        bp_systolic: 120,
        bp_diastolic: 80,
        pulse: 72,
        temperature: 98.6,
        temperature_unit: "F",
        weight: 154,
        weight_unit: "lb",
    };
    assert.deepEqual(vitalsProblems(fine), []);
    assert.match(vitalsProblems({ ...fine, temperature: 37 }).join(), /Temperature: not plausible in °F/);
    assert.deepEqual(vitalsProblems({ ...fine, temperature: 37, temperature_unit: "C" }), []);
    assert.match(vitalsProblems({ ...fine, bp_systolic: 70, bp_diastolic: 90 }).join(), /systolic \(upper\)/);
    assert.match(vitalsProblems({ ...fine, pulse: 7.5 }).join(), /whole number/);
    assert.match(vitalsProblems({ ...fine, weight: 500, weight_unit: "kg" }).join(), /Weight/);
});

test("what is still missing before completing (the server's rules)", () => {
    const complete = {
        room_patient: true,
        vitals: {
            bp_systolic: 120,
            bp_diastolic: 80,
            pulse: 72,
            temperature: 98.6,
            temperature_unit: "F",
            weight: 154,
            weight_unit: "lb",
        },
        medications: [{ name: "Lisinopril", decision: "confirmed" }],
        allergies: [],
        no_allergies: true,
        has_check_in: true,
        check_in_reviewed: true,
        provider_ready: true,
    };
    assert.deepEqual(reviewProgress(complete), {
        done: ["room_patient", "vitals", "medications", "allergies", "check_in_review", "provider_ready"],
        problems: [],
    });
    const open = reviewProgress({
        ...complete,
        medications: [{ name: "Lisinopril" }, { name: "Metformin", decision: "confirmed" }],
    });
    assert.deepEqual(open.problems, ["Medications: clear every item — still open: Lisinopril."]);
    const removedWithoutWhy = reviewProgress({
        ...complete,
        medications: [...complete.medications, { name: "Atorvastatin", decision: "removed", fromChart: true }],
    });
    assert.match(removedWithoutWhy.problems[0], /why Atorvastatin is removed/);
    const nothing = reviewProgress({
        ...complete,
        no_allergies: false,
        has_check_in: false,
        check_in_note: "",
        vitals: {},
    });
    assert.match(nothing.problems.join(" "), /Vital signs: enter the blood pressure \(systolic\)/);
    assert.match(nothing.problems.join(" "), /Allergies: nothing is confirmed/);
    assert.match(nothing.problems.join(" "), /has not checked in/);
    const contradiction = reviewProgress({ ...complete, no_medications: true });
    assert.match(contradiction.problems[0], /is ticked, but Lisinopril is confirmed/);
});
