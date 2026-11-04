import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const JS = fs.readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../js/chatbot.js"), "utf8"
);

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.parent = null;
    this.listeners = {};
    this.dataset = {};
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.className = "";
    this._text = "";
    this.focusCount = 0;
    this.scrollTop = 0;
    this.scrollHeight = 100;
    this.classes = new Set();
    this.classList = {
      toggle: (value, enabled) => {
        if (enabled) this.classes.add(value);
        else this.classes.delete(value);
      },
    };
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  append(...children) {
    for (const child of children) {
      child.parent = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    this._text = "";
    this.children = [];
    this.append(...children);
  }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }
  focus() { this.focusCount += 1; }
  addEventListener(type, callback) {
    (this.listeners[type] ||= []).push(callback);
  }
  fire(type, event = {}) {
    for (const callback of this.listeners[type] || []) callback(event);
  }
}

const REQUIRED_IDS = [
  "chat-messages", "chat-form", "chat-question", "send-chat", "chat-status",
  "evaluation-mode", "new-chat",
];

function response(overrides = {}) {
  return {
    answer: "There are 9 overdue leads.",
    session_id: "server-session-1",
    tools_used: ["get_overdue_leads"],
    used_database_tools: true,
    as_of: "2026-11-01",
    ...overrides,
  };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function setup(overrides = {}) {
  const nodes = Object.fromEntries(REQUIRED_IDS.map((id) => [id, new Element()]));
  const suggestions = [
    "Which deals are overdue?",
    "What is the total value in Proposal stage?",
    "Which companies have missing contact details?",
    "Which five leads need attention first?",
  ].map((question) => {
    const element = new Element("button");
    element.dataset.question = question;
    return element;
  });
  nodes["chat-messages"].append(new Element("div")); // initial HTML greeting
  const events = {};
  const calls = [];
  const api = {
    chat: async (question, sessionId, evaluationMode) => {
      calls.push({ question, sessionId, evaluationMode });
      return response();
    },
    ...overrides,
  };
  const window = { BIAI_API: api };
  const document = {
    getElementById: (id) => nodes[id] ?? null,
    createElement: (tag) => new Element(tag),
    querySelectorAll: (selector) => selector === ".suggestion-button[data-question]" ? suggestions : [],
    addEventListener: (event, callback) => { events[event] = callback; },
  };
  vm.runInNewContext(JS, { window, document, Promise }, { filename: "chatbot.js" });
  events.DOMContentLoaded();
  const send = async (question) => window.BIAI_Chatbot.send(question);
  return { window, nodes, events, suggestions, calls, send };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

function history(fixture) { return fixture.nodes["chat-messages"].children; }

test("initialization does not call Groq or fetch messages automatically", () => {
  const f = setup();
  assert.equal(f.calls.length, 0);
  assert.equal(history(f).length, 1);
  assert.equal(f.window.BIAI_Chatbot.getSessionId(), null);
});

test("normal question sends the correct API request with live date mode", async () => {
  const f = setup();
  assert.equal(await f.send("Which deals are overdue?"), true);
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.calls[0], {
    question: "Which deals are overdue?", sessionId: null, evaluationMode: false,
  });
  assert.match(history(f).at(-1).textContent, /9 overdue leads/);
});

test("trims whitespace and displays the trimmed question", async () => {
  const f = setup();
  await f.send("  Show me L25  ");
  assert.equal(f.calls[0].question, "Show me L25");
  assert.match(history(f).at(-2).textContent, /Show me L25/);
});

test("empty questions do not make API requests", async () => {
  const f = setup();
  assert.equal(await f.send("  "), false);
  assert.equal(f.calls.length, 0);
  assert.match(f.nodes["chat-status"].textContent, /Enter a question/);
});

test("overlong questions are rejected before posting", async () => {
  const f = setup();
  assert.equal(await f.send("x".repeat(2001)), false);
  assert.equal(f.calls.length, 0);
  assert.match(f.nodes["chat-status"].textContent, /2,000/);
});

test("form prevents native submission and clears the input", async () => {
  const f = setup();
  f.nodes["chat-question"].value = "Which deals are overdue?";
  let prevented = false;
  f.nodes["chat-form"].fire("submit", { preventDefault: () => { prevented = true; } });
  await flush();
  assert.equal(prevented, true);
  assert.equal(f.nodes["chat-question"].value, "");
  assert.equal(f.calls.length, 1);
});

test("suggestion buttons send the exact suggested question", async () => {
  const f = setup();
  f.suggestions[1].fire("click");
  await flush();
  assert.equal(f.calls[0].question, "What is the total value in Proposal stage?");
});

test("server session ID is reused for follow-up questions", async () => {
  const f = setup();
  await f.send("Show me Proposal leads");
  await f.send("What is their total value?");
  assert.equal(f.calls[0].sessionId, null);
  assert.equal(f.calls[1].sessionId, "server-session-1");
  assert.equal(f.window.BIAI_Chatbot.getSessionId(), "server-session-1");
});

test("new conversation clears history and starts with a null session", async () => {
  const f = setup();
  await f.send("First question");
  f.nodes["new-chat"].fire("click");
  assert.equal(history(f).length, 1);
  assert.match(history(f)[0].textContent, /Hello!/);
  assert.equal(f.window.BIAI_Chatbot.getSessionId(), null);
  await f.send("Second question");
  assert.equal(f.calls[1].sessionId, null);
});

test("switching to sample evaluation date starts a fresh conversation", async () => {
  const f = setup();
  await f.send("Today?");
  f.nodes["evaluation-mode"].checked = true;
  f.nodes["evaluation-mode"].fire("change");
  assert.equal(f.window.BIAI_Chatbot.getSessionId(), null);
  assert.match(f.nodes["chat-status"].textContent, /Sample date/);
  await f.send("Which deals are overdue?");
  assert.equal(f.calls[1].evaluationMode, true);
  assert.equal(f.calls[1].sessionId, null);
});

test("switching back to live mode also starts fresh session", async () => {
  const f = setup();
  f.nodes["evaluation-mode"].checked = true;
  f.nodes["evaluation-mode"].fire("change");
  await f.send("Sample date question");
  f.nodes["evaluation-mode"].checked = false;
  f.nodes["evaluation-mode"].fire("change");
  await f.send("Live question");
  assert.equal(f.calls[1].sessionId, null);
  assert.equal(f.calls[1].evaluationMode, false);
});

test("disable form and suggestions while waiting and reenable on success", async () => {
  const waiting = deferred();
  const f = setup({ chat: () => waiting.promise });
  const promise = f.send("Question");
  assert.equal(f.nodes["send-chat"].disabled, true);
  assert.equal(f.nodes["chat-question"].disabled, true);
  assert.ok(f.suggestions.every((button) => button.disabled));
  assert.match(f.nodes["chat-status"].textContent, /Checking/);
  waiting.resolve(response());
  await promise;
  assert.equal(f.nodes["send-chat"].disabled, false);
  assert.ok(f.suggestions.every((button) => !button.disabled));
  assert.ok(f.nodes["chat-question"].focusCount > 0);
});

test("duplicate submits are ignored while a request is pending", async () => {
  const waiting = deferred();
  const f = setup({ chat: (...args) => { f.calls.push(args); return waiting.promise; } });
  const first = f.send("First question");
  assert.equal(await f.send("Second question"), false);
  assert.equal(f.calls.length, 1);
  waiting.resolve(response());
  await first;
  assert.equal(history(f).length, 3);
});

test("temporary pending message is removed when the response arrives", async () => {
  const waiting = deferred();
  const f = setup({ chat: () => waiting.promise });
  const sending = f.send("Question");
  assert.match(history(f).at(-1).className, /pending/);
  waiting.resolve(response());
  await sending;
  assert.equal(history(f).length, 3);
  assert.ok(!history(f).some((bubble) => /pending/.test(bubble.className)));
});

test("tool usage and date appear as metadata", async () => {
  const f = setup();
  await f.send("Overdue?");
  const bubble = history(f).at(-1);
  assert.match(bubble.textContent, /Tools used: get_overdue_leads/);
  assert.match(bubble.textContent, /Reference date: 2026-11-01/);
});

test("transparent metadata when AI did not use database tools", async () => {
  const f = setup({ chat: async () => response({ tools_used: [], used_database_tools: false }) });
  await f.send("Hello");
  assert.match(history(f).at(-1).textContent, /No database tools used/);
});

test("malicious HTML in AI response is treated as plain text", async () => {
  const text = '<img src=x onerror="alert(1)">';
  const f = setup({ chat: async () => response({ answer: text, tools_used: ["<script>bad()</script>"] }) });
  await f.send("Question");
  const bubble = history(f).at(-1);
  assert.match(bubble.textContent, /<img src=x onerror/);
  assert.match(bubble.textContent, /<script>bad\(\)<\/script>/);
  assert.equal(bubble.children[1].tag, "p");
  assert.equal(bubble.children[1].children.length, 0);
});

test("backend 503 explains missing Groq configuration", async () => {
  const f = setup({ chat: async () => { throw { status: 503, message: "Server secret error" }; } });
  assert.equal(await f.send("Question"), false);
  assert.match(history(f).at(-1).textContent, /GROQ_API_KEY/);
  assert.ok(!history(f).at(-1).textContent.includes("Server secret error"));
  assert.equal(f.nodes["send-chat"].disabled, false);
});

test("backend 502 explains provider failure", async () => {
  const f = setup({ chat: async () => { throw { status: 502 }; } });
  await f.send("Question");
  assert.match(history(f).at(-1).textContent, /AI provider/);
});

test("bad input errors are displayed to the user", async () => {
  const f = setup({ chat: async () => { throw { status: 422, message: "message: too long" }; } });
  await f.send("Question");
  assert.match(f.nodes["chat-status"].textContent, /message: too long/);
  assert.ok(f.nodes["chat-status"].classes.has("is-error"));
});

test("network failures are displayed without losing user's question", async () => {
  const f = setup({ chat: async () => { throw Error("Cannot reach FastAPI."); } });
  await f.send("Question");
  assert.match(history(f).at(-2).textContent, /Question/);
  assert.match(history(f).at(-1).textContent, /Cannot reach FastAPI/);
  assert.equal(f.window.BIAI_Chatbot.getSessionId(), null);
});

test("empty AI replies are rejected and do not set a session ID", async () => {
  const f = setup({ chat: async () => response({ answer: "   " }) });
  await f.send("Question");
  assert.match(history(f).at(-1).textContent, /empty answer/);
  assert.equal(f.window.BIAI_Chatbot.getSessionId(), null);
});

test("invalid session IDs from the provider are rejected", async () => {
  const f = setup({ chat: async () => response({ session_id: "invalid session with space" }) });
  await f.send("Question");
  assert.match(history(f).at(-1).textContent, /invalid conversation ID/);
  assert.equal(f.window.BIAI_Chatbot.getSessionId(), null);
});

test("new conversation ignores an old pending response", async () => {
  const waiting = deferred();
  const f = setup({ chat: () => waiting.promise });
  const sending = f.send("Old question");
  f.nodes["new-chat"].fire("click");
  waiting.resolve(response({ answer: "Old answer should not appear" }));
  assert.equal(await sending, false);
  assert.equal(f.window.BIAI_Chatbot.getSessionId(), null);
  assert.equal(history(f).length, 1);
  assert.ok(!history(f)[0].textContent.includes("Old answer"));
});

test("date change ignores an old pending response", async () => {
  const waiting = deferred();
  const f = setup({ chat: () => waiting.promise });
  const sending = f.send("Old question");
  f.nodes["evaluation-mode"].checked = true;
  f.nodes["evaluation-mode"].fire("change");
  waiting.resolve(response({ answer: "Stale data" }));
  assert.equal(await sending, false);
  assert.equal(history(f).length, 1);
  assert.ok(!history(f)[0].textContent.includes("Stale data"));
});

test("the chat UI only calls the read-only chat endpoint", async () => {
  const f = setup({
    updateLead: () => { throw Error("Should not edit lead"); },
    mergeLeads: () => { throw Error("Should not merge"); },
    draftEmail: () => { throw Error("Should not send email"); },
  });
  await f.send("Which deals are overdue?");
  assert.equal(f.calls.length, 1);
});
