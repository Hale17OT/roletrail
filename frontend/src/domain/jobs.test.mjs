import assert from "node:assert/strict";
import { test } from "node:test";
import {
  emptyFilters,
  selectJobs,
  regionOf,
  readCache,
  routeFrom,
  routeTo,
  publication,
  stageLabel,
  displayDate,
} from "./jobs.mjs";
test('source calendar days do not gain an invented publication time',()=>{
  assert.equal(displayDate('2026-11-05'),'5 Nov 2026');
  assert.equal(displayDate('6 days ago'),'Source age: 6 days ago');
});
test('employer rejection and candidate withdrawal have distinct labels',()=>{
  assert.equal(stageLabel('rejected'),'Rejected by employer');
  assert.equal(stageLabel('declined'),'Withdrawn / declined by me');
});
const job = (id, changes = {}) => ({
  id,
  title: "Power Apps Developer",
  company: "Example",
  status: "ready",
  region: "EMEA",
  tags: ["Power Apps"],
  _meta: {
    eligibility: { canApply: true, publishedAt: "2026-11-08T00:00:00Z" },
    geography: {
      arrangement: "remote",
      destination: "Espoo, Uusimaa, Finland",
    },
  },
  ...changes,
});
test("Uusimaa is not the US; candidate base is not a local destination", () => {
  assert.equal(regionOf(job("a")), "other");
  assert.equal(
    regionOf(job("a", { region: "Remote, based in Ethiopia" })),
    "other",
  );
});
test("one selector applies every filter and keeps applications separate", () => {
  const jobs = [
    job("a"),
    job("b", { status: "applied" }),
    job("c", { status: "deleted" }),
    job("d", {
      _meta: {
        eligibility: { canApply: false },
        geography: { arrangement: "local" },
      },
    }),
  ];
  const f = emptyFilters();
  assert.deepEqual(
    selectJobs(jobs, "opportunities", f).map((j) => j.id),
    ["a"],
  );
  assert.deepEqual(
    selectJobs(jobs, "applications", f).map((j) => j.id),
    ["b"],
  );
  f.focus = "power";
  f.q = "example";
  f.arrangement = "remote";
  f.region = "other";
  assert.equal(selectJobs(jobs, "opportunities", f).length, 1);
  f.stage = "saved";
  assert.equal(selectJobs(jobs, "opportunities", f).length, 0);
});
test("legacy timestamps stay unknown and review records are separate", () => {
  const j = job("a", { _meta: { eligibility: { canApply: false } } });
  assert.equal(publication(j), 0);
  assert.equal(
    selectJobs([j], "opportunities", {
      ...emptyFilters(),
      eligibility: "review",
    }).length,
    1,
  );
});
test("cache accepts authoritative empty arrays and rejects malformed data", () => {
  assert.deepEqual(
    readCache(JSON.stringify({ version: 2, jobs: [] })).jobs,
    [],
  );
  for (const raw of [
    "bad",
    "null",
    "{}",
    JSON.stringify({ version: 2, jobs: [{ id: "a" }] }),
  ])
    assert.equal(readCache(raw), null);
});
test("URL round-trip preserves combined view state and selected ID", () => {
  const f = {
    ...emptyFilters(),
    q: "Apps & D365",
    stage: "interviewing",
    region: "other",
    sort: "action",
  };
  const r = routeFrom(routeTo("applications", f, "role-a"));
  assert.equal(r.view, "applications");
  assert.deepEqual(r.filters, f);
  assert.equal(r.job, "role-a");
  assert.equal(routeFrom("?view=bad").view, "opportunities");
});
