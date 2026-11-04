/* Tests for frontend/js/leads.js, using Node's built-in test runner. */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const code = fs.readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../js/leads.js"), "utf8"
);

class FakeElement {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.value = "";
    this.type = "";
    this._text = "";
    this.checked = false;
    this.disabled = false;
    this.readOnly = false;
    this.open = false;
    this.hidden = true;
    this.dataset = {};
    this.attributes = {};
    this.colSpan = 1;
    this.className = "";
    this.classes = new Set();
    this.classList = {
      toggle: (name, enabled) => {
        if (enabled) this.classes.add(name);
        else this.classes.delete(name);
      },
    };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent || "").join(""); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) {
    this._text = "";
    this.children = children;
    if (this.tag === "select") {
      this.value = children[0]?.value || "";
    }
  }
  addEventListener(event, callback) {
    (this.listeners[event] ??= []).push(callback);
  }
  async fire(event, data = {}) {
    const callbacks = this.listeners[event] || [];
    return Promise.all(callbacks.map((callback) => callback({ preventDefault() {}, ...data })));
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  reset() { /* Explicit fillForm() resets lead fields. */ }
}

const IDS = [
  "lead-table-count", "leads-table-body", "lead-search", "lead-stage-filter",
  "lead-owner-filter", "include-merged", "evaluation-mode", "add-lead-button",
  "add-owner-button", "lead-form", "lead-dialog", "lead-dialog-title",
  "lead-form-status", "lead-activities", "save-lead", "generate-lead-email",
  "edit-lead-id", "edit-company", "edit-industry", "edit-source",
  "edit-contact-name", "edit-contact-role", "edit-contact-email", "edit-contact-phone",
  "edit-stage", "edit-value", "edit-owner", "edit-last-contact",
  "edit-follow-up", "edit-notes", "email-dialog", "email-recipient",
  "email-subject", "email-body", "email-warnings", "email-status",
  "copy-email", "owner-dialog", "owner-form", "owner-name", "owner-email",
  "owner-form-status", "save-owner", "toast",
  ...["new", "contacted", "meeting", "proposal", "invoice", "paid", "lost"].flatMap(
    (id) => [`cards-${id}`, `count-${id}`]
  ),
];

function lead(id, overrides = {}) {
  return {
    lead_id: id, company: `Company ${id}`, industry: "Textiles", source: "Website",
    contact_name: "A Person", contact_role: "Manager", contact_email: "a@example.com",
    contact_phone: "01700000000", stage: "Proposal", deal_value_bdt: 500000,
    last_contact: "2026-10-15", next_follow_up: "2026-10-28",
    notes: "Customer requested a proposal.", owner_id: null,
    merged_into_id: null, ...overrides,
  };
}

function setup(overrides = {}) {
  const nodes = Object.fromEntries(IDS.map((id) => [id, new FakeElement()]));
  nodes["lead-owner-filter"].tag = "select";
  nodes["edit-owner"].tag = "select";
  const events = {};
  const calls = [];
  let records = [
    lead("L01"), lead("L02", { company: "Meghna Pharma", stage: "Meeting", owner_id: 1 }),
    lead("L03", { company: "Old duplicate", stage: "Proposal", merged_into_id: "L02" }),
    lead("L25", { company: "Bad dates", next_follow_up: "2026-10-21", last_contact: "2026-10-28" }),
  ];
  let owners = [{ id: 1, name: "Maya", email: "maya@example.com" }];
  const api = {
    getLeads: async (params) => { calls.push(["getLeads", params]); return records.map((r) => ({ ...r })); },
    getOwners: async () => { calls.push(["getOwners"]); return owners.map((r) => ({ ...r })); },
    getLead: async (id, merged) => {
      calls.push(["getLead", id, merged]);
      const found = records.find((r) => r.lead_id === id);
      if (!found) throw Error("Not found");
      return { ...found };
    },
    getLeadActivities: async (id) => {
      calls.push(["getLeadActivities", id]);
      return [{ action_type: "stage_changed", created_at: "2026-10-25T10:00:00", old_value: "New", new_value: "Proposal" }];
    },
    createLead: async (data) => {
      calls.push(["createLead", data]);
      records.push(lead(data.lead_id, data));
      return data;
    },
    updateLead: async (id, changes) => {
      calls.push(["updateLead", id, changes]);
      Object.assign(records.find((r) => r.lead_id === id), changes);
      return { ...records.find((r) => r.lead_id === id) };
    },
    createOwner: async (data) => {
      calls.push(["createOwner", data]);
      const owner = { id: owners.length + 1, ...data };
      owners.push(owner);
      return owner;
    },
    draftEmail: async (id) => {
      calls.push(["draftEmail", id]);
      return { subject: "Training follow-up", body: "Hello,\nPlease let us know.",
        recipient_email: "a@example.com", warnings: ["Review recipient address"] };
    },
    ...overrides,
  };
  const window = {
    BIAI_API: api,
    navigator: { clipboard: { writeText: async (text) => calls.push(["clipboard", text]) } },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    dispatchEvent: (event) => { calls.push(["dispatch", event.type, event.detail]); },
    addEventListener: (name, cb) => { (events[name] ??= []).push(cb); },
  };
  const document = {
    createElement: (name) => new FakeElement(name),
    getElementById: (id) => nodes[id] || null,
    addEventListener: (name, cb) => { (events[name] ??= []).push(cb); },
  };
  class Option {
    constructor(label, value) { this.textContent = label; this.value = value; }
  }
  vm.runInNewContext(code, { window, document, Option, Date, Intl, Number, String, Promise }, { filename: "leads.js" });
  return {
    nodes, calls, events, api, window,
    async init() { await Promise.all(events.DOMContentLoaded.map((cb) => cb())); await tick(); },
    async setRecords(value) { records = value; await window.BIAI_LEADS.reload(); },
  };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const called = (fx, name) => fx.calls.filter((call) => call[0] === name);

async function edit(fx, id) { await fx.window.BIAI_LEADS.openLead(id); await tick(); }

test("waits until DOMContentLoaded to fetch", async () => {
  const fx = setup();
  assert.equal(called(fx, "getLeads").length, 0);
  await fx.init();
  assert.equal(called(fx, "getLeads").length, 1);
  assert.equal(called(fx, "getOwners").length, 1);
});

test("renders seven stage columns and excludes merged leads by default", async () => {
  const fx = setup(); await fx.init();
  assert.equal(fx.nodes["leads-table-body"].children.length, 3);
  assert.equal(fx.nodes["count-proposal"].textContent, "2");
  assert.equal(fx.nodes["count-meeting"].textContent, "1");
  assert.equal(fx.nodes["count-new"].textContent, "0");
});

test("including merged records shows the fourth lead", async () => {
  const fx = setup(); await fx.init();
  fx.nodes["include-merged"].checked = true;
  await fx.nodes["include-merged"].fire("change");
  assert.equal(fx.nodes["leads-table-body"].children.length, 4);
});

test("company search is case-insensitive and filters board and table", async () => {
  const fx = setup(); await fx.init();
  fx.nodes["lead-search"].value = "mEGHna";
  await fx.nodes["lead-search"].fire("input");
  assert.equal(fx.nodes["leads-table-body"].children.length, 1);
  assert.equal(fx.nodes["count-meeting"].textContent, "1");
});

test("stage and owner filters work independently", async () => {
  const fx = setup(); await fx.init();
  fx.nodes["lead-stage-filter"].value = "Meeting";
  fx.nodes["lead-owner-filter"].value = "1";
  await fx.nodes["lead-owner-filter"].fire("change");
  assert.equal(fx.nodes["leads-table-body"].children.length, 1);
  fx.nodes["lead-owner-filter"].value = "unassigned";
  await fx.nodes["lead-owner-filter"].fire("change");
  assert.match(fx.nodes["leads-table-body"].textContent, /No leads match/);
});

test("open lead fills form and loads recorded activity", async () => {
  const fx = setup(); await fx.init(); await edit(fx, "L02");
  assert.equal(fx.nodes["lead-dialog"].open, true);
  assert.equal(fx.nodes["edit-company"].value, "Meghna Pharma");
  assert.equal(fx.nodes["edit-stage"].value, "Meeting");
  assert.equal(fx.nodes["edit-owner"].value, "1");
  assert.match(fx.nodes["lead-activities"].textContent, /stage changed/);
});

test("merged lead can be inspected but not edited or emailed", async () => {
  const fx = setup(); await fx.init(); await edit(fx, "L03");
  assert.equal(fx.nodes["save-lead"].disabled, true);
  assert.equal(fx.nodes["generate-lead-email"].disabled, true);
  assert.match(fx.nodes["lead-form-status"].textContent, /read-only/);
});

test("edit sends PATCH with only changed fields including stage and owner", async () => {
  const fx = setup(); await fx.init(); await edit(fx, "L01");
  fx.nodes["edit-stage"].value = "Invoice sent";
  fx.nodes["edit-owner"].value = "1";
  fx.nodes["edit-value"].value = "600000";
  await fx.nodes["lead-form"].fire("submit");
  assert.equal(called(fx, "updateLead").length, 1);
  assert.deepEqual({ ...called(fx, "updateLead")[0][2] }, {
    stage: "Invoice sent", deal_value_bdt: 600000, owner_id: 1,
  });
  assert.equal(fx.nodes["lead-dialog"].open, false);
  assert.equal(called(fx, "dispatch").length, 1);
});

test("save with no changes does not call API", async () => {
  const fx = setup(); await fx.init(); await edit(fx, "L01");
  await fx.nodes["lead-form"].fire("submit");
  assert.equal(called(fx, "updateLead").length, 0);
  assert.match(fx.nodes["lead-form-status"].textContent, /No changes/);
});

test("clearing optional fields sends explicit null", async () => {
  const fx = setup(); await fx.init(); await edit(fx, "L01");
  fx.nodes["edit-contact-phone"].value = "";
  await fx.nodes["lead-form"].fire("submit");
  assert.equal(called(fx, "updateLead")[0][2].contact_phone, null);
});

test("new lead form creates lead and does not generate an email before saving", async () => {
  const fx = setup(); await fx.init();
  await fx.nodes["add-lead-button"].fire("click");
  assert.equal(fx.nodes["generate-lead-email"].disabled, true);
  fx.nodes["edit-lead-id"].value = "L26";
  fx.nodes["edit-company"].value = "New Example";
  fx.nodes["edit-value"].value = "100000";
  await fx.nodes["lead-form"].fire("submit");
  assert.equal(called(fx, "createLead").length, 1);
  assert.equal(called(fx, "createLead")[0][1].deal_value_bdt, 100000);
  assert.equal(fx.nodes["leads-table-body"].children.length, 4);
});

test("rejects invalid amounts before calling the API", async () => {
  const fx = setup(); await fx.init(); fx.window.BIAI_LEADS.showCreate();
  fx.nodes["edit-lead-id"].value = "L26";
  fx.nodes["edit-company"].value = "Example";
  fx.nodes["edit-value"].value = "-1";
  await fx.nodes["lead-form"].fire("submit");
  assert.equal(called(fx, "createLead").length, 0);
  assert.match(fx.nodes["lead-form-status"].textContent, /nonnegative/);
});

test("API validation error keeps editor open and shows useful text", async () => {
  const fx = setup({ updateLead: async () => { throw Error("Date is invalid"); } });
  await fx.init(); await edit(fx, "L01");
  fx.nodes["edit-last-contact"].value = "2026-11-02";
  await fx.nodes["lead-form"].fire("submit");
  assert.equal(fx.nodes["lead-dialog"].open, true);
  assert.match(fx.nodes["lead-form-status"].textContent, /Date is invalid/);
});

test("email drafting opens review dialog and never sends", async () => {
  const fx = setup(); await fx.init(); await edit(fx, "L01");
  await fx.nodes["generate-lead-email"].fire("click");
  assert.equal(called(fx, "draftEmail").length, 1);
  assert.equal(fx.nodes["email-dialog"].open, true);
  assert.equal(fx.nodes["email-subject"].value, "Training follow-up");
  assert.match(fx.nodes["email-warnings"].textContent, /Review recipient/);
  assert.match(fx.nodes["email-status"].textContent, /Nothing was sent/);
});

test("copy email includes edited subject and body", async () => {
  const fx = setup(); await fx.init(); await edit(fx, "L01");
  await fx.nodes["generate-lead-email"].fire("click");
  fx.nodes["email-subject"].value = "Edited subject";
  fx.nodes["email-body"].value = "Edited body";
  await fx.nodes["copy-email"].fire("click");
  assert.equal(called(fx, "clipboard")[0][1], "Subject: Edited subject\n\nEdited body");
});

test("owner creation updates dropdown and allows assignment", async () => {
  const fx = setup(); await fx.init();
  await fx.nodes["add-owner-button"].fire("click");
  fx.nodes["owner-name"].value = "Hassan";
  fx.nodes["owner-email"].value = "hassan@example.com";
  await fx.nodes["owner-form"].fire("submit");
  assert.equal(called(fx, "createOwner").length, 1);
  assert.equal(fx.nodes["edit-owner"].children.length, 3);
  assert.equal(fx.nodes["owner-dialog"].open, false);
});

test("invalid date order shows review warning rather than overdue on the board", async () => {
  const fx = setup(); await fx.init();
  const board = fx.nodes["cards-proposal"].textContent;
  assert.match(board, /Review dates/);
  assert.match(board, /Overdue/); // Another record is overdue.
});

test("untrusted company HTML is rendered only as text", async () => {
  const fx = setup(); await fx.setRecords([lead("L01", { company: "<script>alert(1)</script>" })]);
  assert.match(fx.nodes["leads-table-body"].textContent, /<script>/);
  assert.equal(fx.nodes["leads-table-body"].children[0].children[0].children.length, 0);
});

test("load errors do not leave stale fabricated figures", async () => {
  const fx = setup({ getLeads: async () => { throw Error("Offline"); } });
  await fx.init();
  assert.match(fx.nodes["lead-table-count"].textContent, /Offline/);
  assert.equal(fx.nodes["count-proposal"].textContent, "—");
});

test("reloading after a change ignores events sent by itself", async () => {
  const fx = setup(); await fx.init();
  const before = called(fx, "getLeads").length;
  for (const cb of fx.events["biai:data-changed"]) cb({ detail: { source: "leads" } });
  await tick();
  assert.equal(called(fx, "getLeads").length, before);
  for (const cb of fx.events["biai:data-changed"]) cb({ detail: { source: "issues" } });
  await tick();
  assert.equal(called(fx, "getLeads").length, before + 1);
});


test("email generator asks to save changed notes before drafting", async () => {
  const fx = setup(); await fx.init(); await edit(fx, "L01");
  fx.nodes["edit-notes"].value = "A new, unsaved fact";
  await fx.nodes["generate-lead-email"].fire("click");
  assert.equal(called(fx, "draftEmail").length, 0);
  assert.match(fx.nodes["lead-form-status"].textContent, /Save your changes/);
});
