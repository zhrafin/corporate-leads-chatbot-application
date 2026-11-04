(function (window, document) {
  "use strict";

  const PAGE_SIZE = 500; // Maximum allowed by FastAPI.
  const MAX_PAGES = 20;  // Prevent unlimited requests on unexpected datasets.
  const RULE_NAMES = {
    MISSING_CONTACT: "Missing contact",
    MISSING_OWNER: "Unassigned owner",
    OVERDUE_FOLLOWUP: "Overdue follow-up",
    MISSING_FOLLOWUP: "Missing follow-up",
    INVALID_DATE_ORDER: "Incorrect date order",
    INVALID_STAGE: "Invalid stage",
    POSSIBLE_STAGE_CONFLICT: "Stage / notes conflict",
    POSSIBLE_PAYMENT_CONFLICT: "Payment / stage conflict",
    VERIFY_EMAIL: "Email needs verification",
    NOTES_CONTACT_CONFLICT: "Contact / notes conflict",
    DUPLICATE_LEAD: "Possible duplicate",
  };

  const byId = (id) => document.getElementById(id);
  let allIssues = [];
  let duplicatePairs = [];
  let selectedPair = null;
  let selectedIssue = null;
  let loadVersion = 0;
  let scanning = false;
  let merging = false;

  function createText(tag, value, className = "") {
    const node = document.createElement(tag);
    node.textContent = String(value ?? "");
    if (className) node.className = className;
    return node;
  }

  function setStatus(id, message, error = false) {
    const node = byId(id);
    if (!node) return;
    node.textContent = message;
    node.classList.toggle("is-error", error);
  }

  function empty(container, message) {
    const node = createText("p", message, "empty-state");
    container.replaceChildren(node);
  }

  function notify(message) {
    const toast = byId("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
  }

  function notifyDataChanged() {
    window.dispatchEvent(new window.CustomEvent("biai:data-changed", {
      detail: { source: "validation" },
    }));
  }

  function ruleLabel(code) {
    return RULE_NAMES[code] || String(code || "Unknown issue").replaceAll("_", " ");
  }

  function issueSeverity(severity) {
    return severity === "high" ? "danger" : "warning";
  }

  async function loadAllIssues() {
    const rows = [];
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const batch = await window.BIAI_API.getIssues({
        status: "all", limit: PAGE_SIZE, offset: page * PAGE_SIZE,
      });
      if (!Array.isArray(batch)) throw new Error("The issues API returned invalid data.");
      rows.push(...batch);
      if (batch.length < PAGE_SIZE) return rows;
    }
    throw new Error("Too many issues to display. Please narrow the results.");
  }

  function updateRuleFilter(issues) {
    const select = byId("issue-rule-filter");
    const current = select.value;
    const codes = [...new Set(issues.map((issue) => issue.rule_code))].sort();
    const options = [new Option("All issue types", "")];
    codes.forEach((code) => options.push(new Option(ruleLabel(code), code)));
    select.replaceChildren(...options);
    select.value = codes.includes(current) ? current : "";
  }

  function renderMetrics(summary, pairs) {
    byId("quality-open").textContent = String(summary.open_issues ?? 0);
    byId("quality-flagged").textContent = String(summary.data_quality_flagged_leads ?? 0);
    byId("quality-duplicates").textContent = String(pairs.length);
    byId("quality-dates").textContent = String(summary.open_by_rule?.INVALID_DATE_ORDER ?? 0);
  }

  function filteredIssues() {
    const status = byId("issue-status-filter").value;
    const rule = byId("issue-rule-filter").value;
    return allIssues.filter((item) =>
      (status === "all" || item.status === status) && (!rule || item.rule_code === rule)
    );
  }

  function button(label, onClick, className = "button button--secondary") {
    const node = createText("button", label, className);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  function renderIssues() {
    const tbody = byId("issues-table-body");
    const issues = filteredIssues();
    if (!issues.length) {
      const row = document.createElement("tr");
      const cell = createText("td", "No issues match these filters.", "empty-state");
      cell.colSpan = 6;
      row.append(cell);
      tbody.replaceChildren(row);
      return;
    }

    const rows = issues.map((issue) => {
      const row = document.createElement("tr");
      row.append(createText("td", issue.lead_id));
      row.append(createText("td", ruleLabel(issue.rule_code)));
      row.append(createText("td", issue.message || "Check this record."));
      const severityCell = document.createElement("td");
      severityCell.append(createText(
        "span", issue.severity,
        `status-badge status-badge--${issueSeverity(issue.severity)}`
      ));
      row.append(severityCell);
      row.append(createText("td", issue.status));

      const actions = document.createElement("td");
      actions.className = "issue-actions";
      actions.append(button("View lead", () => {
        if (!window.BIAI_LEADS?.openLead) {
          setStatus("quality-scan-status", "The lead editor is not available.", true);
          return;
        }
        window.BIAI_LEADS.openLead(issue.lead_id);
      }));
      if (issue.status === "open") {
        actions.append(button("Review", () => openReview(issue)));
      }
      row.append(actions);
      return row;
    });
    tbody.replaceChildren(...rows);
  }

  function renderDuplicates() {
    const container = byId("duplicate-pairs");
    if (!duplicatePairs.length) {
      empty(container, "No suspected duplicate pairs among current leads.");
      return;
    }
    const cards = duplicatePairs.map((pair) => {
      const card = document.createElement("article");
      card.className = "duplicate-pair";
      card.append(createText("strong", `${pair.lead_id_1}: ${pair.company_1}`));
      card.append(createText("p", `Possible duplicate: ${pair.lead_id_2}: ${pair.company_2}`));
      card.append(createText("p",
        `Match: ${String(pair.match_type || "review").replaceAll("_", " ")} · ` +
        `Matching fields: ${(pair.matched_fields || []).join(", ") || "not specified"}`,
        "muted"
      ));
      card.append(button("Review merge", () => openMerge(pair), "button button--secondary"));
      return card;
    });
    container.replaceChildren(...cards);
  }

  /** Reading existing issues never runs a scan or changes database records. */
  async function reload() {
    const version = ++loadVersion;
    try {
      const [summary, pairs, issues] = await Promise.all([
        window.BIAI_API.getIssueSummary(),
        window.BIAI_API.getDuplicatePairs(),
        loadAllIssues(),
      ]);
      if (version !== loadVersion) return;
      allIssues = issues;
      duplicatePairs = pairs;
      updateRuleFilter(issues);
      renderMetrics(summary, pairs);
      renderIssues();
      renderDuplicates();
      if ((summary.total_issue_records ?? 0) === 0) {
        setStatus("quality-scan-status", "No saved scans yet. Select Run validation scan to check the leads.");
      } else {
        setStatus("quality-scan-status", `Showing saved results: ${summary.open_issues} open warning(s). Run a scan to refresh them.`);
      }
    } catch (error) {
      if (version !== loadVersion) return;
      setStatus("quality-scan-status", `Could not load data quality results: ${error.message}`, true);
    }
  }

  async function runScan() {
    if (scanning) return;
    scanning = true;
    const scanButton = byId("run-quality-scan");
    scanButton.disabled = true;
    setStatus("quality-scan-status", "Scanning leads. No records will be deleted...");
    try {
      const evaluation = byId("evaluation-mode").checked;
      const result = await window.BIAI_API.scanIssues(evaluation);
      await reload();
      setStatus("quality-scan-status",
        `Scanned ${result.scanned_leads} leads as of ${result.as_of}. ` +
        `${result.open_issues} open warning(s), ${result.duplicate_pairs} duplicate pair(s), ` +
        `${result.overdue_leads} overdue lead(s).`
      );
      notifyDataChanged();
    } catch (error) {
      setStatus("quality-scan-status", `Scan failed: ${error.message}`, true);
    } finally {
      scanning = false;
      scanButton.disabled = false;
    }
  }

  function openReview(issue) {
    selectedIssue = issue;
    byId("review-issue-title").textContent = `Review ${ruleLabel(issue.rule_code)}`;
    byId("review-issue-details").textContent = `${issue.lead_id}: ${issue.message || "Review required"}`;
    byId("review-issue-status").value = "dismissed";
    byId("review-issue-reason").value = "";
    setStatus("review-issue-feedback", "Resolved issues may reopen if the underlying problem remains.");
    byId("issue-review-dialog").showModal();
  }

  async function submitReview(event) {
    event.preventDefault();
    if (!selectedIssue) return;
    const reason = byId("review-issue-reason").value.trim();
    const status = byId("review-issue-status").value;
    if (!reason) return setStatus("review-issue-feedback", "Explain why you are resolving or dismissing this issue.", true);
    if (!["resolved", "dismissed"].includes(status)) {
      return setStatus("review-issue-feedback", "Choose a valid review status.", true);
    }
    const submit = byId("submit-issue-review");
    submit.disabled = true;
    try {
      await window.BIAI_API.reviewIssue(selectedIssue.id, status, reason);
      byId("issue-review-dialog").close();
      selectedIssue = null;
      await reload();
      notifyDataChanged();
      notify("Issue review saved.");
    } catch (error) {
      setStatus("review-issue-feedback", `Could not save review: ${error.message}`, true);
    } finally {
      submit.disabled = false;
    }
  }

  function renderMergeComparison(first, second) {
    const container = byId("merge-compare");
    const fields = [
      ["Company", "company"], ["Contact", "contact_name"],
      ["Email", "contact_email"], ["Phone", "contact_phone"],
      ["Stage", "stage"], ["Value (BDT)", "deal_value_bdt"],
      ["Last contact", "last_contact"], ["Next follow-up", "next_follow_up"],
      ["Notes", "notes"],
    ];
    const table = document.createElement("table");
    table.className = "data-table merge-comparison";
    const heading = document.createElement("thead");
    const titles = document.createElement("tr");
    ["Field", first.lead_id, second.lead_id].forEach((text) => titles.append(createText("th", text)));
    heading.append(titles);
    table.append(heading);
    const body = document.createElement("tbody");
    fields.forEach(([name, field]) => {
      const row = document.createElement("tr");
      row.append(createText("th", name));
      row.append(createText("td", first[field] ?? "Missing"));
      row.append(createText("td", second[field] ?? "Missing"));
      body.append(row);
    });
    table.append(body);
    container.replaceChildren(table);
  }

  function chooseSurvivor() {
    if (!selectedPair) return;
    const keepId = byId("merge-keep-choice").value;
    const one = selectedPair.lead_id_1;
    const two = selectedPair.lead_id_2;
    if (![one, two].includes(keepId)) return;
    byId("merge-surviving-id").value = keepId;
    byId("merge-duplicate-id").value = keepId === one ? two : one;
  }

  async function openMerge(pair) {
    setStatus("quality-scan-status", `Loading records ${pair.lead_id_1} and ${pair.lead_id_2} for comparison...`);
    try {
      const [first, second] = await Promise.all([
        window.BIAI_API.getLead(pair.lead_id_1, true),
        window.BIAI_API.getLead(pair.lead_id_2, true),
      ]);
      if (first.merged_into_id || second.merged_into_id) {
        throw new Error("One of these leads is already merged. Refresh the duplicate list.");
      }
      selectedPair = pair;
      byId("merge-pair-description").textContent =
        `${pair.lead_id_1} (${pair.company_1}) and ${pair.lead_id_2} (${pair.company_2}). ` +
        "Compare both records, then choose which one to keep.";
      const select = byId("merge-keep-choice");
      select.replaceChildren(
        new Option(`${pair.lead_id_1}: ${pair.company_1}`, pair.lead_id_1),
        new Option(`${pair.lead_id_2}: ${pair.company_2}`, pair.lead_id_2)
      );
      select.value = pair.lead_id_1;
      chooseSurvivor();
      renderMergeComparison(first, second);
      byId("merge-reason").value = "";
      byId("merge-confirmed").checked = false;
      setStatus("merge-status", "Merging archives the duplicate. Conflicting details are not combined automatically.");
      byId("merge-dialog").showModal();
    } catch (error) {
      setStatus("quality-scan-status", `Cannot open merge review: ${error.message}`, true);
    }
  }

  async function submitMerge(event) {
    event.preventDefault();
    if (!selectedPair || merging) return;
    if (!byId("merge-confirmed").checked) {
      return setStatus("merge-status", "Check the confirmation box before merging.", true);
    }
    const reason = byId("merge-reason").value.trim();
    const surviving = byId("merge-surviving-id").value;
    const duplicate = byId("merge-duplicate-id").value;
    if (!reason) return setStatus("merge-status", "Enter a reason for the merge.", true);
    if (surviving === duplicate ||
      ![selectedPair.lead_id_1, selectedPair.lead_id_2].includes(surviving) ||
      ![selectedPair.lead_id_1, selectedPair.lead_id_2].includes(duplicate)) {
      return setStatus("merge-status", "The selected lead IDs are invalid.", true);
    }
    merging = true;
    const button = byId("confirm-merge-button");
    button.disabled = true;
    setStatus("merge-status", "Saving confirmed merge...");
    try {
      await window.BIAI_API.mergeLeads(surviving, duplicate, reason, true);
      byId("merge-dialog").close();
      selectedPair = null;
      await reload();
      notifyDataChanged();
      notify(`Merged ${duplicate} into ${surviving}. The original record was preserved.`);
    } catch (error) {
      setStatus("merge-status", `Merge failed: ${error.message}`, true);
    } finally {
      button.disabled = false;
      merging = false;
    }
  }

  function init() {
    if (!window.BIAI_API) {
      setStatus("quality-scan-status", "API client missing. Load js/api.js first.", true);
      return;
    }
    byId("run-quality-scan").addEventListener("click", runScan);
    byId("evaluation-mode").addEventListener("change", () => {
      setStatus("quality-scan-status",
        "Date mode changed. Run validation scan to refresh overdue warnings for the selected date.");
    });
    byId("issue-status-filter").addEventListener("change", renderIssues);
    byId("issue-rule-filter").addEventListener("change", renderIssues);
    byId("issue-review-form").addEventListener("submit", submitReview);
    byId("merge-form").addEventListener("submit", submitMerge);
    byId("merge-keep-choice").addEventListener("change", chooseSurvivor);
    window.addEventListener("biai:data-changed", (event) => {
      if (event.detail?.source !== "validation") reload();
    });
    reload();
  }

  // Expose a small interface for tests and future frontend modules.
  window.BIAI_VALIDATION = Object.freeze({ reload, runScan, openMerge });
  document.addEventListener("DOMContentLoaded", init);
})(window, document);
