# Modular UI Refactor Summary

## What Changed

The Streamlit UI was refactored from a large single-file page implementation into
a modular package.

`streamlit_app.py` is now the application shell. It keeps startup, login gating,
schedule synchronization, data loading orchestration, page routing, and
`page_context()` wiring.

The page implementation now lives in `App/pages/`:

| Module | Responsibility |
|---|---|
| `context.py` | Team and release context selector |
| `dashboard.py` | Dashboard, inventory, latest versions, version comparison, package readiness, compatibility check |
| `operations.py` | Manual workflow actions, schedule controls, operation result summaries |
| `qa_validation.py` | QA validation, manual QA update, evidence upload, signoff, signoff history |
| `security.py` | Vulnerability assessment, scanner upload parsing, heatmap, review queue |
| `admin.py` | Audit logs, settings, user management, release input upload, cache analytics |
| `reports.py` | Report downloads and HTML report preview |
| `support.py` | Shared page helpers |

Supporting code was also split:

| Module | Responsibility |
|---|---|
| `App/data_loaders.py` | Artifact loading and dataframe normalization |
| `App/workflow_actions.py` | Workflow execution and side-effect actions |
| `App/workflow_ui.py` | Workflow Monitor UI |
| `App/layout.py` | Layout, CSS, header, common page labels |
| `App/navigation.py` | Role-aware sidebar navigation |
| `App/ui_components.py` | Shared table and chart components |

## Navigation Fix

The QA Validation tab is now explicitly added for:

- Admin
- QA Engineer

It remains hidden for Release Engineer. This behavior is covered by
`tests/test_page_refactor.py`.

## Tests Added

`tests/test_page_refactor.py` adds coverage for:

- Page package exports and import smoke behavior
- QA signoff save/load/history
- Release input upload save-path behavior
- Vulnerability scanner JSON parse and persisted findings
- Shared page support helper behavior
- QA signoff permission behavior
- QA Validation navigation visibility

## Verification

Use these commands after UI architecture changes:

```powershell
python -m ruff check streamlit_app.py App\pages App\data_loaders.py App\workflow_actions.py --select F401,F841
python -m compileall streamlit_app.py App\pages App\data_loaders.py App\workflow_actions.py
python -m unittest tests.test_page_refactor tests.test_qa_updates tests.test_user_store
python -m unittest tests.test_agent_architecture tests.test_workflow_runs tests.test_workflow_locks tests.test_agent_memory tests.test_page_refactor tests.test_qa_updates tests.test_user_store
```

Expected app smoke check:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8501/ -TimeoutSec 5
```

Expected HTTP status: `200`.
