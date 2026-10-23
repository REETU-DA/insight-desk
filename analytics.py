"""Analytics layer: pure pandas, no Streamlit, so every calculation is unit-testable.

Principle: raw data is never modified. `infer_types` returns a typed *copy* plus a
log of every conversion; quality issues are reported, not silently fixed.
"""
from __future__ import annotations

import io
import re

import numpy as np
import pandas as pd

NUM_CLEAN = re.compile(r"[,$€£₹%\s]")
ID_NAME = re.compile(r"(^|[_\s-])(id|key|uuid|guid|code|no|num|number)$", re.I)
MEAN_NAME = re.compile(r"rate|pct|percent|ratio|price|score|rating|age|margin|discount|temp|avg|average", re.I)
FREQ = {"Day": "D", "Week": "W", "Month": "M", "Quarter": "Q", "Year": "Y"}


# ───────────────────────────── loading ─────────────────────────────
def fix_header(df: pd.DataFrame) -> pd.DataFrame:
    """Drop empty rows/cols; if the header row is not first (many 'Unnamed' columns), promote it."""
    df = df.dropna(how="all").dropna(axis=1, how="all")
    if df.empty:
        return df
    cols = [str(c) for c in df.columns]
    if sum(c.startswith("Unnamed") for c in cols) > len(cols) / 2:
        ok = df.notna().sum(axis=1) >= 0.6 * df.shape[1]
        if ok.any():
            i = ok.idxmax()
            pos = df.index.get_loc(i)
            df.columns = [str(v).strip() if pd.notna(v) else f"column_{k}" for k, v in enumerate(df.iloc[pos])]
            df = df.iloc[pos + 1:]
    seen: dict[str, int] = {}
    names = []
    for c in df.columns:
        c = str(c).strip()
        seen[c] = seen.get(c, 0) + 1
        names.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    df = df.copy()
    df.columns = names
    return df.reset_index(drop=True)


def load_file(name: str, data: bytes) -> dict[str, pd.DataFrame]:
    """Return {table_name: DataFrame}. Excel files yield one table per non-empty sheet."""
    if not data:
        raise ValueError(f"{name}: the file is empty.")
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext in ("csv", "tsv", "txt"):
        df = None
        for enc in ("utf-8-sig", "latin-1"):
            try:
                df = pd.read_csv(io.BytesIO(data), sep=None, engine="python", encoding=enc)
                break
            except UnicodeDecodeError:
                continue
            except Exception as e:
                raise ValueError(f"{name}: could not be read as CSV ({e}).") from e
        df = fix_header(df)
        if df.empty:
            raise ValueError(f"{name}: no data rows found.")
        return {name: df}
    if ext in ("xlsx", "xlsm", "xls"):
        try:
            xl = pd.ExcelFile(io.BytesIO(data))
        except ImportError as e:
            raise ValueError(f"{name}: Excel reader missing ({e}). Install openpyxl (.xlsx) or xlrd (.xls).") from e
        except Exception as e:
            raise ValueError(f"{name}: not a valid Excel file ({e}).") from e
        out = {}
        for sheet in xl.sheet_names:
            d = fix_header(xl.parse(sheet))
            if not d.empty:
                out[f"{name} › {sheet}"] = d
        if not out:
            raise ValueError(f"{name}: no sheet contains data.")
        return out
    raise ValueError(f"{name}: unsupported file type '.{ext}'. Upload CSV, XLSX or XLS.")


# ───────────────────────────── typing & profiling ─────────────────────────────
def infer_types(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Typed copy of df + a plain-language log of each conversion."""
    out, notes = df.copy(), []
    for c in out.columns:
        s = out[c]
        if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
            continue
        nn = s.dropna().astype(str).str.strip()
        nn = nn[nn != ""]
        if nn.empty:
            continue
        cleaned = nn.str.replace(NUM_CLEAN, "", regex=True)
        num = pd.to_numeric(cleaned, errors="coerce")
        if num.notna().mean() >= 0.95 and not nn.str.match(r"^0\d").any():
            out[c] = pd.to_numeric(s.astype(str).str.replace(NUM_CLEAN, "", regex=True), errors="coerce")
            bad = int(out[c].isna().sum() - s.isna().sum())
            notes.append(f"'{c}': text → number" + (f" ({bad} unparseable value(s) became missing)" if bad else ""))
            continue
        if nn.str.contains(r"\d").mean() >= 0.9 and nn.str.contains(r"[-/.]|[A-Za-z]{3}").mean() >= 0.9:  # pure digit strings (zip codes, ids) are not dates
            smp = nn.sample(min(len(nn), 300), random_state=0)
            if pd.to_datetime(smp, errors="coerce", format="mixed").notna().mean() >= 0.9:
                out[c] = pd.to_datetime(s, errors="coerce", format="mixed")
                bad = int(out[c].isna().sum() - s.isna().sum())
                notes.append(f"'{c}': text → date (day/month order inferred)" + (f"; {bad} unparseable value(s) became missing" if bad else ""))
    return out, notes


def column_roles(df: pd.DataFrame) -> dict[str, str]:
    """date | numeric | category | id | text"""
    roles, n = {}, len(df)
    for c in df.columns:
        s = df[c]
        nu, nn = s.nunique(dropna=True), int(s.notna().sum())
        if pd.api.types.is_datetime64_any_dtype(s):
            roles[c] = "date"
        elif pd.api.types.is_bool_dtype(s):
            roles[c] = "category"
        elif pd.api.types.is_numeric_dtype(s):
            intlike = bool((s.dropna() % 1 == 0).all()) if nn else False
            roles[c] = "id" if (ID_NAME.search(c) or (intlike and nn > 20 and nu >= 0.98 * nn)) else "numeric"
        elif nu <= max(30, 0.05 * n):
            roles[c] = "category"
        else:
            roles[c] = "id" if (ID_NAME.search(c) or nu >= 0.9 * max(nn, 1)) and nu > 0 and s.astype(str).str.len().max() < 40 else "text"
    return roles


def iqr_bounds(s: pd.Series, k: float = 1.5):
    s = s.dropna()
    if len(s) < 8:
        return None
    q1, q3 = s.quantile([0.25, 0.75])
    return q1 - k * (q3 - q1), q3 + k * (q3 - q1)


def quality_report(raw: pd.DataFrame, typed: pd.DataFrame, roles: dict) -> dict:
    miss = raw.isna().sum()
    missing = pd.DataFrame({"missing": miss, "pct": (miss / max(len(raw), 1) * 100).round(1)})
    missing = missing[missing.missing > 0].sort_values("missing", ascending=False)
    rows = []
    for c, r in roles.items():
        if r == "numeric":
            b = iqr_bounds(typed[c])
            if b:
                m = int(((typed[c] < b[0]) | (typed[c] > b[1])).sum())
                if m:
                    rows.append({"column": c, "outliers": m, "lower_fence": b[0], "upper_fence": b[1]})
    return {
        "rows": len(raw), "cols": raw.shape[1],
        "duplicates": int(raw.duplicated().sum()),
        "missing": missing, "outliers": pd.DataFrame(rows),
        "complete_rows": int(raw.notna().all(axis=1).sum()),
    }


# ───────────────────────────── relationships ─────────────────────────────
def _norm(c: str) -> str:
    return re.sub(r"[^a-z0-9]", "", c.lower())


def detect_relationships(tables: dict[str, pd.DataFrame], min_overlap: float = 0.6) -> list[dict]:
    """Candidate keys: same normalised column name in two tables with strongly overlapping values."""
    res, names = [], list(tables)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for ca in tables[a].columns:
                for cb in tables[b].columns:
                    if _norm(ca) != _norm(cb):
                        continue
                    sa, sb = tables[a][ca].dropna(), tables[b][cb].dropna()
                    if sa.empty or sb.empty or any(pd.api.types.is_float_dtype(x) or pd.api.types.is_datetime64_any_dtype(x) for x in (sa, sb)):
                        continue
                    va, vb = set(sa.astype(str)), set(sb.astype(str))
                    ov = len(va & vb) / min(len(va), len(vb))
                    if ov >= min_overlap and len(va & vb) >= 2:
                        res.append({"left": a, "left_key": ca, "right": b, "right_key": cb, "overlap": round(ov, 3),
                                    "left_unique": bool(sa.is_unique), "right_unique": bool(sb.is_unique)})
    return sorted(res, key=lambda r: -r["overlap"])


def merge_tables(l: pd.DataFrame, r: pd.DataFrame, lk: str, rk: str) -> pd.DataFrame:
    """Left join; refuses many-to-many joins because they silently multiply rows."""
    if not r[rk].dropna().is_unique:
        raise ValueError(f"'{rk}' is not unique in the right table, so the join would duplicate rows. Swap the tables or pick a unique key.")
    l, r = l.copy(), r.copy()
    if l[lk].dtype != r[rk].dtype:
        l[lk], r[rk] = l[lk].astype("string"), r[rk].astype("string")
    out = l.merge(r, left_on=lk, right_on=rk, how="left", suffixes=("", "_right"))
    assert len(out) == len(l), "join changed the row count"
    return out


# ───────────────────────────── filtering ─────────────────────────────
def apply_filters(df, date=None, date_rng=None, cats=None, nums=None, extra=None) -> pd.DataFrame:
    m = pd.Series(True, index=df.index)
    if date and date_rng and len(date_rng) == 2:
        d = df[date]
        m &= d.isna() | ((d >= pd.Timestamp(date_rng[0])) & (d < pd.Timestamp(date_rng[1]) + pd.Timedelta(days=1)))
        m &= d.notna()
    for c, vals in (cats or {}).items():
        if vals:
            m &= df[c].isin(vals)
    for c, (lo, hi) in (nums or {}).items():
        m &= df[c].between(lo, hi)
    for c, vals in (extra or {}).items():
        if vals and c in df:
            m &= df[c].isin(vals)
    return df[m]


# ───────────────────────────── KPIs & insights ─────────────────────────────
def default_agg(measure: str) -> str:
    return "mean" if MEAN_NAME.search(measure) else "sum"


def auto_grain(dates: pd.Series) -> str:
    d = dates.dropna()
    span = (d.max() - d.min()).days if len(d) else 0
    return "Day" if span <= 45 else "Week" if span <= 180 else "Month" if span <= 1100 else "Quarter" if span <= 3650 else "Year"


def time_series(df, date, measure, agg="sum", grain="Month", by=None) -> pd.DataFrame:
    cols = [date, measure] + ([by] if by else [])
    d = df[cols].dropna(subset=[date])
    if d.empty:
        return pd.DataFrame(columns=["period"] + ([by] if by else []) + [measure])
    keys = [d[date].dt.to_period(FREQ[grain])] + ([d[by]] if by else [])
    out = d.groupby(keys, observed=True)[measure].agg(agg).reset_index()
    out.columns = ["period"] + ([by] if by else []) + [measure]
    out["period"] = out["period"].dt.to_timestamp()
    return out


def kpi(df, measure, agg, date=None, grain=None) -> dict:
    s = df[measure].dropna()
    res = {"value": float(getattr(s, agg)()) if len(s) else np.nan, "n": len(s), "missing": int(df[measure].isna().sum()),
           "change": None, "last": None, "prev": None, "grain": None}
    if date and df[date].notna().any():
        g = grain or auto_grain(df[date])
        ts = time_series(df, date, measure, agg, g)[measure]
        if len(ts) >= 2:
            last, prev = float(ts.iloc[-1]), float(ts.iloc[-2])
            res.update(last=last, prev=prev, grain=g, change=(last - prev) / abs(prev) if prev else None)
    return res


def insights(df, roles, measure, agg, date=None, dim=None) -> list[tuple[str, str]]:
    """(headline, calculation) pairs. Every statement is computed from the filtered data."""
    out, s = [], df[measure].dropna()
    if s.empty:
        return out
    if dim and dim in df:
        g = df.groupby(dim, observed=True)[measure].agg(agg).dropna().sort_values(ascending=False)
        if len(g) >= 2:
            if agg == "sum" and g.sum() > 0:
                top3 = g.head(3).sum() / g.sum()
                out.append((f"{g.index[0]} leads {measure} with {g.iloc[0] / g.sum():.1%} of the total",
                            f"{g.iloc[0]:,.2f} ÷ {g.sum():,.2f}. Top 3 of {len(g)} {dim} values = {top3:.1%}."))
            else:
                out.append((f"{g.index[0]} has the highest {agg} {measure} ({g.iloc[0]:,.2f}); {g.index[-1]} the lowest ({g.iloc[-1]:,.2f})",
                            f"{agg} of {measure} grouped by {dim}, {len(g)} groups."))
    if date and df[date].notna().any():
        grain = auto_grain(df[date])
        ts = time_series(df, date, measure, agg, grain).set_index("period")[measure]
        if len(ts) >= 3:
            pk = ts.idxmax()
            out.append((f"Peak {grain.lower()} for {measure}: {pk:%Y-%m-%d} ({ts.max():,.2f})",
                        f"{len(ts)} {grain.lower()} periods; first={ts.iloc[0]:,.2f}, last={ts.iloc[-1]:,.2f}. Edge periods may be partial."))
    nums = [c for c, r in roles.items() if r == "numeric" and c in df]
    pairs = []
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            both = df[[a, b]].dropna()
            if len(both) >= 20 and both[a].std() > 0 and both[b].std() > 0:
                r = both[a].corr(both[b])
                if abs(r) >= 0.7:
                    pairs.append((abs(r), a, b, r, len(both)))
    for _, a, b, r, n in sorted(pairs, reverse=True)[:3]:
        out.append((f"{a} and {b} move {'together' if r > 0 else 'in opposite directions'} (r = {r:.2f})",
                    f"Pearson correlation over {n} complete rows. Correlation does not show causation."))
    b = iqr_bounds(s)
    if b:
        m = int(((s < b[0]) | (s > b[1])).sum())
        if m:
            out.append((f"{m} outlier value(s) in {measure}", f"Outside IQR fences [{b[0]:,.2f}, {b[1]:,.2f}] (1.5 × IQR). Values are kept in all figures."))
    return out


# ───────────────────────────── demo data (synthetic, clearly labelled) ─────────────────────────────
def demo_tables(seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    n = 1500
    cust = pd.DataFrame({"customer_id": [f"C{i:03d}" for i in range(1, 121)],
                         "segment": rng.choice(["Consumer", "Corporate", "Small business"], 120, p=[.5, .3, .2]),
                         "country": rng.choice(["India", "Germany", "Brazil", "Canada"], 120)})
    dates = pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 660, n), unit="D")
    cat = rng.choice(["Furniture", "Technology", "Office supplies"], n, p=[.25, .35, .4])
    units = rng.integers(1, 12, n)
    price = np.select([cat == "Technology", cat == "Furniture"], [320, 190], 35) * rng.uniform(.8, 1.2, n)
    season = 1 + 0.25 * np.sin((dates.month.to_numpy() - 3) / 12 * 2 * np.pi) + (dates - dates.min()).days.to_numpy() / 1500
    orders = pd.DataFrame({"order_date": dates, "customer_id": rng.choice(cust.customer_id, n), "region": rng.choice(["North", "South", "East", "West"], n),
                           "category": cat, "units": units, "unit_price": price.round(2), "discount": rng.choice([0, .05, .1, .2], n)})
    orders["revenue"] = (orders.units * orders.unit_price * season * (1 - orders.discount)).round(2)
    orders.loc[rng.choice(n, 6, replace=False), "revenue"] *= 12          # planted outliers
    orders.loc[rng.choice(n, 40, replace=False), "region"] = None          # planted missing values
    orders = pd.concat([orders, orders.sample(15, random_state=1)], ignore_index=True)  # planted duplicates
    return {"Demo · orders": orders, "Demo · customers": cust}
