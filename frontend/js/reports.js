(function (window, document) {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const numberFormatter = new Intl.NumberFormat("en-BD");
  let requestVersion = 0;
  let exporting = false;

  function numberText(value) {
    return Number.isFinite(value) ? numberFormatter.format(value) : "—";
  }

  function moneyText(value) {
    return Number.isFinite(value) ? `BDT ${numberText(value)}` : "—";
  }

  function percentText(value) {
    // FastAPI returns a fraction such as 0.88, not 88.
    return Number.isFinite(value) && value >= 0 && value <= 1
      ? `${(value * 100).toFixed(1)}%`
      : "Not available";
  }

  function dateText(value) {
    // The backend uses YYYY-MM-DD. Avoid the browser's local timezone.
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      return "unknown date";
    }
    const [year, month, day] = value.split("-").map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 ||
        date.getUTCDate() !== day) return "unknown date";
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "UTC", year: "numeric", month: "short", day: "numeric",
    }).format(date);
  }

  // Always create text nodes. Company names and notes are untrusted data.
  function textNode(tag, text, className = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = String(text ?? "");
    return node;
  }

  function showEmpty(id, message) {
    const container = byId(id);
    if (container) container.replaceChildren(textNode("p", message, "empty-state"));
  }

  function status(message, error = false) {
    const item = byId("reports-status");
    if (!item) return;
    item.textContent = message;
    item.classList.toggle("is-error", error);
  }

  function setValue(id, text) {
    const item = byId(id);
    if (item) item.textContent = String(text);
  }

  function clearSummary() {
    ["report-current-leads", "report-pipeline-value", "report-overdue",
      "report-issues", "report-pending-duplicates", "report-merged-records"]
      .forEach((id) => setValue(id, "—"));
  }

  function renderSummary(report) {
    const dashboard = report.dashboard;
    const issues = report.issues;
    if (!dashboard || !issues) throw new Error("Report summary is missing required fields.");

    setValue("report-current-leads", numberText(dashboard.current_leads));
    setValue("report-pipeline-value", moneyText(dashboard.active_pipeline_value_bdt));
    setValue("report-overdue", numberText(dashboard.overdue_leads));
    setValue("report-issues", numberText(issues.open_issues));
    setValue("report-pending-duplicates", numberText(report.pending_duplicate_pairs));
    setValue("report-merged-records", numberText(report.merged_records));
  }

  function clearEvaluation(message) {
    ["evaluation-total", "evaluation-flagged", "evaluation-reviewed", "evaluation-correct"]
      .forEach((id) => setValue(id, "—"));
    setValue("evaluation-accuracy", "Not verified");
    setValue("evaluation-precision", "Not verified");
    setValue("evaluation-recall", "Not verified");
    setValue("evaluation-note", message);
    showEmpty("evaluation-findings", message);
  }

  function renderEvaluation(report) {
    setValue("evaluation-total", numberText(report.original_leads));
    setValue("evaluation-flagged", numberText(report.source_data_flagged_leads));
    setValue("evaluation-reviewed", `${numberText(report.manually_reviewed_leads)} / ${numberText(report.original_leads)}`);

    // Metrics are ONLY trustworthy after every original lead is reviewed.
    const complete = report.verification_status === "complete" && report.metrics;
    setValue("evaluation-accuracy", complete ? percentText(report.metrics.accuracy) : "Not verified");
    setValue("evaluation-precision", complete ? percentText(report.metrics.precision) : "Not verified");
    setValue("evaluation-recall", complete ? percentText(report.metrics.recall) : "Not verified");
    setValue("evaluation-correct", complete
      ? `${numberText(report.metrics.correctly_classified_leads)} / ${numberText(report.original_leads)}`
      : "Not verified");
    setValue("evaluation-note", complete
      ? "All original leads were manually reviewed. Metrics compare detection with those labels."
      : `Detection is not verification. Review all ${numberText(report.original_leads)} original leads ` +
        "in data/evaluation_labels.json to calculate accuracy.");

    const container = byId("evaluation-findings");
    if (!container) return;

    const overview = document.createElement("div");
    overview.className = "report-finding-summary";
    overview.append(
      textNode("p", `Reference date: ${dateText(report.as_of)} (original CSV, unchanged by later edits).`),
      textNode("p", `${numberText(report.overdue_leads)} overdue lead(s) on the original reference date. ` +
        "Overdue work is counted separately from source-data problems."),
      textNode("p", `${numberText((report.duplicate_pairs || []).length)} suspected duplicate pair(s). ` +
        "Each merge requires human approval.")
    );

    const dateHeading = textNode("h4", "Invalid date-order records");
    const dateList = textNode("p", (report.invalid_date_order_leads || []).length
      ? report.invalid_date_order_leads.join(", ")
      : "No date-order errors detected.", "muted");

    const duplicatesHeading = textNode("h4", "Duplicate candidates");
    const duplicateList = document.createElement("ul");
    duplicateList.className = "report-findings-list";
    if (!Array.isArray(report.duplicate_pairs) || report.duplicate_pairs.length === 0) {
      duplicateList.append(textNode("li", "No duplicate pairs detected."));
    } else {
      report.duplicate_pairs.forEach((pair) => {
        duplicateList.append(textNode("li",
          `${pair.lead_id_1}: ${pair.company_1} / ${pair.lead_id_2}: ${pair.company_2}`));
      });
    }

    const rulesHeading = textNode("h4", "Detection counts by rule");
    const ruleList = document.createElement("ul");
    ruleList.className = "report-findings-list";
    const entries = Object.entries(report.counts_by_rule || {}).sort(([a], [b]) => a.localeCompare(b));
    if (!entries.length) {
      ruleList.append(textNode("li", "No rule warnings detected."));
    } else {
      entries.forEach(([rule, count]) => {
        ruleList.append(textNode("li", `${rule.replaceAll("_", " ")}: ${numberText(count)} lead(s)`));
      });
    }

    container.replaceChildren(overview, dateHeading, dateList,
      duplicatesHeading, duplicateList, rulesHeading, ruleList);
  }

  function renderWeekly(report) {
    const container = byId("weekly-summary");
    if (!container) return;

    const header = document.createElement("div");
    header.className = "weekly-summary__overview";
    header.append(
      textNode("p", `Date: ${dateText(report.as_of)} · ` +
        `${numberText(report.overdue_active_leads)} overdue active deal(s) · ` +
        `${moneyText(report.overdue_value_bdt)} overdue deal value.`),
      textNode("p", "Priorities use Python scoring rules. This is not AI-written text.", "muted")
    );

    const leads = Array.isArray(report.top_leads) ? report.top_leads : [];
    if (!leads.length) {
      container.replaceChildren(header, textNode("p", "No active leads to prioritize.", "empty-state"));
      return;
    }

    const list = document.createElement("ol");
    list.className = "priority-list weekly-priority-list";
    leads.forEach((lead, index) => {
      const item = document.createElement("li");
      item.append(
        textNode("strong", `${lead.rank || index + 1}. ${lead.company} (${lead.lead_id})`),
        textNode("p", `${lead.stage} · ${moneyText(lead.deal_value_bdt)} · ` +
          `Priority score: ${numberText(lead.priority_score)}`, "muted"),
        textNode("p", lead.recommended_action || "Review lead notes before following up.")
      );
      if (lead.requires_date_review) {
        item.append(textNode("span", "Date needs review", "badge badge--warning"));
      } else if (lead.followup_status === "overdue") {
        item.append(textNode("span", "Overdue", "badge badge--danger"));
      }
      list.append(item);
    });

    container.replaceChildren(header, list, textNode("p", report.note || "", "muted"));
  }

  async function loadReports() {
    const api = window.BIAI_API;
    if (!api) {
      status("API client missing. Check that api.js loads before reports.js.", true);
      return;
    }

    const version = ++requestVersion;
    const evaluationMode = Boolean(byId("evaluation-mode")?.checked);
    const refresh = byId("refresh-reports");
    refresh.disabled = true;
    status("Loading reports from FastAPI...");

    // Independent results: a missing archived CSV must not hide the live report.
    const results = await Promise.allSettled([
      api.getReportSummary(evaluationMode),
      api.getEvaluation(),
      api.getWeeklySummary(evaluationMode, 5),
    ]);
    if (version !== requestVersion) return; // Ignore an outdated date-mode response.
    refresh.disabled = false;

    const [summary, evaluation, weekly] = results;
    const errors = [];

    if (summary.status === "fulfilled") {
      try { renderSummary(summary.value); }
      catch (error) { clearSummary(); errors.push(`Summary: ${error.message}`); }
    } else {
      clearSummary();
      errors.push(`Summary: ${summary.reason?.message || "Unavailable"}`);
    }

    if (evaluation.status === "fulfilled") {
      try { renderEvaluation(evaluation.value); }
      catch (error) {
        clearEvaluation("Evaluation response could not be displayed.");
        errors.push(`Evaluation: ${error.message}`);
      }
    } else {
      clearEvaluation("Original CSV evaluation unavailable. Import the sample CSV first.");
      errors.push(`Evaluation: ${evaluation.reason?.message || "Unavailable"}`);
    }

    if (weekly.status === "fulfilled") {
      try { renderWeekly(weekly.value); }
      catch (error) { showEmpty("weekly-summary", "Weekly summary could not be displayed."); errors.push(`Weekly priorities: ${error.message}`); }
    } else {
      showEmpty("weekly-summary", "Weekly summary unavailable. Try Refresh reports.");
      errors.push(`Weekly priorities: ${weekly.reason?.message || "Unavailable"}`);
    }

    const reportDate = summary.status === "fulfilled" ? summary.value?.as_of : null;
    const mode = evaluationMode ? "Sample date" : "Live date";
    const scanNote = "Saved warning counts reflect the last scan. Run a new scan in Data Quality to update them.";
    status(`${mode}: ${dateText(reportDate)}. ${scanNote}` +
      (errors.length ? ` Some sections failed: ${errors.join("; ")}` : ""), errors.length > 0);
  }

  async function downloadCsv() {
    if (exporting) return false;
    if (!window.BIAI_API) {
      setValue("export-feedback", "API client missing.");
      return false;
    }

    exporting = true;
    const button = byId("export-pipeline");
    button.disabled = true;
    setValue("export-feedback", "Preparing your CSV export...");
    let objectUrl = null;

    try {
      const includeMerged = Boolean(byId("report-include-merged")?.checked);
      const csv = await window.BIAI_API.exportCsv(includeMerged);
      if (!csv || typeof csv.size !== "number" || csv.size === 0) {
        throw new Error("FastAPI returned an empty CSV file.");
      }

      objectUrl = window.URL.createObjectURL(csv);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = "biai-pipeline.csv";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      // Give the browser time to take ownership of the download first.
      const urlToRevoke = objectUrl;
      window.setTimeout(() => window.URL.revokeObjectURL(urlToRevoke), 1000);
      objectUrl = null;
      setValue("export-feedback", "CSV download started. It contains the current saved pipeline.");
      return true;
    } catch (error) {
      if (objectUrl) window.URL.revokeObjectURL(objectUrl);
      setValue("export-feedback", `Export failed: ${error.message || "Unknown error"}`);
      return false;
    } finally {
      exporting = false;
      button.disabled = false;
    }
  }

  function init() {
    if (!byId("reports") || !byId("refresh-reports") || !byId("export-pipeline") ||
        !byId("evaluation-mode")) return;

    byId("refresh-reports").addEventListener("click", () => { void loadReports(); });
    byId("export-pipeline").addEventListener("click", () => { void downloadCsv(); });
    byId("evaluation-mode").addEventListener("change", () => { void loadReports(); });
    window.addEventListener("biai:data-changed", () => { void loadReports(); });
    void loadReports();
  }

  window.BIAI_Reports = Object.freeze({ load: loadReports, downloadCsv });
  document.addEventListener("DOMContentLoaded", init);
})(window, document);
