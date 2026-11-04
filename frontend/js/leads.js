(function (window, document) {
  "use strict";

  const STAGES = ["New", "Contacted", "Meeting", "Proposal", "Invoice sent", "Paid", "Lost"];
  const STAGE_IDS = ["new", "contacted", "meeting", "proposal", "invoice", "paid", "lost"];
  const FIELDS = {
    company: "edit-company",
    industry: "edit-industry",
    source: "edit-source",
    contact_name: "edit-contact-name",
    contact_role: "edit-contact-role",
    contact_email: "edit-contact-email",
    contact_phone: "edit-contact-phone",
    stage: "edit-stage",
    deal_value_bdt: "edit-value",
    owner_id: "edit-owner",
    last_contact: "edit-last-contact",
    next_follow_up: "edit-follow-up",
    notes: "edit-notes",
  };

  let leads = [];
  let owners = [];
  let editingLead = null;
  let loadingId = 0;

  const el = (id) => document.getElementById(id);
  const moneyFormat = new Intl.NumberFormat("en-BD");

  function textNode(tag, value, className = "") {
    const item = document.createElement(tag);
    item.textContent = String(value ?? ""); // Never insert API values using innerHTML.
    if (className) item.className = className;
    return item;
  }

  function money(value) {
    return value == null ? "Not agreed" : `BDT ${moneyFormat.format(value)}`;
  }

  function ownerName(id) {
    const owner = owners.find((item) => item.id === id);
    return owner ? owner.name : "Unassigned";
  }

  function currentDate() {
    // Use the business timezone, not the user's device timezone.
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Dhaka", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date());
    const part = (type) => parts.find((item) => item.type === type).value;
    return `${part("year")}-${part("month")}-${part("day")}`;
  }

  function followupLabel(lead) {
    const followup = lead.next_follow_up;
    if (!followup) return "No follow-up set";
    if (lead.last_contact && followup < lead.last_contact) return "Review dates";
    if (lead.stage === "Paid" || lead.stage === "Lost") return followup;
    const asOf = el("evaluation-mode").checked ? "2026-11-01" : currentDate();
    return followup < asOf ? `${followup} · Overdue` : followup;
  }

  function isOverdue(lead) {
    const followup = lead.next_follow_up;
    if (!followup || ["Paid", "Lost"].includes(lead.stage)) return false;
    if (lead.last_contact && followup < lead.last_contact) return false;
    return followup < (el("evaluation-mode").checked ? "2026-11-01" : currentDate());
  }

  function status(id, message, error = false) {
    const item = el(id);
    if (!item) return;
    item.textContent = message;
    item.classList.toggle("is-error", error);
  }

  function toast(message) {
    const item = el("toast");
    if (!item) return;
    item.textContent = message;
    item.hidden = false;
  }

  function dispatchChange() {
    // Dashboard refreshes; this module does not reload its own data twice.
    window.dispatchEvent(new window.CustomEvent("biai:data-changed", {
      detail: { source: "leads" },
    }));
  }

  function showEmpty(container, message, tag = "p") {
    container.replaceChildren(textNode(tag, message, "empty-state"));
  }

  function populateOwnerChoices() {
    // Keep the selected filter while refreshing the owner list.
    const filter = el("lead-owner-filter");
    const oldValue = filter.value;
    const filterOptions = [
      new Option("All owners", ""),
      new Option("Unassigned", "unassigned"),
      ...owners.map((owner) => new Option(owner.name, String(owner.id))),
    ];
    filter.replaceChildren(...filterOptions);
    filter.value = oldValue;

    const select = el("edit-owner");
    select.replaceChildren(
      new Option("Unassigned", ""),
      ...owners.map((owner) => new Option(owner.name, String(owner.id)))
    );
  }

  function visibleLeads() {
    const search = el("lead-search").value.trim().toLocaleLowerCase();
    const stage = el("lead-stage-filter").value;
    const selectedOwner = el("lead-owner-filter").value;
    const includeMerged = el("include-merged").checked;

    return leads.filter((lead) => {
      if (!includeMerged && lead.merged_into_id) return false;
      if (stage && lead.stage !== stage) return false;
      if (search && !lead.company.toLocaleLowerCase().includes(search)) return false;
      if (selectedOwner === "unassigned" && lead.owner_id != null) return false;
      if (selectedOwner && selectedOwner !== "unassigned" && String(lead.owner_id) !== selectedOwner) return false;
      return true;
    });
  }

  function boardCard(lead) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "stage-card";
    button.dataset.leadId = lead.lead_id;
    button.setAttribute("aria-label", `Open ${lead.company}, ${lead.stage}`);
    button.append(
      textNode("strong", lead.company),
      textNode("small", `${lead.lead_id} · ${money(lead.deal_value_bdt)}`),
      textNode("small", `Owner: ${ownerName(lead.owner_id)}`),
      textNode("small", followupLabel(lead), isOverdue(lead) ? "is-overdue" : "")
    );
    if (lead.merged_into_id) button.append(textNode("small", `Merged into ${lead.merged_into_id}`));
    button.addEventListener("click", () => openLead(lead.lead_id));
    return button;
  }

  function emptyTable(message) {
    const row = document.createElement("tr");
    const cell = textNode("td", message, "empty-state");
    cell.colSpan = 8;
    row.append(cell);
    el("leads-table-body").replaceChildren(row);
  }

  function tableRow(lead) {
    const row = document.createElement("tr");
    const label = lead.merged_into_id
      ? `${lead.company} (${lead.lead_id}, merged into ${lead.merged_into_id})`
      : `${lead.company} (${lead.lead_id})`;
    const values = [
      label, lead.contact_name || "Missing contact", lead.industry || "—",
      lead.stage, money(lead.deal_value_bdt), ownerName(lead.owner_id), followupLabel(lead),
    ];
    values.forEach((value, index) => {
      const cell = textNode("td", value);
      if (index === 6 && isOverdue(lead)) cell.className = "is-overdue";
      row.append(cell);
    });
    const actionCell = document.createElement("td");
    const button = textNode("button", lead.merged_into_id ? "View" : "Edit", "button button--secondary");
    button.type = "button";
    button.addEventListener("click", () => openLead(lead.lead_id));
    actionCell.append(button);
    row.append(actionCell);
    return row;
  }

  function render() {
    const shown = visibleLeads();
    el("lead-table-count").textContent = `${shown.length} shown · ${leads.length} records loaded`;
    const body = el("leads-table-body");
    if (shown.length) body.replaceChildren(...shown.map(tableRow));
    else emptyTable("No leads match these filters.");

    STAGES.forEach((stage, index) => {
      const matching = shown.filter((lead) => lead.stage === stage);
      el(`count-${STAGE_IDS[index]}`).textContent = String(matching.length);
      const container = el(`cards-${STAGE_IDS[index]}`);
      if (matching.length) container.replaceChildren(...matching.map(boardCard));
      else showEmpty(container, "No matching deals.");
    });
  }

  async function reload() {
    if (!window.BIAI_API) return;
    const id = ++loadingId;
    el("lead-table-count").textContent = "Loading leads...";
    try {
      // This project has 25 sample records. Limit 500 matches FastAPI's maximum.
      const [newLeads, newOwners] = await Promise.all([
        window.BIAI_API.getLeads({ include_merged: true, limit: 500 }),
        window.BIAI_API.getOwners(),
      ]);
      if (id !== loadingId) return;
      leads = newLeads;
      owners = newOwners;
      populateOwnerChoices();
      render();
    } catch (error) {
      if (id !== loadingId) return;
      // Never show records from an older successful request after a connection error.
      leads = [];
      el("lead-table-count").textContent = `Could not load leads: ${error.message}`;
      emptyTable("Could not load lead records. Check FastAPI.");
      STAGE_IDS.forEach((stageId) => {
        el(`count-${stageId}`).textContent = "—";
        showEmpty(el(`cards-${stageId}`), "Unavailable.");
      });
    }
  }

  function toggleEditorReadOnly(readonly) {
    el("edit-lead-id").readOnly = Boolean(editingLead);
    Object.values(FIELDS).forEach((id) => { el(id).disabled = readonly; });
    el("save-lead").disabled = readonly;
    el("generate-lead-email").disabled = readonly || !editingLead;
  }

  function fillForm(lead) {
    el("edit-lead-id").value = lead?.lead_id || "";
    Object.entries(FIELDS).forEach(([name, id]) => {
      const value = lead?.[name];
      el(id).value = value == null ? "" : String(value);
    });
    if (!lead) el("edit-stage").value = "New";
  }

  function showCreate() {
    editingLead = null;
    el("lead-form").reset();
    fillForm(null);
    toggleEditorReadOnly(false);
    el("lead-dialog-title").textContent = "Add company lead";
    status("lead-form-status", "Save the lead before generating an AI email.");
    showEmpty(el("lead-activities"), "Activity history starts after saving.");
    el("lead-dialog").showModal();
  }

  async function loadActivities(leadId) {
    const container = el("lead-activities");
    showEmpty(container, "Loading activity history...");
    try {
      const activities = await window.BIAI_API.getLeadActivities(leadId);
      if (el("edit-lead-id").value !== leadId) return;
      if (!activities.length) return showEmpty(container, "No activity recorded yet.");
      const items = activities.map((activity) => {
        const row = document.createElement("li");
        row.append(
          textNode("strong", activity.action_type.replaceAll("_", " ")),
          textNode("small", ` · ${activity.created_at?.slice(0, 10) || ""}`),
          textNode("p", activity.description || `${activity.old_value || "—"} → ${activity.new_value || "—"}`)
        );
        return row;
      });
      const list = document.createElement("ul");
      list.append(...items);
      container.replaceChildren(list);
    } catch (error) {
      showEmpty(container, `Could not load history: ${error.message}`);
    }
  }

  async function openLead(leadId) {
    status("lead-form-status", "Loading lead...");
    try {
      const lead = await window.BIAI_API.getLead(leadId, true);
      editingLead = lead;
      fillForm(lead);
      const readonly = Boolean(lead.merged_into_id);
      toggleEditorReadOnly(readonly);
      el("lead-dialog-title").textContent = `${lead.company} (${lead.lead_id})`;
      status("lead-form-status", readonly
        ? `This record was merged into ${lead.merged_into_id}. It is read-only.`
        : "Edit details, stage or owner. Changes are recorded in the activity history.");
      el("lead-dialog").showModal();
      loadActivities(leadId);
    } catch (error) {
      toast(`Could not open lead: ${error.message}`);
    }
  }

  function formValues() {
    const values = { lead_id: el("edit-lead-id").value.trim() };
    Object.entries(FIELDS).forEach(([name, id]) => {
      const raw = el(id).value.trim();
      values[name] = raw === "" ? null : raw;
    });
    values.company = (values.company || "").trim();
    values.stage = values.stage || "New";
    if (!values.lead_id || !values.company) throw new Error("Lead ID and company are required.");
    if (values.deal_value_bdt != null) {
      const amount = Number(values.deal_value_bdt);
      if (!Number.isSafeInteger(amount) || amount < 0) {
        throw new Error("Deal value must be a nonnegative whole number.");
      }
      values.deal_value_bdt = amount;
    }
    if (values.owner_id != null) values.owner_id = Number(values.owner_id);
    return values;
  }

  function changesOnly(values, original) {
    const changes = {};
    for (const [key, value] of Object.entries(values)) {
      if (key === "lead_id") continue;
      if ((original[key] ?? null) !== value) changes[key] = value;
    }
    return changes;
  }

  async function saveLead(event) {
    event.preventDefault();
    if (el("save-lead").disabled) return;
    try {
      const values = formValues();
      const changes = editingLead ? changesOnly(values, editingLead) : values;
      if (editingLead && Object.keys(changes).length === 0) {
        status("lead-form-status", "No changes to save.");
        return;
      }
      el("save-lead").disabled = true;
      status("lead-form-status", "Saving...");
      if (editingLead) await window.BIAI_API.updateLead(editingLead.lead_id, changes);
      else await window.BIAI_API.createLead(values);
      el("lead-dialog").close();
      await reload();
      dispatchChange();
      toast("Lead saved successfully.");
    } catch (error) {
      status("lead-form-status", error.message, true);
    } finally {
      if (!editingLead?.merged_into_id) el("save-lead").disabled = false;
    }
  }

  async function draftEmail() {
    if (!editingLead || editingLead.merged_into_id) return;
    // The AI reads notes from SQLite, so unsaved edits must not be ignored.
    try {
      if (Object.keys(changesOnly(formValues(), editingLead)).length > 0) {
        status("lead-form-status", "Save your changes before generating an email.", true);
        return;
      }
    } catch (error) {
      status("lead-form-status", error.message, true);
      return;
    }
    const button = el("generate-lead-email");
    button.disabled = true;
    status("lead-form-status", "Generating draft with Groq...");
    try {
      const draft = await window.BIAI_API.draftEmail(editingLead.lead_id);
      el("email-recipient").value = draft.recipient_email || "Recipient needs verification";
      el("email-subject").value = draft.subject;
      el("email-body").value = draft.body;
      status("email-warnings", draft.warnings?.length
        ? draft.warnings.join(" · ")
        : "Review this AI draft before using it.");
      status("email-status", "Draft only. Nothing was sent.");
      el("email-dialog").showModal();
      status("lead-form-status", "AI draft ready. Review it before copying.");
    } catch (error) {
      status("lead-form-status", `Email draft failed: ${error.message}`, true);
    } finally {
      button.disabled = false;
    }
  }

  async function copyDraft() {
    const subject = el("email-subject").value.trim();
    const body = el("email-body").value.trim();
    if (!subject || !body) return status("email-status", "Subject and message are required.", true);
    try {
      await window.navigator.clipboard.writeText(`Subject: ${subject}\n\n${body}`);
      status("email-status", "Draft copied. Review the recipient before sending it manually.");
    } catch (_) {
      status("email-status", "Clipboard unavailable. Select the text and copy it manually.", true);
    }
  }

  function showOwnerDialog() {
    el("owner-form").reset();
    status("owner-form-status", "Create an employee before assigning them to leads.");
    el("owner-dialog").showModal();
  }

  async function saveOwner(event) {
    event.preventDefault();
    const name = el("owner-name").value.trim();
    const email = el("owner-email").value.trim();
    if (!name) return status("owner-form-status", "Employee name is required.", true);
    const button = el("save-owner");
    button.disabled = true;
    try {
      const saved = await window.BIAI_API.createOwner({ name, email: email || null });
      el("owner-dialog").close();
      await reload();
      if (el("lead-dialog").open) el("edit-owner").value = String(saved.id);
      toast(`Employee ${saved.name} added.`);
    } catch (error) {
      status("owner-form-status", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function init() {
    if (!window.BIAI_API) {
      el("lead-table-count").textContent = "Missing API client: load js/api.js first.";
      return;
    }
    el("add-lead-button").addEventListener("click", showCreate);
    el("add-owner-button").addEventListener("click", showOwnerDialog);
    el("lead-form").addEventListener("submit", saveLead);
    el("owner-form").addEventListener("submit", saveOwner);
    el("generate-lead-email").addEventListener("click", draftEmail);
    el("copy-email").addEventListener("click", copyDraft);
    ["lead-search", "lead-stage-filter", "lead-owner-filter", "include-merged"]
      .forEach((id) => el(id).addEventListener(id === "lead-search" ? "input" : "change", render));
    el("evaluation-mode").addEventListener("change", render);
    window.addEventListener("biai:data-changed", (event) => {
      if (event.detail?.source !== "leads") reload();
    });
    reload();
  }

  window.BIAI_LEADS = Object.freeze({ reload, openLead, showCreate });
  document.addEventListener("DOMContentLoaded", init);
})(window, document);
