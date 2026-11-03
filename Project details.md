# BIAI Corporate Leads Pipeline: Complete Application Architecture

Recommended architecture

Your technology stack is suitable for this project:

Python + FastAPI + SQLite + LangGraph + Groq API + HTML + CSS + JavaScript

I recommend a tool-based AI assistant connected to a traditional sales management system.

The most important design decision is to separate the system into two parts:

1. Core CRM engine: Handles leads, stages, dates, data validation, calculations, and database updates using Python and SQLite.
2. AI engine: Uses LangGraph and Groq to understand user questions, call the correct Python tools, and write follow-up emails.

This separation matters because the LLM should never invent deal values, overdue dates, or lead information.

## 1. Complete system architecture

Frontend: HTML + CSS + JavaScript

Dashboard · Lead management · Data quality · AI assistant · Email drafts

HTTP / REST API

FastAPI Backend

API routes · Request validation · Business rules · Error handling

CRM Engine

Lead CRUD

Pipeline stages

Validation rules

Duplicate handling

Reporting

LangGraph AI Engine

Intent understanding

Tool selection

Groq LLM

Email generation

Answer verification

SQLite Database

Single source of current lead data · Flags · Owners · Activity history

The AI tools will call the same business functions used by FastAPI. This prevents the dashboard and chatbot from calculating different answers.

## 2. Requirements mapped to actual application features

| BIAI requirement               | Your solution                       |
| ------------------------------ | ----------------------------------- |
| Show company leads             | Lead table and detail view          |
| Track seven stages             | Pipeline board with stage updates   |
| Identify overdue follow-ups    | Python date validation service      |
| Find missing details           | Required-field validation           |
| Detect duplicates              | Exact and similarity-based matching |
| Detect incorrect dates         | Date consistency rules              |
| Draft AI emails                | Groq-powered email workflow         |
| Answer plain-English questions | LangGraph tool-calling assistant    |
| Weekly sales priorities        | Optional AI report                  |
| Spreadsheet export             | Optional CSV or XLSX export         |
| Show correct detection counts  | Validation test report              |

### Findings that must shape your implementation

I checked the supplied 25-row CSV against the stage definitions.

| Finding                              | Actual result                    |
| ------------------------------------ | -------------------------------- |
| Original lead records                | 25                               |
| Duplicate groups                     | 2, involving L01/L23 and L02/L03 |
| Expected unique records after review | 23                               |
| Overdue follow-ups on 1 Nov 2026     | 9                                |
| Clear follow-up date-order error     | L25                              |
| Missing contact phone                | L06                              |
| Missing contact name, role and email | L24                              |
| Payment-stage inconsistency          | L12                              |
| Owner column in original CSV         | Missing entirely                 |

You should also review L18's email typo and L06's notes, which disagree with its populated email field.

Use 1 November 2026 as the fixed evaluation date, because the supplied README and stage definitions use that date. In normal application mode, use the current date in `Asia/Dhaka`. Keep the two modes separate.

## 3. The five application screens

Screen 1: Sales Dashboard

Show total leads, unique deals, pipeline value, overdue follow-ups, unassigned leads, stage distribution, and the highest-priority deals.

Screen 2: Pipeline and Lead Details

Show seven stage columns, searchable leads, contact information, notes, follow-up dates, assigned owners, and stage-editing controls. Open a lead to generate its AI email draft.

Screen 3: Data Quality Center

Display duplicate groups, missing fields, invalid dates, inconsistent stages, and unresolved warnings. Allow users to fix, dismiss with a reason, or merge records.

Screen 4: AI Sales Assistant

Users ask plain-English questions. LangGraph selects tools, retrieves results, and returns answers with matching lead IDs and amounts.

Screen 5: Reports and Export

Show validation accuracy, stage totals, unresolved issues, and an optional weekly sales summary. Add CSV and Excel export.

The email generator can live inside Screen 2, so no sixth screen is needed.

## 4. Data ingestion and cleaning pipeline

This is the foundation of your entire application. Build it before the chatbot.

25-row CSV file

Python CSV importer

Normalize fields and dates

Run validation rules

Identify duplicate candidates

Human review and corrections

Save records and flags in SQLite

### Step A. Read and normalize the CSV

Use Pandas during the initial import.

- Read with `encoding="utf-8-sig"` because the supplied CSV contains a UTF-8 byte-order mark.
- Rename columns such as `lead id` to `lead_id` and `deal value (BDT)` to `deal_value_bdt`.
- Parse dates explicitly using `%m/%d/%Y`.
- Convert deal values to integers in BDT.
- Trim unnecessary spaces.
- Keep missing values as missing, rather than inventing contact details.
- Preserve the original 25 CSV rows in an import archive.

Store dates in SQLite in ISO format, such as `2026-10-28`, rather than strings like `10/28/2026`.

### Step B. Run validation rules

Create a separate Python validation service.

| Rule                    | Condition                                                    | Action                                          |
| ----------------------- | ------------------------------------------------------------ | ----------------------------------------------- |
| `MISSING_CONTACT`       | Required contact information is missing                      | Flag affected fields                            |
| `MISSING_OWNER`         | Owner is null                                                | Flag unassigned lead                            |
| `DUPLICATE_LEAD`        | Same company/contact, or matching email with similar company | Create duplicate group                          |
| `INVALID_DATE_ORDER`    | Next follow-up precedes last contact                         | Flag conflicting dates                          |
| `OVERDUE_FOLLOWUP`      | Active lead's follow-up is before reference date             | Mark overdue                                    |
| `INVALID_STAGE`         | Stage is outside the seven allowed stages                    | Flag record                                     |
| `STAGE_NOTE_CONFLICT`   | Notes contradict recorded stage                              | Request human review                            |
| `SUSPECTED_EMAIL_ERROR` | Notes or format checks indicate a possible typo              | Request verification                            |
| `MISSING_FOLLOWUP`      | Follow-up is absent                                          | Flag, with a configurable final-stage exception |

Make exact missing-field, date, and overdue checks deterministic.

For notes such as L12's partial payment or L18's reported email typo, you can add a separate AI-assisted review feature. The AI may suggest a warning, but it should not silently change the record.

### Step C. Handle duplicates safely

Your duplicate detection should use two methods:

Exact matching

Normalize company names, email addresses, and phone numbers, then identify identical records.

Similarity matching

Use company-name similarity together with matching email or phone information.

For your CSV:

- L01 and L23 should be grouped together.
- L02 and L03 should be grouped together, despite their different company names.

Do not automatically delete duplicate rows. Show a merge preview where the user can choose which record to keep.

Once confirmed, link the duplicate to the surviving record. Exclude the duplicate from pipeline totals, but preserve the original for auditing.

## 5. SQLite database architecture

I recommend six core tables and one optional table.

leads

Current lead records

owners

Responsible employees

lead_issues

Validation flags

lead_activities

Stage and follow-up history

import_rows

Original CSV data

email_drafts

Saved AI email drafts

merge_history

Optional duplicate audit log

### Main `leads` table

| Column             | Type               | Purpose                    |
| ------------------ | ------------------ | -------------------------- |
| `lead_id`          | TEXT, primary key  | L01, L02, etc.             |
| `company`          | TEXT               | Company name               |
| `industry`         | TEXT               | Business industry          |
| `contact_name`     | TEXT               | Contact person             |
| `contact_role`     | TEXT               | Job title                  |
| `contact_email`    | TEXT               | Email address              |
| `contact_phone`    | TEXT               | Phone number               |
| `source`           | TEXT               | Lead origin                |
| `stage`            | TEXT               | One of seven stages        |
| `deal_value_bdt`   | INTEGER            | Deal amount                |
| `last_contact`     | DATE               | Last recorded contact      |
| `next_follow_up`   | DATE               | Scheduled follow-up        |
| `notes`            | TEXT               | Sales notes                |
| `owner_id`         | INTEGER, nullable  | Assigned employee          |
| `merged_into_id`   | TEXT, nullable     | Surviving lead after merge |
| `stage_changed_at` | DATETIME, nullable | Last known stage change    |
| `updated_at`       | DATETIME           | Last record update         |

`stage_changed_at` is a new field. The CSV does not provide historical stage changes. For imported records, leave this value unknown until the application records an actual stage change.

### `lead_issues` table

Store each detected issue separately.

For example:

```
{
  "lead_id": "L25",
  "rule_code": "INVALID_DATE_ORDER",
  "severity": "high",
  "evidence": {
    "last_contact": "2026-10-28",
    "next_follow_up": "2026-10-21"
  },
  "status": "open"
}
```

This table lets your dashboard, validation center, reports, and chatbot use exactly the same issue records.

Use SQLModel or SQLAlchemy to manage SQLite connections and transactions. FastAPI's official database guide supports this approach.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://fastapi.tiangolo.com\&sz=32)

FastAPI



## 6. FastAPI backend and REST endpoints

Your frontend should never connect directly to SQLite or the Groq API.

All requests should pass through FastAPI.

| HTTP  | Endpoint                  | Purpose                  |
| ----- | ------------------------- | ------------------------ |
| GET   | `/api/dashboard`          | Dashboard statistics     |
| GET   | `/api/leads`              | List and filter leads    |
| GET   | `/api/leads/{id}`         | Retrieve lead details    |
| POST  | `/api/leads`              | Create lead              |
| PATCH | `/api/leads/{id}`         | Edit lead                |
| PATCH | `/api/leads/{id}/stage`   | Change stage             |
| GET   | `/api/pipeline`           | Group deals by stage     |
| GET   | `/api/leads/overdue`      | List overdue follow-ups  |
| GET   | `/api/issues`             | List validation flags    |
| POST  | `/api/issues/scan`        | Run validation           |
| PATCH | `/api/issues/{id}`        | Resolve or dismiss flag  |
| POST  | `/api/duplicates/merge`   | Confirm duplicate merge  |
| POST  | `/api/ai/chat`            | Ask the AI assistant     |
| POST  | `/api/ai/draft-email`     | Generate email draft     |
| GET   | `/api/reports/evaluation` | Data quality accuracy    |
| GET   | `/api/export`             | Optional CSV/XLSX export |

For the first version, keep stage updates and merges as normal, user-confirmed API actions. Do not allow an unrestricted AI tool to edit the database.

## 7. LangGraph AI assistant architecture

This is the AI pipeline I recommend.

User question from JavaScript

LangGraph Agent Node

Groq LLM understands the request and selects tools

Conditional tool routing

Lead search

Overdue checks

Deal totals

Data issues

Priority ranking

Lead notes

Python Services + SQLite

Answer Generation and Verification

Use tool results, report relevant lead IDs, do not invent values

Final response in chatbot UI

### Recommended LangGraph tools

Build the following tools around your Python service layer.

| Tool name                 | Input                          | Output                        |
| ------------------------- | ------------------------------ | ----------------------------- |
| `list_leads`              | Filters, limit                 | Matching leads                |
| `get_lead_details`        | Lead ID                        | One lead's information        |
| `get_overdue_leads`       | None, date supplied by backend | Overdue lead list             |
| `get_stage_total`         | Stage                          | Sum of deal values in BDT     |
| `get_pipeline_summary`    | None                           | Stage counts and totals       |
| `get_data_quality_issues` | Issue type                     | Flagged leads                 |
| `search_lead_notes`       | Keyword                        | Matching notes and leads      |
| `get_priority_leads`      | Limit                          | Ranked deals and explanations |

These eight tools are enough for most questions in the brief.

Do not give the model a generic tool that executes arbitrary SQL or Python. Use specific, read-only functions and parameterized queries.

### Example LangGraph implementation

The following is the core agent structure. The service functions it imports must be implemented separately.

```
    """List matching data quality issues."""    return analytics.quality_issues(issue_type)tools = [    get_stage_total,    get_lead_details,    get_data_quality_issues,]llm = ChatGroq(    model=os.environ["GROQ_MODEL"],    temperature=0,)llm_with_tools = llm.bind_tools(tools)SYSTEM_PROMPT = """You are BIAI's Corporate Sales Assistant.Use tools for all factual pipeline questions.Never invent deal values, dates, or contacts.Use the current database, not remembered values.Do not count merged duplicate records.Always state money values in BDT.If records are incomplete, explain the limitation.Never modify records without user confirmation."""def agent_node(state: MessagesState):    response = llm_with_tools.invoke(        [            SystemMessage(content=SYSTEM_PROMPT),            *state["messages"]        ]    )    return {"messages": [response]}builder = StateGraph(MessagesState)builder.add_node("agent", agent_node)builder.add_node("tools", ToolNode(tools))builder.add_edge(START, "agent")builder.add_conditional_edges(    "agent",    tools_condition)builder.add_edge("tools", "agent")sales_agent = builder.compile()
```

LangGraph's `StateGraph`, `ToolNode`, and conditional routing support this pattern. Groq's Python integration supports tool calling through `ChatGroq`.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://reference.langchain.com\&sz=32)

LangChain Reference+3



For the complete system, add the remaining tools, session-specific chat history, a maximum tool-call limit, and a final answer checker.

The backend should supply the trusted reference date for calculations. Do not allow the LLM to silently choose a different date.

## 8. AI email drafting pipeline

Email drafting should be a separate LangGraph workflow because the user has already selected a specific lead.

Select lead

User clicks Generate Email

Retrieve record

Get contact, stage, notes and history

Check data quality

Warn about missing or suspected incorrect contact details

Generate draft

Groq writes subject and email body

Validate draft

Check length, language, claims and format

Human review

User edits, copies or saves the draft

Define an email-generation prompt with these rules:

- Write in English.
- Keep the body around 60 to 120 words.
- Use the actual contact name, company, stage, and notes.
- Mention a relevant earlier discussion only when the notes support it.
- Include one clear next action.
- Do not invent promises, discounts, training dates, or payment status.
- Output a subject and an email body.

For example, L02's notes say the proposal was sent, budget approval is pending, and Bangla support is wanted. The draft can refer to these facts.

For L10, the notes say the written proposal has not been sent. The AI must not claim that the proposal was already delivered.

Do not automatically send the email. BIAI only asks for drafting. Let the user review and copy it.

If a selected lead has questionable contact data, such as L18, display a warning before the user uses the draft.

## 9. Pipeline stage management

Model the pipeline using these active stages:

`New → Contacted → Meeting → Proposal → Invoice sent`

And the terminal outcomes:

`Paid` or `Lost`

Use the stage definitions as the source of truth:

- Paid means the full deal amount was received.
- Lost means the company declined or stopped responding for at least 30 days.
- Invoice sent means payment remains pending.

When a user changes a stage, validate the new stage, update the lead, and record the change in `lead_activities`.

Allow users to correct earlier mistakes, even if a correction moves a deal backward. Record the previous and new values.

### How to identify a stuck deal

The original CSV has last-contact dates but no stage-change history. Therefore, you cannot reliably calculate how long each imported lead has stayed in its current stage.

For the initial dataset, define an attention-needed deal using overdue follow-ups, missing ownership, and unresolved next actions.

After launch, your `stage_changed_at` and activity history will allow more precise stuck-stage detection.

You can also add an optional business rule, such as highlighting a lead that has had no activity for seven days. Label that as your proposed rule, since BIAI did not specify a seven-day threshold.

## 10. Sales priority ranking

The optional weekly summary should use a defined scoring or ordering method before the LLM writes its explanation.

My suggested priority order is:

1. Active overdue deals with a clear next action.
2. High-value overdue deals in Meeting, Proposal, or Invoice sent.
3. Unassigned leads requiring an owner.
4. Other active leads approaching their follow-up dates.

Check the notes before recommending action.

For example, L22 has an overdue invoice follow-up, but its notes say to verify the bank payment first. The correct next action is to check payment status, not immediately send a payment reminder.

Your `get_priority_leads()` tool should calculate the rankings. The LLM should explain them in natural language.

## 11. Frontend implementation

Use plain HTML, CSS, and JavaScript, as you requested.

| Layer      | Responsibility                                          |
| ---------- | ------------------------------------------------------- |
| HTML       | App layout, forms, tables and dialogs                   |
| CSS        | Responsive layout, status colors and stage cards        |
| JavaScript | API calls, search, filtering, stage updates and chatbot |
| FastAPI    | Backend endpoints and static asset serving              |

Use the JavaScript `fetch()` API to communicate with FastAPI.

For example, asking a question could send:

```
{
  "message": "What is the total value in Proposal stage?",
  "session_id": "demo-session",
  "as_of": "2026-11-01"
}
```

The backend should validate the request, set the permitted date context, invoke the LangGraph workflow, and return the answer.

A useful structured response would look like:

```
{
  "answer": "The cleaned Proposal-stage total is BDT 920,000.",
  "value_bdt": 920000,
  "lead_ids": ["L02", "L14"],
  "excluded_duplicates": ["L03"],
  "as_of": "2026-11-01"
}
```

This is an expected example response, not an existing implemented API.

Your JavaScript can show the answer, display the two lead records, and allow the user to open either company.

## 12. Project folder structure

I recommend this structure:

```
biai-sales-pipeline/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   │
│   │   ├── api/
│   │   │   ├── leads.py
│   │   │   ├── pipeline.py
│   │   │   ├── issues.py
│   │   │   ├── ai.py
│   │   │   └── reports.py
│   │   │
│   │   ├── services/
│   │   │   ├── lead_service.py
│   │   │   ├── validation.py
│   │   │   ├── duplicates.py
│   │   │   ├── analytics.py
│   │   │   ├── priority.py
│   │   │   └── csv_importer.py
│   │   │
│   │   └── ai/
│   │       ├── graph.py
│   │       ├── tools.py
│   │       ├── email_graph.py
│   │       └── prompts.py
│   │
│   ├── tests/
│   │   ├── test_validation.py
│   │   ├── test_duplicates.py
│   │   ├── test_analytics.py
│   │   ├── test_api.py
│   │   └── test_agent.py
│   └── requirements.txt
│
├── frontend/
│   ├── index.html
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── api.js
│       ├── dashboard.js
│       ├── leads.js
│       ├── validation.js
│       └── chatbot.js
│
├── data/
│   ├── corporate-leads.csv
│   └── stage-definitions.md
│
├── scripts/
│   └── import_leads.py
│
├── .env.example
├── .gitignore
└── README.md
```

The SQLite database can live in a separate `storage/` directory. Keep it out of version control.

### Core packages

```
pip install fastapi uvicorn sqlmodel pandas \
  langgraph langchain-core langchain-groq \
  python-dotenv pydantic pytest
```

For duplicate name similarity, you can optionally add `rapidfuzz`. For Excel export, use `openpyxl`.

Configure the Groq API key through environment variables. Keep API credentials in the backend, never in frontend JavaScript.

## 13. Testing and evaluation pipeline

BIAI explicitly asks you to show how many leads you flagged correctly, so you should treat evaluation as a required feature.

Recommended test cases

| Test                    | Expected result                     |
| ----------------------- | ----------------------------------- |
| CSV import              | 25 rows loaded without losing data  |
| Exact duplicates        | L01 and L23 matched                 |
| Similar-name duplicates | L02 and L03 matched                 |
| Date validation         | L25 flagged                         |
| Missing details         | L06 and L24 flagged                 |
| Payment consistency     | L12 flagged for review              |
| Overdue calculation     | 9 active leads on reference date    |
| Proposal total          | ৳920,000 after duplicate resolution |
| Email grounding         | No facts invented beyond lead data  |
| Database editing        | Audit history retained              |

Build a manually verified answer key with one row per lead and separate labels for each issue type.

Report:

- True positives: Real problems correctly flagged.
- False positives: Correct records incorrectly flagged.
- False negatives: Real problems that your app missed.
- Precision and recall: How accurately and completely your validation works.

Keep overdue follow-ups separate from data-quality errors. An overdue deal is an action that needs attention, but its information may be correct.

Test the chatbot with around 20 to 30 questions, including stage totals, overdue records, duplicates, missing information, and follow-up drafting. Compare its answers with direct SQL or Python results.

## 14. Security and deployment

For a local demo, one FastAPI process serving the frontend and backend is enough.

Before making the app publicly accessible, add login protection, restrict who can edit records, and keep the Groq key private.

Other important safeguards:

- Use parameterized database queries.
- Validate inputs with Pydantic.
- Never execute AI-generated Python or unrestricted SQL.
- Limit the amount of conversation history and number of tool calls.
- Escape untrusted text when rendering HTML.
- Keep a backup of the SQLite database.
- Use persistent storage if deploying to a cloud service.
- Record AI tool failures rather than returning invented answers.

You do not need Docker or Kubernetes for the first working version. Docker is useful later if you want a repeatable deployment.

## 15. Implementation order

Build the application in six phases.

## 01

Database and data importer

Create the schema, import the CSV, preserve the source data, and add the missing owner field.

## 02

Validation and lead services

Implement missing-field checks, overdue calculations, duplicate detection, date validation, lead editing, and merge review.

## 03

FastAPI and frontend

Build the dashboard, lead board, detail view, validation center, and REST endpoints.

## 04

LangGraph chatbot

Add Groq, the read-only tools, answer verification, and the frontend chat interface.

## 05

Email generation and optional reporting

Build the lead-specific drafting workflow, then add the weekly summary and export features if the core functions are working.

## 06

Evaluation and demonstration

Test all 25 records, measure validation accuracy, test chatbot questions, and demonstrate how L25 and both duplicate groups are handled.

## Final application pipeline

The full design has three connected workflows.

| Workflow     | Technology                        | Responsibility                                           |
| ------------ | --------------------------------- | -------------------------------------------------------- |
| Data and CRM | Python, Pandas, SQLite, FastAPI   | Import, clean, validate, store, update and calculate     |
| AI Assistant | LangGraph, Groq API, Python tools | Understand questions, retrieve facts and explain answers |
| AI Email     | LangGraph, Groq API, lead notes   | Draft, check and present follow-up emails for review     |

My strongest recommendation is to build the CRM and validation services first, then connect them to LangGraph. This gives you a chatbot that works from verified business data rather than relying on the LLM to reason over a messy CSV by itself.

With five screens, eight focused AI tools, a small SQLite database, and a clear evaluation report, you can meet the full brief without making the project unnecessarily large.