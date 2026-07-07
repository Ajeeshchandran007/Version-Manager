# Streamlit Page Architecture

`streamlit_app.py` is intentionally kept as the thin application shell. It owns
startup, authentication, scheduling setup, data loading orchestration, page
routing, and the shared `page_context()` adapter.

Page UI lives under `App/pages/`:

- `context.py` handles team and release selection.
- `dashboard.py` handles dashboard, inventory, latest versions, version comparison,
  package readiness, and compatibility views.
- `operations.py` handles manual workflow actions and schedule controls.
- `qa_validation.py` handles QA validation, manual QA updates, evidence upload,
  QA signoff, and signoff history.
- `security.py` handles vulnerability assessment and uploaded scanner findings.
- `admin.py` handles audit logs, settings, user management, input upload, and cache
  analytics.
- `reports.py` handles report download and HTML preview.
- `support.py` contains shared page helpers such as posture cards, output visibility,
  and operation result rendering.

Supporting layers:

- `App/data_loaders.py` loads and normalizes output artifacts.
- `App/workflow_actions.py` runs workflows and role-specific backend actions.
- `App/workflow_ui.py` renders the workflow monitor.
- `App/layout.py`, `App/navigation.py`, and `App/ui_components.py` provide reusable
  UI primitives.

When adding a page, put Streamlit UI in the closest `App/pages/*` module and expose
only a thin wrapper from `streamlit_app.py` if the main router needs one. Keep
business logic in service modules where it can be tested without Streamlit.
