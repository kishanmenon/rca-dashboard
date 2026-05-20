"""
dashboard.py — Sales RCA Dashboard
No Anthropic API. No external AI. Pure code logic via rca_engine.py.

Run locally:
    streamlit run dashboard.py

Host for your whole team on internal network (e.g. http://10.83.75.181):
    streamlit run dashboard.py --server.port 80 --server.address 0.0.0.0 --server.headless true
"""

import io, os, re, json
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaInMemoryUpload

from rca_engine import (
    run_rca, tag_rows, filter_df,
    get_dimension_values, normalise_cols,
    DIMENSION_COLS, DIMENSION_LABELS, RULE_DEFINITIONS,
)
from report_generator import generate_html

# ── page ───────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Sales RCA Dashboard",
    page_icon="📉",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── config ─────────────────────────────────────────────────────────────────────
RAW_FOLDER_ID        = "1J6vs8w3gEwQu2CGd4p6Q6Nrg6mkgQWh1"
REPORTS_FOLDER_NAME  = "RCA_Reports"
SCOPES               = ["https://www.googleapis.com/auth/drive"]
SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_SA_FILE",
    r"C:\Users\h.kishandasmenon\Downloads\creds.json"
)


# ── drive helpers ──────────────────────────────────────────────────────────────
@st.cache_resource
def get_drive_service():
    if "gcp_service_account" in st.secrets:
        info = dict(st.secrets["gcp_service_account"])
        # Fix: Streamlit secrets sometimes stores \n as literal backslash-n
        if "private_key" in info:
            info["private_key"] = info["private_key"].replace("\\n", "\n")
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


@st.cache_data(ttl=120, show_spinner=False)
def list_folder(_svc, folder_id):
    files, token = [], None
    while True:
        r = _svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id,name,modifiedTime,size)",
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True).execute()
        files.extend(r.get("files",[]))
        token = r.get("nextPageToken")
        if not token:
            break
    return files


def find_file_for_date(files, target: date):
    day = target.day
    sfx = "th" if 11<=day<=13 else {1:"st",2:"nd",3:"rd"}.get(day%10,"th")
    pats = [
        target.strftime("%Y-%m-%d"),
        target.strftime("%d-%m-%Y"),
        target.strftime("%d%m%Y"),
        f"{day}{sfx} {target.strftime('%B %Y')}".lower(),
        f"{day} {target.strftime('%B %Y')}".lower(),
    ]
    for f in files:
        if not f["name"].lower().endswith(".csv"):
            continue
        if any(k in f["name"].lower() for k in ["summary_","rca_report"]):
            continue
        if any(p in f["name"].lower() for p in pats):
            return f
    return None


def download_csv(svc, file_id, file_name, size_bytes=None) -> pd.DataFrame:
    req  = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf  = io.BytesIO()
    dl   = MediaIoBaseDownload(buf, req, chunksize=20*1024*1024)
    mb   = f"{size_bytes/1e6:.0f} MB" if size_bytes else "file"
    bar  = st.progress(0, text=f"⬇️  Downloading {file_name} ({mb})…")
    done = False
    while not done:
        status, done = dl.next_chunk()
        bar.progress(status.progress(), text=f"Downloading… {status.progress()*100:.0f}%")
    bar.progress(1.0, text="✓  Download complete")
    buf.seek(0)
    return pd.read_csv(buf, low_memory=False)


def ensure_reports_folder(svc, parent_id):
    q   = (f"'{parent_id}' in parents and name='{REPORTS_FOLDER_NAME}' "
           f"and mimeType='application/vnd.google-apps.folder' and trashed=false")
    res = svc.files().list(q=q, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files",[])
    if res:
        return res[0]["id"]
    f = svc.files().create(body={"name":REPORTS_FOLDER_NAME,
        "mimeType":"application/vnd.google-apps.folder","parents":[parent_id]},
        fields="id", supportsAllDrives=True).execute()
    return f["id"]


def upload_report(svc, folder_id, filename, html):
    old = svc.files().list(
        q=f"'{folder_id}' in parents and name='{filename}' and trashed=false",
        fields="files(id)", supportsAllDrives=True,
        includeItemsFromAllDrives=True).execute().get("files",[])
    for f in old:
        svc.files().delete(fileId=f["id"], supportsAllDrives=True).execute()
    media = MediaInMemoryUpload(html.encode(), mimetype="text/html")
    up    = svc.files().create(
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


# ── render: KPI row ────────────────────────────────────────────────────────────
def kpi_row(summary):
    c = st.columns(6)
    c[0].metric("Loss vs L7D (orders/day)",  n(summary.get("total_loss_vs_l7d")),
                delta="orders/day lost", delta_color="inverse")
    c[1].metric("Loss vs L30D (orders/day)", n(summary.get("total_loss_vs_l30d")),
                delta="orders/day lost", delta_color="inverse")
    c[2].metric("L1D Orders (yesterday)",    n(summary.get("total_l1d_orders")))
    c[3].metric("Monthly Impact",            n(summary.get("monthly_impact")),
                delta="orders at risk", delta_color="inverse")
    c[4].metric("Avg Health Score",          f"{n(summary.get('avg_health_score'))} /100")
    c[5].metric("On Velocity Cliff",         n(summary.get("velocity_cliff_count")),
                delta="crashed yesterday", delta_color="inverse")


# ── render: rules summary ──────────────────────────────────────────────────────
def show_rules_summary(rules):
    if not rules:
        return
    st.markdown("#### Rules causing order loss — ranked by impact")
    st.caption("Each rule is a detected problem pattern. Sorted by orders lost yesterday vs 7-day average.")

    rows = [{
        "#":                          i+1,
        "Rule":                       f"{r.get('icon','')} {r['rule']}",
        "Listings Hit":               n(r.get("listings_affected")),
        "Loss vs L7D (orders/day)":   n(r.get("loss_vs_l7d")),
        "Loss vs L30D (orders/day)":  n(r.get("loss_vs_l30d")),
        "Recovery if Fixed":          f"{r.get('recovery_pct',0)}% likely",
        "Top Owners Affected":        ", ".join(r.get("top_owner_names",[])[:2]) or "—",
    } for i,r in enumerate(rules)]

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 column_config={
                     "Loss vs L7D (orders/day)":  st.column_config.TextColumn(width="medium"),
                     "Loss vs L30D (orders/day)": st.column_config.TextColumn(width="medium"),
                 })

    with st.expander("📖 What does each rule mean? (click to expand)"):
        for r in rules:
            defn = RULE_DEFINITIONS.get(r["rule"],{})
            st.markdown(f"**{defn.get('icon','')} {r['rule']}**")
            col1, col2 = st.columns([3,1])
            col1.markdown(f"**What it means:** {defn.get('what','')}")
            col1.markdown(f"**How detected:** `{defn.get('how','')}`")
            col1.markdown(f"**What to do:** {defn.get('fix','')}")
            col2.metric("Listings hit",    n(r.get("listings_affected")))
            col2.metric("Loss vs L7D",     n(r.get("loss_vs_l7d")), delta="orders/day", delta_color="inverse")
            col2.metric("Loss vs L30D",    n(r.get("loss_vs_l30d")), delta="orders/day", delta_color="inverse")
            st.divider()


# ── render: actionables ────────────────────────────────────────────────────────
def show_actionables(actions, df_filtered):
    if not actions:
        st.info("No actionables."); return

    st.markdown("#### Filters")
    fc1, fc2, fc3, fc4 = st.columns(4)

    f_name  = fc1.selectbox("Owner Name",   ["All"]+sorted({str(a.get("name","")).strip()     for a in actions if str(a.get("name","")).strip()}),    key="fn")
    f_owner = fc2.selectbox("Owner Type",   ["All"]+sorted({str(a.get("owner","")).strip()    for a in actions if str(a.get("owner","")).strip()}),   key="fo")
    f_rule  = fc3.selectbox("Rule",         ["All"]+sorted({str(a.get("rule","")).strip()     for a in actions if str(a.get("rule","")).strip()}),    key="fr")
    f_bu    = fc4.selectbox("sell_bu",      ["All"]+sorted({str(a.get("sell_bu","")).strip()  for a in actions if str(a.get("sell_bu","")).strip()}), key="fb")

    filtered = [a for a in actions
        if (f_name  == "All" or str(a.get("name","")).strip()    == f_name)
        and(f_owner == "All" or str(a.get("owner","")).strip()   == f_owner)
        and(f_rule  == "All" or str(a.get("rule","")).strip()    == f_rule)
        and(f_bu    == "All" or str(a.get("sell_bu","")).strip() == f_bu)]

    st.caption(f"Showing {len(filtered)} of {len(actions)} actionables")

    rows = [{
        "#":              a.get("priority",""),
        "Rule":           a.get("rule",""),
        "Listing":        str(a.get("product_title",""))[:55],
        "Seller":         str(a.get("display_name",""))[:28],
        "Owner Type":     a.get("owner",""),
        "Owner Name":     a.get("name",""),
        "sell_bu":        a.get("sell_bu",""),
        "Category":       a.get("analytic_category",""),
        "Loss vs L7D":    n(a.get("loss_vs_l7d")),
        "Loss vs L30D":   n(a.get("loss_vs_l30d")),
        "Recovery/Day":   n(a.get("recovery_per_day")),
        "Monthly Impact": n(a.get("monthly_impact")),
        "Action":         str(a.get("action",""))[:160],
    } for a in filtered]

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 column_config={"Action": st.column_config.TextColumn(width="large")})

    # tagged download
    st.markdown("#### Download raw data with action tags")
    st.caption("Every row from your dump gets tagged with rule, priority, order loss (L7D + L30D), and recommended action.")
    if st.button("🏷️  Generate tagged download", key="gen_tag"):
        with st.spinner("Tagging all rows…"):
            tagged = tag_rows(df_filtered.copy())
        col_order = [
            "sell_bu","analytic_business_unit","analytic_super_category",
            "analytic_category","cms_vertical","analytic_vertical",
            "owner","name","seller_id","display_name",
            "product_id","product_title","listing_id",
            "listing_status","listing_internal_state","latest_deactivation_reason",
            "yesterday_atp",
            "l30d_orders","l30d_units","l30d_gmv","l30d_asp","l30d_tp",
            "l7d_orders", "l7d_units", "l7d_gmv", "l7d_asp", "l7d_tp",
            "l1d_orders", "l1d_units", "l1d_gmv", "l1d_asp", "l1d_tp",
            "rca_tag","priority_rank",
            "order_loss_vs_l7d","order_loss_vs_l30d",
            "health_score","recommended_action",
        ]
        present = [c for c in col_order if c in tagged.columns]
        csv_bytes = tagged[present].to_csv(index=False).encode()
        st.download_button("⬇️  Download tagged CSV", data=csv_bytes,
            file_name=f"RCA_tagged_{date.today()}.csv", mime="text/csv", key="dl_tag")
        st.success(f"Ready — {len(tagged):,} rows, {len(present)} columns")


# ── render: ASP issues ─────────────────────────────────────────────────────────
def show_asp(issues):
    if not issues:
        st.success("No ASP pricing issues."); return
    st.dataframe(pd.DataFrame([{
        "Listing":     str(r.get("product_title",""))[:55],
        "Seller":      str(r.get("display_name",""))[:28],
        "Owner":       r.get("owner",""), "Owner Name": r.get("name",""),
        "L30D ASP":    inr(r.get("l30d_asp")), "L7D ASP":  inr(r.get("l7d_asp")),
        "Δ ASP":       pct(r.get("asp_change_pct")),
        "L7D Orders":  n(r.get("l7d_orders")),
        "Loss vs L7D": n(r.get("loss_vs_l7d")), "Loss vs L30D":n(r.get("loss_vs_l30d")),
        "Rec. ASP":    inr(r.get("recommended_asp")),
    } for r in issues]), use_container_width=True, hide_index=True)


# ── render: OOS ────────────────────────────────────────────────────────────────
def show_oos(issues):
    if not issues:
        st.success("No OOS issues."); return
    st.dataframe(pd.DataFrame([{
        "Listing":     str(r.get("product_title",""))[:55],
        "Seller":      str(r.get("display_name",""))[:28],
        "Owner":       r.get("owner",""), "Owner Name":r.get("name",""),
        "ATP":         n(r.get("yesterday_atp")),
        "L7D Orders":  n(r.get("l7d_orders")), "L7D GMV": inr(r.get("l7d_gmv")),
        "Loss vs L7D": n(r.get("loss_vs_l7d")), "Loss vs L30D":n(r.get("loss_vs_l30d")),
        "Action":      str(r.get("action",""))[:120],
    } for r in issues]), use_container_width=True, hide_index=True)


# ── render: inactive ───────────────────────────────────────────────────────────
def show_inactive(inact):
    fk  = inact.get("fk_inactivated",{})
    sel = inact.get("seller_inactivated",{})
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**🔴 Deactivated by Flipkart** — {n(fk.get('total_count'))} listings · {n(fk.get('total_order_loss_per_day'))} orders/day")
        if fk.get("by_reason"):
            st.dataframe(pd.DataFrame([{"Reason":r["reason"],"Count":r["count"],
                "Loss/Day":n(r.get("order_loss_per_day"))} for r in fk["by_reason"]]),
                use_container_width=True, hide_index=True)
    with c2:
        st.markdown(f"**🟡 Seller Switched Off** — {n(sel.get('total_count'))} listings · {n(sel.get('total_order_loss_per_day'))} orders/day")
        if sel.get("by_seller"):
            st.dataframe(pd.DataFrame([{"Seller":s["seller"],"Count":s["count"],
                "Loss/Day":n(s.get("order_loss_per_day"))} for s in sel["by_seller"]]),
                use_container_width=True, hide_index=True)


# ── render: innovative insights ────────────────────────────────────────────────
def show_innovative(innov):
    tabs = st.tabs(["⚡ Compounding Issues","🌊 Cascade Risk","📉 Velocity Cliff","🧊 Stranded Stock"])
    ci = innov.get("compounding_issues",{})
    with tabs[0]:
        st.caption("Listings with 2+ problems at once — disproportionate order loss")
        st.metric("Listings", ci.get("count",0),
                  delta=f"−{n(ci.get('total_loss_vs_l7d'))} orders/day vs L7D", delta_color="inverse")
        if ci.get("listings"):
            st.dataframe(pd.DataFrame([{
                "Listing":i["product_title"],"Seller":i["display_name"],"Owner":i.get("name",""),
                "Problems":i["problems"],"#":i["issue_count"],
                "Loss vs L7D":i["loss_vs_l7d"],"Health":i["health_score"],
            } for i in ci["listings"]]),use_container_width=True,hide_index=True)
    with tabs[1]:
        st.caption("If these sellers go fully inactive — your total order exposure")
        sellers = innov.get("seller_cascade_risk",{}).get("top_sellers",[])
        if sellers:
            st.dataframe(pd.DataFrame([{"Seller":s["seller"],"Listings":s["listings"],
                "Orders/Day":s["orders_per_day"],"OOS Listings":s["oos_count"],
                "Cascade Risk Score":s["risk_score"]} for s in sellers]),
                use_container_width=True,hide_index=True)
    with tabs[2]:
        vc = innov.get("velocity_cliff_listings",{})
        st.caption("Yesterday's orders < 15% of 30-day daily average — something happened")
        st.metric("Listings on cliff", vc.get("count",0))
        if vc.get("listings"):
            st.dataframe(pd.DataFrame([{"Listing":i["product_title"],"Seller":i["display_name"],
                "Owner":i.get("name",""),"L1D Rate":i["rate_l1d"],"L30D Rate":i["rate_l30d"],
                "Health":i["health_score"]} for i in vc["listings"]]),
                use_container_width=True,hide_index=True)
    with tabs[3]:
        si = innov.get("stranded_inventory",{})
        st.caption("Active listings with stock but zero demand — trapped working capital")
        st.metric("Stranded listings", si.get("count",0))
        if si.get("listings"):
            st.dataframe(pd.DataFrame([{"Listing":i["product_title"],"Seller":i["display_name"],
                "ATP":i["yesterday_atp"],"ASP":inr(i["l7d_asp"])} for i in si["listings"]]),
                use_container_width=True,hide_index=True)


# ── render: recommendations ────────────────────────────────────────────────────
def show_recommendations(recs):
    um = recs.get("um_to_kam",[])
    st.markdown("**⬆️ UM → KAM Upgrade Candidates**")
    if um:
        st.dataframe(pd.DataFrame([{"Seller":s["seller"],"L30D Orders":n(s["l30d_orders"]),
            "ASP":inr(s["avg_asp"]),"Health":s["health"],"Growth":pct(s["growth_pct"]),
            "Trend":s["trend"]} for s in um]),use_container_width=True,hide_index=True)
    else:
        st.info("No UM→KAM candidates found.")


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    for k,v in [("df_raw",None),("loaded_date",None),("dim_values",{}),
                ("result",None),("result_meta",{}),("share_url",None),
                ("filtered_df",None)]:
        if k not in st.session_state:
            st.session_state[k] = v

    st.title("📉 Sales RCA Dashboard")
    st.caption("Google Drive → CSV by date → rules engine → actionables by owner / BU / category → shareable report link")

    # ── sidebar ────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## Step 1 — Pick a date")
        sel_date = st.date_input("Date", value=date.today()-timedelta(days=1))

        st.markdown("## Step 2 — Load file from Drive")
        load_btn = st.button("🔄  Find & Load File", use_container_width=True)

        dim_choice = None
        val_choice = None
        if st.session_state.dim_values:
            st.markdown("## Step 3 — Slice by")
            st.caption("What dimension do you want insights on?")
            opts = {DIMENSION_LABELS.get(c,c):c
                    for c in DIMENSION_COLS
                    if c in st.session_state.dim_values and st.session_state.dim_values[c]}
            label     = st.selectbox("Analyse by", list(opts.keys()), key="dim_lbl")
            dim_choice= opts[label]
            values    = ["Overall (all BUs)"] + st.session_state.dim_values.get(dim_choice,[])
            val_choice= st.selectbox(f"Value", values, key="val_choice")

            st.markdown("## Step 4 — Run")
        run_btn = st.button("▶  Run RCA Analysis", type="primary",
                            use_container_width=True, disabled=(dim_choice is None))

        if st.session_state.share_url:
            st.divider()
            st.markdown("**Latest shareable report**")
            st.markdown(f"[Open report ↗]({st.session_state.share_url})")
            st.code(st.session_state.share_url, language=None)

    # ── step 2: load ───────────────────────────────────────────────────────────
    if load_btn:
        try:
            svc = get_drive_service()
        except Exception as e:
            st.error(f"Drive connection failed: {e}")
            st.info("Make sure creds.json path is correct and the service account has been shared on the Drive folder.")
            st.stop()

        with st.spinner("Scanning Drive folder…"):
            all_files = list_folder(svc, RAW_FOLDER_ID)
        raw_file = find_file_for_date(all_files, sel_date)
        if not raw_file:
            st.error(f"No CSV found for {sel_date}.")
            all_csv = [f["name"] for f in all_files]
            st.warning(f"Total files seen in folder: {len(all_files)}")
            if all_csv:
                st.write("All files visible to the app:", all_csv)
            else:
                st.error("The app can see 0 files in the Drive folder. This means the service account does not have access to the folder. Please share the folder with the service account email from your creds.json (the client_email field).")
            st.stop()

        size = int(raw_file.get("size",0))
        st.success(f"Found: **{raw_file['name']}** ({size/1e6:.0f} MB)")
        df_raw = download_csv(svc, raw_file["id"], raw_file["name"], size)

        df_norm = normalise_cols(df_raw.copy())
        dim_values = {}
        for col in DIMENSION_COLS:
            if col in df_norm.columns:
                vals = sorted(df_norm[col].dropna().astype(str).str.strip()
                              .replace("",pd.NA).dropna().unique().tolist())
                if vals:
                    dim_values[col] = vals

        st.session_state.df_raw      = df_raw
        st.session_state.loaded_date = sel_date
        st.session_state.dim_values  = dim_values
        st.session_state.result      = None
        st.session_state.share_url   = None
        found_dims = list(dim_values.keys())
        st.success(f"Loaded {len(df_raw):,} rows. Dimensions: {', '.join(found_dims)}")
        st.rerun()

    # ── step 4: run ────────────────────────────────────────────────────────────
    if run_btn and dim_choice and st.session_state.df_raw is not None:
        value    = "OVERALL" if "overall" in val_choice.lower() else val_choice
        date_str = st.session_state.loaded_date.strftime("%Y-%m-%d")
        df_filt  = filter_df(st.session_state.df_raw.copy(), dim_choice, value)
        if df_filt.empty:
            st.error(f"No rows for {dim_choice} = '{value}'"); st.stop()

        with st.spinner(f"Running RCA engine on {len(df_filt):,} rows…"):
            result = run_rca(st.session_state.df_raw, dim_choice, value, date_str)
        if "error" in result:
            st.error(result["error"]); st.stop()

        st.session_state.result      = result
        st.session_state.result_meta = {"dim":dim_choice,"val":val_choice,"date":date_str}
        st.session_state.filtered_df = df_filt

        # upload HTML report to Drive
        try:
            svc   = get_drive_service()
            rfid  = ensure_reports_folder(svc, RAW_FOLDER_ID)
            ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe  = re.sub(r"[^a-zA-Z0-9]","_", f"{dim_choice}_{value}")[:40]
            fname = f"RCA_{safe}_{date_str}_{ts}.html"
            html  = generate_html(result, f"{val_choice} ({dim_choice})", date_str,
                                  datetime.now().strftime("%d %b %Y, %I:%M %p"))
            url   = upload_report(svc, rfid, fname, html)
            st.session_state.share_url = url
        except Exception as e:
            st.warning(f"Analysis done — Drive upload failed: {e}")
        st.rerun()

    # ── render results ─────────────────────────────────────────────────────────
    result = st.session_state.result
    if not result:
        if st.session_state.df_raw is None:
            st.info("👈 Step 1: pick a date and click **Find & Load File**")
        else:
            st.info("👈 Step 3: choose a dimension + value, then click **Run RCA Analysis**")
        return

    meta    = st.session_state.result_meta
    summary = result.get("summary",{})
    rmeta   = result.get("meta",{})

    if st.session_state.share_url:
        st.success("📎 Report saved — share this link:")
        st.markdown(f"### [Open Report — {meta.get('val','')} · {meta.get('date','')} ↗]({st.session_state.share_url})")
        st.code(st.session_state.share_url, language=None)
        st.divider()

    st.markdown(
        f"### {meta.get('val','')} &nbsp;·&nbsp; {meta.get('dim','')} &nbsp;·&nbsp; "
        f"{meta.get('date','')} &nbsp;·&nbsp; **{n(rmeta.get('total_rows'))}** rows &nbsp;·&nbsp; "
        f"top issue: **{summary.get('top_issue','—')}**"
    )

    kpi_row(summary)
    st.divider()

    st.subheader("📋 Rules Causing Order Loss — Ranked by Impact")
    show_rules_summary(result.get("rules_summary",[]))
    st.divider()

    st.subheader("🎯 Priority Actionables")
    show_actionables(result.get("actionables",[]), st.session_state.filtered_df)
    st.divider()

    st.subheader("💸 ASP-Driven Loss")
    show_asp(result.get("asp_issues",[]))
    st.divider()

    st.subheader("📦 Out of Stock")
    show_oos(result.get("oos_issues",[]))
    st.divider()

    st.subheader("🚫 Inactive Listings")
    show_inactive(result.get("inactive_issues",{}))
    st.divider()

    st.subheader("🔬 Innovative Insights")
    show_innovative(result.get("innovative_insights",{}))
    st.divider()

    st.subheader("💡 Recommendations")
    show_recommendations(result.get("recommendations",{}))

    with st.expander("🗂 Raw JSON"):
        st.json(result)


if __name__ == "__main__":
    main()
