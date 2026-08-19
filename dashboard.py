"""
Signal Dashboard
================
Run with:  streamlit run dashboard.py
"""

import os
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

import re
import time
import numpy as np
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import gspread
from datetime import datetime, date, timedelta
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────
 
GOOGLE_SHEET_ID  = "1dprlfL3WN3ynj6TgqngvgY_a44zZpgHFm0VDrrF7zrA"
CREDENTIALS_FILE = "credentials.json"
HISTORY_FILE     = "history.csv"
REFRESH_SECONDS  = 60
 
# ── Trending indicators (momentum / trend-following) ──────────────────────────
TRENDING_INDICATORS = [
    "Bullish Swing",
    "Bottom Hourly",
    "Trending Buy",
    "Hourly Breakout",
    "Scoreboard",
    "KovaScore",
]
 
TRENDING_RULES = {
    "Bullish Swing":  [(2, 6), (5, 4), (10, 2), (20, 1)],
    "Bottom Hourly":  [(2, 6), (5, 4), (10, 2), (20, 1)],
    "Trending Buy":   [(2, 3), (5, 2), (10, 1)],
    "Hourly Breakout":[(2, 3), (5, 2), (10, 1)],
    "Scoreboard":     [(2, 3), (5, 2), (10, 1)],
    "KovaScore":      [(2, 3), (5, 2), (10, 1)],
}
 
MAX_TRENDING = sum(r[0][1] for r in TRENDING_RULES.values() if r)
 
# ── Observation indicators (no score, shown for reference) ───────────────────
OBSERVATION_INDICATORS = [
    "Volume Spike",
]
 
# ── Reversal indicators (mean reversion / bottom-finding) ─────────────────────
REVERSAL_INDICATORS = [
    "Hourly Bullish Divergence",
    "Golden Pocket",
    "Major Bottom",
    "Bottom Daily",
    "Mean Reversion",
]
 
REVERSAL_RULES = {
    "Hourly Bullish Divergence": [(2, 3), (5, 2), (10, 1)],
    "Golden Pocket":             [(2, 3), (5, 2), (10, 1)],
    "Major Bottom":              [(2, 6), (5, 4), (10, 2), (20, 1)],
    "Bottom Daily":              [(2, 6), (5, 4), (10, 2), (20, 1)],
    "Mean Reversion":            [(2, 3), (5, 2), (10, 1)],
}
 
MAX_REVERSAL = sum(r[0][1] for r in REVERSAL_RULES.values() if r)
 
# ── Combined (all indicators in display order) ────────────────────────────────
INDICATORS = TRENDING_INDICATORS + OBSERVATION_INDICATORS + REVERSAL_INDICATORS
SCORE_RULES = {**TRENDING_RULES, **{"Volume Spike": []}, **REVERSAL_RULES}
MAX_SCORE   = MAX_TRENDING + MAX_REVERSAL
 
# ── Hourly JY Score fields (from the Discord "Hourly JY Score" cards) ─────────
# These are plain columns straight from the sheet — no date-based scoring,
# just shown as-is right after Section.
JY_FIELDS = [
    "JY Score",
    "Health",
    "Momentum",
    "ATR from 20D MA",
    "Stretch Status",
    "Vol Pace vs Avg",
]
 
CHART_BG   = "#0e1117"
CHART_GRID = "#1e222d"
CHART_TEXT = "#aaaaaa"
 
# ─── PAGE SETUP ───────────────────────────────────────────────────────────────
 
st.set_page_config(page_title="Signal Dashboard", page_icon="📈", layout="wide")
st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    .block-container { padding-top: 1rem; }
    td, th { text-align: center !important; font-size: 13px !important; }
    td:first-child, td:nth-child(2) { text-align: left !important; font-weight: bold; }
</style>
""", unsafe_allow_html=True)
 
# ─── HELPERS ──────────────────────────────────────────────────────────────────
 
def days_ago(date_str: str):
    if not date_str or not date_str.strip():
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%Y %H:%M"):
        try:
            signal_date = datetime.strptime(date_str.strip(), fmt).date()
            last_close  = np.busday_offset(
                datetime.now().date(), 0, roll="backward"
            ).astype("datetime64[D]").astype(object)
            return max(0, int(np.busday_count(signal_date, last_close)))
        except ValueError:
            continue
    return None
 
 
def _score_from_rules(row, rules: dict) -> int:
    total = 0
    for ind, rule_list in rules.items():
        d = days_ago(row.get(f"{ind} Date", ""))
        if d is None:
            continue
        for max_days, pts in rule_list:
            if d <= max_days:
                total += pts
                break
    return total
 
 
def compute_trending_score(row) -> int:
    return _score_from_rules(row, TRENDING_RULES)
 
def compute_reversal_score(row) -> int:
    return _score_from_rules(row, REVERSAL_RULES)
 
def compute_score(row) -> int:
    return compute_trending_score(row) + compute_reversal_score(row)
 
 
def score_gained_today_total(row) -> int:
    total = 0
    for ind, rules in SCORE_RULES.items():
        d = days_ago(row.get(f"{ind} Date", ""))
        if d is not None and d <= 1:
            for max_days, pts in rules:
                if d <= max_days:
                    total += pts
                    break
    return total
 
 
def score_from_recent(row, rules: dict, window=3) -> int:
    total = 0
    for ind, rule_list in rules.items():
        d = days_ago(row.get(f"{ind} Date", ""))
        if d is not None and d <= window:
            for max_days, pts in rule_list:
                if d <= max_days:
                    total += pts
                    break
    return total
 
 
def signal_count(row, days_limit=None) -> int:
    return sum(
        1 for ind in INDICATORS
        if (d := days_ago(row.get(f"{ind} Date", ""))) is not None
        and (days_limit is None or d <= days_limit)
    )
 
 
def score_badge_colour(score: int, max_val: int):
    ratio = score / max_val if max_val else 0
    if ratio >= 0.75: return "#003020", "#00e676"
    if ratio >= 0.4:  return "#1a3a1a", "#4caf50"
    if ratio >= 0.15: return "#1e2a00", "#8bc34a"
    return "#1a1a1a", "#666"
 
 
def parse_leading_float(value):
    """Pulls the leading signed number out of a text field like
    '-0.7 ATRs' or '1.2x' -> -0.7 / 1.2. Returns None if nothing numeric
    is found (e.g. 'Waiting for data')."""
    if value is None:
        return None
    m = re.search(r'-?\d+\.?\d*', str(value))
    return float(m.group()) if m else None
 
 
def jy_score_colour(value):
    """Colour-codes the JY Score cell: >65 green, 35-65 orange, <35 red.
    Blank/non-numeric (no data fed in yet) stays neutral grey."""
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return {"bg": "#1a1428", "fg": "#666"}
    if v > 65:
        return {"bg": "#003020", "fg": "#00e676"}
    if v >= 35:
        return {"bg": "#3a2a00", "fg": "#ffb74d"}
    return {"bg": "#3a0000", "fg": "#ff5252"}
 
 
def cell_colour(d):
    if d is None: return {"bg": "#111111", "fg": "#333333"}
    if d <= 2:    return {"bg": "#003020", "fg": "#00e676"}
    if d <= 5:    return {"bg": "#1a3a1a", "fg": "#4caf50"}
    if d <= 10:   return {"bg": "#1e3a00", "fg": "#8bc34a"}
    return               {"bg": "#1a1a1a", "fg": "#444444"}
 
# ─── DATA LOADING ─────────────────────────────────────────────────────────────
 
def _gspread_client():
    # On Streamlit Cloud, credentials are stored as a TOML table in st.secrets
    # Locally, fall back to credentials.json file
    try:
        creds_dict = dict(st.secrets["GOOGLE_CREDENTIALS"])
        return gspread.service_account_from_dict(creds_dict)
    except (KeyError, Exception):
        return gspread.service_account(filename=CREDENTIALS_FILE)
 
 
@st.cache_data(ttl=REFRESH_SECONDS)
def load_live_data():
    gc    = _gspread_client()
    sheet = gc.open_by_key(GOOGLE_SHEET_ID).sheet1
    rows  = sheet.get_all_values()
    if len(rows) < 3:
        return pd.DataFrame()
 
    header1, header2 = rows[0], rows[1]
    columns, last_name = [], ""
    for h1, h2 in zip(header1, header2):
        h1, h2 = h1.strip(), h2.strip()
        if h1 and h2 not in ("Date", "Price"):
            columns.append(h1)
        elif h1:
            last_name = h1
            columns.append(f"{last_name} {h2}")
        else:
            columns.append(f"{last_name} {h2}")
 
    df = pd.DataFrame(rows[2:], columns=columns)
    # Ticker is always treated as text — a handful of markets (Korean
    # KOSPI codes like "005930", HK/China codes like "0700", "9988") use
    # purely numeric ticker symbols, and without forcing str here pandas
    # can silently infer that column as numeric, which is what made the
    # ticker-axis charts below render as a numeric scale instead of
    # category labels for those rows.
    df["Ticker"] = df["Ticker"].astype(str)
    return df[df["Ticker"].str.strip().ne("")]
 
 
@st.cache_data(ttl=REFRESH_SECONDS)
def load_history():
    path = Path(HISTORY_FILE)
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, on_bad_lines="skip")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df
 
 
@st.cache_data(ttl=REFRESH_SECONDS)
def load_jy_history():
    """Reads the bot's 'JY History' tab (Ticker, Timestamp, JY Score — one
    row per ticker per hourly blast). Returns empty if the tab doesn't
    exist yet or has no rows (e.g. right after this feature is deployed —
    it builds up going forward, not retroactively)."""
    try:
        gc = _gspread_client()
        ws = gc.open_by_key(GOOGLE_SHEET_ID).worksheet(JY_HISTORY_SHEET_NAME)
    except Exception:
        return pd.DataFrame()
 
    rows = ws.get_all_values()
    if len(rows) < 2:
        return pd.DataFrame()
 
    df = pd.DataFrame(rows[1:], columns=["Ticker", "Timestamp", "JY Score"])
    df["Ticker"]    = df["Ticker"].astype(str)  # same numeric-ticker fix as load_live_data
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df["JY Score"]  = pd.to_numeric(df["JY Score"], errors="coerce")
    return df.dropna(subset=["Timestamp", "JY Score"])
 
 
def jy_score_24h_delta(ticker: str, current_value, jy_history: pd.DataFrame, tolerance_hours: float = 4):
    """Change in JY Score vs. the reading closest to 24h ago for this
    ticker, or None if there's no history close enough yet to compare
    against (e.g. history tracking only just started)."""
    if jy_history is None or jy_history.empty or current_value is None or pd.isna(current_value):
        return None
    hist = jy_history[jy_history["Ticker"] == ticker]
    if hist.empty:
        return None
 
    target = datetime.now() - timedelta(hours=24)
    diffs  = (hist["Timestamp"] - target).abs()
    within = hist[diffs <= pd.Timedelta(hours=tolerance_hours)]
    if within.empty:
        return None
 
    nearest = within.loc[diffs[within.index].idxmin()]
    return current_value - nearest["JY Score"]
 
 
def get_available_dates(history: pd.DataFrame):
    if history.empty:
        return []
    return sorted(history["date"].unique().tolist(), reverse=True)
 
# ─── CHARTS ───────────────────────────────────────────────────────────────────
 
def chart_sector_treemap(df, mode="overview"):
    """
    mode='overview'  → root node visible, sectors only at top level, click to drill into tickers
    mode='detailed'  → no root node, sectors contain tickers directly (dense view)
    """
    sec_df = df[df["Section"].str.strip().ne("")].copy()
    sec_grp = (
        sec_df.groupby("Section")
        .agg(avg_score=("_score", "mean"), count=("Ticker", "count"))
        .reset_index()
    )
    score_vals = sec_df["_score"].tolist()
    cmin = min(score_vals) if score_vals else 0
    cmax = max(score_vals) if score_vals else MAX_SCORE
 
    if mode == "overview":
        # Three-level: invisible root → sectors → tickers (click to drill)
        labels  = ["root"]
        parents = [""]
        values  = [0]
        colors  = [0]
        hovers  = [""]
        for _, sr in sec_grp.iterrows():
            labels.append(sr["Section"])
            parents.append("root")
            values.append(int(sr["count"]))
            colors.append(float(sr["avg_score"]))
            hovers.append(f"<b>{sr['Section']}</b><br>Avg score: {sr['avg_score']:.1f}<br>{int(sr['count'])} tickers")
        for _, row in sec_df.iterrows():
            labels.append(row["Ticker"])
            parents.append(row["Section"])
            values.append(1)
            colors.append(float(row["_score"]))
            hovers.append(
                f"<b>{row['Ticker']}</b><br>"
                f"Total: {int(row['_score'])}  Trend: {int(row['_trending_score'])}  Rev: {int(row['_reversal_score'])}<br>"
                f"{row['Section']}"
            )
        title_text = "Sector Heatmap — click a sector to drill in"
        maxdepth   = 2
        pad        = 3
    else:
        # Two-level: sectors contain tickers directly (all visible)
        labels, parents, values, colors, hovers = [], [], [], [], []
        for _, sr in sec_grp.iterrows():
            labels.append(sr["Section"])
            parents.append("")
            values.append(int(sr["count"]))
            colors.append(float(sr["avg_score"]))
            hovers.append(f"<b>{sr['Section']}</b><br>Avg score: {sr['avg_score']:.1f}<br>{int(sr['count'])} tickers")
        for _, row in sec_df.iterrows():
            labels.append(row["Ticker"])
            parents.append(row["Section"])
            values.append(1)
            colors.append(float(row["_score"]))
            hovers.append(
                f"<b>{row['Ticker']}</b><br>"
                f"Total: {int(row['_score'])}  Trend: {int(row['_trending_score'])}  Rev: {int(row['_reversal_score'])}<br>"
                f"{row['Section']}"
            )
        title_text = "Sector Heatmap — sectors + tickers"
        maxdepth   = 2
        pad        = 2
 
    fig = go.Figure(go.Treemap(
        labels=labels,
        parents=parents,
        values=values,
        customdata=hovers,
        marker=dict(
            colors=colors,
            colorscale=[[0.0,"#8b0000"],[0.35,"#cc3300"],[0.5,"#555500"],[0.7,"#1e5a1e"],[1.0,"#00c853"]],
            cmin=cmin, cmax=cmax,
            showscale=True,
            colorbar=dict(tickfont=dict(color=CHART_TEXT, size=10), thickness=10, len=0.75),
            line=dict(width=2, color="#0e1117"),
        ),
        textfont=dict(color="#ffffff", size=11),
        hovertemplate="%{customdata}<extra></extra>",
        maxdepth=maxdepth,
        root_color="#0e1117",
        tiling=dict(packing="squarify", pad=pad),
        pathbar=dict(visible=True, side="top", thickness=22, textfont=dict(color="#ffffff", size=11)),
    ))
    fig.update_layout(
        title=dict(text=title_text, font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=10, t=50, b=10), height=600,
    )
    return fig
 
 
def chart_top_sections_stacked(df, n=10):
    """Stacked bar: trending vs reversal avg score per section."""
    grp = (
        df[df["Section"].str.strip().ne("")]
        .groupby("Section")
        .agg(trend=("_trending_score", "mean"), reversal=("_reversal_score", "mean"))
        .assign(total=lambda x: x["trend"] + x["reversal"])
        .sort_values("total", ascending=True)
        .tail(n)
    )
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Trending", x=grp["trend"], y=grp.index, orientation="h",
        marker_color="#1e88e5",
        text=[f"{v:.1f}" for v in grp["trend"]],
        textposition="inside", textfont=dict(color="#fff", size=10),
    ))
    fig.add_trace(go.Bar(
        name="Reversal", x=grp["reversal"], y=grp.index, orientation="h",
        marker_color="#00c853",
        text=[f"{v:.1f}" for v in grp["reversal"]],
        textposition="inside", textfont=dict(color="#fff", size=10),
    ))
    fig.update_layout(
        barmode="stack",
        title=dict(text=f"Top {n} Sections — Trending vs Reversal", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=50, t=40, b=10),
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False, range=[0, MAX_SCORE * 1.15]),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False),
        legend=dict(font=dict(color=CHART_TEXT), orientation="h", y=1.08),
        height=380,
    )
    return fig
 
 
def chart_top_gainers_today(df, n=10):
    tmp = df.copy()
    tmp["_gained_today"] = tmp.apply(score_gained_today_total, axis=1)
    top = tmp[tmp["_gained_today"] > 0].nlargest(n, "_gained_today")
    if top.empty:
        return None
    colours = px.colors.sample_colorscale(
        "Greens", [i / max(len(top) - 1, 1) for i in range(len(top))]
    )[::-1]
    fig = go.Figure(go.Bar(
        x=top["Ticker"], y=top["_gained_today"],
        marker_color=colours,
        text=top["_gained_today"], textposition="outside",
        textfont=dict(color=CHART_TEXT, size=11),
        customdata=top["_score"],
        hovertemplate="<b>%{x}</b><br>Gained today: %{y} pts<br>Total score: %{customdata}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Top 10 Score Gainers Today", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=10, t=40, b=10),
        # type="category" forces every ticker to render as a labeled bar,
        # never auto-converted to a numeric axis — matters for markets
        # with purely numeric ticker codes (e.g. Korean/HK/China symbols),
        # which Plotly would otherwise treat as a continuous number scale.
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False, type="category"),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False, range=[0, MAX_SCORE * 1.2], title="Pts gained"),
        showlegend=False, height=320,
    )
    return fig
 
 
def chart_top_section_gainers_today(df, n=10):
    tmp = df.copy()
    tmp["_gained_today"] = tmp.apply(score_gained_today_total, axis=1)
    grp = (
        tmp[tmp["Section"].str.strip().ne("")]
        .groupby("Section")["_gained_today"]
        .mean().sort_values(ascending=True).tail(n)
    )
    grp = grp[grp > 0]
    if grp.empty:
        return None
    colours = px.colors.sample_colorscale(
        "Greens", [i / max(len(grp) - 1, 1) for i in range(len(grp))]
    )
    fig = go.Figure(go.Bar(
        x=grp.values, y=grp.index, orientation="h",
        marker_color=colours,
        text=[f"{v:.2f}" for v in grp.values], textposition="outside",
        textfont=dict(color=CHART_TEXT, size=11),
    ))
    fig.update_layout(
        title=dict(text=f"Top {n} Section Gainers Today (avg pts)", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=50, t=40, b=10),
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False),
        showlegend=False, height=320,
    )
    return fig
 
 
def chart_top_trending(df, n=10):
    top = df.nlargest(n, "_trending_score")
    top = top[top["_trending_score"] > 0]
    if top.empty:
        return None
    colours = px.colors.sample_colorscale(
        "Blues", [i / max(len(top) - 1, 1) for i in range(len(top))]
    )[::-1]
    fig = go.Figure(go.Bar(
        x=top["Ticker"], y=top["_trending_score"],
        marker_color=colours,
        text=top["_trending_score"], textposition="outside",
        textfont=dict(color=CHART_TEXT, size=11),
        customdata=top["Section"],
        hovertemplate="<b>%{x}</b><br>Trending Score: %{y}<br>Section: %{customdata}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Top 10 Trending", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False, type="category"),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False, range=[0, MAX_TRENDING * 1.2], title="Trending Score"),
        showlegend=False, height=320,
    )
    return fig
 
 
def chart_potential_reversals(df, n=10):
    tmp = df.copy()
    tmp["_recent_reversal"] = tmp.apply(
        lambda r: score_from_recent(r, REVERSAL_RULES, window=3), axis=1
    )
    tmp = tmp[
        (tmp["_reversal_score"] >= 2) &
        (tmp["_recent_reversal"] == tmp["_reversal_score"])
    ].nlargest(n, "_reversal_score")
    if tmp.empty:
        return None
    colours = px.colors.sample_colorscale(
        [[0, "#1a3a4a"], [0.5, "#0288d1"], [1, "#00e5ff"]],
        [i / max(len(tmp) - 1, 1) for i in range(len(tmp))],
    )[::-1]
    fig = go.Figure(go.Bar(
        x=tmp["Ticker"], y=tmp["_reversal_score"],
        marker_color=colours,
        text=tmp["_reversal_score"], textposition="outside",
        textfont=dict(color=CHART_TEXT, size=11),
        customdata=tmp["Section"],
        hovertemplate="<b>%{x}</b><br>Reversal Score: %{y}<br>Section: %{customdata}<br>All from last 3 trading days<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Top 10 Potential Reversals (reversal score, last 3td)", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False, type="category"),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False, range=[0, MAX_REVERSAL * 1.2], title="Reversal Score"),
        showlegend=False, height=320,
    )
    return fig
 
# ── JY Score charts (mirrors the point-system charts above, same layout) ──────
 
JY_COLORSCALE = [[0.0, "#8b0000"], [0.35, "#cc3300"], [0.5, "#8a6d00"], [0.7, "#1e5a1e"], [1.0, "#00c853"]]
 
 
def chart_jy_sector_treemap(df, mode="overview"):
    """Same heatmap as chart_sector_treemap, coloured by JY Score instead
    of the point-system total. Fixed 0-100 colour range (not min/max of the
    data) so colour always reflects the same green/orange/red health bands
    used in the table, not a relative comparison."""
    sec_df = df[(df["Section"].str.strip().ne("")) & df["_jy_score_num"].notna()].copy()
    if sec_df.empty:
        return None
    sec_grp = (
        sec_df.groupby("Section")
        .agg(avg_score=("_jy_score_num", "mean"), count=("Ticker", "count"))
        .reset_index()
    )
 
    if mode == "overview":
        labels, parents, values, colors, hovers = ["root"], [""], [0], [0], [""]
        for _, sr in sec_grp.iterrows():
            labels.append(sr["Section"]); parents.append("root")
            values.append(int(sr["count"])); colors.append(float(sr["avg_score"]))
            hovers.append(f"<b>{sr['Section']}</b><br>Avg JY Score: {sr['avg_score']:.1f}<br>{int(sr['count'])} tickers")
        for _, row in sec_df.iterrows():
            labels.append(row["Ticker"]); parents.append(row["Section"])
            values.append(1); colors.append(float(row["_jy_score_num"]))
            hovers.append(f"<b>{row['Ticker']}</b><br>JY Score: {int(row['_jy_score_num'])}<br>{row['Section']}")
        title_text = "JY Score Heatmap — click a sector to drill in"
        maxdepth, pad = 2, 3
    else:
        labels, parents, values, colors, hovers = [], [], [], [], []
        for _, sr in sec_grp.iterrows():
            labels.append(sr["Section"]); parents.append("")
            values.append(int(sr["count"])); colors.append(float(sr["avg_score"]))
            hovers.append(f"<b>{sr['Section']}</b><br>Avg JY Score: {sr['avg_score']:.1f}<br>{int(sr['count'])} tickers")
        for _, row in sec_df.iterrows():
            labels.append(row["Ticker"]); parents.append(row["Section"])
            values.append(1); colors.append(float(row["_jy_score_num"]))
            hovers.append(f"<b>{row['Ticker']}</b><br>JY Score: {int(row['_jy_score_num'])}<br>{row['Section']}")
        title_text = "JY Score Heatmap — sectors + tickers"
        maxdepth, pad = 2, 2
 
    fig = go.Figure(go.Treemap(
        labels=labels, parents=parents, values=values, customdata=hovers,
        marker=dict(
            colors=colors, colorscale=JY_COLORSCALE, cmin=0, cmax=100,
            showscale=True,
            colorbar=dict(tickfont=dict(color=CHART_TEXT, size=10), thickness=10, len=0.75),
            line=dict(width=2, color="#0e1117"),
        ),
        textfont=dict(color="#ffffff", size=11),
        hovertemplate="%{customdata}<extra></extra>",
        maxdepth=maxdepth, root_color="#0e1117",
        tiling=dict(packing="squarify", pad=pad),
        pathbar=dict(visible=True, side="top", thickness=22, textfont=dict(color="#ffffff", size=11)),
    ))
    fig.update_layout(
        title=dict(text=title_text, font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=10, t=50, b=10), height=600,
    )
    return fig
 
 
def chart_jy_top_sections(df, n=10):
    """Top n sections by average JY Score."""
    grp = (
        df[(df["Section"].str.strip().ne("")) & df["_jy_score_num"].notna()]
        .groupby("Section")["_jy_score_num"]
        .mean().sort_values(ascending=True).tail(n)
    )
    if grp.empty:
        return None
    colours = px.colors.sample_colorscale(
        [[0, "#4a1a1a"], [0.35, "#8a6d00"], [1, "#00c853"]],
        [min(v / 100, 1.0) for v in grp.values],
    )
    fig = go.Figure(go.Bar(
        x=grp.values, y=grp.index, orientation="h",
        marker_color=colours,
        text=[f"{v:.1f}" for v in grp.values], textposition="outside",
        textfont=dict(color=CHART_TEXT, size=11),
    ))
    fig.update_layout(
        title=dict(text=f"Top {n} Sections — Avg JY Score", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=50, t=40, b=10),
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False, range=[0, 105]),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False),
        showlegend=False, height=380,
    )
    return fig
 
 
def chart_jy_top_section_gainers(df, n=10):
    """Top n sections by average JY Score change vs ~24h ago."""
    grp = (
        df[(df["Section"].str.strip().ne("")) & df["_jy_delta"].notna()]
        .groupby("Section")["_jy_delta"]
        .mean().sort_values(ascending=True).tail(n)
    )
    grp = grp[grp > 0]
    if grp.empty:
        return None
    colours = px.colors.sample_colorscale(
        "Greens", [i / max(len(grp) - 1, 1) for i in range(len(grp))]
    )
    fig = go.Figure(go.Bar(
        x=grp.values, y=grp.index, orientation="h",
        marker_color=colours,
        text=[f"+{v:.1f}" for v in grp.values], textposition="outside",
        textfont=dict(color=CHART_TEXT, size=11),
    ))
    fig.update_layout(
        title=dict(text=f"Top {n} Section Gainers Today — JY Score (avg vs 24h ago)", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=50, t=40, b=10),
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False),
        showlegend=False, height=380,
    )
    return fig
 
 
def chart_jy_top_gainers(df, n=10):
    """Top n tickers by JY Score change vs ~24h ago."""
    top = df[df["_jy_delta"].notna() & (df["_jy_delta"] > 0)].nlargest(n, "_jy_delta")
    if top.empty:
        return None
    colours = px.colors.sample_colorscale(
        "Greens", [i / max(len(top) - 1, 1) for i in range(len(top))]
    )[::-1]
    fig = go.Figure(go.Bar(
        x=top["Ticker"], y=top["_jy_delta"],
        marker_color=colours,
        text=[f"+{v:.0f}" for v in top["_jy_delta"]], textposition="outside",
        textfont=dict(color=CHART_TEXT, size=11),
        customdata=top["_jy_score_num"],
        hovertemplate="<b>%{x}</b><br>Change vs 24h ago: +%{y:.0f}<br>Current JY Score: %{customdata}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Top 10 JY Score Gainers (vs 24h ago)", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=10, t=40, b=10),
        # See chart_top_gainers_today for why type="category" matters —
        # this is the chart that was rendering "2k, 4k, 6k..." tick
        # labels instead of ticker names before this fix.
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False, type="category"),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False, title="Δ vs 24h ago"),
        showlegend=False, height=320,
    )
    return fig
 
 
def chart_jy_top_stretched(df, n=10):
    """Top n tickers by 'ATR from 20D MA' — highest positive value first
    (most stretched above the 20D moving average)."""
    top = df[df["_atr_20d_num"].notna()].nlargest(n, "_atr_20d_num")
    top = top[top["_atr_20d_num"] > 0]
    if top.empty:
        return None
    colours = px.colors.sample_colorscale(
        [[0, "#4a1a4a"], [1, "#c9a6ff"]],
        [i / max(len(top) - 1, 1) for i in range(len(top))],
    )[::-1]
    fig = go.Figure(go.Bar(
        x=top["Ticker"], y=top["_atr_20d_num"],
        marker_color=colours,
        text=[f"{v:.2f}" for v in top["_atr_20d_num"]], textposition="outside",
        textfont=dict(color=CHART_TEXT, size=11),
        customdata=top["Section"],
        hovertemplate="<b>%{x}</b><br>ATR from 20D MA: %{y:.2f}<br>Section: %{customdata}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Top 10 Stretched from 20D MA", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False, type="category"),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False, title="ATRs from 20D MA"),
        showlegend=False, height=320,
    )
    return fig
 
 
def chart_historical_section_avg(hist_df, sections=None):
    grp = (
        hist_df[hist_df["section"].str.strip().ne("")]
        .groupby(["date", "section"])["score"]
        .mean().reset_index()
    )
    if sections is not None:
        grp = grp[grp["section"].isin(sections)]
    fig = go.Figure()
    for sec in sorted(grp["section"].unique()):
        s = grp[grp["section"] == sec].sort_values("date")
        fig.add_trace(go.Scatter(
            x=s["date"], y=s["score"], mode="lines+markers", name=sec,
            hovertemplate=f"<b>{sec}</b><br>%{{x}}<br>Avg: %{{y:.1f}}<extra></extra>",
        ))
    fig.update_layout(
        title=dict(text="Section Avg Score Over Time", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=False, range=[0, MAX_SCORE]),
        legend=dict(font=dict(color=CHART_TEXT, size=10)),
        height=380,
    )
    return fig
 
 
def chart_score_change(hist_df, selected_date, compare_date):
    day_a  = hist_df[hist_df["date"] == compare_date][["ticker", "section", "score"]].rename(columns={"score": "score_prev"})
    day_b  = hist_df[hist_df["date"] == selected_date][["ticker", "score"]].rename(columns={"score": "score_now"})
    merged = day_a.merge(day_b, on="ticker")
    merged["change"] = merged["score_now"] - merged["score_prev"]
    merged = merged[merged["change"] != 0].sort_values("change", ascending=False)
 
    top_gainers = merged.head(10)
    top_losers  = merged.tail(10).sort_values("change")
 
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Gainers", x=top_gainers["ticker"], y=top_gainers["change"],
        marker_color="#00c853",
        text=top_gainers["change"].apply(lambda x: f"+{x}"),
        textposition="outside", textfont=dict(color=CHART_TEXT, size=10),
    ))
    fig.add_trace(go.Bar(
        name="Losers", x=top_losers["ticker"], y=top_losers["change"],
        marker_color="#cc3300",
        text=top_losers["change"].apply(str),
        textposition="outside", textfont=dict(color=CHART_TEXT, size=10),
    ))
    fig.update_layout(
        title=dict(text=f"Score Change vs {compare_date}", font=dict(color=CHART_TEXT, size=13)),
        paper_bgcolor=CHART_BG, plot_bgcolor=CHART_GRID, font=dict(color=CHART_TEXT),
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(gridcolor="#2a2a2a", zeroline=False, type="category"),
        yaxis=dict(gridcolor="#2a2a2a", zeroline=True, zerolinecolor="#444"),
        barmode="group", legend=dict(font=dict(color=CHART_TEXT)),
        height=340,
    )
    return fig
 
# ─── TABLE ────────────────────────────────────────────────────────────────────
 
def score_cell(score, max_val, label=""):
    bg, fg = score_badge_colour(score, max_val)
    return (
        f'<td style="padding:5px 6px; background:{bg}; color:{fg}; '
        f'font-weight:bold; font-size:12px; white-space:nowrap;">'
        f'{score}<span style="color:#555;font-size:9px;">/{max_val}</span>'
        f'{"<br>" if label else ""}'
        f'<span style="font-size:9px;color:#666;">{label}</span></td>'
    )
 
 
def group_header(label, colspan, colour):
    return f'<th colspan="{colspan}" style="padding:4px; background:{colour}; color:#ccc; font-size:11px; letter-spacing:1px;">{label}</th>'
 
 
def build_html_table(df: pd.DataFrame) -> str:
    html = ['<table style="width:100%; border-collapse:collapse;">']
 
    # Row 1: group headers
    html.append('<thead>')
    html.append('<tr>')
    html.append('<th rowspan="2" style="padding:6px 10px; text-align:left; background:#1e222d; color:#aaa;">Ticker</th>')
    html.append('<th rowspan="2" style="padding:6px 10px; text-align:left; background:#1e222d; color:#aaa;">Section</th>')
    for field in JY_FIELDS:
        html.append(f'<th rowspan="2" style="padding:6px 6px; background:#2a1e3a; color:#c9a6ff; font-size:11px;">{field}</th>')
    html.append('<th rowspan="2" style="padding:6px 6px; background:#1e222d; color:#aaa;">Trend</th>')
    html.append('<th rowspan="2" style="padding:6px 6px; background:#1e222d; color:#aaa;">Rev</th>')
    html.append('<th rowspan="2" style="padding:6px 6px; background:#1e222d; color:#aaa;">Total</th>')
    html.append(group_header("— TRENDING —", len(TRENDING_INDICATORS) * 2, "#0d2a45"))
    html.append(group_header("OBS", len(OBSERVATION_INDICATORS) * 2, "#1a1a2a"))
    html.append(group_header("— REVERSAL —", len(REVERSAL_INDICATORS) * 2, "#0d3020"))
    html.append('</tr>')
 
    # Row 2: indicator names
    html.append('<tr style="background:#1e222d; color:#aaa;">')
    for ind in INDICATORS:
        html.append(
            f'<th colspan="2" style="padding:4px 3px; font-size:11px;">{ind}'
            f'<br><span style="font-size:9px;color:#555">Date · Price</span></th>'
        )
    html.append('</tr>')
    html.append('</thead><tbody>')
 
    for _, row in df.iterrows():
        ticker  = row.get("Ticker", "")
        section = row.get("Section", "")
        if not ticker:
            continue
 
        t_score = int(row.get("_trending_score", 0))
        r_score = int(row.get("_reversal_score",  0))
        total   = t_score + r_score
 
        html.append('<tr style="border-bottom:1px solid #1a1a1a;">')
        html.append(f'<td style="padding:5px 10px; color:#e0e0e0;">{ticker}</td>')
        html.append(f'<td style="padding:5px 8px; color:#888; font-size:11px;">{section}</td>')
        for field in JY_FIELDS:
            val = row.get(field, "") or "—"
            if field == "JY Score":
                c = jy_score_colour(row.get(field, ""))
                delta = row.get("_jy_delta")
                if delta is not None and pd.notna(delta):
                    delta_num  = round(float(delta))
                    delta_sign = "+" if delta_num >= 0 else ""
                    delta_fg   = "#00e676" if delta_num > 0 else ("#ff5252" if delta_num < 0 else "#888")
                    delta_html = f' <span style="color:{delta_fg}; font-weight:normal; font-size:10px;">({delta_sign}{delta_num})</span>'
                else:
                    delta_html = ''
                html.append(
                    f'<td style="padding:5px 6px; background:{c["bg"]}; color:{c["fg"]}; '
                    f'font-weight:bold; font-size:12px; white-space:nowrap;">{val}{delta_html}</td>'
                )
            else:
                html.append(f'<td style="padding:5px 6px; background:#1a1428; color:#d8c7f2; font-size:11px; white-space:nowrap;">{val}</td>')
        html.append(score_cell(t_score, MAX_TRENDING))
        html.append(score_cell(r_score, MAX_REVERSAL))
        # Total
        tbg, tfg = score_badge_colour(total, MAX_SCORE)
        html.append(
            f'<td style="padding:5px 6px; background:{tbg}; color:{tfg}; '
            f'font-weight:bold; font-size:13px;">'
            f'{total}<span style="color:#555;font-size:9px;">/{MAX_SCORE}</span></td>'
        )
 
        for ind in INDICATORS:
            date_val  = row.get(f"{ind} Date",  "")
            price_val = row.get(f"{ind} Price", "")
            d = days_ago(date_val)
            c = cell_colour(d)
            # date_val is "YYYY-MM-DD HH:MM" for most indicators (date-only
            # for a few legacy rows or the JY Score channel) — show it as-is
            # so the time comes through, don't truncate to just the date.
            short_date = date_val.strip() if date_val and date_val.strip() else "—"
            price_disp = f"${price_val}" if price_val else "—"
 
            # Observation column gets slightly different bg tint
            if ind in OBSERVATION_INDICATORS:
                c["bg"] = c["bg"] if d is not None else "#0d0d1a"
 
            html.append(
                f'<td colspan="2" style="padding:4px; background:{c["bg"]}; color:{c["fg"]}; font-size:11px;">'
                f'{short_date}<br><span style="font-size:10px;">{price_disp}</span></td>'
            )
        html.append('</tr>')
 
    html.append('</tbody></table>')
    return "".join(html)
 
 
def build_historical_table(hist_day: pd.DataFrame, compare_day: pd.DataFrame = None) -> str:
    if compare_day is not None and not compare_day.empty:
        merged = hist_day.merge(
            compare_day[["ticker", "score"]].rename(columns={"score": "prev_score"}),
            on="ticker", how="left"
        )
        merged["change"] = (merged["score"] - merged["prev_score"].fillna(0)).astype(int)
    else:
        merged = hist_day.copy()
        merged["change"] = None
 
    merged = merged.sort_values("score", ascending=False)
 
    html = ['<table style="width:100%; border-collapse:collapse;">']
    html.append('<thead><tr style="background:#1e222d; color:#aaa;">')
    html.append('<th style="padding:6px 10px; text-align:left;">Ticker</th>')
    html.append('<th style="padding:6px 10px; text-align:left;">Section</th>')
    html.append('<th style="padding:6px 8px;">Score</th>')
    if compare_day is not None:
        html.append('<th style="padding:6px 8px;">Change</th>')
    for ind in SCORE_RULES:
        if SCORE_RULES[ind]:
            html.append(f'<th style="padding:6px 4px;">{ind}</th>')
    html.append('</tr></thead><tbody>')
 
    for _, row in merged.iterrows():
        ticker  = row.get("ticker", "")
        section = row.get("section", "")
        score   = int(row.get("score", 0))
        sbg, sfg = score_badge_colour(score, MAX_SCORE)
 
        html.append('<tr style="border-bottom:1px solid #1a1a1a;">')
        html.append(f'<td style="padding:5px 10px; color:#e0e0e0;">{ticker}</td>')
        html.append(f'<td style="padding:5px 10px; color:#888; font-size:11px;">{section}</td>')
        html.append(
            f'<td style="padding:5px; background:{sbg}; color:{sfg}; font-weight:bold; font-size:13px;">'
            f'{score}<span style="color:#555;font-size:10px;">/{MAX_SCORE}</span></td>'
        )
        if compare_day is not None:
            chg = row.get("change")
            if chg is None or chg != chg:
                chg_str, chg_col = "—", "#555"
            elif chg > 0:
                chg_str, chg_col = f"+{int(chg)}", "#00e676"
            elif chg < 0:
                chg_str, chg_col = str(int(chg)), "#cc3300"
            else:
                chg_str, chg_col = "0", "#555"
            html.append(f'<td style="padding:5px; color:{chg_col}; font-weight:bold;">{chg_str}</td>')
 
        for ind in SCORE_RULES:
            if not SCORE_RULES[ind]:
                continue
            pts = int(row.get(ind, 0))
            bg, fg = ("#1a3a1a", "#4caf50") if pts > 0 else ("#111", "#333")
            html.append(f'<td style="padding:4px; background:{bg}; color:{fg}; font-size:12px;">{pts if pts else "—"}</td>')
 
        html.append('</tr>')
 
    html.append('</tbody></table>')
    return "".join(html)
 
# ─── MAIN ─────────────────────────────────────────────────────────────────────
 
history = load_history()
available_dates = get_available_dates(history)
 
# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Controls")
 
    view_mode = st.radio("View", ["📡 Live", "📅 Historical", "🔔 Change Log", "📖 Definitions"], horizontal=False)
 
    treemap_mode_label = st.radio(
        "Heatmap style",
        ["Overview (sectors only)", "Detailed (sectors + tickers)"],
        index=0,
    )
    treemap_mode = "overview" if treemap_mode_label.startswith("Overview") else "detailed"
    st.markdown("---")
 
    if view_mode == "📅 Historical":
        if not available_dates:
            st.warning("No snapshots yet. Run snapshot.py first.")
            selected_date, compare_date = None, None
        else:
            selected_date = st.selectbox(
                "Select date", available_dates,
                format_func=lambda d: d.strftime("%A, %d %b %Y"),
            )
            compare_options = [d for d in available_dates if d < selected_date]
            compare_date = st.selectbox(
                "Compare against",
                [None] + compare_options,
                format_func=lambda d: "None" if d is None else d.strftime("%d %b %Y"),
            ) if compare_options else None
 
        section_filter = []; freshness_days = None; min_score = 0
        min_trending = 0; min_reversal = 0; min_signals = 1
        must_have = []; ticker_filter = []; sort_by = "Score (high→low)"
 
    elif view_mode in ("🔔 Change Log", "📖 Definitions"):
        selected_date = compare_date = None
        section_filter = []; freshness_days = None; min_score = 0
        min_trending = 0; min_reversal = 0; min_signals = 1
        must_have = []; ticker_filter = []; sort_by = "Total score (high→low)"
 
    else:
        selected_date = compare_date = None
 
        freshness_label = st.selectbox(
            "Show signals triggered within",
            ["All time", "Today only", "Last 1 trading day", "Last 3 trading days",
             "Last 5 trading days", "Last 90 days"], index=0,
        )
        freshness_days = {
            "All time": None, "Today only": 0, "Last 1 trading day": 1,
            "Last 3 trading days": 3, "Last 5 trading days": 5, "Last 90 days": 90,
        }[freshness_label]
 
        st.markdown("---")
        st.markdown("**Score filters**")
        min_trending = st.slider(f"Min Trending score (max {MAX_TRENDING})", 0, MAX_TRENDING, 0)
        min_reversal = st.slider(f"Min Reversal score (max {MAX_REVERSAL})", 0, MAX_REVERSAL, 0)
        min_score    = st.slider(f"Min Total score (max {MAX_SCORE})",    0, MAX_SCORE,    0)
        min_signals  = st.slider("Min signals", 1, len(INDICATORS), 1)
 
        st.markdown("---")
        must_have     = st.multiselect("Must have signal in", INDICATORS, placeholder="Any")
        section_filter = []
        ticker_filter  = []
        sort_by = st.selectbox("Sort by", [
            "Total score (high→low)", "JY Score (high→low)", "Trending score (high→low)",
            "Reversal score (high→low)", "Signals (high→low)", "Ticker (A→Z)"
        ])
 
    st.markdown("---")
    st.markdown(f"🔄 Auto-refreshes every **{REFRESH_SECONDS}s**")
    if st.button("🔄 Refresh now"):
        st.cache_data.clear()
        st.rerun()
 
# ── Header ────────────────────────────────────────────────────────────────────
st.title("📈 Signal Dashboard")
last_close = np.busday_offset(datetime.now().date(), 0, roll="backward")
last_close_str = last_close.astype("datetime64[D]").astype(object).strftime("%A %d %b %Y")
st.caption(f"Last loaded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Last trading day: **{last_close_str}**")
 
# ══════════════════════════════════════════════════════════════════════════════
# HISTORICAL VIEW
# ══════════════════════════════════════════════════════════════════════════════
 
if view_mode == "📅 Historical":
    if history.empty or selected_date is None:
        st.warning("No snapshot data available. Run `python snapshot.py` first.")
        st.stop()
 
    hist_day    = history[history["date"] == selected_date].copy()
    compare_day = history[history["date"] == compare_date].copy() if compare_date else pd.DataFrame()
 
    st.subheader(f"📅 Snapshot — {selected_date.strftime('%A, %d %b %Y')}")
 
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tickers", len(hist_day))
    c2.metric("Avg score", f"{hist_day['score'].mean():.1f}")
    c3.metric("Top ticker", hist_day.loc[hist_day["score"].idxmax(), "ticker"] if not hist_day.empty else "—")
    c4.metric("Top score",  int(hist_day["score"].max()) if not hist_day.empty else 0)
 
    st.markdown("---")
    if len(available_dates) > 1:
        st.markdown("**Section Avg Score Over Time**")
 
        # Section selector with Select All / Clear All
        all_hist_sections = sorted(history["section"].dropna().unique().tolist())
        all_hist_sections = [s for s in all_hist_sections if s.strip()]
 
        b1, b2, _ = st.columns([1, 1, 6])
        if b1.button("✅ Select All", key="hist_sel_all"):
            st.session_state["hist_selected_sections"] = all_hist_sections
            for _s in all_hist_sections:
                st.session_state[f"hist_cb_{_s}"] = True
        if b2.button("🗑 Clear All", key="hist_clear_all"):
            st.session_state["hist_selected_sections"] = []
            for _s in all_hist_sections:
                st.session_state[f"hist_cb_{_s}"] = False
 
        # Checkboxes — 4 per row
        if "hist_selected_sections" not in st.session_state:
            st.session_state["hist_selected_sections"] = all_hist_sections
 
        checked = list(st.session_state["hist_selected_sections"])
        rows = [all_hist_sections[i:i+4] for i in range(0, len(all_hist_sections), 4)]
        for row_secs in rows:
            cols = st.columns(4)
            for col, sec in zip(cols, row_secs):
                val = col.checkbox(sec, value=(sec in checked), key=f"hist_cb_{sec}")
                if val and sec not in checked:
                    checked.append(sec)
                elif not val and sec in checked:
                    checked.remove(sec)
 
        st.session_state["hist_selected_sections"] = checked
 
        col_l, col_r = st.columns([2, 1])
        with col_l:
            if checked:
                st.plotly_chart(
                    chart_historical_section_avg(history, sections=checked),
                    use_container_width=True,
                )
            else:
                st.info("No sections selected. Tick some boxes above to see the chart.")
        with col_r:
            if compare_date:
                st.plotly_chart(chart_score_change(history, selected_date, compare_date), use_container_width=True)
            else:
                st.info("Select a comparison date to see score changes.")
    else:
        st.info("Collect more snapshots over time to see trend charts.")
 
    st.markdown("---")
    st.subheader("📋 Scores on this date")
    st.markdown(build_historical_table(hist_day, compare_day if not compare_day.empty else None), unsafe_allow_html=True)
    st.stop()
 
# ══════════════════════════════════════════════════════════════════════════════
# CHANGE LOG VIEW
# ══════════════════════════════════════════════════════════════════════════════
 
if view_mode == "🔔 Change Log":
    st.title("🔔 Change Log")
    st.caption(f"Showing recent signals as of {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    st.markdown("---")
 
    with st.spinner("Loading data..."):
        df_cl = load_live_data()
 
    if df_cl.empty:
        st.error("No data found.")
        st.stop()
 
    df_cl["_trending_score"] = df_cl.apply(compute_trending_score, axis=1)
    df_cl["_reversal_score"]  = df_cl.apply(compute_reversal_score,  axis=1)
    df_cl["_score"]           = df_cl["_trending_score"] + df_cl["_reversal_score"]
 
    WATCH_INDICATORS = ["Bullish Swing", "Bottom Hourly", "Bottom Daily", "Major Bottom"]
 
    for ind in WATCH_INDICATORS:
        st.subheader(f"📌 {ind}")
        date_col = f"{ind} Date"
        price_col = f"{ind} Price"
        if date_col not in df_cl.columns:
            st.info("No data for this indicator.")
            continue
 
        recent = df_cl.copy()
        recent["_days"] = recent[date_col].apply(days_ago)
        recent = recent[recent["_days"].notna() & (recent["_days"] <= 5)].copy()
        recent = recent.sort_values("_days")
 
        if recent.empty:
            st.info(f"No signals in the last 5 trading days.")
        else:
            rows = []
            for _, r in recent.iterrows():
                rows.append({
                    "Ticker":   r["Ticker"],
                    "Section":  r.get("Section", ""),
                    "Date":     r[date_col].strip() if r[date_col] and r[date_col].strip() else "—",
                    "Price":    f"${r[price_col]}" if r.get(price_col) else "—",
                    "Days Ago": int(r["_days"]),
                    "Total Score": int(r["_score"]),
                })
            st.dataframe(pd.DataFrame(rows).set_index("Ticker"), use_container_width=True)
        st.markdown("---")
 
    st.subheader("🏆 Tickers that crossed above 15 points today")
    df_cl["_gained_today"] = df_cl.apply(score_gained_today_total, axis=1)
    high_scorers = df_cl[
        (df_cl["_score"] >= 15) & (df_cl["_gained_today"] > 0)
    ].sort_values("_score", ascending=False)
    if high_scorers.empty:
        st.info("No tickers moved above 15 points today.")
    else:
        display = high_scorers[["Ticker", "Section", "_gained_today", "_trending_score", "_reversal_score", "_score"]].rename(columns={
            "_gained_today": "Gained Today", "_trending_score": "Trend", "_reversal_score": "Reversal", "_score": "Total"
        }).set_index("Ticker")
        st.dataframe(display, use_container_width=True)
 
    st.stop()
 
# ══════════════════════════════════════════════════════════════════════════════
# DEFINITIONS VIEW
# ══════════════════════════════════════════════════════════════════════════════
 
if view_mode == "📖 Definitions":
    st.title("📖 Indicator Definitions")
    st.caption("What each signal means, what timeframe it runs on, and how it scores.")
    st.markdown("---")
 
    DEFS = [
        {
            "name": "🔵 Bullish Swing",
            "category": "Trending",
            "scoring": "≤2td: 6pts | ≤5td: 4pts | ≤10td: 2pts | ≤20td: 1pt",
            "description": (
                "Fires on the **5-minute** chart when the 140-period MA crosses above the 625-period MA, "
                "signalling that short-term momentum has turned bullish on the intraday trend. "
                "One of the two highest-weighted trending signals due to its reliability as a momentum confirmation."
            ),
        },
        {
            "name": "🔵 Bottom Hourly",
            "category": "Trending",
            "scoring": "≤2td: 6pts | ≤5td: 4pts | ≤10td: 2pts | ≤20td: 1pt",
            "description": (
                "A buy signal on the **hourly** chart that combines a Supertrend flip (from bearish to bullish) "
                "with a new-low condition and an RSI oversold filter — it fires when price has been making new lows "
                "but momentum is starting to reverse upward. One of the two highest-weighted trending signals."
            ),
        },
        {
            "name": "🔵 Trending Buy",
            "category": "Trending",
            "scoring": "≤2td: 3pts | ≤5td: 2pts | ≤10td: 1pt",
            "description": (
                "The same reversal algorithm as Bottom Hourly but applied to the **10-minute** timeframe, "
                "providing an earlier, more sensitive confirmation that a short-term bottom may be forming."
            ),
        },
        {
            "name": "🔵 Hourly Breakout",
            "category": "Trending",
            "scoring": "≤2td: 3pts | ≤5td: 2pts | ≤10td: 1pt",
            "description": (
                "Fires on the **hourly** chart when the bar-count since the last price bottom crosses above "
                "the bar-count since the last price top — indicating that a new bullish structure is taking over "
                "from the prior bearish one. The background turns blue while bullish."
            ),
        },
        {
            "name": "🔵 Scoreboard",
            "category": "Trending",
            "scoring": "≤2td: 3pts | ≤5td: 2pts | ≤10td: 1pt",
            "description": (
                "A composite dashboard score (out of 6) that checks: 140MA > 625MA on the 5-minute chart, "
                "a recent hourly buy signal, a recent 5-minute buy signal, price above the 20D SMA, "
                "price above the 50D SMA, and a volume spike at the open. "
                "Alert fires when the score reaches **4 or above**."
            ),
        },
        {
            "name": "🔵 KovaScore",
            "category": "Trending",
            "scoring": "≤2td: 3pts | ≤5td: 2pts | ≤10td: 1pt",
            "description": (
                "A proprietary stock health score (1–99) combining relative strength vs 63-day return, "
                "EMA stack alignment (10/20/50/200), distance from the 52-week high, volume trend, "
                "and price position vs the 200 SMA. Alert fires when the score crosses **75 or above**."
            ),
        },
        {
            "name": "⚪ Volume Spike",
            "category": "Observation",
            "scoring": "Observation only — no score",
            "description": (
                "Detects when the first 30 minutes of market open volume exceeds **1.5× the average** "
                "of the same opening window over the prior 7 trading days. "
                "Used as a confirmation signal rather than a standalone buy signal — no score is awarded."
            ),
        },
        {
            "name": "🟢 Hourly Bullish Divergence",
            "category": "Reversal",
            "scoring": "≤2td: 3pts | ≤5td: 2pts | ≤10td: 1pt",
            "description": (
                "Fires on the **hourly** chart when price makes a lower low but the RSI makes a higher low — "
                "a classic bullish divergence pattern indicating weakening selling pressure and a potential "
                "trend reversal to the upside."
            ),
        },
        {
            "name": "🟢 Golden Pocket",
            "category": "Reversal",
            "scoring": "≤2td: 3pts | ≤5td: 2pts | ≤10td: 1pt",
            "description": (
                "Fires when price on the **hourly** chart enters the 0.618–0.786 Fibonacci retracement zone "
                "calculated from the swing high and low of the last 500 candles. "
                "This zone is considered a high-probability support area for a bounce after a pullback."
            ),
        },
        {
            "name": "🟢 Major Bottom",
            "category": "Reversal",
            "scoring": "≤2td: 6pts | ≤5td: 4pts | ≤10td: 2pts | ≤20td: 1pt",
            "description": (
                "A multi-condition **daily** reversal finder that first requires three hard filters (price below "
                "200D SMA, in a downtrend >30 days, no new 30-day high), then scores accumulation signals "
                "including no new lows, RSI divergence, up-volume dominance, OBV trending up, a 4H reversal, "
                "and EMA-20 flattening. Alert fires when the composite score reaches the threshold. "
                "One of the two highest-weighted reversal signals."
            ),
        },
        {
            "name": "🟢 Bottom Daily",
            "category": "Reversal",
            "scoring": "≤2td: 6pts | ≤5td: 4pts | ≤10td: 2pts | ≤20td: 1pt",
            "description": (
                "The same Supertrend + RSI filter reversal algorithm used by Bottom Hourly, applied to the "
                "**daily** timeframe. Fires when the daily Supertrend flips bullish after a period of new lows "
                "with RSI recently oversold — a higher-timeframe confirmation of a potential base. "
                "One of the two highest-weighted reversal signals."
            ),
        },
        {
            "name": "🟢 Mean Reversion",
            "category": "Reversal",
            "scoring": "≤2td: 3pts | ≤5td: 2pts | ≤10td: 1pt",
            "description": (
                "Fires on the **hourly** chart when at least 2 of 3 conditions are met: price at or below "
                "the lower Bollinger Band, RSI recently below 40, or price more than 1.5% below the 50 EMA. "
                "Only triggers when price is above the 200-day DMA, filtering out downtrending stocks."
            ),
        },
    ]
 
    for d in DEFS:
        cat_color = {"Trending": "#0d2a45", "Observation": "#1a1a2a", "Reversal": "#0d3020"}.get(d["category"], "#1a1a1a")
        cat_text  = {"Trending": "#90caf9", "Observation": "#888888", "Reversal": "#80cbc4"}.get(d["category"], "#aaa")
        st.markdown(
            f'<div style="background:{cat_color}; border-left: 4px solid {cat_text}; '
            f'padding: 14px 18px; border-radius: 6px; margin-bottom: 12px;">'
            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
            f'<span style="font-size:16px; font-weight:bold; color:#e0e0e0;">{d["name"]}</span>'
            f'<span style="font-size:11px; background:#1a1a1a; color:{cat_text}; padding:3px 10px; border-radius:12px;">{d["category"]}</span>'
            f'</div>'
            f'<div style="font-size:11px; color:#666; margin:6px 0 8px 0;">📊 {d["scoring"]}</div>'
            f'<div style="font-size:13px; color:#bbb; line-height:1.6;">{d["description"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
 
    st.stop()
 
# ══════════════════════════════════════════════════════════════════════════════
# LIVE VIEW
# ══════════════════════════════════════════════════════════════════════════════
 
with st.spinner("Loading live data..."):
    df = load_live_data()
 
if df.empty:
    st.error("No data found. Check your Sheet ID and credentials.json.")
    st.stop()
 
df["_trending_score"] = df.apply(compute_trending_score, axis=1)
df["_reversal_score"]  = df.apply(compute_reversal_score,  axis=1)
df["_score"]           = df["_trending_score"] + df["_reversal_score"]
df["_signal_count"]    = df.apply(signal_count, axis=1)
df["_jy_score_num"]    = pd.to_numeric(df["JY Score"], errors="coerce") if "JY Score" in df.columns else pd.NA
df["_atr_20d_num"]     = df["ATR from 20D MA"].apply(parse_leading_float) if "ATR from 20D MA" in df.columns else pd.NA
 
jy_history      = load_jy_history()
df["_jy_delta"] = pd.to_numeric(
    df.apply(lambda r: jy_score_24h_delta(r["Ticker"], r["_jy_score_num"], jy_history), axis=1),
    errors="coerce",
)
 
# Sidebar section/ticker filters (populated after data load)
all_sections = sorted([s for s in df["Section"].dropna().unique() if s.strip()])
all_tickers  = sorted(df["Ticker"].dropna().unique().tolist())
with st.sidebar:
    if view_mode == "📡 Live":
        section_filter = st.multiselect("Section", all_sections, placeholder="All sections", key="sec_live")
        ticker_filter  = st.multiselect("Show specific tickers", all_tickers, placeholder="All tickers", key="tick_live")
 
# Apply filters
filtered = df.copy()
if freshness_days is not None:
    filtered["_signal_count"] = filtered.apply(lambda r: signal_count(r, freshness_days), axis=1)
if section_filter:
    filtered = filtered[filtered["Section"].isin(section_filter)]
filtered = filtered[filtered["_trending_score"] >= min_trending]
filtered = filtered[filtered["_reversal_score"]  >= min_reversal]
filtered = filtered[filtered["_score"]           >= min_score]
filtered = filtered[filtered["_signal_count"]    >= min_signals]
for ind in must_have:
    col = f"{ind} Date"
    if freshness_days is not None:
        filtered = filtered[filtered[col].apply(
            lambda x: days_ago(x) is not None and days_ago(x) <= freshness_days
        )]
    else:
        filtered = filtered[filtered[col].apply(lambda x: days_ago(x) is not None)]
if ticker_filter:
    pinned   = df[df["Ticker"].isin(ticker_filter)]
    filtered = pd.concat([filtered, pinned]).drop_duplicates(subset="Ticker")
 
if sort_by == "Total score (high→low)":
    filtered = filtered.sort_values("_score", ascending=False)
elif sort_by == "JY Score (high→low)":
    filtered = filtered.sort_values("_jy_score_num", ascending=False, na_position="last")
elif sort_by == "Trending score (high→low)":
    filtered = filtered.sort_values("_trending_score", ascending=False)
elif sort_by == "Reversal score (high→low)":
    filtered = filtered.sort_values("_reversal_score", ascending=False)
elif sort_by == "Signals (high→low)":
    filtered = filtered.sort_values("_signal_count", ascending=False)
else:
    filtered = filtered.sort_values("Ticker")
 
# Metrics
top_row = filtered.iloc[0] if not filtered.empty else None
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Shown",        len(filtered[filtered["Ticker"].str.strip() != ""]))
c2.metric("Total tickers",len(df[df["Ticker"].str.strip() != ""]))
c3.metric("Max Trend",    MAX_TRENDING)
c4.metric("Max Reversal", MAX_REVERSAL)
c5.metric("Top ticker",   top_row["Ticker"] if top_row is not None else "—")
c6.metric("Top score",    int(top_row["_score"]) if top_row is not None else 0)
 
# Charts row 1 — full-width treemap
st.markdown("---")
st.subheader("📊 Overview")
st.plotly_chart(chart_sector_treemap(df, mode=treemap_mode), use_container_width=True)
 
# Charts row 2 — section-level
col_r2a, col_r2b = st.columns(2)
with col_r2a:
    st.plotly_chart(chart_top_sections_stacked(df, n=10), use_container_width=True)
with col_r2b:
    fig_sec_gainers = chart_top_section_gainers_today(df)
    if fig_sec_gainers:
        st.plotly_chart(fig_sec_gainers, use_container_width=True)
    else:
        st.info("No section gains today yet.")
 
# Charts row 3 — ticker-level
col_r3a, col_r3b, col_r3c = st.columns(3)
with col_r3a:
    fig_gainers = chart_top_gainers_today(df)
    if fig_gainers:
        st.plotly_chart(fig_gainers, use_container_width=True)
    else:
        st.info("No signals fired in the last trading day yet.")
with col_r3b:
    fig_trending = chart_top_trending(df)
    if fig_trending:
        st.plotly_chart(fig_trending, use_container_width=True)
    else:
        st.info("No trending scores yet.")
with col_r3c:
    fig_reversals = chart_potential_reversals(df)
    if fig_reversals:
        st.plotly_chart(fig_reversals, use_container_width=True)
    else:
        st.info("No potential reversals detected.")
 
# ── JY Score charts — mirrors the point-system section above ─────────────────
st.markdown("---")
st.subheader("🟣 JY Score Overview")
 
fig_jy_treemap = chart_jy_sector_treemap(df, mode=treemap_mode)
if fig_jy_treemap:
    st.plotly_chart(fig_jy_treemap, use_container_width=True)
else:
    st.info("No JY Score data yet.")
 
col_jy1, col_jy2 = st.columns(2)
with col_jy1:
    fig_jy_sections = chart_jy_top_sections(df)
    if fig_jy_sections:
        st.plotly_chart(fig_jy_sections, use_container_width=True)
    else:
        st.info("No JY Score data yet.")
with col_jy2:
    fig_jy_sec_gainers = chart_jy_top_section_gainers(df)
    if fig_jy_sec_gainers:
        st.plotly_chart(fig_jy_sec_gainers, use_container_width=True)
    else:
        st.info("No JY Score section gains yet (needs ~24h of history).")
 
col_jy3, col_jy4 = st.columns(2)
with col_jy3:
    fig_jy_gainers = chart_jy_top_gainers(df)
    if fig_jy_gainers:
        st.plotly_chart(fig_jy_gainers, use_container_width=True)
    else:
        st.info("No JY Score gainers yet (needs ~24h of history).")
with col_jy4:
    fig_jy_stretched = chart_jy_top_stretched(df)
    if fig_jy_stretched:
        st.plotly_chart(fig_jy_stretched, use_container_width=True)
    else:
        st.info("No stretched-from-20D-MA data yet.")
 
# Legend + Table
st.markdown("---")
st.subheader("📋 Signal Table")
col_leg1, col_leg2, col_leg3 = st.columns(3)
with col_leg1:
    st.markdown(
        '<span style="background:#0d2a45;color:#90caf9;padding:3px 10px;border-radius:4px;font-size:12px;">■ Trending indicators</span>',
        unsafe_allow_html=True,
    )
with col_leg2:
    st.markdown(
        '<span style="background:#1a1a2a;color:#888;padding:3px 10px;border-radius:4px;font-size:12px;">■ Observation</span>',
        unsafe_allow_html=True,
    )
with col_leg3:
    st.markdown(
        '<span style="background:#0d3020;color:#80cbc4;padding:3px 10px;border-radius:4px;font-size:12px;">■ Reversal indicators</span>',
        unsafe_allow_html=True,
    )
 
st.markdown(
    '<br>'
    '<span style="background:#003020;color:#00e676;padding:3px 8px;border-radius:4px;font-size:12px;">● ≤ 2td</span>&nbsp;'
    '<span style="background:#1a3a1a;color:#4caf50;padding:3px 8px;border-radius:4px;font-size:12px;">● ≤ 5td</span>&nbsp;'
    '<span style="background:#1e3a00;color:#8bc34a;padding:3px 8px;border-radius:4px;font-size:12px;">● ≤ 10td</span>&nbsp;'
    '<span style="background:#1a1a1a;color:#444;padding:3px 8px;border-radius:4px;font-size:12px;">● Older</span>&nbsp;'
    '<span style="background:#111;color:#333;padding:3px 8px;border-radius:4px;font-size:12px;">● No signal</span>',
    unsafe_allow_html=True,
)
st.markdown("<br>", unsafe_allow_html=True)
 
if filtered.empty:
    st.warning("No tickers match the current filters.")
else:
    st.markdown(build_html_table(filtered), unsafe_allow_html=True)
 
# Auto refresh
time.sleep(REFRESH_SECONDS)
st.rerun()