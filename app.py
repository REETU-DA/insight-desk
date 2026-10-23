"""Insight Desk: upload data, get a profiled, filterable dashboard. Data stays in this session's memory."""
import hashlib
import io
from datetime import date as _date

import pandas as pd
import plotly.express as px
import streamlit as st

import analytics as A

st.set_page_config(page_title="Insight Desk", page_icon="📊", layout="wide")
S = st.session_state
PALETTE = ["#2E6F95", "#C8832B", "#4C8C7A", "#7A6C9E", "#8F9AA6", "#A94F4F"]
CFG = {"displaylogo": False, "toImageButtonOptions": {"format": "png", "scale": 2}}
px.defaults.color_discrete_sequence = PALETTE
px.defaults.template = "simple_white"

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap');
html, body, [class*="css"], .stMarkdown, button { font-family: 'IBM Plex Sans', sans-serif; }
.block-container { padding-top: 1.4rem; max-width: 1400px; }
h1 { font-weight: 600; letter-spacing: -0.02em; color: #1B2A41; margin-bottom: 0; }
[data-testid="stMetric"] { background: #fff; border: 1px solid #D5DBE1; border-left: 4px solid #2E6F95; padding: 12px 16px; border-radius: 4px; min-height: 112px; }
[data-testid="stMetricLabel"] p { color: #4A5B6D; font-size: .85rem; }
[data-testid="stMetricValue"] { color: #1B2A41; font-weight: 600; }
.stTabs [data-baseweb="tab"] { font-weight: 500; }
.note { color: #4A5B6D; font-size: .85rem; }
</style>""", unsafe_allow_html=True)

for k, v in {"tables": {}, "source": None, "bookmarks": {}, "xf": {}, "chart_ver": 0, "up_sig": None, "prep": {}, "active_prev": None}.items():
    S.setdefault(k, v)


# ───────────────────────── helpers ─────────────────────────
def load_tables(tables, source):
    S.tables, S.source, S.prep, S.xf = tables, source, {}, {}
    for k in [k for k in S if k.startswith(("f_", "cfg_"))]:
        del S[k]


def prepared(name):
    if name not in S.prep:
        raw = S.tables[name]
        typed, notes = A.infer_types(raw)
        S.prep[name] = (raw, typed, notes, A.column_roles(typed))
    return S.prep[name]


def chart(fig, key, height=340, select=False):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=40, b=8), title_font_size=15,
                      legend=dict(orientation="h", y=-0.2), font_family="IBM Plex Sans")
    figs.append(fig)
    if select:
        return st.plotly_chart(fig, width="stretch", config=CFG, key=f"{key}_{S.chart_ver}", on_select="rerun", selection_mode="points")
    st.plotly_chart(fig, width="stretch", config=CFG, key=key)


def fmt(v):
    if v is None or pd.isna(v):
        return "n/a"
    a = abs(v)
    return f"{v/1e9:,.2f}B" if a >= 1e9 else f"{v/1e6:,.2f}M" if a >= 1e6 else f"{v:,.0f}" if a >= 1e4 else f"{v:,.2f}"


def to_excel(df, filters_text):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        df.to_excel(w, sheet_name="Filtered data", index=False)
        pd.DataFrame({"Active filters": filters_text or ["None"]}).to_excel(w, sheet_name="Filters", index=False)
    return buf.getvalue()


# ───────────────────────── sidebar: data ─────────────────────────
figs: list = []
with st.sidebar:
    st.header("Data")
    files = st.file_uploader("Upload CSV, XLSX or XLS (several allowed)", type=["csv", "xlsx", "xls"], accept_multiple_files=True)
    sig = hashlib.md5("|".join(f"{f.name}:{f.size}" for f in files).encode()).hexdigest() if files else None
    if files and sig != S.up_sig:
        tabs, errs = {}, []
        with st.spinner("Reading files…"):
            for f in files:
                try:
                    tabs.update(A.load_file(f.name, f.getvalue()))
                except ValueError as e:
                    errs.append(str(e))
        S.up_sig = sig
        S.upload_errors = errs
        if tabs:
            load_tables(tabs, "upload")
            st.rerun()
    for e in S.get("upload_errors", []):
        st.error(e)
    if not files and S.source == "upload":
        load_tables({}, None)
    if st.button("Load demo data", help="Synthetic sample data for a tour. It replaces, and is never mixed with, uploaded data."):
        load_tables(A.demo_tables(), "demo")
        st.rerun()

if not S.tables:
    st.title("Insight Desk")
    st.write("Upload one or more CSV or Excel files in the sidebar to build a dashboard from your own data. "
             "Nothing leaves this session, and no data is sent to any AI service.")
    st.stop()

with st.sidebar:
    if S.source == "demo":
        st.info("Showing synthetic demo data.")
    names = list(S.tables)
    active = st.selectbox("Table or sheet", names, key="cfg_table")
    rels = A.detect_relationships({n: S.tables[n] for n in names}) if len(names) > 1 else []
    if rels:
        with st.expander(f"Possible relationships ({len(rels)})"):
            for i, r in enumerate(rels):
                st.write(f"**{r['left_key']}** in {r['left']} ↔ **{r['right_key']}** in {r['right']} · {r['overlap']:.0%} overlap")
                if st.button("Join into new table", key=f"join{i}"):
                    try:
                        S.tables[f"Joined · {r['left']} + {r['right']}"] = A.merge_tables(S.tables[r["left"]], S.tables[r["right"]], r["left_key"], r["right_key"])
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

raw, typed, notes, roles = prepared(active)
if S.active_prev != active:
    for k in [k for k in S if k.startswith("f_")]:
        del S[k]
    S.xf, S.active_prev = {}, active

dates = [c for c, r in roles.items() if r == "date"]
measures = [c for c, r in roles.items() if r == "numeric"]
cats = [c for c, r in roles.items() if r == "category"]

with st.sidebar:
    st.header("Filters")
    dedupe = st.toggle("Exclude exact duplicate rows", key="f_dedupe", help="Only affects the analysis view; your original data is untouched.")
    date_col = st.selectbox("Date field", dates, key="cfg_date") if dates else None
    date_rng = None
    if date_col:
        lo, hi = typed[date_col].min().date(), typed[date_col].max().date()
        S.setdefault("f_date", (lo, hi))
        date_rng = st.date_input("Date range", min_value=lo, max_value=hi, key="f_date")
    cat_filter_cols = st.multiselect("Category filters", cats, default=cats[:3], key="cfg_catf")
    cat_sel = {c: st.multiselect(c, sorted(typed[c].dropna().astype(str).unique()), key=f"f_cat_{c}") for c in cat_filter_cols}
    num_filter_cols = st.multiselect("Numeric range filters", measures, key="cfg_numf")
    num_sel = {}
    for c in num_filter_cols:
        lo_, hi_ = float(typed[c].min()), float(typed[c].max())
        if lo_ < hi_:
            S.setdefault(f"f_num_{c}", (lo_, hi_))
            num_sel[c] = st.slider(c, lo_, hi_, key=f"f_num_{c}")
    st.header("Customize")
    kpi_cols = st.multiselect("KPI measures", measures, default=measures[:4], key="cfg_kpis")
    agg_mode = st.radio("Aggregation", ["Auto", "Sum", "Average"], horizontal=True, key="cfg_agg",
                        help="Auto uses an average for rate, price, score-type fields and a sum otherwise.")

    st.header("Bookmarks")
    bname = st.text_input("Bookmark name", key="bm_name")

    def save_bm():
        if S.bm_name.strip():
            S.bookmarks[S.bm_name.strip()] = {k: S[k] for k in S if k.startswith(("f_", "cfg_")) and k != "cfg_table"} | {"_xf": dict(S.xf), "_table": S.cfg_table}
    st.button("Save current view", on_click=save_bm, disabled=not bname.strip())
    for n_, bm in list(S.bookmarks.items()):
        c1, c2, c3 = st.columns([3, 1, 1])
        c1.write(n_)

        def restore(bm=bm):
            if bm["_table"] != S.cfg_table:
                S.cfg_table = bm["_table"]
            for k in [k for k in S if k.startswith(("f_", "cfg_")) and k != "cfg_table"]:
                del S[k]
            S.update({k: v for k, v in bm.items() if not k.startswith("_")})
            S.xf = dict(bm["_xf"])
            S.active_prev = bm["_table"]
        c2.button("↩", key=f"r_{n_}", on_click=restore, help="Restore")
        c3.button("✕", key=f"d_{n_}", on_click=lambda n_=n_: S.bookmarks.pop(n_), help="Delete")

# apply filters
base = typed
if dedupe:
    base = base[~raw.duplicated().values]
filtered = A.apply_filters(base, date_col, date_rng, {c: v for c, v in cat_sel.items()}, num_sel)
F = A.apply_filters(filtered, extra=S.xf)   # filters + cross-filter selections
active_filters = ([f"{date_col}: {date_rng[0]} to {date_rng[1]}"] if date_col and date_rng and len(date_rng) == 2 else []) \
    + [f"{c}: {', '.join(v)}" for c, v in cat_sel.items() if v] + [f"{c}: {a} to {b}" for c, (a, b) in num_sel.items()] \
    + [f"{c} (chart selection): {', '.join(map(str, v))}" for c, v in S.xf.items()]

st.title("Insight Desk")
h1, h2 = st.columns([5, 1])
h1.markdown(f"<span class='note'>{active} · {len(F):,} of {len(typed):,} rows after filters</span>", unsafe_allow_html=True)
if S.xf and h2.button("Clear chart selection"):
    S.xf, S.chart_ver = {}, S.chart_ver + 1
    st.rerun()
if F.empty:
    st.warning("No rows match the current filters. Widen the date range or clear a filter.")
    st.stop()


def agg_for(m):
    return {"Sum": "sum", "Average": "mean"}.get(agg_mode) or A.default_agg(m)


def xselect(fig_event, dim, horizontal=False):
    pts = (fig_event.selection.points if fig_event else []) or []
    vals = sorted({str(p.get("y" if horizontal else "x")) for p in pts})
    if vals != sorted(map(str, S.xf.get(dim, []))):
        S.xf = {k: v for k, v in S.xf.items() if k != dim} | ({dim: vals} if vals else {})
        st.rerun()


tab_o, tab_t, tab_d, tab_q = st.tabs(["Overview", "Trends", "Detailed analysis", "Data quality"])

# ───────────────────────── Overview ─────────────────────────
with tab_o:
    cols = st.columns(len(kpi_cols) + 1)
    cols[0].metric("Records", f"{len(F):,}", f"{len(F) / len(typed):.0%} of all rows", delta_color="off")
    kpi_rows = []
    for col, m in zip(cols[1:], kpi_cols):
        a = agg_for(m)
        k = A.kpi(F, m, a, date_col)
        label = {"sum": "Total", "mean": "Average"}[a] + f" {m}"
        delta = f"{k['change']:+.1%} vs prior {k['grain'].lower()}" if k["change"] is not None else None
        col.metric(label, fmt(k["value"]), delta, help=f"{a}({m}) over {k['n']:,} non-missing rows ({k['missing']:,} missing). "
                   + (f"Change compares the last two {k['grain'].lower()} periods ({fmt(k['last'])} vs {fmt(k['prev'])}); the latest period may be incomplete." if k["last"] is not None else ""))
        kpi_rows.append((label, fmt(k["value"]), delta or ""))
    if not measures:
        st.info("No numeric columns were found, so only counts and categories are shown.")
    pm = st.selectbox("Primary measure", measures, key="cfg_pm") if measures else None
    if pm:
        a = agg_for(pm)
        left, right = st.columns(2)
        with left:
            if date_col:
                ct = st.radio("Trend style", ["Line", "Area", "Bar"], horizontal=True, key="cfg_ct")
                g = A.auto_grain(F[date_col])
                ts = A.time_series(F, date_col, pm, a, g)
                fn = {"Line": px.line, "Area": px.area, "Bar": px.bar}[ct]
                chart(fn(ts, x="period", y=pm, title=f"{a} of {pm} by {g.lower()}"), "ov_trend")
            else:
                chart(px.histogram(F, x=pm, title=f"Distribution of {pm}"), "ov_hist")
        with right:
            if cats:
                dim = st.selectbox("Break down by", cats, key="cfg_dim")
                base_ = A.apply_filters(filtered, extra={k: v for k, v in S.xf.items() if k != dim})
                g_ = base_.groupby(dim, observed=True)[pm].agg(a).sort_values().tail(15).reset_index()
                g_[dim] = g_[dim].astype(str)
                fig = px.bar(g_, x=pm, y=dim, orientation="h", title=f"{a} of {pm} by {dim} (click a bar to cross-filter)")
                if dim in S.xf:
                    fig.update_traces(marker_color=[PALETTE[0] if v in map(str, S.xf[dim]) else "#C3CCD6" for v in g_[dim]])
                xselect(chart(fig, "ov_bar", select=True), dim, horizontal=True)
        st.subheader("Insights")
        for head, calc in A.insights(F, roles, pm, a, date_col, S.get("cfg_dim") if cats else None):
            with st.expander(head):
                st.caption(calc)

# ───────────────────────── Trends ─────────────────────────
with tab_t:
    if not date_col or not measures:
        st.info("Trends need at least one date column and one numeric column.")
    else:
        c1, c2, c3 = st.columns(3)
        grain = c1.selectbox("Time grain", list(A.FREQ), index=list(A.FREQ).index(A.auto_grain(F[date_col])), key="cfg_grain")
        tm = c2.selectbox("Measure", measures, key="cfg_tm")
        split = c3.selectbox("Split by", ["(none)"] + cats, key="cfg_split")
        a = agg_for(tm)
        by = None if split == "(none)" else split
        ts = A.time_series(F, date_col, tm, a, grain, by)
        if by:
            top = F[by].value_counts().head(8).index
            ts = ts[ts[by].isin(top)]
            style = st.radio("Style", ["Lines", "Stacked bars", "Stacked area"], horizontal=True, key="cfg_ts")
            fig = {"Lines": px.line, "Stacked bars": px.bar, "Stacked area": px.area}[style](ts, x="period", y=tm, color=by, title=f"{a} of {tm} by {grain.lower()} and {by} (top 8)")
        else:
            ma = st.slider("Moving average window (periods)", 1, 12, 1, key="cfg_ma")
            fig = px.line(ts, x="period", y=tm, markers=True, title=f"{a} of {tm} by {grain.lower()}")
            if ma > 1:
                ts["ma"] = ts[tm].rolling(ma).mean()
                fig.add_scatter(x=ts.period, y=ts.ma, name=f"{ma}-period average", line=dict(color=PALETTE[1], dash="dash"))
        chart(fig, "tr_main", 400)
        tot = A.time_series(F, date_col, tm, a, grain)
        tot["growth"] = tot[tm].pct_change() * 100
        gfig = px.bar(tot.dropna(subset=["growth"]), x="period", y="growth", title=f"Period-over-period change in {tm} (%)")
        gfig.update_traces(marker_color=[PALETTE[2] if v >= 0 else PALETTE[5] for v in tot.dropna(subset=["growth"]).growth])
        chart(gfig, "tr_growth")
        st.caption("Growth is computed from the aggregated values shown above; the first and last periods can be partial.")

# ───────────────────────── Detailed analysis ─────────────────────────
with tab_d:
    if measures:
        c1, c2 = st.columns(2)
        with c1:
            hm = st.selectbox("Histogram of", measures, key="cfg_hm")
            chart(px.histogram(F, x=hm, nbins=30, title=f"Distribution of {hm}"), "d_hist")
        with c2:
            if len(measures) >= 2:
                sx = st.selectbox("Scatter X", measures, key="cfg_sx")
                sy = st.selectbox("Scatter Y", measures, index=1, key="cfg_sy")
                sc = st.selectbox("Colour by", ["(none)"] + cats, key="cfg_sc")
                smp = F.sample(min(len(F), 5000), random_state=0)
                chart(px.scatter(smp, x=sx, y=sy, color=None if sc == "(none)" else sc, opacity=.6, title=f"{sy} vs {sx}" + (" (5,000-row sample)" if len(F) > 5000 else "")), "d_scatter")
    if len(cats) >= 1 and measures:
        st.subheader("Drill-down")
        d1, d2, d3, d4 = st.columns(4)
        l1 = d1.selectbox("Level 1", cats, key="cfg_l1")
        v1 = d2.selectbox("Value", ["(all)"] + sorted(F[l1].dropna().astype(str).unique()), key="cfg_v1")
        l2 = d3.selectbox("Level 2", [c for c in cats if c != l1] or [l1], key="cfg_l2")
        dm = d4.selectbox("Measure", measures, key="cfg_dm")
        sub = F if v1 == "(all)" else F[F[l1].astype(str) == v1]
        chart(px.bar(sub.groupby([l1, l2], observed=True)[dm].agg(agg_for(dm)).reset_index(), x=l1, y=dm, color=l2, barmode="stack",
                     title=f"{agg_for(dm)} of {dm}: {l1} → {l2}" + ("" if v1 == "(all)" else f" (within {v1})")), "d_drill")
    if len(measures) >= 3:
        chart(px.imshow(F[measures].corr(), text_auto=".2f", zmin=-1, zmax=1, color_continuous_scale="RdBu_r", title="Correlation matrix"), "d_corr", 380)
    st.subheader("Records")
    q = st.text_input("Search all columns", key="f_search")
    tbl = F[F.astype(str).apply(lambda col: col.str.contains(q, case=False, regex=False)).any(axis=1)] if q else F
    st.dataframe(tbl, width="stretch", height=360)
    st.caption(f"{len(tbl):,} rows · click a column header to sort")
    e1, e2, e3 = st.columns(3)
    e1.download_button("Download CSV", tbl.to_csv(index=False).encode("utf-8-sig"), "filtered_data.csv", "text/csv")
    e2.download_button("Download Excel", to_excel(tbl, active_filters), "filtered_data.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    html = ["<html><head><meta charset='utf-8'><title>Insight Desk report</title><style>body{font-family:sans-serif;max-width:1000px;margin:auto}"
            "@media print{.pb{page-break-inside:avoid}}</style></head><body>", f"<h1>{active}</h1><p>{len(F):,} of {len(typed):,} rows. Filters: {'; '.join(active_filters) or 'none'}</p><table border=1 cellpadding=6>"]
    html += [f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in kpi_rows] + ["</table>"]
    html += [f"<div class='pb'>{f.to_html(full_html=False, include_plotlyjs='cdn' if i == 0 else False)}</div>" for i, f in enumerate(figs)] + ["</body></html>"]
    e3.download_button("Report (HTML → print to PDF)", "".join(html).encode("utf-8"), "report.html", "text/html")

# ───────────────────────── Data quality ─────────────────────────
with tab_q:
    qr = A.quality_report(raw, typed, roles)
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Rows × columns", f"{qr['rows']:,} × {qr['cols']}")
    q2.metric("Complete rows", f"{qr['complete_rows'] / max(qr['rows'], 1):.1%}")
    q3.metric("Duplicate rows", f"{qr['duplicates']:,}")
    q4.metric("Columns with outliers", len(qr["outliers"]))
    st.caption("These checks only report issues. Your uploaded data is never altered; use the sidebar toggle to exclude duplicates from the analysis view.")
    st.subheader("Detected column types")
    st.dataframe(pd.DataFrame({"column": list(roles), "role": list(roles.values()), "dtype": [str(typed[c].dtype) for c in roles],
                               "distinct": [typed[c].nunique() for c in roles]}), width="stretch", hide_index=True)
    if notes:
        st.subheader("Conversions applied to the analysis copy")
        for n_ in notes:
            st.write("• " + n_)
    st.subheader("Missing values")
    if qr["missing"].empty:
        st.success("No missing values.")
    else:
        st.dataframe(qr["missing"], width="stretch")
    st.subheader("Outliers (1.5 × IQR rule)")
    if qr["outliers"].empty:
        st.success("No outliers flagged.")
    else:
        st.dataframe(qr["outliers"], width="stretch", hide_index=True)
    st.subheader("Preview of uploaded data")
    st.dataframe(raw.head(100), width="stretch")
