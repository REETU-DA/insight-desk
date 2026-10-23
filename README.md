# Insight Desk

Upload CSV/XLSX/XLS files, get an auto-profiled, filterable dashboard (Overview, Trends, Detailed analysis, Data quality).

## Run locally
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    streamlit run app.py
Tests: `pip install pytest && pytest tests`

## Deploy
- **Streamlit Community Cloud**: push this folder to GitHub, create an app, set main file `app.py`.
- **Docker / Cloud Run / Render / Fly**: `pip install -r requirements.txt`, start with
  `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`.
Upload limit is set in `.streamlit/config.toml` (100 MB). Put the app behind HTTPS (all platforms above do).

## Privacy and data handling
Uploads are parsed in memory and held in the visitor's own Streamlit session; nothing is written to disk or sent to any external service. The only outbound requests are the browser loading the IBM Plex font and, in the exported HTML report, the Plotly script from a CDN.

## Behaviour notes
- Original data is never modified. Type conversions apply to a copy and are listed under Data quality. Duplicates and outliers are flagged; duplicates can be excluded from the analysis view with one toggle.
- Cross-filtering: click bars in "Break down by". Bookmarks last for the browser session.
- Charts: use the camera icon for PNG. PDF: open the HTML report and print to PDF.
- Joins refuse many-to-many keys so rows are never silently multiplied.
