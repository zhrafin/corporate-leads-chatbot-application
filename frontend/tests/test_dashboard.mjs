import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const code = fs.readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../js/dashboard.js"), "utf8"
);

// Small HTML element substitute for testing DOM updates without extra packages.
class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.classes = new Set();
    this.classList = {
      toggle: (name, active) => {
        if (active) this.classes.add(name);
        else this.classes.delete(name);
      },
    };
    this.disabled = false;
    this.checked = false;
    this.className = "";
    this._text = "";
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ""; this.children = children; }
  addEventListener(event, callback) { this.listeners[event] = callback; }
  fire(event) { return this.listeners[event]?.(); }
}

const IDS = [
  "dashboard", "refresh-dashboard", "evaluation-mode", "dashboard-date-note",
  "metric-total-leads", "metric-active-value", "metric-overdue", "metric-unassigned",
  "dashboard-stages", "dashboard-priorities",
];

const stages = [
  "New", "Contacted", "Meeting", "Proposal", "Invoice sent", "Paid", "Lost",
];

function stats(overrides = {}) {
  return {
    as_of: "2026-11-04",
    current_leads: 25,
    active_pipeline_value_bdt: 3920000,
    overdue_leads: 12,
    unassigned_leads: 25,
    stage_summary: stages.map((stage, i) => ({
      stage, lead_count: i + 1, total_value_bdt: i * 2000,
    })),
    ...overrides,
  };
}

function priorities() {
  return [{
    rank: 1, lead_id: "L22", company: "Khulna Jute Works", stage: "Invoice sent",
    deal_value_bdt: 400000, followup_status: "overdue",
    requires_date_review: false, recommended_action: "Check payment before chasing.",
  }];
}

function fixture(apiOverrides = {}) {
  const calls = { dashboard: [], priorities: [] };
  const nodes = Object.fromEntries(IDS.map((id) => [id, new Element()]));
  const events = {};
  const window = {
    BIAI_API: {
      getDashboard: async (mode) => {
        calls.dashboard.push(mode);
        return stats();
      },
      getPriorities: async (mode, limit) => {
        calls.priorities.push({ mode, limit });
        return priorities();
      },
      ...apiOverrides,
    },
    addEventListener: (name, callback) => { events[name] = callback; },
  };
  const document = {
    getElementById: (id) => nodes[id] || null,
    createElement: (tag) => new Element(tag),
    addEventListener: (name, callback) => { events[name] = callback; },
  };
  vm.runInNewContext(code, { window, document, Date, Intl, Number, Map, Promise }, {
    filename: "dashboard.js",
  });
  return { calls, nodes, window, events };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("dashboard waits for DOMContentLoaded before fetching", async () => {
  const { calls, events } = fixture();
  assert.deepEqual(calls.dashboard, []);
  events.DOMContentLoaded();
  await flush();
  assert.deepEqual(calls.dashboard, [false]);
  assert.deepEqual(calls.priorities, [{ mode: false, limit: 5 }]);
});

test("renders four KPI cards with data from the API", async () => {
  const { nodes, events } = fixture();
  events.DOMContentLoaded();
  await flush();
  assert.equal(nodes["metric-total-leads"].textContent, "25");
  assert.match(nodes["metric-active-value"].textContent, /^BDT 3[,.]920[,.]000$|^BDT 39,20,000$/);
  assert.equal(nodes["metric-overdue"].textContent, "12");
  assert.equal(nodes["metric-unassigned"].textContent, "25");
  assert.match(nodes["dashboard-date-note"].textContent, /Live date: 4 Nov 2026/);
});

test("renders seven stages in the official order", async () => {
  const { nodes, events } = fixture();
  events.DOMContentLoaded();
  await flush();
  const rows = nodes["dashboard-stages"].children;
  assert.equal(rows.length, 7);
  assert.deepEqual(rows.map((row) => row.children[0].children[0].textContent), stages);
  assert.match(rows[3].textContent, /4 lead\(s\)/);
});

test("priority entries show the real recommended action", async () => {
  const { nodes, events } = fixture();
  events.DOMContentLoaded();
  await flush();
  const list = nodes["dashboard-priorities"];
  assert.equal(list.children.length, 1);
  assert.match(list.textContent, /L22/);
  assert.match(list.textContent, /Check payment before chasing/);
  assert.match(list.textContent, /Overdue/);
});

test("API company names are assigned as text, not innerHTML", async () => {
  const { nodes, events } = fixture({
    getPriorities: async () => [{ ...priorities()[0], company: "<script>bad()</script>" }],
  });
  events.DOMContentLoaded();
  await flush();
  assert.match(nodes["dashboard-priorities"].textContent, /<script>bad\(\)<\/script>/);
  assert.equal(nodes["dashboard-priorities"].children[0].children[0].children.length, 0);
});

test("refresh button calls the API again", async () => {
  const { nodes, events, calls } = fixture();
  events.DOMContentLoaded();
  await flush();
  nodes["refresh-dashboard"].fire("click");
  await flush();
  assert.deepEqual(calls.dashboard, [false, false]);
  assert.equal(nodes["refresh-dashboard"].disabled, false);
});

test("date-mode checkbox reloads both endpoints in evaluation mode", async () => {
  const { nodes, events, calls } = fixture({
    getDashboard: async (mode) => {
      calls.dashboard.push(mode);
      return stats({ as_of: mode ? "2026-11-01" : "2026-11-04", overdue_leads: mode ? 9 : 12 });
    },
  });
  events.DOMContentLoaded();
  await flush();
  nodes["evaluation-mode"].checked = true;
  nodes["evaluation-mode"].fire("change");
  await flush();
  assert.equal(nodes["metric-overdue"].textContent, "9");
  assert.match(nodes["dashboard-date-note"].textContent, /Sample reference date: 1 Nov 2026/);
  assert.deepEqual(calls.dashboard, [false, true]);
  assert.equal(calls.priorities.at(-1).mode, true);
});

test("empty database shows zero KPIs and useful guidance", async () => {
  const { nodes, events } = fixture({
    getDashboard: async () => stats({ current_leads: 0, overdue_leads: 0, unassigned_leads: 0, active_pipeline_value_bdt: 0 }),
    getPriorities: async () => [],
  });
  events.DOMContentLoaded();
  await flush();
  assert.equal(nodes["metric-total-leads"].textContent, "0");
  assert.match(nodes["dashboard-date-note"].textContent, /Import the sample CSV/);
  assert.match(nodes["dashboard-priorities"].textContent, /No active leads/);
});

test("backend failure shows an error without displaying invented values", async () => {
  const { nodes, events } = fixture({
    getDashboard: async () => { throw new Error("Cannot reach FastAPI"); },
  });
  events.DOMContentLoaded();
  await flush();
  assert.equal(nodes["metric-total-leads"].textContent, "—");
  assert.match(nodes["dashboard-date-note"].textContent, /Cannot reach FastAPI/);
  assert.equal(nodes["dashboard-date-note"].classes.has("is-error"), true);
});

test("priorities may fail while stage data still displays", async () => {
  const { nodes, events } = fixture({
    getPriorities: async () => { throw new Error("Temporary failure"); },
  });
  events.DOMContentLoaded();
  await flush();
  assert.equal(nodes["metric-total-leads"].textContent, "25");
  assert.match(nodes["dashboard-priorities"].textContent, /Priority list unavailable/);
});

test("late response for an earlier mode does not replace new data", async () => {
  const first = deferred();
  const { nodes, events } = fixture({
    getDashboard: (mode) => mode ? Promise.resolve(stats({ as_of: "2026-11-01", overdue_leads: 9 })) : first.promise,
  });
  events.DOMContentLoaded(); // first request is still pending
  nodes["evaluation-mode"].checked = true;
  nodes["evaluation-mode"].fire("change");
  await flush();
  assert.equal(nodes["metric-overdue"].textContent, "9");
  first.resolve(stats({ overdue_leads: 500 }));
  await flush();
  assert.equal(nodes["metric-overdue"].textContent, "9");
});

test("data-changed event refreshes the dashboard", async () => {
  const { events, calls } = fixture();
  events.DOMContentLoaded();
  await flush();
  events["biai:data-changed"]();
  await flush();
  assert.equal(calls.dashboard.length, 2);
});

test("invalid priority date is marked for review, not overdue outreach", async () => {
  const { nodes, events } = fixture({
    getPriorities: async () => [{ ...priorities()[0], lead_id: "L25", followup_status: "invalid_date", requires_date_review: true }],
  });
  events.DOMContentLoaded();
  await flush();
  assert.match(nodes["dashboard-priorities"].textContent, /Date needs review/);
  assert.doesNotMatch(nodes["dashboard-priorities"].textContent, /Overdue/);
});

test("a pending request disables refresh until it finishes", async () => {
  const pending = deferred();
  const { nodes, events } = fixture({ getDashboard: () => pending.promise });
  events.DOMContentLoaded();
  assert.equal(nodes["refresh-dashboard"].disabled, true);
  pending.resolve(stats());
  await flush();
  assert.equal(nodes["refresh-dashboard"].disabled, false);
});
