import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const file = resolve(dirname(fileURLToPath(import.meta.url)), "../js/validation.js");
const source = fs.readFileSync(file, "utf8");

class Element {
  constructor(tag = "div") {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.value = "";
    this._text = "";
    this.className = "";
    this.classes = new Set();
    this.classList = {
      toggle: (key, enabled) => enabled ? this.classes.add(key) : this.classes.delete(key),
    };
    this.checked = false;
    this.disabled = false;
    this.hidden = true;
    this.open = false;
    this.type = "";
    this.colSpan = 1;
  }
  get textContent() { return this._text + this.children.map((item) => item.textContent ?? "").join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) {
    this._text = "";
    this.children = items;
    if (this.tagName === "select") this.value = items[0]?.value ?? "";
  }
  addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback); }
  async fire(name) {
    for (const callback of this.listeners[name] || []) {
      await callback({ preventDefault() {} });
    }
  }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

const ids = [
  "run-quality-scan", "quality-scan-status", "evaluation-mode", "quality-open",
  "quality-flagged", "quality-duplicates", "quality-dates", "issue-rule-filter",
  "issue-status-filter", "issues-table-body", "duplicate-pairs", "toast",
  "review-issue-title", "review-issue-details", "review-issue-status",
  "review-issue-reason", "review-issue-feedback", "issue-review-dialog",
  "issue-review-form", "submit-issue-review", "merge-dialog", "merge-form",
  "merge-keep-choice", "merge-surviving-id", "merge-duplicate-id",
  "merge-compare", "merge-reason", "merge-confirmed", "merge-status",
  "merge-pair-description", "confirm-merge-button",
];

function issue(id, rule_code, overrides = {}) {
  return {
    id, lead_id: id === 1 ? "L25" : "L02", rule_code,
    message: id === 1 ? "Date is before last contact" : "Possible duplicate company",
    severity: id === 1 ? "high" : "medium", status: "open",
    evidence: { source: "csv" }, ...overrides,
  };
}
const pair = {
  lead_id_1: "L02", company_1: "Meghna Pharma",
  lead_id_2: "L03", company_2: "Meghna Pharmaceuticals",
  match_type: "same_contact_different_company_name",
  matched_fields: ["contact_email", "contact_phone"],
};
function summary(items) {
  const open = items.filter((x) => x.status === "open");
  return {
    total_issue_records: items.length,
    open_issues: open.length,
    data_quality_flagged_leads: new Set(open.map((x) => x.lead_id)).size,
    open_by_rule: Object.fromEntries([...new Set(open.map((x) => x.rule_code))].map((k) => [k, open.filter((x) => x.rule_code === k).length])),
  };
}

function setup(override = {}) {
  const nodes = Object.fromEntries(ids.map((id) => [id, new Element()]));
  nodes["issue-rule-filter"].tagName = "select";
  nodes["merge-keep-choice"].tagName = "select";
  nodes["issue-status-filter"].tagName = "select";
  nodes["issue-status-filter"].value = "open";
  nodes["evaluation-mode"].checked = false;
  const events = {};
  const calls = [];
  let records = [issue(1, "INVALID_DATE_ORDER"), issue(2, "DUPLICATE_LEAD")];
  let pairs = [pair];
  const api = {
    getIssueSummary: async () => { calls.push(["summary"]); return summary(records); },
    getDuplicatePairs: async () => { calls.push(["duplicates"]); return pairs; },
    getIssues: async (filters) => { calls.push(["issues", filters]); return records.slice(filters.offset, filters.offset + filters.limit); },
    scanIssues: async (mode) => {
      calls.push(["scan", mode]);
      return {
        scanned_leads: 25,
        as_of: mode ? "2026-11-01" : "2026-11-04",
        open_issues: summary(records).open_issues,
        duplicate_pairs: pairs.length,
        overdue_leads: 9,
      };
    },
    reviewIssue: async (id, status, reason) => {
      calls.push(["review", id, status, reason]);
      records.find((x) => x.id === id).status = status;
      return records.find((x) => x.id === id);
    },
    getLead: async (id, merged) => {
      calls.push(["lead", id, merged]);
      return { lead_id: id, company: id === "L02" ? "Meghna Pharma" : "Meghna Pharmaceuticals",
        notes: id === "L02" ? "Budget approval pending" : "Needs Bangla support",
        deal_value_bdt: id === "L02" ? 700000 : 700000, stage: "Proposal", merged_into_id: null };
    },
    mergeLeads: async (surviving, duplicate, reason, confirmed) => {
      calls.push(["merge", surviving, duplicate, reason, confirmed]);
      pairs = [];
      return { lead_id: surviving };
    },
    ...override,
  };
  const window = {
    BIAI_API: api,
    BIAI_LEADS: { openLead: (id) => calls.push(["openLead", id]) },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    addEventListener: (name, fn) => { (events[name] ??= []).push(fn); },
    dispatchEvent: (event) => calls.push(["dispatch", event.type, event.detail]),
  };
  const document = {
    getElementById: (id) => nodes[id] || null,
    createElement: (tag) => new Element(tag),
    addEventListener: (name, fn) => { (events[name] ??= []).push(fn); },
  };
  const Option = class { constructor(text, value) { this.textContent = text; this.value = value; } };
  vm.runInNewContext(source, { window, document, Option, Promise, Set, String, Object }, { filename: file });
  return { window, nodes, calls, events, api, updateRecords: (newRecords) => { records = newRecords; },
    updatePairs: (newPairs) => { pairs = newPairs; } };
}

const load = async (fx) => { for (const cb of fx.events.DOMContentLoaded || []) await cb(); await fx.window.BIAI_VALIDATION.reload(); };
const click = async (parent, index = 0) => { await parent.children[index].fire("click"); };
const qualityButtons = (fx) => fx.nodes["issues-table-body"].children.map((r) => r.children[5]);

// All data comes from the API, never hardcoded into the rendered totals.
test("does not scan on page load", async () => {
  const fx = setup(); await load(fx);
  assert.equal(fx.calls.some((c) => c[0] === "scan"), false);
  assert.equal(fx.nodes["quality-open"].textContent, "2");
  assert.equal(fx.nodes["quality-duplicates"].textContent, "1");
  assert.equal(fx.nodes["quality-dates"].textContent, "1");
});

test("displays both warnings with lead IDs and readable labels", async () => {
  const fx = setup(); await load(fx);
  const rows = fx.nodes["issues-table-body"].children;
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /L25/);
  assert.match(rows[0].textContent, /Incorrect date order/);
  assert.match(rows[1].textContent, /Possible duplicate/);
});

test("uses textContent, not HTML, for untrusted issue descriptions", async () => {
  const fx = setup();
  fx.updateRecords([issue(1, "INVALID_DATE_ORDER", { message: '<img src=x onerror="alert(1)">' })]);
  await load(fx);
  assert.match(fx.nodes["issues-table-body"].textContent, /<img src=x/);
});

test("status filter shows matching warnings", async () => {
  const fx = setup(); await load(fx);
  fx.nodes["issue-status-filter"].value = "dismissed";
  await fx.nodes["issue-status-filter"].fire("change");
  assert.match(fx.nodes["issues-table-body"].textContent, /No issues match/);
});

test("issue-type filter shows only chosen rule", async () => {
  const fx = setup(); await load(fx);
  fx.nodes["issue-rule-filter"].value = "DUPLICATE_LEAD";
  await fx.nodes["issue-rule-filter"].fire("change");
  assert.equal(fx.nodes["issues-table-body"].children.length, 1);
  assert.match(fx.nodes["issues-table-body"].textContent, /L02/);
});

test("issue rule selection persists across refresh", async () => {
  const fx = setup(); await load(fx);
  fx.nodes["issue-rule-filter"].value = "DUPLICATE_LEAD";
  await fx.window.BIAI_VALIDATION.reload();
  assert.equal(fx.nodes["issue-rule-filter"].value, "DUPLICATE_LEAD");
});

test("can open a lead from a warning", async () => {
  const fx = setup(); await load(fx);
  await click(qualityButtons(fx)[0]);
  assert.equal(fx.calls.some((c) => c[0] === "openLead" && c[1] === "L25"), true);
});

test("review dialog opens with correct issue and default dismissal", async () => {
  const fx = setup(); await load(fx);
  await click(qualityButtons(fx)[0], 1);
  assert.equal(fx.nodes["issue-review-dialog"].open, true);
  assert.match(fx.nodes["review-issue-details"].textContent, /L25/);
  assert.equal(fx.nodes["review-issue-status"].value, "dismissed");
});

test("issue review requires a reason", async () => {
  const fx = setup(); await load(fx);
  await click(qualityButtons(fx)[0], 1);
  await fx.nodes["issue-review-form"].fire("submit");
  assert.equal(fx.calls.some((c) => c[0] === "review"), false);
  assert.match(fx.nodes["review-issue-feedback"].textContent, /Explain why/);
});

test("issue review sends correct status and reason, then refreshes", async () => {
  const fx = setup(); await load(fx);
  await click(qualityButtons(fx)[0], 1);
  fx.nodes["review-issue-reason"].value = "Verified with sales team";
  fx.nodes["review-issue-status"].value = "dismissed";
  await fx.nodes["issue-review-form"].fire("submit");
  assert.ok(fx.calls.some((c) => c[0] === "review" && c[1] === 1 && c[2] === "dismissed"));
  assert.equal(fx.nodes["quality-open"].textContent, "1");
  assert.equal(fx.nodes["issue-review-dialog"].open, false);
  assert.ok(fx.calls.some((c) => c[0] === "dispatch" && c[1] === "biai:data-changed"));
});

test("API review failure leaves dialog open with a useful error", async () => {
  const fx = setup({ reviewIssue: async () => { throw Error("Server rejected decision"); } });
  await load(fx); await click(qualityButtons(fx)[0], 1);
  fx.nodes["review-issue-reason"].value = "A reason";
  await fx.nodes["issue-review-form"].fire("submit");
  assert.equal(fx.nodes["issue-review-dialog"].open, true);
  assert.match(fx.nodes["review-issue-feedback"].textContent, /Server rejected/);
});

test("scan honors the evaluation date and refreshes saved results", async () => {
  const fx = setup(); await load(fx);
  fx.nodes["evaluation-mode"].checked = true;
  await fx.nodes["run-quality-scan"].fire("click");
  assert.ok(fx.calls.some((c) => c[0] === "scan" && c[1] === true));
  assert.match(fx.nodes["quality-scan-status"].textContent, /2026-11-01/);
  assert.equal(fx.nodes["run-quality-scan"].disabled, false);
});

test("failed scan shows error and re-enables button", async () => {
  const fx = setup({ scanIssues: async () => { throw Error("Timeout"); } });
  await load(fx);
  await fx.nodes["run-quality-scan"].fire("click");
  assert.match(fx.nodes["quality-scan-status"].textContent, /Scan failed: Timeout/);
  assert.equal(fx.nodes["run-quality-scan"].disabled, false);
});

test("duplicate preview renders actual matched fields", async () => {
  const fx = setup(); await load(fx);
  const text = fx.nodes["duplicate-pairs"].textContent;
  assert.match(text, /Meghna Pharma/);
  assert.match(text, /contact_email/);
});

test("duplicate preview loads both lead records", async () => {
  const fx = setup(); await load(fx);
  await click(fx.nodes["duplicate-pairs"].children[0], 3);
  assert.ok(fx.calls.some((c) => c[0] === "lead" && c[1] === "L02"));
  assert.ok(fx.calls.some((c) => c[0] === "lead" && c[1] === "L03"));
  assert.equal(fx.nodes["merge-dialog"].open, true);
  assert.match(fx.nodes["merge-compare"].textContent, /Budget approval pending/);
});

test("merge reviewer can choose which lead survives", async () => {
  const fx = setup(); await load(fx);
  await click(fx.nodes["duplicate-pairs"].children[0], 3);
  fx.nodes["merge-keep-choice"].value = "L03";
  await fx.nodes["merge-keep-choice"].fire("change");
  assert.equal(fx.nodes["merge-surviving-id"].value, "L03");
  assert.equal(fx.nodes["merge-duplicate-id"].value, "L02");
});

test("merge refuses unchecked confirmation", async () => {
  const fx = setup(); await load(fx);
  await click(fx.nodes["duplicate-pairs"].children[0], 3);
  fx.nodes["merge-reason"].value = "Confirmed same contact";
  await fx.nodes["merge-form"].fire("submit");
  assert.equal(fx.calls.some((c) => c[0] === "merge"), false);
  assert.match(fx.nodes["merge-status"].textContent, /confirmation box/);
});

test("merge refuses an empty reason", async () => {
  const fx = setup(); await load(fx);
  await click(fx.nodes["duplicate-pairs"].children[0], 3);
  fx.nodes["merge-confirmed"].checked = true;
  await fx.nodes["merge-form"].fire("submit");
  assert.equal(fx.calls.some((c) => c[0] === "merge"), false);
});

test("merge calls FastAPI only after confirmation and refreshes other screens", async () => {
  const fx = setup(); await load(fx);
  await click(fx.nodes["duplicate-pairs"].children[0], 3);
  fx.nodes["merge-confirmed"].checked = true;
  fx.nodes["merge-reason"].value = "Same company and contact, confirmed";
  await fx.nodes["merge-form"].fire("submit");
  assert.ok(fx.calls.some((c) => c[0] === "merge" && c[1] === "L02" && c[2] === "L03" && c[4] === true));
  assert.equal(fx.nodes["merge-dialog"].open, false);
  assert.equal(fx.nodes["quality-duplicates"].textContent, "0");
  assert.ok(fx.calls.some((c) => c[0] === "dispatch"));
});

test("failed merge keeps review open and preserves records", async () => {
  const fx = setup({ mergeLeads: async () => { throw Error("Cannot merge"); } }); await load(fx);
  await click(fx.nodes["duplicate-pairs"].children[0], 3);
  fx.nodes["merge-confirmed"].checked = true;
  fx.nodes["merge-reason"].value = "Verified";
  await fx.nodes["merge-form"].fire("submit");
  assert.equal(fx.nodes["merge-dialog"].open, true);
  assert.match(fx.nodes["merge-status"].textContent, /Cannot merge/);
});

test("data-change event reloads unless sent by this screen", async () => {
  const fx = setup(); await load(fx);
  const initial = fx.calls.filter((c) => c[0] === "summary").length;
  for (const fn of fx.events["biai:data-changed"] || []) await fn({ detail: { source: "leads" } });
  assert.equal(fx.calls.filter((c) => c[0] === "summary").length, initial + 1);
  for (const fn of fx.events["biai:data-changed"] || []) await fn({ detail: { source: "validation" } });
  assert.equal(fx.calls.filter((c) => c[0] === "summary").length, initial + 1);
});

test("empty issue and duplicate collections show useful messages", async () => {
  const fx = setup(); fx.updateRecords([]); fx.updatePairs([]); await load(fx);
  assert.match(fx.nodes["issues-table-body"].textContent, /No issues match/);
  assert.match(fx.nodes["duplicate-pairs"].textContent, /No suspected duplicate/);
  assert.match(fx.nodes["quality-scan-status"].textContent, /No saved scans yet/);
});

test("missing API displays setup error instead of throwing", async () => {
  const fx = setup(); fx.window.BIAI_API = null;
  for (const fn of fx.events.DOMContentLoaded) await fn();
  assert.match(fx.nodes["quality-scan-status"].textContent, /API client missing/);
});

test("changing the sample date warns that saved issues need a new scan", async () => {
  const fx = setup(); await load(fx);
  fx.nodes["evaluation-mode"].checked = true;
  await fx.nodes["evaluation-mode"].fire("change");
  assert.match(fx.nodes["quality-scan-status"].textContent, /Run validation scan/);
  assert.equal(fx.calls.some((c) => c[0] === "scan"), false);
});
