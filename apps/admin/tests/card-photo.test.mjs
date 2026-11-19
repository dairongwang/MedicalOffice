// The insurance card photo helpers of the edit form (pure parts): `node --test apps/admin/tests`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_EDGE_PX, dataUrlBytes, fitWithin, formatBytes, prepareCardPhoto } from "../js/card-photo.js";

test("scales to the long edge, never up", () => {
  assert.deepEqual(fitWithin(4000, 3000), { width: MAX_EDGE_PX, height: 1200 });
  assert.deepEqual(fitWithin(3000, 4000), { width: 1200, height: MAX_EDGE_PX });
  assert.deepEqual(fitWithin(800, 500), { width: 800, height: 500 });
});

test("measures and formats", () => {
  assert.equal(dataUrlBytes("data:image/jpeg;base64," + Buffer.from("abcde").toString("base64")), 5);
  assert.equal(dataUrlBytes("nonsense"), 0);
  assert.equal(formatBytes(200 * 1024), "200 KB");
  assert.equal(formatBytes(2.5 * 1024 * 1024), "2.5 MB");
});

test("refuses empty, oversized and non-image files before decoding", async () => {
  await assert.rejects(prepareCardPhoto({ size: 0, type: "image/jpeg" }), /empty/);
  await assert.rejects(prepareCardPhoto({ size: 30 * 1024 * 1024, type: "image/jpeg" }), /too large/);
  await assert.rejects(prepareCardPhoto({ size: 10, type: "application/pdf" }), /JPEG, PNG or WebP/);
});
