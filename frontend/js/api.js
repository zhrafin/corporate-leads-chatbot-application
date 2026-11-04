
(function (window) {
  "use strict";

  const baseUrl = (window.BIAI_API_BASE_URL || "http://127.0.0.1:8000/api")
    .replace(/\/+$/, "");

  // A helpful error type lets the UI distinguish HTTP errors from connection errors.
  class ApiError extends Error {
    constructor(message, status = 0, details = null) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.details = details;
    }
  }

  // FastAPI returns {detail: "..."} or a list of Pydantic field errors.
  function readableError(payload, fallback) {
    const detail = payload && payload.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const messages = detail.map((item) => {
        const location = Array.isArray(item.loc)
          ? item.loc.filter((part) => part !== "body").join(".")
          : "";
        return `${location ? location + ": " : ""}${item.msg || "Invalid value"}`;
      });
      if (messages.length) return messages.join("; ");
    }
    return fallback;
  }

  // Skip undefined, null, and empty-string filters, but keep false and zero.
  function makeUrl(path, params = {}) {
    const url = new URL(baseUrl + path);
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });
    return url.toString();
  }

  function requiredId(value, name = "ID") {
    const id = String(value ?? "").trim();
    if (!id) throw new ApiError(`${name} is required.`);
    return encodeURIComponent(id);
  }

  // All HTTP calls come through this function, so errors work the same way.
  async function request(path, options = {}) {
    const {
      method = "GET",
      params = {},
      data,
      asBlob = false,
      timeoutMs = 15000,
      signal,
    } = options;

    const controller = new AbortController();
    let timedOut = false;
    const onAbort = () => controller.abort();
    if (signal) {
      if (signal.aborted) controller.abort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);

    const headers = { Accept: asBlob ? "text/csv" : "application/json" };
    const fetchOptions = { method, headers, signal: controller.signal };
    if (data !== undefined) {
      headers["Content-Type"] = "application/json";
      fetchOptions.body = JSON.stringify(data);
    }

    try {
      const response = await fetch(makeUrl(path, params), fetchOptions);
      const contentType = response.headers.get("content-type") || "";
      let result = null;

      if (response.status !== 204) {
        if (asBlob && response.ok) {
          return await response.blob();
        }
        if (contentType.includes("json")) {
          result = await response.json();
        } else {
          result = await response.text();
        }
      }

      if (!response.ok) {
        const message = readableError(result, `Request failed (HTTP ${response.status}).`);
        throw new ApiError(message, response.status, result);
      }
      return result;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (timedOut) throw new ApiError("The server took too long to respond. Please retry.");
      if (controller.signal.aborted) throw new ApiError("Request cancelled.");
      throw new ApiError(
        "Cannot reach FastAPI. Start the backend at http://127.0.0.1:8000 " +
        "and check that the frontend is served on port 5500."
      );
    } finally {
      clearTimeout(timeout);
      if (signal) signal.removeEventListener("abort", onAbort);
    }
  }

  // Small helpers to keep the endpoint methods easy to read.
  const get = (path, params, options) => request(path, { ...options, params });
  const post = (path, data, options) => request(path, { ...options, method: "POST", data });
  const patch = (path, data, options) => request(path, { ...options, method: "PATCH", data });

  // These names will be used by dashboard.js, leads.js, validation.js, etc.
  const api = {
    baseUrl,
    ApiError,
    request,

    // System
    getHealth: () => get("/health", undefined, { timeoutMs: 5000 }),

    // Dashboard and pipeline
    getDashboard: (evaluationMode = false) =>
      get("/dashboard", { evaluation_mode: evaluationMode }),
    getStages: () => get("/pipeline/stages"),
    getStageTotal: (stage) =>
      get(`/pipeline/stages/${requiredId(stage, "Stage")}`),
    getOverdueLeads: (evaluationMode = false) =>
      get("/pipeline/overdue", { evaluation_mode: evaluationMode }),
    getOverdueSummary: (evaluationMode = false) =>
      get("/pipeline/overdue/summary", { evaluation_mode: evaluationMode }),
    getPriorities: (evaluationMode = false, limit = 5) =>
      get("/pipeline/priorities", { evaluation_mode: evaluationMode, limit }),
    getWeeklySummary: (evaluationMode = false, limit = 5) =>
      get("/pipeline/weekly-summary", { evaluation_mode: evaluationMode, limit }),

    // Lead management
    getLeads: (filters = {}) => get("/leads", filters),
    getLead: (leadId, includeMerged = false) =>
      get(`/leads/${requiredId(leadId, "Lead ID")}`, { include_merged: includeMerged }),
    createLead: (lead) => post("/leads", lead),
    updateLead: (leadId, changes) =>
      patch(`/leads/${requiredId(leadId, "Lead ID")}`, changes),
    updateStage: (leadId, stage, reason = null) =>
      patch(`/leads/${requiredId(leadId, "Lead ID")}/stage`, { stage, reason }),
    assignOwner: (leadId, ownerId) =>
      patch(`/leads/${requiredId(leadId, "Lead ID")}/owner`, { owner_id: ownerId }),
    getLeadActivities: (leadId) =>
      get(`/leads/${requiredId(leadId, "Lead ID")}/activities`),
    mergeLeads: (survivingId, duplicateId, reason, confirmed) =>
      post("/leads/merge", {
        surviving_id: survivingId,
        duplicate_id: duplicateId,
        reason,
        confirmed, // FastAPI requires true. UI must ask the user first.
      }),
    getOwners: () => get("/owners"),
    createOwner: (owner) => post("/owners", owner),

    // Data-quality management
    getIssues: (filters = {}) => get("/issues", filters),
    getIssueSummary: () => get("/issues/summary"),
    getDuplicatePairs: () => get("/issues/duplicates"),
    scanIssues: (evaluationMode = false) =>
      post(`/issues/scan`, undefined, { params: { evaluation_mode: evaluationMode } }),
    getIssue: (issueId) => get(`/issues/${requiredId(issueId, "Issue ID")}`),
    reviewIssue: (issueId, status, reason) =>
      patch(`/issues/${requiredId(issueId, "Issue ID")}/status`, { status, reason }),

    // Reports and export
    getReportSummary: (evaluationMode = false) =>
      get("/reports/summary", { evaluation_mode: evaluationMode }),
    getEvaluation: () => get("/reports/evaluation"),
    exportCsv: (includeMerged = false) =>
      request("/reports/export.csv", { params: { include_merged: includeMerged }, asBlob: true }),

    // AI. The session ID must be reused for follow-up questions.
    chat: (message, sessionId = null, evaluationMode = false) =>
      post("/ai/chat", {
        message,
        session_id: sessionId,
        evaluation_mode: evaluationMode,
      }, { timeoutMs: 90000 }),
    draftEmail: (leadId) =>
      post("/ai/draft-email", { lead_id: leadId }, { timeoutMs: 90000 }),
  };

  // Make the API available to other plain <script> files (no build tools needed).
  window.BIAI_API = Object.freeze(api);

  // This is the only UI action in Step 22: show actual server connectivity.
  // The other screens will load their real data in the next JavaScript steps.
  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", async () => {
      const status = document.getElementById("api-status");
      if (!status) return;
      status.textContent = "Checking API...";
      try {
        await api.getHealth();
        status.textContent = "API connected";
        status.dataset.status = "connected";
      } catch (error) {
        status.textContent = "API offline";
        status.dataset.status = "error";
        status.title = error.message;
      }
    });
  }
})(window);
