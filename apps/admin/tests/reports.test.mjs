// Reports on request and immunizations (js/format.js): `node --test apps/admin/tests` or `python dev.py test`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { immunizationProblems, neededBy, reportStatusBadge } from "../js/format.js";

const TODAY = new Date(2027, 5, 7); // June 7, 2027

test("the queue's status badges", () => {
  assert.deepEqual(reportStatusBadge("requested"), ["to prepare", "warn"]);
  assert.deepEqual(reportStatusBadge("declined"), ["returned by the doctor", "bad"]);
  assert.deepEqual(reportStatusBadge("signed"), ["signed", "ok"]);
});

test("needed by: urgency for open reports only", () => {
  assert.equal(neededBy({ status: "requested", needed_by: null }, TODAY), null);
  assert.equal(neededBy({ status: "signed", needed_by: "2027-06-01" }, TODAY), null);
  assert.equal(neededBy({ status: "requested", needed_by: "2027-06-01" }, TODAY).kind, "bad");
  assert.match(neededBy({ status: "draft", needed_by: "2027-06-07" }, TODAY).text, /today/);
  assert.match(neededBy({ status: "draft", needed_by: "2027-06-09" }, TODAY).text, /in 2 days/);
  assert.equal(neededBy({ status: "requested", needed_by: "2027-07-01" }, TODAY).kind, "");
});

test("the immunization form", () => {
  assert.deepEqual(immunizationProblems({ vaccine: "Hepatitis A", date_given: "2027-01-01" }, TODAY), []);
  assert.equal(immunizationProblems({ vaccine: "", date_given: "" }, TODAY).length, 2);
  assert.match(immunizationProblems({ vaccine: "Flu", date_given: "2027-12-01" }, TODAY)[0], /future/);
  const series = immunizationProblems({ vaccine: "Hep B", date_given: "2020-01-01", dose_number: "3", series_doses: "2" }, TODAY);
  assert.match(series[0], /doses of the series/);
});
