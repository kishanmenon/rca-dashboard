"""
dashboard.py — Sales RCA Dashboard
No Anthropic. No external AI. Pure rules engine (rca_engine.py).

Run locally:   streamlit run dashboard.py
"""

import sys, os, io, re, json, traceback
from datetime import date, datetime, timedelta
import streamlit as st

st.set_page_config(
    page_title="Sales RCA Dashboard",
    page_icon="📉",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    import pandas as pd
    import numpy as np
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaInMemoryUpload
    from rca_engine import (
        run_rca, tag_rows, filter_df, normalise_cols,
        get_dimension_values, DIMENSION_COLS, DIMENSION_LABELS,
        RULE_DEFINITIONS, COL_ALIASES, W_L30D, W_L7D,
    )
    from report_generator import generate_html
except Exception as _e:
    st.error(f"Import failed: {_e}")
    st.code(traceback.format_exc())
    st.stop()

# ── config ─────────────────────────────────────────────────────────────────────
RAW_FOLDER_ID        = "1J6vs8w3gEwQu2CGd4p6Q6Nrg6mkgQWh1"
REPORTS_FOLDER_NAME  = "RCA_Reports"
SCOPES               = ["https://www.googleapis.com/auth/drive"]
SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_SA_FILE", r"C:\Users\h.kishandasmenon\Downloads\creds.json"
)

# ── drive ──────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_drive_service():
    if "gcp_service_account" in st.secrets:
        info = dict(st.secrets["gcp_service_account"])
        if "private_key" in info:
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


@st.cache_data(ttl=120, show_spinner=False)
def list_folder(_svc, folder_id):
    files, token = [], None
    while True:
        r = _svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id,name,modifiedTime,size)",
            pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True
        ).execute()
        files.extend(r.get("files",[]))
        token = r.get("nextPageToken")
        if not token: break
    return files


def find_file_for_date(files, target: date):
    day = target.day
    sfx = "th" if 11<=day<=13 else {1:"st",2:"nd",3:"rd"}.get(day%10,"th")
    pats = [
        target.strftime("%Y-%m-%d"), target.strftime("%d-%m-%Y"),
        target.strftime("%d%m%Y"),
        f"{day}{sfx} {target.strftime('%B %Y')}".lower(),
        f"{day} {target.strftime('%B %Y')}".lower(),
    ]
    for f in files:
        if not f["name"].lower().endswith(".csv"): continue
        if any(k in f["name"].lower() for k in ["summary_","rca_report"]): continue
        if any(p in f["name"].lower() for p in pats): return f
    return None


def download_and_filter_csv(svc, file_id, file_name, dim_col, target_value, size_bytes=None):
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    dl  = MediaIoBaseDownload(buf, req, chunksize=20*1024*1024)
    mb  = f"{size_bytes/1e6:.0f} MB" if size_bytes else "file"
    bar = st.progress(0, text=f"⬇️  Downloading {file_name} ({mb})…")
    done = False
    while not done:
        status, done = dl.next_chunk()
        bar.progress(status.progress(), text=f"Downloading… {status.progress()*100:.0f}%")
    bar.progress(1.0, text="✓  Download complete — filtering rows…")
    buf.seek(0)

    # peek at header to find actual column name
    header_df = pd.read_csv(buf, nrows=0)
    buf.seek(0)
    actual_col = dim_col
    actual_map = {c.lower().strip(): c for c in header_df.columns}
    for alias in COL_ALIASES.get(dim_col, [dim_col]):
        if alias.lower() in actual_map:
            actual_col = actual_map[alias.lower()]
            break

    filtered, total, kept = [], 0, 0
    cbar = st.progress(0, text="Filtering…")
    for i, chunk in enumerate(pd.read_csv(buf, chunksize=50_000, low_memory=False)):
        total += len(chunk)
        if target_value.upper() != "OVERALL" and actual_col in chunk.columns:
            chunk = chunk[chunk[actual_col].astype(str).str.strip().str.lower()
                         == target_value.strip().lower()]
        kept += len(chunk)
        filtered.append(chunk)
        if i % 10 == 0:
            cbar.progress(min(0.99, i*50_000/max(1,size_bytes//100)),
                          text=f"Scanned {total:,} rows — kept {kept:,}…")
    cbar.progress(1.0, text=f"✓  {kept:,} matching rows loaded into memory")
    if not filtered: return pd.DataFrame()
    return pd.concat(filtered, ignore_index=True)


def ensure_reports_folder(svc, parent_id):
    q = (f"'{parent_id}' in parents and name='{REPORTS_FOLDER_NAME}' "
         f"and mimeType='application/vnd.google-apps.folder' and trashed=false")
    res = svc.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                           includeItemsFromAllDrives=True).execute().get("files",[])
    if res: return res[0]["id"]
    f = svc.files().create(
        body={"name":REPORTS_FOLDER_NAME,"mimeType":"application/vnd.google-apps.folder",
              "parents":[parent_id]},
        fields="id", supportsAllDrives=True).execute()
    return f["id"]


def upload_report(svc, folder_id, filename, html):
    old = svc.files().list(
        q=f"'{folder_id}' in parents and name='{filename}' and trashed=false",
        fields="files(id)", supportsAllDrives=True,
        includeItemsFromAllDrives=True).execute().get("files",[])
    for f in old: svc.files().delete(fileId=f["id"], supportsAllDrives=True).execute()
    media = MediaInMemoryUpload(html.encode(), mimetype="text/html")
    up = svc.files().create(
        body={"name":filename,"parents":[folder_id],"mimeType":"text/html"},
        media_body=media, fields="id", supportsAllDrives=True).execute()
    svc.permissions().create(fileId=up["id"],
        body={"type":"anyone","role":"reader"}).execute()
    return f"https://drive.google.com/file/d/{up['id']}/view"


# ── formatters ─────────────────────────────────────────────────────────────────
def n(v):
    try:    return f"{round(float(v)):,}" if v not in (None,"","—") else "—"
    except: return "—"
def inr(v):
    try:    return f"₹{round(float(v)):,}" if v not in (None,"","—") else "—"
    except: return "—"
def pct(v):
    try:    x=float(v); return f"{'+'if x>0 else ''}{x:.1f}%"
    except: return "—"
def drr(v):
    try:    return f"{float(v):.1f}"
    except: return "—"


# ── KPI row ────────────────────────────────────────────────────────────────────
def kpi_row(summary):
    c = st.columns(6)
    c[0].metric("Loss vs L7D (orders/day)",  n(summary.get("total_loss_vs_l7d")),  delta="↓", delta_color="inverse")
    c[1].metric("Loss vs L30D (orders/day)", n(summary.get("total_loss_vs_l30d")), delta="↓", delta_color="inverse")
    c[2].metric("Yesterday Orders (L1D)",    n(summary.get("total_l1d_orders")))
    c[3].metric("Monthly Impact",            n(summary.get("monthly_impact")),      delta="orders at risk", delta_color="inverse")
    c[4].metric("Avg Health Score",          f"{n(summary.get('avg_health_score'))} /100")
    c[5].metric("Sudden Drops Today",        n(summary.get("sudden_drop_count")),   delta="listings", delta_color="inverse")


# ── rules summary ──────────────────────────────────────────────────────────────
def show_rules_summary(rules):
    if not rules: return
    st.markdown("#### What rules are causing the most order loss?")
    st.caption(f"Ranked by orders/day lost yesterday vs L7D run rate. Window sizes: L30D = {W_L30D} days, L7D = {W_L7D} days.")
    rows = [{
        "#":                          i+1,
        "Rule":                       f"{r.get('icon','')} {r['rule']}",
        "Listings Hit":               n(r.get("listings_affected")),
        "Loss vs L7D (orders/day)":   n(r.get("loss_vs_l7d")),
        "Loss vs L30D (orders/day)":  n(r.get("loss_vs_l30d")),
        "Recovery if Fixed":          f"{r.get('recovery_pct',0)}%",
        "Top Owners":                 ", ".join(r.get("top_owner_names",[])[:2]) or "—",
    } for i,r in enumerate(rules)]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with st.expander("📖 What does each rule mean?"):
        for r in rules:
            defn = RULE_DEFINITIONS.get(r["rule"],{})
            st.markdown(f"**{defn.get('icon','')} {r['rule']}**")
            c1,c2 = st.columns([3,1])
            c1.markdown(f"**What:** {defn.get('what','')}")
            c1.markdown(f"**Detected by:** `{defn.get('how','')}`")
            c1.markdown(f"**Fix:** {defn.get('fix','')}")
            c2.metric("Listings", n(r.get("listings_affected")))
            c2.metric("Loss vs L7D", n(r.get("loss_vs_l7d")), delta="orders/day", delta_color="inverse")
            c2.metric("Loss vs L30D", n(r.get("loss_vs_l30d")), delta="orders/day", delta_color="inverse")
            st.divider()


# ── actionables ────────────────────────────────────────────────────────────────
def show_actionables(actions, df_filtered):
    if not actions: st.info("No actionables."); return
    st.markdown("#### Filters")
    fc1,fc2,fc3,fc4 = st.columns(4)
    f_name  = fc1.selectbox("Owner Name",  ["All"]+sorted({str(a.get("name","")).strip()    for a in actions if str(a.get("name","")).strip()}),    key="fn")
    f_owner = fc2.selectbox("Owner Type",  ["All"]+sorted({str(a.get("owner","")).strip()   for a in actions if str(a.get("owner","")).strip()}),   key="fo")
    f_rule  = fc3.selectbox("Rule",        ["All"]+sorted({str(a.get("rule","")).strip()    for a in actions if str(a.get("rule","")).strip()}),    key="fr")
    f_bu    = fc4.selectbox("sell_bu",     ["All"]+sorted({str(a.get("sell_bu","")).strip() for a in actions if str(a.get("sell_bu","")).strip()}), key="fb")
    filtered = [a for a in actions
        if (f_name  == "All" or str(a.get("name","")).strip()    == f_name)
        and(f_owner == "All" or str(a.get("owner","")).strip()   == f_owner)
        and(f_rule  == "All" or str(a.get("rule","")).strip()    == f_rule)
        and(f_bu    == "All" or str(a.get("sell_bu","")).strip() == f_bu)]
    st.caption(f"Showing {len(filtered)} of {len(actions)} actionables — sorted by rule precedence then order loss")
    rows = [{
        "#":              a.get("priority",""),
        "Rule":           a.get("rule",""),
        "Listing":        str(a.get("product_title",""))[:55],
        "Seller":         str(a.get("display_name",""))[:28],
        "Owner Type":     a.get("owner",""),
        "Owner Name":     a.get("name",""),
        "sell_bu":        a.get("sell_bu",""),
        "Category":       a.get("analytic_category",""),
        "L1D DRR":        drr(a.get("drr_l1d")),
        "L7D DRR":        drr(a.get("drr_l7d")),
        "L30D DRR":       drr(a.get("drr_l30d")),
        "Loss vs L7D":    n(a.get("loss_vs_l7d")),
        "Loss vs L30D":   n(a.get("loss_vs_l30d")),
        "Recovery/Day":   n(a.get("recovery_per_day")),
        "Monthly Impact": n(a.get("monthly_impact")),
        "Action":         str(a.get("action",""))[:160],
    } for a in filtered]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 column_config={"Action": st.column_config.TextColumn(width="large")})

    st.markdown("#### Download raw data with RCA tags")
    if st.button("🏷️  Generate tagged download", key="gen_tag"):
        with st.spinner("Tagging all rows…"):
            tagged = tag_rows(df_filtered.copy())
        col_order = [
            "sell_bu","analytic_business_unit","analytic_super_category","analytic_category",
            "cms_vertical","analytic_vertical","owner","name","seller_id","display_name",
            "product_id","product_title","listing_id","listing_status",
            "listing_internal_state","latest_deactivation_reason","yesterday_atp",
            "l30d_orders","l30d_units","l30d_gmv","l30d_asp","l30d_tp",
            "l7d_orders","l7d_units","l7d_gmv","l7d_asp","l7d_tp",
            "l1d_orders","l1d_units","l1d_gmv","l1d_asp","l1d_tp",
            "drr_l30d","drr_l7d","drr_l1d",
            "rca_tag","priority_rank","order_loss_vs_l7d","order_loss_vs_l30d",
            "health_score","recommended_action",
        ]
        present = [c for c in col_order if c in tagged.columns]
        st.download_button("⬇️  Download tagged CSV",
            data=tagged[present].to_csv(index=False).encode(),
            file_name=f"RCA_tagged_{date.today()}.csv", mime="text/csv", key="dl_tag")
        st.success(f"Ready — {len(tagged):,} rows, {len(present)} columns")


# ── price hike tables ──────────────────────────────────────────────────────────
def show_price_hike(tp_issues, asp_issues):
    tab1, tab2 = st.tabs(["🏷️ TP Price Hike", "💸 ASP Price Hike"])
    for tab, issues, pt, label in [
        (tab1, tp_issues,  "tp",  "Transaction Price"),
        (tab2, asp_issues, "asp", "Avg Selling Price"),
    ]:
        with tab:
            if not issues:
                st.success(f"No {label} hike issues."); continue
            st.caption(f"L1D {label} > L7D or L30D by >5% AND orders dropped. "
                       f"Loss shown as 0 where that window was NOT triggered.")
            rows = [{
                "Listing":           str(r.get("product_title",""))[:50],
                "Seller":            str(r.get("display_name",""))[:28],
                "Owner":             r.get("owner",""), "Owner Name": r.get("name",""),
                f"L30D {pt.upper()}": inr(r.get(f"l30d_{pt}")),
                f"L7D {pt.upper()}":  inr(r.get(f"l7d_{pt}")),
                f"L1D {pt.upper()}":  inr(r.get(f"l1d_{pt}")),
                "Δ vs L7D":          pct(r.get(f"{pt}_chg_vs_l7d")),
                "Δ vs L30D":         pct(r.get(f"{pt}_chg_vs_l30d")),
                "L1D DRR":           drr(r.get("drr_l1d")),
                "L7D DRR":           drr(r.get("drr_l7d")),
                "L30D DRR":          drr(r.get("drr_l30d")),
                "Loss vs L7D":       n(r.get("plot_loss_vs_l7d")),
                "Loss vs L30D":      n(r.get("plot_loss_vs_l30d")),
                "Triggered":         ("Both" if r.get("triggered_vs_l7d") and r.get("triggered_vs_l30d")
                                      else "L7D only" if r.get("triggered_vs_l7d")
                                      else "L30D only"),
                "Action":            str(r.get("action",""))[:120],
            } for r in issues]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ── OOS ────────────────────────────────────────────────────────────────────────
def show_oos(issues):
    if not issues: st.success("No OOS issues."); return
    st.caption("Both L7D and L30D losses plotted — OOS impacts both windows.")
    st.dataframe(pd.DataFrame([{
        "Listing":      str(r.get("product_title",""))[:55],
        "Seller":       str(r.get("display_name",""))[:28],
        "Owner":        r.get("owner",""), "Owner Name": r.get("name",""),
        "ATP":          n(r.get("yesterday_atp")),
        "L1D DRR":      drr(r.get("drr_l1d")),
        "L7D DRR":      drr(r.get("drr_l7d")),
        "L30D DRR":     drr(r.get("drr_l30d")),
        "L7D Orders":   n(r.get("l7d_orders")),
        "L30D Orders":  n(r.get("l30d_orders")),
        "L7D GMV":      inr(r.get("l7d_gmv")),
        "Loss vs L7D":  n(r.get("plot_loss_vs_l7d")),
        "Loss vs L30D": n(r.get("plot_loss_vs_l30d")),
        "Action":       str(r.get("action",""))[:120],
    } for r in issues]), use_container_width=True, hide_index=True)


# ── inactive ───────────────────────────────────────────────────────────────────
def show_inactive(inact):
    fk  = inact.get("fk_inactivated",{})
    sel = inact.get("seller_inactivated",{})
    c1,c2 = st.columns(2)
    with c1:
        st.markdown(f"**🔴 Deactivated by Flipkart** — {n(fk.get('total_count'))} listings")
        st.caption(f"Loss vs L7D: {n(fk.get('total_loss_vs_l7d'))} orders/day  |  vs L30D: {n(fk.get('total_loss_vs_l30d'))} orders/day")
        if fk.get("by_reason"):
            st.dataframe(pd.DataFrame([{
                "Reason": r["reason"], "Count": r["count"],
                "Loss vs L7D": n(r.get("loss_vs_l7d")),
                "Loss vs L30D": n(r.get("loss_vs_l30d")),
            } for r in fk["by_reason"]]), use_container_width=True, hide_index=True)
    with c2:
        st.markdown(f"**🟡 Seller Switched Off** — {n(sel.get('total_count'))} listings")
        st.caption(f"Loss vs L7D: {n(sel.get('total_loss_vs_l7d'))} orders/day  |  vs L30D: {n(sel.get('total_loss_vs_l30d'))} orders/day")
        if sel.get("by_seller"):
            st.dataframe(pd.DataFrame([{
                "Seller": s["seller"], "Count": s["count"],
                "Loss vs L7D": n(s.get("loss_vs_l7d")),
                "Loss vs L30D": n(s.get("loss_vs_l30d")),
            } for s in sel["by_seller"]]), use_container_width=True, hide_index=True)


# ── innovative insights ────────────────────────────────────────────────────────
def show_innovative(innov):
    mp  = innov.get("multiple_problems_at_once",{})
    hds = innov.get("high_dependency_sellers",{})
    sod = innov.get("sudden_order_drop",{})
    ds  = innov.get("dead_stock_no_demand",{})

    tabs = st.tabs([
        mp.get("label","⚡ Multiple Problems at Once"),
        hds.get("label","🌊 High Dependency Sellers"),
        sod.get("label","📉 Sudden Order Drop"),
        ds.get("label","🧊 Dead Stock — No Demand"),
    ])
    with tabs[0]:
        st.caption(mp.get("desc",""))
        st.metric("Listings with 2+ problems", mp.get("count",0),
                  delta=f"−{n(mp.get('total_loss_vs_l7d'))} orders/day vs L7D", delta_color="inverse")
        if mp.get("listings"):
            st.dataframe(pd.DataFrame([{
                "Listing":  i["product_title"], "Seller": i["display_name"],
                "Owner":    i.get("name",""), "Problems": i["problems"],
                "#Issues":  i["issue_count"],
                "L1D DRR":  drr(i.get("drr_l1d")), "L7D DRR": drr(i.get("drr_l7d")), "L30D DRR": drr(i.get("drr_l30d")),
                "Loss vs L7D": i["loss_vs_l7d"], "Health": i["health_score"],
            } for i in mp["listings"]]), use_container_width=True, hide_index=True)

    with tabs[1]:
        st.caption(hds.get("desc",""))
        sellers = hds.get("top_sellers",[])
        if sellers:
            st.dataframe(pd.DataFrame([{
                "Seller": s["seller"], "Listings": s["listings"],
                "Orders/Day (L7D DRR)": s["orders_per_day"],
                "OOS Listings": s["oos_count"], "Risk Score": s["risk_score"],
            } for s in sellers]), use_container_width=True, hide_index=True)

    with tabs[2]:
        st.caption(sod.get("desc",""))
        st.metric("Listings", sod.get("count",0))
        if sod.get("listings"):
            st.dataframe(pd.DataFrame([{
                "Listing": i["product_title"], "Seller": i["display_name"], "Owner": i.get("name",""),
                "L1D DRR": drr(i.get("drr_l1d")), "L7D DRR": drr(i.get("drr_l7d")), "L30D DRR": drr(i.get("drr_l30d")),
                "Health": i["health_score"],
            } for i in sod["listings"]]), use_container_width=True, hide_index=True)

    with tabs[3]:
        st.caption(ds.get("desc",""))
        st.metric("Listings", ds.get("count",0))
        if ds.get("listings"):
            st.dataframe(pd.DataFrame([{
                "Listing": i["product_title"], "Seller": i["display_name"],
                "ATP": i["yesterday_atp"],
                "L1D DRR": drr(i.get("drr_l1d")), "L7D DRR": drr(i.get("drr_l7d")), "L30D DRR": drr(i.get("drr_l30d")),
                "ASP": inr(i["l7d_asp"]),
            } for i in ds["listings"]]), use_container_width=True, hide_index=True)


# ── recommendations ────────────────────────────────────────────────────────────
def show_recommendations(recs):
    tab1, tab2 = st.tabs(["⬆️ UM → KAM Candidates", "🔍 Google Search Trends"])

    with tab1:
        um = recs.get("um_to_kam",[])
        st.caption("Qualification: L1D DRR > L7D DRR > L30D DRR (continuously growing) or strong L30D volume. "
                   "Growth % = (DRR change / base DRR) × 100.")
        if um:
            rows = []
            for s in um:
                cg = s.get("continuously_growing", False)
                rows.append({
                    "Seller":          s["seller"],
                    "L30D DRR":        drr(s.get("drr_l30d")),
                    "L7D DRR":         drr(s.get("drr_l7d")),
                    "L1D DRR":         drr(s.get("drr_l1d")),
                    "Growth L30D→L1D": pct(s.get("growth_l30d_to_l1d")),
                    "Growth L7D→L1D":  pct(s.get("growth_l7d_to_l1d")),
                    "Trend":           s.get("trend",""),
                    "Continuously Growing": "✅ Yes" if cg else "—",
                    "Health Score":    s.get("health",""),
                    "ASP":             inr(s.get("avg_asp")),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No UM→KAM candidates found.")

    with tab2:
        trends = recs.get("search_trends",[])
        if not trends:
            st.info("No trend data available.")
        elif trends and trends[0].get("search_keyword") == "pytrends not installed":
            st.warning("Add `pytrends` to requirements.txt to enable Google Trends.")
            st.code("pytrends")
        else:
            st.caption("Google Trends score (0–100, India, last 7 days) fuzzy-matched against your product titles. "
                       "⚠️ = High search demand but low stock.")
            rows = [{
                "Search Keyword":      t.get("search_keyword",""),
                "Matched Product":     t.get("product_title",""),
                "Trend Score (0-100)": n(t.get("google_trend_score")),
                "L1D DRR":             drr(t.get("drr_l1d")),
                "L7D DRR":             drr(t.get("drr_l7d")),
                "L30D DRR":            drr(t.get("drr_l30d")),
                "Stock (ATP)":         n(t.get("total_atp")),
                "⚠️ Gap":              "⚠️ Restock" if t.get("demand_supply_gap") else "OK",
                "Action":              t.get("action","")[:100],
            } for t in trends]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    for k,v in [("df_raw",None),("loaded_date",None),("result",None),
                ("result_meta",{}),("share_url",None),("filtered_df",None)]:
        if k not in st.session_state: st.session_state[k] = v

    st.title("📉 Sales RCA Dashboard")
    st.caption("Google Drive → CSV by date → rules engine → actionables by owner / BU / category → shareable report")

    with st.sidebar:
        st.markdown("## Step 1 — Pick a date")
        sel_date = st.date_input("Date", value=date.today()-timedelta(days=1))

        st.markdown("## Step 2 — Choose what to analyse")
        st.caption("Select BEFORE loading — only matching rows are read into memory (saves RAM).")
        dim_label  = st.selectbox("Analyse by",
                                  [DIMENSION_LABELS.get(d,d) for d in DIMENSION_COLS], key="dim_lbl")
        dim_choice = {DIMENSION_LABELS.get(d,d):d for d in DIMENSION_COLS}[dim_label]
        val_input  = st.text_input("Value (exact match)",
                                   placeholder='e.g. Fashion   |   blank = Overall (all rows)',
                                   key="val_input")
        val_choice = val_input.strip() if val_input.strip() else "Overall (all BUs)"

        st.markdown("## Step 3 — Load & Run")
        run_btn = st.button("▶  Load file & Run RCA", type="primary", use_container_width=True)

        if st.session_state.share_url:
            st.divider()
            st.markdown("**Latest report**")
            st.markdown(f"[Open ↗]({st.session_state.share_url})")
            st.code(st.session_state.share_url, language=None)

    if run_btn:
        try: svc = get_drive_service()
        except Exception as e:
            st.error(f"Drive connection failed: {e}"); st.code(traceback.format_exc()); st.stop()

        with st.spinner("Scanning Drive folder…"):
            all_files = list_folder(svc, RAW_FOLDER_ID)
        raw_file = find_file_for_date(all_files, sel_date)
        if not raw_file:
            st.error(f"No CSV found for {sel_date}.")
            st.warning(f"Files visible ({len(all_files)}): {[f['name'] for f in all_files]}")
            st.stop()

        size = int(raw_file.get("size",0))
        target = "OVERALL" if "overall" in val_choice.lower() else val_choice
        st.info(f"Found **{raw_file['name']}** ({size/1e6:.0f} MB) — streaming with chunked filter for {dim_choice} = {val_choice}")

        try:
            df_filt = download_and_filter_csv(
                svc, raw_file["id"], raw_file["name"],
                dim_col=dim_choice, target_value=target, size_bytes=size)
        except Exception as e:
            st.error(f"Download failed: {e}"); st.code(traceback.format_exc()); st.stop()

        if df_filt.empty:
            st.error(f"No rows matched {dim_choice} = '{val_choice}'. Check exact value — case sensitive."); st.stop()

        st.success(f"Loaded **{len(df_filt):,} rows** for {dim_choice} = {val_choice}")
        date_str = sel_date.strftime("%Y-%m-%d")

        with st.spinner(f"Running RCA engine on {len(df_filt):,} rows…"):
            try: result = run_rca(df_filt, dim_choice, target, date_str)
            except Exception as e:
                st.error(f"RCA engine error: {e}"); st.code(traceback.format_exc()); st.stop()

        if "error" in result: st.error(result["error"]); st.stop()

        st.session_state.result      = result
        st.session_state.result_meta = {"dim":dim_choice,"val":val_choice,"date":date_str}
        st.session_state.filtered_df = df_filt
        st.session_state.loaded_date = sel_date

        try:
            rfid  = ensure_reports_folder(svc, RAW_FOLDER_ID)
            ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe  = re.sub(r"[^a-zA-Z0-9]","_",f"{dim_choice}_{val_choice}")[:40]
            fname = f"RCA_{safe}_{date_str}_{ts}.html"
            html  = generate_html(result, f"{val_choice} ({dim_choice})", date_str,
                                  datetime.now().strftime("%d %b %Y, %I:%M %p"))
            url   = upload_report(svc, rfid, fname, html)
            st.session_state.share_url = url
        except Exception as e:
            st.warning(f"Analysis done — report upload failed: {e}")
        st.rerun()

    result = st.session_state.result
    if not result:
        st.info("👈 Pick a date + dimension + value, then click **Load file & Run RCA**")
        return

    meta    = st.session_state.result_meta
    summary = result.get("summary",{})
    rmeta   = result.get("meta",{})

    if st.session_state.share_url:
        st.success("📎 Shareable report:")
        st.markdown(f"### [Open Report — {meta.get('val','')} · {meta.get('date','')} ↗]({st.session_state.share_url})")
        st.code(st.session_state.share_url, language=None)
        st.divider()

    st.markdown(
        f"### {meta.get('val','')} · {meta.get('dim','')} · {meta.get('date','')} · "
        f"**{n(rmeta.get('total_rows'))}** rows · top issue: **{summary.get('top_issue','—')}**"
    )
    kpi_row(summary)
    st.divider()

    st.subheader("📋 Rules Causing Order Loss — Ranked")
    show_rules_summary(result.get("rules_summary",[]))
    st.divider()

    st.subheader("🎯 Priority Actionables")
    st.caption("Precedence: Deactivated by FK → Seller Switched Off → Out of Stock → TP Price Hike → ASP Price Hike")
    show_actionables(result.get("actionables",[]), st.session_state.filtered_df)
    st.divider()

    st.subheader("💸 Price Hike Analysis")
    show_price_hike(result.get("tp_hike_issues",[]), result.get("asp_hike_issues",[]))
    st.divider()

    st.subheader("📦 Out of Stock")
    show_oos(result.get("oos_issues",[]))
    st.divider()

    st.subheader("🚫 Inactive Listings")
    show_inactive(result.get("inactive_issues",{}))
    st.divider()

    st.subheader("🔬 Additional Insights")
    show_innovative(result.get("innovative_insights",{}))
    st.divider()

    st.subheader("💡 Recommendations")
    show_recommendations(result.get("recommendations",{}))

    with st.expander("🗂 Raw JSON"):
        st.json(result)


if __name__ == "__main__":
    main()
