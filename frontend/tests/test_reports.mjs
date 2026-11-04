import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const code = fs.readFileSync(resolve(root, "js/reports.js"), "utf8");

class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this._text = "";
    this.classes = new Set();
    this.className = "";
    this.classList = {
      toggle: (key, active) => active ? this.classes.add(key) : this.classes.delete(key),
    };
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.href = "";
    this.download = "";
    this.clicked = false;
    this.removed = false;
  }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent || "").join("");
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ""; this.children = children; }
  addEventListener(event, callback) { (this.listeners[event] ??= []).push(callback); }
  fire(event) { return Promise.all((this.listeners[event] || []).map((fn) => fn({ preventDefault() {} }))); }
  click() { this.clicked = true; }
  remove() { this.removed = true; }
}

const IDS = [
  "reports", "refresh-reports", "export-pipeline", "report-include-merged",
  "reports-status", "export-feedback", "evaluation-mode",
  "report-current-leads", "report-pipeline-value", "report-overdue", "report-issues",
  "report-pending-duplicates", "report-merged-records", "evaluation-total",
  "evaluation-flagged", "evaluation-reviewed", "evaluation-correct",
  "evaluation-accuracy", "evaluation-precision", "evaluation-recall",
  "evaluation-note", "evaluation-findings", "weekly-summary",
];

const demoSummary = (overrides = {}) => ({
  as_of: "2026-11-01",
  dashboard: { current_leads: 25, active_pipeline_value_bdt: 3920000, overdue_leads: 9 },
  issues: { open_issues: 48 },
  pending_duplicate_pairs: 2,
  merged_records: 0,
  note: "Warning counts reflect the last saved scan.",
  ...overrides,
});
const demoEvaluation = (overrides = {}) => ({
  as_of: "2026-11-01", original_leads: 25, source_data_flagged_leads: 11,
  manually_reviewed_leads: 0, verification_status: "awaiting_review", metrics: null,
  overdue_leads: 9,
  duplicate_pairs: [{ lead_id_1: "L01", company_1: "Padma Textiles Ltd",
    lead_id_2: "L23", company_2: "Padma Textiles Ltd" },
  { lead_id_1: "L02", company_1: "Meghna Pharma Ltd",
    lead_id_2: "L03", company_2: "Meghna Pharmaceuticals" }],
  invalid_date_order_leads: ["L25"],
  counts_by_rule: { DUPLICATE_LEAD: 4, INVALID_DATE_ORDER: 1, OVERDUE_FOLLOWUP: 9 },
  ...overrides,
});
const demoWeekly = (overrides = {}) => ({
  as_of: "2026-11-01", currency: "BDT", overdue_active_leads: 9,
  overdue_value_bdt: 770000,
  note: "Verify invalid dates and invoice payments before outreach.",
  top_leads: [
    { rank: 1, lead_id: "L22", company: "Khulna Jute Works", stage: "Invoice sent",
      deal_value_bdt: 400000, priority_score: 114, followup_status: "overdue",
      requires_date_review: false, recommended_action: "Check payment status first." },
    { rank: 2, lead_id: "L25", company: "Sylhet Hospitality Group", stage: "Contacted",
      deal_value_bdt: 0, priority_score: 55, followup_status: "invalid_date",
      requires_date_review: true, recommended_action: "Verify follow-up date." },
  ],
  ...overrides,
});

const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolvePromise, rejectPromise;
  const promise = new Promise((yes, no) => { resolvePromise = yes; rejectPromise = no; });
  return { promise, resolve: resolvePromise, reject: rejectPromise };
}

function fixture(overrides = {}, options = {}) {
  const nodes = Object.fromEntries(IDS.map((id) => [id, new Element()]));
  const events = {};
  const calls = [];
  const anchors = [];
  const createdUrls = [];
  const revokedUrls = [];
  const api = {
    getReportSummary: async (mode) => { calls.push(["summary", mode]); return demoSummary(); },
    getEvaluation: async () => { calls.push(["evaluation"]); return demoEvaluation(); },
    getWeeklySummary: async (mode, limit) => { calls.push(["weekly", mode, limit]); return demoWeekly(); },
    exportCsv: async (merged) => { calls.push(["csv", merged]); return { size: 120 }; },
    ...overrides,
  };
  const document = {
    getElementById: (id) => nodes[id] || null,
    createElement: (tag) => new Element(tag),
    addEventListener: (name, cb) => { (events[name] ??= []).push(cb); },
    body: { append: (element) => anchors.push(element) },
  };
  const window = {
    BIAI_API: api,
    addEventListener: (name, cb) => { (events[name] ??= []).push(cb); },
    URL: {
      createObjectURL: (blob) => { createdUrls.push(blob); return "blob:demo-1"; },
      revokeObjectURL: (url) => revokedUrls.push(url),
    },
    setTimeout: (cb) => options.setTimeout ? options.setTimeout(cb) : cb(),
  };
  vm.runInNewContext(code, { window, document, Number, String, Date, Intl, Object, Promise }, {
    filename: "reports.js",
  });
  return { nodes, events, calls, api, window, document, anchors, createdUrls, revokedUrls };
}
async function load(fx) {
  for (const cb of fx.events.DOMContentLoaded || []) await cb();
  await flush();
}

// The screen must be a view of current API data, never hard-coded sample totals.
test("waits for DOMContentLoaded before reading reports", async () => {
  const fx = fixture();
  assert.equal(fx.calls.length, 0);
  await load(fx);
  assert.deepEqual(fx.calls.map((c) => c[0]), ["summary", "evaluation", "weekly"]);
});

test("shows real summary values and saved issue counts", async () => {
  const fx = fixture(); await load(fx);
  assert.equal(fx.nodes["report-current-leads"].textContent, "25");
  assert.match(fx.nodes["report-pipeline-value"].textContent, /BDT 3[,.]920[,.]000|BDT 39,20,000/);
  assert.equal(fx.nodes["report-overdue"].textContent, "9");
  assert.equal(fx.nodes["report-issues"].textContent, "48");
  assert.equal(fx.nodes["report-pending-duplicates"].textContent, "2");
  assert.equal(fx.nodes["report-merged-records"].textContent, "0");
  assert.match(fx.nodes["reports-status"].textContent, /last scan/i);
});

test("shows the original archived evaluation independent of live date", async () => {
  const fx = fixture(); await load(fx);
  assert.equal(fx.nodes["evaluation-total"].textContent, "25");
  assert.equal(fx.nodes["evaluation-flagged"].textContent, "11");
  assert.equal(fx.nodes["evaluation-reviewed"].textContent, "0 / 25");
  assert.match(fx.nodes["evaluation-findings"].textContent, /1 Nov 2026/);
});

test("does not claim accuracy without complete manual labels", async () => {
  const fx = fixture(); await load(fx);
  for (const field of ["evaluation-accuracy", "evaluation-precision", "evaluation-recall", "evaluation-correct"]) {
    assert.equal(fx.nodes[field].textContent, "Not verified");
  }
  assert.match(fx.nodes["evaluation-note"].textContent, /evaluation_labels\.json/);
});

test("partially reviewed labels remain unverified", async () => {
  const fx = fixture({ getEvaluation: async () => demoEvaluation({
    manually_reviewed_leads: 10, verification_status: "partial",
    metrics: { accuracy: 1, precision: 1, recall: 1 },
  }) });
  await load(fx);
  assert.equal(fx.nodes["evaluation-reviewed"].textContent, "10 / 25");
  assert.equal(fx.nodes["evaluation-accuracy"].textContent, "Not verified");
});

test("complete manual labels show accuracy precision recall and correct count", async () => {
  const fx = fixture({ getEvaluation: async () => demoEvaluation({
    manually_reviewed_leads: 25, verification_status: "complete",
    metrics: { accuracy: 0.88, precision: 0.80, recall: 0.75, correctly_classified_leads: 22 },
  }) });
  await load(fx);
  assert.equal(fx.nodes["evaluation-accuracy"].textContent, "88.0%");
  assert.equal(fx.nodes["evaluation-precision"].textContent, "80.0%");
  assert.equal(fx.nodes["evaluation-recall"].textContent, "75.0%");
  assert.equal(fx.nodes["evaluation-correct"].textContent, "22 / 25");
});

test("zero percent is a real verified value, not missing", async () => {
  const fx = fixture({ getEvaluation: async () => demoEvaluation({
    manually_reviewed_leads: 25, verification_status: "complete",
    metrics: { accuracy: 0, precision: null, recall: 0, correctly_classified_leads: 0 },
  }) });
  await load(fx);
  assert.equal(fx.nodes["evaluation-accuracy"].textContent, "0.0%");
  assert.equal(fx.nodes["evaluation-precision"].textContent, "Not available");
  assert.equal(fx.nodes["evaluation-recall"].textContent, "0.0%");
});

test("duplicate and date findings use API lead IDs", async () => {
  const fx = fixture(); await load(fx);
  const text = fx.nodes["evaluation-findings"].textContent;
  assert.match(text, /L01: Padma Textiles Ltd \/ L23: Padma Textiles Ltd/);
  assert.match(text, /L02: Meghna Pharma Ltd \/ L03: Meghna Pharmaceuticals/);
  assert.match(text, /L25/);
  assert.match(text, /INVALID DATE ORDER: 1 lead/);
});

test("no duplicate or invalid date records is handled correctly", async () => {
  const fx = fixture({ getEvaluation: async () => demoEvaluation({
    duplicate_pairs: [], invalid_date_order_leads: [], counts_by_rule: {},
  }) });
  await load(fx);
  assert.match(fx.nodes["evaluation-findings"].textContent, /No duplicate pairs detected/);
  assert.match(fx.nodes["evaluation-findings"].textContent, /No date-order errors detected/);
});

test("untrusted names appear as text, never HTML", async () => {
  const fx = fixture({ getEvaluation: async () => demoEvaluation({
    duplicate_pairs: [{ lead_id_1: "L01", lead_id_2: "L23", company_1: "<img src=x onerror=alert(1)>", company_2: "ok" }],
  }) });
  await load(fx);
  assert.match(fx.nodes["evaluation-findings"].textContent, /<img src=x onerror=alert\(1\)>/);
  const list = fx.nodes["evaluation-findings"].children[4];
  assert.equal(list.children[0].children.length, 0); // textContent, no HTML children
});

test("weekly report shows verified priority facts with actions", async () => {
  const fx = fixture(); await load(fx);
  const text = fx.nodes["weekly-summary"].textContent;
  assert.match(text, /9 overdue active deal/);
  assert.match(text, /BDT 770[,.]000/);
  assert.match(text, /L22/);
  assert.match(text, /Check payment status first/);
  assert.match(text, /Priority score: 114/);
  assert.match(text, /not AI-written text/i);
});

test("invalid date in priorities requests review rather than overdue outreach", async () => {
  const fx = fixture(); await load(fx);
  const list = fx.nodes["weekly-summary"].children[1];
  assert.match(list.children[1].textContent, /Date needs review/);
  assert.doesNotMatch(list.children[1].textContent, /Overdue/);
});

test("empty weekly priorities show a useful message", async () => {
  const fx = fixture({ getWeeklySummary: async () => demoWeekly({ top_leads: [], overdue_active_leads: 0, overdue_value_bdt: 0 }) });
  await load(fx);
  assert.match(fx.nodes["weekly-summary"].textContent, /No active leads/);
});

test("sample date mode is passed only to live summary and weekly report", async () => {
  const fx = fixture(); await load(fx);
  fx.nodes["evaluation-mode"].checked = true;
  await fx.nodes["evaluation-mode"].fire("change");
  await flush();
  assert.deepEqual(fx.calls.slice(-3).map((c) => c[0]), ["summary", "evaluation", "weekly"]);
  assert.deepEqual(fx.calls.slice(-3).filter((c) => c[0] !== "evaluation").map((c) => c[1]), [true, true]);
  assert.match(fx.nodes["reports-status"].textContent, /Sample date/);
});

test("refresh button requests new reports", async () => {
  const fx = fixture(); await load(fx);
  await fx.nodes["refresh-reports"].fire("click"); await flush();
  assert.equal(fx.calls.filter((c) => c[0] === "summary").length, 2);
  assert.equal(fx.nodes["refresh-reports"].disabled, false);
});

test("lead or issue changes refresh report automatically", async () => {
  const fx = fixture(); await load(fx);
  for (const cb of fx.events["biai:data-changed"] || []) cb();
  await flush();
  assert.equal(fx.calls.filter((c) => c[0] === "summary").length, 2);
});

test("failed evaluation does not hide live summary and weekly section", async () => {
  const fx = fixture({ getEvaluation: async () => { throw new Error("Archive missing"); } });
  await load(fx);
  assert.equal(fx.nodes["report-current-leads"].textContent, "25");
  assert.match(fx.nodes["evaluation-note"].textContent, /Import the sample CSV/);
  assert.match(fx.nodes["weekly-summary"].textContent, /L22/);
  assert.match(fx.nodes["reports-status"].textContent, /Archive missing/);
  assert.equal(fx.nodes["reports-status"].classes.has("is-error"), true);
});

test("failed weekly call does not hide evaluation or current data", async () => {
  const fx = fixture({ getWeeklySummary: async () => { throw new Error("Service error"); } });
  await load(fx);
  assert.equal(fx.nodes["evaluation-flagged"].textContent, "11");
  assert.equal(fx.nodes["report-current-leads"].textContent, "25");
  assert.match(fx.nodes["weekly-summary"].textContent, /Weekly summary unavailable/);
});

test("failed summary clears old figures instead of showing stale counts", async () => {
  let failed = false;
  const fx = fixture({ getReportSummary: async () => failed ? Promise.reject(new Error("API offline")) : demoSummary() });
  await load(fx);
  assert.equal(fx.nodes["report-overdue"].textContent, "9");
  failed = true;
  await fx.window.BIAI_Reports.load();
  assert.equal(fx.nodes["report-overdue"].textContent, "—");
  assert.match(fx.nodes["reports-status"].textContent, /API offline/);
});

test("late response from old mode cannot replace new figures", async () => {
  const slow = deferred();
  const fx = fixture({ getReportSummary: (mode) => mode ? demoSummary({
    as_of: "2026-11-01", dashboard: { current_leads: 23, active_pipeline_value_bdt: 3170000, overdue_leads: 6 },
  }) : slow.promise });
  await load(fx); // first load waits on slow promise, but DOM callback does not
  fx.nodes["evaluation-mode"].checked = true;
  await fx.nodes["evaluation-mode"].fire("change"); await flush();
  assert.equal(fx.nodes["report-current-leads"].textContent, "23");
  slow.resolve(demoSummary()); await flush();
  assert.equal(fx.nodes["report-current-leads"].textContent, "23");
});

test("exports a browser CSV file without any database modification", async () => {
  const fx = fixture(); await load(fx);
  const result = await fx.window.BIAI_Reports.downloadCsv();
  assert.equal(result, true);
  assert.deepEqual(fx.calls.at(-1), ["csv", false]);
  assert.equal(fx.anchors.length, 1);
  assert.equal(fx.anchors[0].download, "biai-pipeline.csv");
  assert.equal(fx.anchors[0].href, "blob:demo-1");
  assert.equal(fx.anchors[0].clicked, true);
  assert.equal(fx.anchors[0].removed, true);
  assert.deepEqual(fx.revokedUrls, ["blob:demo-1"]);
  assert.equal(fx.nodes["export-pipeline"].disabled, false);
});

test("include merged checkbox is passed to export only", async () => {
  const fx = fixture(); await load(fx);
  fx.nodes["report-include-merged"].checked = true;
  await fx.window.BIAI_Reports.downloadCsv();
  assert.deepEqual(fx.calls.at(-1), ["csv", true]);
});

test("export rejects empty files without triggering download", async () => {
  const fx = fixture({ exportCsv: async () => ({ size: 0 }) }); await load(fx);
  assert.equal(await fx.window.BIAI_Reports.downloadCsv(), false);
  assert.equal(fx.anchors.length, 0);
  assert.match(fx.nodes["export-feedback"].textContent, /empty CSV/);
});

test("export failure is shown and allows another attempt", async () => {
  const fx = fixture({ exportCsv: async () => { throw new Error("FastAPI offline"); } }); await load(fx);
  assert.equal(await fx.window.BIAI_Reports.downloadCsv(), false);
  assert.match(fx.nodes["export-feedback"].textContent, /FastAPI offline/);
  assert.equal(fx.nodes["export-pipeline"].disabled, false);
});

test("double clicks cannot start multiple parallel downloads", async () => {
  const slow = deferred();
  let count = 0;
  const fx = fixture({ exportCsv: () => { count += 1; return slow.promise; } }); await load(fx);
  const first = fx.window.BIAI_Reports.downloadCsv();
  const second = await fx.window.BIAI_Reports.downloadCsv();
  assert.equal(second, false);
  assert.equal(count, 1);
  assert.equal(fx.nodes["export-pipeline"].disabled, true);
  slow.resolve({ size: 100 });
  assert.equal(await first, true);
});

test("missing API client shows an error, not fake values", async () => {
  const fx = fixture();
  fx.window.BIAI_API = null;
  await load(fx);
  assert.match(fx.nodes["reports-status"].textContent, /API client missing/);
  assert.equal(await fx.window.BIAI_Reports.downloadCsv(), false);
  assert.match(fx.nodes["export-feedback"].textContent, /API client missing/);
});

test("export is not triggered on loading the Reports screen", async () => {
  const fx = fixture(); await load(fx);
  assert.equal(fx.calls.some((c) => c[0] === "csv"), false);
  assert.equal(fx.anchors.length, 0);
});


test("CSV object URL is revoked correctly after delayed browser timer", async () => {
  const timers = [];
  const fx = fixture({}, { setTimeout: (cb) => timers.push(cb) }); await load(fx);
  assert.equal(await fx.window.BIAI_Reports.downloadCsv(), true);
  assert.deepEqual(fx.revokedUrls, []);
  timers[0]();
  assert.deepEqual(fx.revokedUrls, ["blob:demo-1"]);
});
