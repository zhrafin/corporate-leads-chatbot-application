(function (window, document) {
  "use strict";

  const STAGES = [
    "New", "Contacted", "Meeting", "Proposal", "Invoice sent", "Paid", "Lost",
  ];

  // Keep a simple counter so a slower OLD request cannot replace newer results.
  let latestRequest = 0;

  const element = (id) => document.getElementById(id);
  const numberFormatter = new Intl.NumberFormat("en-BD");

  function numberText(value) {
    return Number.isFinite(value) ? numberFormatter.format(value) : "—";
  }

  function moneyText(value) {
    return Number.isFinite(value) ? `BDT ${numberText(value)}` : "—";
  }

  // YYYY-MM-DD is parsed as UTC to avoid off-by-one dates in different time zones.
  function friendlyDate(isoDate) {
    if (typeof isoDate !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(isoDate)) {
      return "unknown date";
    }
    const [year, month, day] = isoDate.split("-").map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    if (Number.isNaN(date.getTime()) || date.getUTCDate() !== day ||
        date.getUTCMonth() !== month - 1) {
      return "unknown date";
    }
    return new Intl.DateTimeFormat("en-GB", {
      day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
    }).format(date);
  }

  // Text nodes protect the page from names or notes containing HTML markup.
  function node(tag, text, className = "") {
    const item = document.createElement(tag);
    if (className) item.className = className;
    item.textContent = String(text);
    return item;
  }

  function showMessage(container, message, tag = "p") {
    if (!container) return;
    container.replaceChildren(node(tag, message, "empty-state"));
  }

  function setKpis(stats) {
    element("metric-total-leads").textContent = numberText(stats.current_leads);
    element("metric-active-value").textContent = moneyText(stats.active_pipeline_value_bdt);
    element("metric-overdue").textContent = numberText(stats.overdue_leads);
    element("metric-unassigned").textContent = numberText(stats.unassigned_leads);
  }

  function clearKpis() {
    ["metric-total-leads", "metric-active-value", "metric-overdue", "metric-unassigned"]
      .forEach((id) => { element(id).textContent = "—"; });
  }

  function renderStages(stageSummary) {
    const container = element("dashboard-stages");
    const byStage = new Map(
      (Array.isArray(stageSummary) ? stageSummary : []).map((entry) => [entry.stage, entry])
    );

    const rows = STAGES.map((stage) => {
      const data = byStage.get(stage) || { lead_count: 0, total_value_bdt: 0 };
      const row = document.createElement("div");
      row.className = "stage-summary__item";

      const description = document.createElement("div");
      description.append(
        node("strong", stage),
        node("small", `${numberText(data.lead_count)} lead(s)`, "muted")
      );

      row.append(description, node("span", moneyText(data.total_value_bdt), "stage-summary__value"));
      return row;
    });
    container.replaceChildren(...rows);
  }

  function renderPriorities(leads) {
    const list = element("dashboard-priorities");
    if (!Array.isArray(leads) || leads.length === 0) {
      showMessage(list, "No active leads to prioritize. Import or create leads first.", "li");
      return;
    }

    const items = leads.map((lead, index) => {
      const item = document.createElement("li");
      const title = node("strong", `${lead.rank || index + 1}. ${lead.company} (${lead.lead_id})`);
      const facts = node("p", `${lead.stage} · ${moneyText(lead.deal_value_bdt)}`, "muted");
      const action = node("p", lead.recommended_action || "Review this lead's notes.");
      item.append(title, facts, action);
      if (lead.requires_date_review) {
        item.append(node("span", "Date needs review", "badge badge--warning"));
      } else if (lead.followup_status === "overdue") {
        item.append(node("span", "Overdue", "badge badge--danger"));
      }
      return item;
    });

    list.replaceChildren(...items);
  }

  function setNotice(message, isError = false) {
    const note = element("dashboard-date-note");
    note.textContent = message;
    note.classList.toggle("is-error", isError);
  }

  async function loadDashboard() {
    if (!window.BIAI_API) {
      setNotice("API client missing. Check that js/api.js loaded before dashboard.js.", true);
      return;
    }

    const requestId = ++latestRequest;
    const evaluationMode = element("evaluation-mode").checked;
    const refreshButton = element("refresh-dashboard");
    refreshButton.disabled = true;
    setNotice("Loading dashboard from FastAPI...");

    // A failed priority request should NOT hide successful dashboard figures.
    const [dashboardResult, priorityResult] = await Promise.allSettled([
      window.BIAI_API.getDashboard(evaluationMode),
      window.BIAI_API.getPriorities(evaluationMode, 5),
    ]);

    // The user may have changed the date mode while the first request was running.
    if (requestId !== latestRequest) return;
    refreshButton.disabled = false;

    if (dashboardResult.status === "rejected") {
      clearKpis();
      showMessage(element("dashboard-stages"), "Could not load stage statistics.");
      showMessage(element("dashboard-priorities"), "Could not load priorities.", "li");
      const message = dashboardResult.reason?.message || "Unknown error";
      setNotice(`Dashboard unavailable: ${message}`, true);
      return;
    }

    const stats = dashboardResult.value;
    setKpis(stats);
    renderStages(stats.stage_summary);

    if (priorityResult.status === "fulfilled") {
      renderPriorities(priorityResult.value);
    } else {
      showMessage(element("dashboard-priorities"), "Priority list unavailable. Try Refresh data.", "li");
    }

    const mode = evaluationMode ? "Sample reference date" : "Live date";
    const leadHint = stats.current_leads === 0
      ? " No leads found. Import the sample CSV from the backend."
      : " Statistics use your current saved leads.";
    const priorityWarning = priorityResult.status === "rejected"
      ? " Priority list could not be loaded."
      : "";
    setNotice(`${mode}: ${friendlyDate(stats.as_of)}.${leadHint}${priorityWarning}`);
  }

  function init() {
    if (!element("dashboard") || !element("refresh-dashboard") || !element("evaluation-mode")) {
      return;
    }
    element("refresh-dashboard").addEventListener("click", loadDashboard);
    element("evaluation-mode").addEventListener("change", loadDashboard);

    // Later files (leads.js and validation.js) can dispatch this event after edits.
    window.addEventListener("biai:data-changed", loadDashboard);
    loadDashboard();
  }

  window.BIAI_Dashboard = Object.freeze({ load: loadDashboard });
  document.addEventListener("DOMContentLoaded", init);
})(window, document);
