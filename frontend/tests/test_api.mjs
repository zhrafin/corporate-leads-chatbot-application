/* Step 22: Real api.js tests using Node's built-in test runner.
 * Run from the project root: node --test frontend/tests/test_api.mjs
 * No server, browser, external npm package, or Groq key needed.
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const code = fs.readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../js/api.js"), "utf8"
);

function success(data = { ok: true }, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(data), {
    status, headers: { "content-type": "application/json" },
  });
}

function fixture(fetchImpl = async () => success(), opts = {}) {
  const calls = [];
  const window = {};
  if (opts.baseUrl) window.BIAI_API_BASE_URL = opts.baseUrl;
  const listeners = {};
  const status = { textContent: "", dataset: {}, title: "" };
  const document = opts.document ? {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    getElementById: (id) => id === "api-status" ? status : null,
  } : undefined;
  const fetch = async (url, options) => {
    calls.push({ url, options });
    return fetchImpl(url, options);
  };
  vm.runInNewContext(code, {
    window, document, fetch, URL, AbortController, setTimeout, clearTimeout,
  }, { filename: "api.js" });
  return { api: window.BIAI_API, calls, listeners, status };
}

const lastUrl = (calls) => new URL(calls.at(-1).url);
const lastBody = (calls) => JSON.parse(calls.at(-1).options.body);

test("exposes one shared client without fetching on script load", () => {
  const { api, calls } = fixture();
  assert.ok(Object.isFrozen(api));
  assert.equal(typeof api.getLeads, "function");
  assert.equal(calls.length, 0);
});

test("health check makes a GET with no JSON request body", async () => {
  const { api, calls } = fixture();
  assert.deepEqual(await api.getHealth(), { ok: true });
  assert.equal(lastUrl(calls).pathname, "/api/health");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.body, undefined);
});

test("backend URL can be configured before loading api.js", async () => {
  const { api, calls } = fixture(undefined, { baseUrl: "https://demo.example/api/" });
  await api.getHealth();
  assert.equal(lastUrl(calls).href, "https://demo.example/api/health");
});

test("dashboard sends evaluation_mode=true only when selected", async () => {
  const { api, calls } = fixture();
  await api.getDashboard(true);
  assert.equal(lastUrl(calls).searchParams.get("evaluation_mode"), "true");
  await api.getDashboard(false);
  assert.equal(lastUrl(calls).searchParams.get("evaluation_mode"), "false");
});

test("listLeads keeps false and zero but drops empty filters", async () => {
  const { api, calls } = fixture();
  await api.getLeads({ stage: "Proposal", include_merged: false, offset: 0, company: "", owner_id: null });
  const url = lastUrl(calls);
  assert.equal(url.pathname, "/api/leads");
  assert.equal(url.searchParams.get("stage"), "Proposal");
  assert.equal(url.searchParams.get("include_merged"), "false");
  assert.equal(url.searchParams.get("offset"), "0");
  assert.equal(url.searchParams.has("company"), false);
  assert.equal(url.searchParams.has("owner_id"), false);
});

test("stage and lead IDs are URL encoded", async () => {
  const { api, calls } = fixture();
  await api.getStageTotal("Invoice sent");
  assert.equal(lastUrl(calls).pathname, "/api/pipeline/stages/Invoice%20sent");
  await api.getLead("L 02", true);
  assert.equal(lastUrl(calls).pathname, "/api/leads/L%2002");
  assert.equal(lastUrl(calls).searchParams.get("include_merged"), "true");
});

test("does not allow empty lead IDs", async () => {
  const { api, calls } = fixture();
  assert.throws(() => api.getLead("  "), /Lead ID is required/);
  assert.equal(calls.length, 0);
});

test("priority endpoint sends limit and date mode", async () => {
  const { api, calls } = fixture();
  await api.getPriorities(true, 10);
  assert.equal(lastUrl(calls).pathname, "/api/pipeline/priorities");
  assert.equal(lastUrl(calls).searchParams.get("limit"), "10");
});

test("lead edits use PATCH with JSON request body", async () => {
  const { api, calls } = fixture();
  await api.updateLead("L25", { next_follow_up: "2026-11-05" });
  assert.equal(lastUrl(calls).pathname, "/api/leads/L25");
  assert.equal(calls[0].options.method, "PATCH");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(lastBody(calls), { next_follow_up: "2026-11-05" });
});

test("stage changes include the human-provided reason", async () => {
  const { api, calls } = fixture();
  await api.updateStage("L04", "Meeting", "Meeting completed");
  assert.equal(lastUrl(calls).pathname, "/api/leads/L04/stage");
  assert.deepEqual(lastBody(calls), { stage: "Meeting", reason: "Meeting completed" });
});

test("assignOwner can explicitly clear an owner using null", async () => {
  const { api, calls } = fixture();
  await api.assignOwner("L09", null);
  assert.deepEqual(lastBody(calls), { owner_id: null });
});

test("merge request forwards confirmation rather than forcing it", async () => {
  const { api, calls } = fixture();
  await api.mergeLeads("L01", "L23", "Confirmed same company", false);
  assert.equal(lastUrl(calls).pathname, "/api/leads/merge");
  assert.equal(lastBody(calls).confirmed, false);
  assert.equal(lastBody(calls).duplicate_id, "L23");
});

test("issue scan uses POST with query parameter and no JSON body", async () => {
  const { api, calls } = fixture();
  await api.scanIssues(true);
  assert.equal(lastUrl(calls).pathname, "/api/issues/scan");
  assert.equal(lastUrl(calls).searchParams.get("evaluation_mode"), "true");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.body, undefined);
});

test("issue review sends status and reason", async () => {
  const { api, calls } = fixture();
  await api.reviewIssue(7, "dismissed", "Verified details");
  assert.equal(lastUrl(calls).pathname, "/api/issues/7/status");
  assert.deepEqual(lastBody(calls), { status: "dismissed", reason: "Verified details" });
});

test("chat includes session ID and evaluation mode", async () => {
  const { api, calls } = fixture();
  await api.chat("Which deals are overdue?", "demo-1", true);
  assert.equal(lastUrl(calls).pathname, "/api/ai/chat");
  assert.deepEqual(lastBody(calls), {
    message: "Which deals are overdue?", session_id: "demo-1", evaluation_mode: true,
  });
});

test("email endpoint requests a draft but no email is sent", async () => {
  const { api, calls } = fixture();
  await api.draftEmail("L02");
  assert.equal(lastUrl(calls).pathname, "/api/ai/draft-email");
  assert.deepEqual(lastBody(calls), { lead_id: "L02" });
});

test("CSV export returns a Blob and preserves include_merged", async () => {
  const { api, calls } = fixture(async () => new Response("lead_id,company\nL01,Padma", {
    headers: { "content-type": "text/csv" },
  }));
  const blob = await api.exportCsv(true);
  assert.equal(lastUrl(calls).pathname, "/api/reports/export.csv");
  assert.equal(lastUrl(calls).searchParams.get("include_merged"), "true");
  assert.equal(await blob.text(), "lead_id,company\nL01,Padma");
});

test("FastAPI field errors include their field name", async () => {
  const { api } = fixture(async () => success({
    detail: [{ loc: ["body", "lead_id"], msg: "Field required" }],
  }, 422));
  await assert.rejects(() => api.createLead({}), (error) => {
    assert.equal(error.status, 422);
    assert.match(error.message, /lead_id: Field required/);
    return true;
  });
});

test("FastAPI string errors preserve the server message and status", async () => {
  const { api } = fixture(async () => success({ detail: "Lead not found." }, 404));
  await assert.rejects(() => api.getLead("L999"), (error) => {
    assert.equal(error.status, 404);
    assert.equal(error.message, "Lead not found.");
    return true;
  });
});

test("plain-text HTTP error gets a safe fallback", async () => {
  const { api } = fixture(async () => new Response("server trace", { status: 500 }));
  await assert.rejects(() => api.getHealth(), /Request failed \(HTTP 500\)/);
});

test("network errors become an understandable message", async () => {
  const { api } = fixture(async () => { throw new TypeError("Failed to fetch"); });
  await assert.rejects(() => api.getHealth(), /Cannot reach FastAPI/);
});

test("slow requests are aborted and show a timeout message", async () => {
  const { api } = fixture((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  }));
  await assert.rejects(() => api.request("/dashboard", { timeoutMs: 5 }), /took too long/);
});

test("204 No Content returns null", async () => {
  const { api } = fixture(async () => success(null, 204));
  assert.equal(await api.request("/health"), null);
});

test("health indicator reflects successful backend connection", async () => {
  const { listeners, status } = fixture(async () => success({ status: "ok" }), { document: true });
  await listeners.DOMContentLoaded();
  assert.equal(status.textContent, "API connected");
  assert.equal(status.dataset.status, "connected");
});

test("health indicator reports offline status on connection failure", async () => {
  const { listeners, status } = fixture(async () => { throw new Error("offline"); }, { document: true });
  await listeners.DOMContentLoaded();
  assert.equal(status.textContent, "API offline");
  assert.equal(status.dataset.status, "error");
});
