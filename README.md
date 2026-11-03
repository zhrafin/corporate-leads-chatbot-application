# corporate-leads-chatbot-application

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