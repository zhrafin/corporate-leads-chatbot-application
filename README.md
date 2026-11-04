# BIAI Corporate Leads Pipeline

A local sales-pipeline application for **25 fictional corporate training leads**. It uses a Python/FastAPI backend, SQLite, plain HTML/CSS/JavaScript, and optional Groq-powered LangGraph assistance.

**Project status:** Core backend and all five frontend screens are implemented. Offline integration checks passed in Step 28. A real Groq request and local cross-port networking still require checking on your computer. This is a **trusted local demonstration**, not a secured public website.

## Start here (Windows PowerShell)

Run these commands in **the folder containing `backend/`, `frontend/`, and `scripts/`**:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
python scripts/import_leads.py
```

Start **two terminals** from the project folder. In Terminal 1:

```powershell
cd backend
python -m uvicorn app.main:app --reload
```

In Terminal 2:

```powershell
cd frontend
python -m http.server 5500
```

Open **http://localhost:5500** for the application or **http://127.0.0.1:8000/docs** for API documentation. The frontend should say **API connected**. If it does not, see [Troubleshooting](docs/TROUBLESHOOTING.md).

The backend runs on **port 8000**, while the frontend runs on **port 5500**. Both need to be running. The CSV importer is safe to run again: it does not overwrite existing leads.

For **macOS/Linux**, use `source .venv/bin/activate`; the other commands are the same. We recommend Python 3.11 or 3.12 and a recent version of Node.js for testing. Node.js is **not** required to run the app.

## What the app can do

| Screen | Capabilities |
|---|---|
| **Dashboard** | Current leads, active pipeline value, overdue follow-ups, unassigned leads, priority deals, stage summaries |
| **Sales Pipeline** | Search/filter, view and edit leads, assign owners, change stages, create leads, review generated email drafts |
| **Data Quality** | Scan missing details, date problems, stage conflicts, overdue actions, and possible duplicates; review warnings; approve merges |
| **AI Assistant** | Ask read-only questions about the database through LangGraph tools and Groq (API key required) |
| **Reports** | Current pipeline summary, original-CSV evaluation, rule-based weekly priority summary, CSV export |

Seven supported stages: **New → Contacted → Meeting → Proposal → Invoice sent → Paid → Lost**. Amounts are **BDT** (Bangladeshi Taka). The date setting can use the local date in **Asia/Dhaka** or BIAI's fixed evaluation date, **2026-11-01**.

### Sample-data baseline

After importing the untouched CSV and selecting **Sample date: 1 Nov 2026**, expect:

| Measure | Expected |
|---|---:|
| Current lead records | 25 |
| Active pipeline value | BDT 3,920,000 |
| Overdue active follow-ups | 9 |
| Unassigned leads | 25 |
| Open warnings after running a scan | 48 |
| Suspected duplicate pairs | 2 |
| Source-data issue-flagged leads | 11 |

These values change when you edit leads, resolve warnings, or confirm merges. The **48 warnings** include follow-up and other operational warnings; they are not 48 distinct data-entry errors. The **original CSV evaluation** remains based on the archived 25 rows. Verified accuracy, precision, and recall remain unavailable until a human reviews **all 25** original lead labels.

## Optional AI setup

The Dashboard, Pipeline, Data Quality, and Reports screens work without a Groq key. The chatbot and AI email drafting require the key, internet access, and the installed AI packages.

**PowerShell (set before starting the backend):**

```powershell
$env:GROQ_API_KEY = "your_actual_groq_api_key"
$env:GROQ_MODEL = "openai/gpt-oss-120b"
cd backend
python -m uvicorn app.main:app --reload
```

**macOS/Linux:** use `export GROQ_API_KEY="..."` and `export GROQ_MODEL="..."` before starting the backend.

The file `.env.example` is only a reference: **the app does not automatically load `.env` files**. Never put a real key in frontend JavaScript or in the ZIP. Confirm your Groq account supports the selected model before running a live request.

The chatbot reads leads using read-only Python tools. Email generation **creates drafts for human review only**. This project does not send email or automatically save drafts. The AI instructions live directly in `backend/app/ai/graph.py` and `backend/app/ai/email_graph.py`; there is no `prompts.py` file.

## Test the project

From the project root, with dependencies and Node.js installed:

```powershell
python scripts/verify_step28.py
```

Optional browser integration test (requires Playwright + Chromium):

```powershell
python -m pip install playwright
python -m playwright install chromium
python scripts/verify_step28.py --browser
```

Optional **real** Groq test. It contacts an external service and uses fictional test data in a temporary database:

```powershell
$env:GROQ_API_KEY = "your_actual_groq_api_key"
python scripts/verify_step28.py --live-ai
```

The test runner does not modify your working `storage/biai_pipeline.db`. See [Step 28 integration](docs/STEP28_INTEGRATION.md) and [Step 28 results](docs/STEP28_RESULTS.md) for scope and known limitations.

## Demonstration and submission

- [Detailed setup guide](docs/INSTALLATION.md)
- [10-minute demonstration script](docs/DEMO_SCRIPT.md)
- [Submission checklist](docs/SUBMISSION_CHECKLIST.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Integration test results](docs/STEP28_RESULTS.md)
- [Step 29 release notes and checks](docs/STEP29_RELEASE_NOTES.md)

To create a clean shareable ZIP that excludes local databases, credentials, and private review labels:

```powershell
python scripts/package_submission.py
```

The script writes `biai-sales-pipeline-submission.zip` **beside the project folder**. Always inspect what you are sharing. The included CSV contains fictional training leads.

## Main folders

```text
biai-sales-pipeline/
├── backend/
│   ├── app/
│   │   ├── ai/           # chatbot + email workflow, instructions in these files
│   │   ├── api/          # FastAPI routes
│   │   └── services/     # imports, business rules, analytics, validation
│   ├── tests/            # Python tests
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   ├── js/               # six frontend scripts
│   └── tests/            # Node.js tests
├── data/                 # fictional CSV and evaluation-label example
├── docs/
├── scripts/              # import, verification, and packaging
└── storage/              # local SQLite database, created when needed; not shared
```

## Important limitations

- **No login, access roles, or per-user session ownership.** Do not expose this app publicly with customer information.
- **No email sending.** A draft always needs a person's review.
- **AI output can contain mistakes.** Check figures against the database and review email wording.
- **Saved validation warnings can become stale.** Rerun the scan after modifying records.
- **Duplicate merges are user-confirmed actions.** Make a backup first; do not merge on name similarity alone.
- **Accuracy is not automatically verified.** The evaluation-label example contains null values until a reviewer completes it.
- **Weekly report is rule-based**, not AI-written.

Copyright: This project is a training/demonstration artifact using fictional sample data.
