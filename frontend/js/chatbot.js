/*
 * Step 26: The BIAI Sales Assistant chat interface.
 *
 * Needs api.js and these elements from index.html:
 *   chat-messages, chat-form, chat-question, send-chat, chat-status,
 *   evaluation-mode, new-chat, and .suggestion-button elements.
 *
 * The backend (LangGraph + Groq) chooses tools and reads SQLite.
 * This file only displays messages and calls BIAI_API.chat().
 */
(function (window, document) {
  "use strict";

  const byId = (id) => document.getElementById(id);

  // The server creates the session ID on the first successful response.
  // We reuse it for follow-up questions. Keep private chats in memory; do not save them in the browser.
  let sessionId = null;
  let busy = false;
  let requestNumber = 0;

  function textNode(tag, value, className = "") {
    const el = document.createElement(tag);
    if (className) el.className = className;
    // Use text nodes so questions and AI output cannot inject markup.
    el.textContent = String(value);
    return el;
  }

  function messageBubble(who, message, extraClass = "") {
    const bubble = document.createElement("div");
    bubble.className = `chat-message chat-message--${who}${extraClass ? ` ${extraClass}` : ""}`;
    bubble.append(
      textNode("strong", who === "user" ? "You" : "BIAI Assistant"),
      textNode("p", message)
    );
    return bubble;
  }

  function addMessage(who, message, extraClass = "") {
    const bubble = messageBubble(who, message, extraClass);
    const container = byId("chat-messages");
    container.append(bubble);
    container.scrollTop = container.scrollHeight;
    return bubble;
  }

  function showStatus(message, isError = false) {
    const status = byId("chat-status");
    status.textContent = message;
    status.classList.toggle("is-error", isError);
  }

  function setBusy(value) {
    busy = value;
    byId("send-chat").disabled = value;
    byId("chat-question").disabled = value;
    document.querySelectorAll(".suggestion-button[data-question]").forEach((button) => {
      button.disabled = value;
    });
  }

  function addAnswerMetadata(bubble, response) {
    const tools = Array.isArray(response.tools_used)
      ? response.tools_used.filter((name) => typeof name === "string").slice(0, 10)
      : [];

    const notes = [];
    if (response.used_database_tools === true) {
      notes.push(tools.length ? `Tools used: ${tools.join(", ")}` : "Database tools were used");
    } else {
      notes.push("No database tools used for this reply");
    }
    if (typeof response.as_of === "string" && /^\d{4}-\d{2}-\d{2}$/.test(response.as_of)) {
      notes.push(`Reference date: ${response.as_of}`);
    }

    bubble.append(textNode("small", notes.join(" · "), "chat-message__meta"));
  }

  function errorMessage(error) {
    // BIAI_API converts FastAPI errors into safe, readable messages.
    if (error?.status === 503) {
      return "The AI service is not configured or available. Check the backend, its dependencies, and GROQ_API_KEY.";
    }
    if (error?.status === 502) {
      return "The AI provider could not finish the request. Please try again.";
    }
    if (error?.status === 422 || error?.status === 400) {
      return error.message || "Please check your question and try again.";
    }
    return error?.message || "The request failed. Check your FastAPI connection and try again.";
  }

  async function send(question) {
    const message = typeof question === "string" ? question.trim() : "";
    if (busy) return false;
    if (!message) {
      showStatus("Enter a question before sending.", true);
      return false;
    }
    if (message.length > 2000) {
      showStatus("Your question must be 2,000 characters or fewer.", true);
      return false;
    }
    if (!window.BIAI_API || typeof window.BIAI_API.chat !== "function") {
      showStatus("The API client is missing. Check that api.js loads before chatbot.js.", true);
      return false;
    }

    // A unique request number lets us ignore a reply from an old conversation.
    const thisRequest = ++requestNumber;
    const evaluationMode = Boolean(byId("evaluation-mode")?.checked);

    addMessage("user", message);
    byId("chat-question").value = "";
    setBusy(true);
    showStatus("Checking your pipeline...");
    const pending = addMessage("assistant", "Checking the pipeline...", "chat-message--pending");

    try {
      const response = await window.BIAI_API.chat(message, sessionId, evaluationMode);
      if (thisRequest !== requestNumber) return false;

      if (!response || typeof response.answer !== "string" || !response.answer.trim()) {
        throw new Error("The AI returned an empty answer. Please retry.");
      }
      if (typeof response.session_id !== "string" ||
          !/^[A-Za-z0-9_-]{1,64}$/.test(response.session_id)) {
        throw new Error("The AI returned an invalid conversation ID. Please start a new chat.");
      }

      // Only a successful response updates the conversation ID.
      sessionId = response.session_id;
      pending.remove();
      const bubble = addMessage("assistant", response.answer);
      addAnswerMetadata(bubble, response);
      showStatus("Answer received. You can ask a follow-up question.");
      return true;
    } catch (error) {
      if (thisRequest !== requestNumber) return false;
      pending.remove();
      const message = errorMessage(error);
      addMessage("assistant", message, "chat-message--error");
      showStatus(message, true);
      return false;
    } finally {
      if (thisRequest === requestNumber) {
        setBusy(false);
        byId("chat-question").focus();
      }
    }
  }

  function reset(reason = "New conversation started.") {
    // Old replies will be discarded, even if Groq finishes after this reset.
    requestNumber += 1;
    sessionId = null;
    setBusy(false);
    byId("chat-question").value = "";

    const history = byId("chat-messages");
    history.replaceChildren(messageBubble(
      "assistant",
      "Hello! Ask me about overdue follow-ups, deal totals, company details, or sales priorities."
    ));
    history.scrollTop = history.scrollHeight;
    showStatus(reason);
  }

  function init() {
    if (!byId("chat-form") || !byId("chat-messages") || !byId("chat-question") ||
        !byId("send-chat") || !byId("chat-status") || !byId("new-chat")) return;

    byId("chat-form").addEventListener("submit", (event) => {
      event.preventDefault();
      void send(byId("chat-question").value);
    });

    document.querySelectorAll(".suggestion-button[data-question]").forEach((button) => {
      button.addEventListener("click", () => {
        void send(button.dataset.question || "");
      });
    });

    byId("new-chat").addEventListener("click", () => {
      reset();
      byId("chat-question").focus();
    });

    const mode = byId("evaluation-mode");
    if (mode) {
      mode.addEventListener("change", () => {
        reset(mode.checked
          ? "Sample date selected (1 Nov 2026). Started a new conversation."
          : "Live date selected. Started a new conversation.");
      });
    }
  }

  window.BIAI_Chatbot = Object.freeze({ send, reset, getSessionId: () => sessionId });
  document.addEventListener("DOMContentLoaded", init);
})(window, document);
