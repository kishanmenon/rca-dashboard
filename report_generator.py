"""
report_generator.py
Generates a fully self-contained HTML report from rca_engine.run_rca() output.
Matches the new engine structure exactly: TP/ASP split, DRR columns, new rule names.
"""

import json
from datetime import datetime


# ── formatters ─────────────────────────────────────────────────────────────────
def _n(v):
    try:    return f"{round(float(v)):,}" if v not in (None,"","—") else "—"
    except: return "—"

def _inr(v):
    try:    return f"₹{round(float(v)):,}" if v not in (None,"","—") else "—"
    except: return "—"

def _pct(v):
    try:    x=float(v); return f"{'+'if x>0 else ''}{x:.1f}%"
    except: return "—"

def _drr(v):
    try:    return f"{float(v):.1f}"
    except: return "—"

def _badge(text, color="red"):
    bg = {"red":"#fde8e8","orange":"#fef3e2","green":"#e8f8f0",
          "blue":"#e8f0fe","gray":"#f1f3f4","purple":"#f3e8fd"}
    fg = {"red":"#c0392b","orange":"#e67e22","green":"#27ae60",
          "blue":"#1a73e8","gray":"#5f6368","purple":"#7b2fbe"}
    return (f'<span style="background:{bg.get(color,"#f1f3f4")};color:{fg.get(color,"#5f6368")};'
            f'padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">{text}</span>')


def _table(headers, rows, empty_msg="No issues found."):
    if not rows:
        return f"<p class='empty'>{empty_msg}</p>"
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead>{head}</thead><tbody>{body}</tbody></table>"


def _section(icon, title, content, id_=""):
    return f"""
<div class="section" id="{id_}">
  <div class="section-title">{icon} {title}</div>
  {content}
</div>"""


# ── HTML builder ───────────────────────────────────────────────────────────────
def generate_html(result: dict, label: str, date_str: str, generated_at: str = None) -> str:
    if generated_at is None:
        generated_at = datetime.now().strftime("%d %b %Y, %I:%M %p")

    meta    = result.get("meta", {})
    summary = result.get("summary", {})
    rules   = result.get("rules_summary", [])
    actions = result.get("actionables", [])
    tp_iss  = result.get("tp_hike_issues", [])
    asp_iss = result.get("asp_hike_issues", [])
    oos     = result.get("oos_issues", [])
    inact   = result.get("inactive_issues", {})
    innov   = result.get("innovative_insights", {})
    recs    = result.get("recommendations", {})

    fk  = inact.get("fk_inactivated", {})
    sel = inact.get("seller_inactivated", {})

    # ── summary cards ──────────────────────────────────────────────────────────
    cards = f"""
<div class="cards">
  <div class="card red">
    <div class="card-label">Loss vs L7D (orders/day)</div>
    <div class="card-value">{_n(summary.get('total_loss_vs_l7d'))}</div>
    <div class="card-sub">yesterday vs 6-day avg</div>
  </div>
  <div class="card orange">
    <div class="card-label">Loss vs L30D (orders/day)</div>
    <div class="card-value">{_n(summary.get('total_loss_vs_l30d'))}</div>
    <div class="card-sub">yesterday vs 29-day avg</div>
  </div>
  <div class="card blue">
    <div class="card-label">Yesterday Orders (L1D)</div>
    <div class="card-value">{_n(summary.get('total_l1d_orders'))}</div>
    <div class="card-sub">L7D DRR: {_drr(summary.get('total_drr_l7d'))} &nbsp;|&nbsp; L30D DRR: {_drr(summary.get('total_drr_l30d'))}</div>
  </div>
  <div class="card red">
    <div class="card-label">Monthly Order Impact</div>
    <div class="card-value">{_n(summary.get('monthly_impact'))}</div>
    <div class="card-sub">Top issue: {summary.get('top_issue','—')}</div>
  </div>
  <div class="card blue">
    <div class="card-label">Listings Analysed</div>
    <div class="card-value">{_n(meta.get('total_rows'))}</div>
    <div class="card-sub">{_n(meta.get('oos_count'))} OOS · {_n(meta.get('inactive'))} inactive</div>
  </div>
  <div class="card blue">
    <div class="card-label">Avg Health Score</div>
    <div class="card-value">{_n(summary.get('avg_health_score'))} /100</div>
    <div class="card-sub">{_n(summary.get('sudden_drop_count'))} sudden drops today</div>
  </div>
</div>"""

    # ── rules summary ──────────────────────────────────────────────────────────
    rule_rows = [(
        f"{i+1}",
        f"{r.get('icon','')} {r['rule']}",
        _n(r.get("listings_affected")),
        f"<strong style='color:#c0392b'>{_n(r.get('loss_vs_l7d'))}</strong>",
        f"<strong style='color:#e67e22'>{_n(r.get('loss_vs_l30d'))}</strong>",
        f"{r.get('recovery_pct',0)}%",
        ", ".join(r.get("top_owner_names",[])[:2]) or "—",
    ) for i,r in enumerate(rules)]
    rules_html = _table(
        ["#","Rule","Listings Hit","Loss vs L7D /day","Loss vs L30D /day","Recovery","Top Owners"],
        rule_rows
    )

    # ── rule explainers ────────────────────────────────────────────────────────
    rule_explainers = ""
    for r in rules:
        rule_explainers += f"""
<div style="margin-bottom:16px;padding:12px;background:#f8f9fa;border-radius:8px;border-left:3px solid #dadce0">
  <strong>{r.get('icon','')} {r['rule']}</strong><br>
  <span style="font-size:12px;color:#3c4043"><b>What:</b> {r.get('what','')}</span><br>
  <span style="font-size:11px;color:#5f6368"><b>Detected by:</b> <code>{r.get('how','')}</code></span><br>
  <span style="font-size:12px;color:#1a73e8"><b>Fix:</b> {r.get('fix','')}</span>
</div>"""

    # ── actionables ────────────────────────────────────────────────────────────
    def priority_badge(p):
        p = int(p) if p else 99
        if p <= 3:   return _badge(f"#{p} Critical","red")
        if p <= 8:   return _badge(f"#{p} High","orange")
        return _badge(f"#{p}","gray")

    action_rows = [(
        priority_badge(a.get("priority","")),
        a.get("rule",""),
        str(a.get("product_title",""))[:55],
        str(a.get("display_name",""))[:28],
        a.get("owner",""), a.get("name",""),
        a.get("sell_bu",""), a.get("analytic_category",""),
        _drr(a.get("drr_l1d")), _drr(a.get("drr_l7d")), _drr(a.get("drr_l30d")),
        f"<strong style='color:#c0392b'>{_n(a.get('loss_vs_l7d'))}</strong>",
        f"<strong style='color:#e67e22'>{_n(a.get('loss_vs_l30d'))}</strong>",
        f"<span style='color:#27ae60'>{_n(a.get('recovery_per_day'))}</span>",
        f"<em style='font-size:11px'>{str(a.get('action',''))[:120]}</em>",
    ) for a in actions[:30]]
    actions_html = _table(
        ["#","Rule","Listing","Seller","Owner Type","Owner Name",
         "sell_bu","Category","L1D DRR","L7D DRR","L30D DRR",
         "Loss vs L7D","Loss vs L30D","Recovery/Day","Action"],
        action_rows
    )

    # ── TP hike ────────────────────────────────────────────────────────────────
    tp_rows = [(
        str(r.get("product_title",""))[:50], str(r.get("display_name",""))[:25],
        r.get("owner",""), r.get("name",""),
        _inr(r.get("l30d_tp")), _inr(r.get("l7d_tp")), _inr(r.get("l1d_tp")),
        _pct(r.get("tp_chg_vs_l7d")), _pct(r.get("tp_chg_vs_l30d")),
        _drr(r.get("drr_l1d")), _drr(r.get("drr_l7d")), _drr(r.get("drr_l30d")),
        f"<span style='color:#c0392b'>{_n(r.get('plot_loss_vs_l7d'))}</span>",
        f"<span style='color:#e67e22'>{_n(r.get('plot_loss_vs_l30d'))}</span>",
        ("Both" if r.get("triggered_vs_l7d") and r.get("triggered_vs_l30d")
         else "L7D only" if r.get("triggered_vs_l7d") else "L30D only"),
    ) for r in tp_iss[:20]]
    tp_html = _table(
        ["Listing","Seller","Owner","Owner Name","L30D TP","L7D TP","L1D TP",
         "Δ vs L7D","Δ vs L30D","L1D DRR","L7D DRR","L30D DRR",
         "Loss vs L7D","Loss vs L30D","Triggered"],
        tp_rows
    )

    # ── ASP hike ───────────────────────────────────────────────────────────────
    asp_rows = [(
        str(r.get("product_title",""))[:50], str(r.get("display_name",""))[:25],
        r.get("owner",""), r.get("name",""),
        _inr(r.get("l30d_asp")), _inr(r.get("l7d_asp")), _inr(r.get("l1d_asp")),
        _pct(r.get("asp_chg_vs_l7d")), _pct(r.get("asp_chg_vs_l30d")),
        _drr(r.get("drr_l1d")), _drr(r.get("drr_l7d")), _drr(r.get("drr_l30d")),
        f"<span style='color:#c0392b'>{_n(r.get('plot_loss_vs_l7d'))}</span>",
        f"<span style='color:#e67e22'>{_n(r.get('plot_loss_vs_l30d'))}</span>",
        ("Both" if r.get("triggered_vs_l7d") and r.get("triggered_vs_l30d")
         else "L7D only" if r.get("triggered_vs_l7d") else "L30D only"),
    ) for r in asp_iss[:20]]
    asp_html = _table(
        ["Listing","Seller","Owner","Owner Name","L30D ASP","L7D ASP","L1D ASP",
         "Δ vs L7D","Δ vs L30D","L1D DRR","L7D DRR","L30D DRR",
         "Loss vs L7D","Loss vs L30D","Triggered"],
        asp_rows
    )

    # ── OOS ────────────────────────────────────────────────────────────────────
    oos_rows = [(
        str(r.get("product_title",""))[:50], str(r.get("display_name",""))[:25],
        r.get("owner",""), r.get("name",""),
        _n(r.get("yesterday_atp")),
        _drr(r.get("drr_l1d")), _drr(r.get("drr_l7d")), _drr(r.get("drr_l30d")),
        _n(r.get("l7d_orders")), _n(r.get("l30d_orders")), _inr(r.get("l7d_gmv")),
        f"<strong style='color:#c0392b'>{_n(r.get('plot_loss_vs_l7d'))}</strong>",
        f"<strong style='color:#e67e22'>{_n(r.get('plot_loss_vs_l30d'))}</strong>",
    ) for r in oos[:20]]
    oos_html = _table(
        ["Listing","Seller","Owner","Owner Name","ATP",
         "L1D DRR","L7D DRR","L30D DRR",
         "L7D Orders","L30D Orders","L7D GMV",
         "Loss vs L7D","Loss vs L30D"],
        oos_rows
    )

    # ── inactive ───────────────────────────────────────────────────────────────
    fk_reason_rows = [(
        str(r.get("reason",""))[:50], _n(r.get("count")),
        f"<span style='color:#c0392b'>{_n(r.get('loss_vs_l7d'))}</span>",
        f"<span style='color:#e67e22'>{_n(r.get('loss_vs_l30d'))}</span>",
    ) for r in fk.get("by_reason",[])[:15]]
    fk_reason_html = _table(["Reason","Count","Loss vs L7D","Loss vs L30D"], fk_reason_rows)

    fk_listing_rows = [(
        str(r.get("product_title",""))[:50], str(r.get("display_name",""))[:25],
        str(r.get("latest_deactivation_reason",""))[:40],
        _drr(r.get("drr_l1d")), _drr(r.get("drr_l7d")), _drr(r.get("drr_l30d")),
        f"<span style='color:#c0392b'>{_n(r.get('plot_loss_vs_l7d'))}</span>",
        f"<span style='color:#e67e22'>{_n(r.get('plot_loss_vs_l30d'))}</span>",
    ) for r in fk.get("top_listings",[])[:10]]
    fk_listing_html = _table(
        ["Listing","Seller","Reason","L1D DRR","L7D DRR","L30D DRR","Loss vs L7D","Loss vs L30D"],
        fk_listing_rows
    )

    sel_rows = [(
        str(s.get("seller",""))[:40], _n(s.get("count")),
        f"<span style='color:#e67e22'>{_n(s.get('loss_vs_l7d'))}</span>",
        f"<span style='color:#e67e22'>{_n(s.get('loss_vs_l30d'))}</span>",
    ) for s in sel.get("by_seller",[])[:15]]
    sel_html = _table(["Seller","Count","Loss vs L7D","Loss vs L30D"], sel_rows)

    # ── innovative insights ────────────────────────────────────────────────────
    mp  = innov.get("multiple_problems_at_once",{})
    hds = innov.get("high_dependency_sellers",{})
    sod = innov.get("sudden_order_drop",{})
    ds  = innov.get("dead_stock_no_demand",{})

    mp_rows = [(
        str(i["product_title"])[:50], str(i["display_name"])[:25],
        i.get("name",""), i["problems"], str(i["issue_count"]),
        _drr(i.get("drr_l1d")), _drr(i.get("drr_l7d")), _drr(i.get("drr_l30d")),
        f"<span style='color:#c0392b'>{_n(i['loss_vs_l7d'])}</span>",
        str(i["health_score"]),
    ) for i in mp.get("listings",[])[:15]]
    mp_html = _table(
        ["Listing","Seller","Owner","Problems","#Issues",
         "L1D DRR","L7D DRR","L30D DRR","Loss vs L7D","Health"],
        mp_rows
    )

    hds_rows = [(
        str(s["seller"])[:35], _n(s["listings"]),
        _drr(s["orders_per_day"]), _n(s["oos_count"]), _n(s["risk_score"]),
    ) for s in hds.get("top_sellers",[])[:10]]
    hds_html = _table(["Seller","Listings","Orders/Day (L7D DRR)","OOS Listings","Risk Score"], hds_rows)

    sod_rows = [(
        str(i["product_title"])[:50], str(i["display_name"])[:25],
        i.get("name",""),
        _drr(i.get("drr_l1d")), _drr(i.get("drr_l7d")), _drr(i.get("drr_l30d")),
        str(i["health_score"]),
    ) for i in sod.get("listings",[])[:12]]
    sod_html = _table(["Listing","Seller","Owner","L1D DRR","L7D DRR","L30D DRR","Health"], sod_rows)

    ds_rows = [(
        str(i["product_title"])[:50], str(i["display_name"])[:25],
        _n(i["yesterday_atp"]),
        _drr(i.get("drr_l1d")), _drr(i.get("drr_l7d")), _drr(i.get("drr_l30d")),
        _inr(i["l7d_asp"]),
    ) for i in ds.get("listings",[])[:12]]
    ds_html = _table(["Listing","Seller","ATP","L1D DRR","L7D DRR","L30D DRR","ASP"], ds_rows)

    # ── UM → KAM ──────────────────────────────────────────────────────────────
    um = recs.get("um_to_kam",[])
    um_rows = [(
        str(s["seller"])[:35],
        _drr(s.get("drr_l30d")), _drr(s.get("drr_l7d")), _drr(s.get("drr_l1d")),
        _pct(s.get("growth_l30d_to_l1d")), _pct(s.get("growth_l7d_to_l1d")),
        s.get("trend",""),
        "✅ Yes" if s.get("continuously_growing") else "—",
        _inr(s.get("avg_asp")), str(s.get("health","")),
    ) for s in um[:10]]
    um_html = _table(
        ["Seller","L30D DRR","L7D DRR","L1D DRR",
         "Growth L30D→L1D","Growth L7D→L1D","Trend",
         "Continuously Growing","ASP","Health"],
        um_rows
    )

    # ── Google Trends ──────────────────────────────────────────────────────────
    trends = recs.get("search_trends",[])
    trend_rows = [(
        str(t.get("search_keyword",""))[:30],
        str(t.get("product_title",""))[:45],
        _n(t.get("google_trend_score")),
        _drr(t.get("drr_l1d")), _drr(t.get("drr_l7d")), _drr(t.get("drr_l30d")),
        _n(t.get("total_atp")),
        "⚠️ Restock" if t.get("demand_supply_gap") else "OK",
        str(t.get("action",""))[:80],
    ) for t in trends[:15]]
    trends_html = _table(
        ["Keyword","Matched Product","Trend Score","L1D DRR","L7D DRR","L30D DRR",
         "ATP","Gap","Action"],
        trend_rows
    ) if trends else "<p class='empty'>Add pytrends to requirements.txt to enable Google Trends.</p>"

    # ── chart data ─────────────────────────────────────────────────────────────
    chart_data = json.dumps({
        "rule_labels": [r["rule"] for r in rules[:6]],
        "loss_l7d":  [r.get("loss_vs_l7d",0)  for r in rules[:6]],
        "loss_l30d": [r.get("loss_vs_l30d",0) for r in rules[:6]],
        "action_labels": [a.get("product_title","")[:25] for a in actions[:8]],
        "action_l7d":  [a.get("loss_vs_l7d",0)  for a in actions[:8]],
        "action_l30d": [a.get("loss_vs_l30d",0) for a in actions[:8]],
    })

    # ── full HTML ──────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>RCA Report — {label} — {date_str}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8f9fa;color:#1a1a2e;font-size:13px}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);color:white;padding:28px 40px}}
.header h1{{font-size:22px;font-weight:700;margin-bottom:4px}}
.header .meta{{font-size:12px;opacity:.7;margin-top:6px}}
.badge{{display:inline-block;background:rgba(255,255,255,.15);padding:3px 10px;border-radius:12px;font-size:11px;margin-left:8px}}
.toc{{background:#fff;border-bottom:1px solid #e8eaed;padding:10px 40px;display:flex;gap:16px;flex-wrap:wrap}}
.toc a{{color:#1a73e8;text-decoration:none;font-size:12px;white-space:nowrap}}
.body{{max-width:1400px;margin:0 auto;padding:28px 40px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:28px}}
.card{{background:white;border-radius:10px;padding:16px;border-left:4px solid #e8eaed;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.card.red{{border-color:#c0392b}}.card.orange{{border-color:#e67e22}}.card.blue{{border-color:#1a73e8}}
.card-label{{font-size:11px;color:#5f6368;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.card-value{{font-size:26px;font-weight:700;line-height:1;margin-bottom:4px}}
.card.red .card-value{{color:#c0392b}}.card.orange .card-value{{color:#e67e22}}.card.blue .card-value{{color:#1a73e8}}
.card-sub{{font-size:11px;color:#80868b}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:28px}}
.chart-box{{background:white;border-radius:10px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.chart-box h3{{font-size:13px;color:#5f6368;margin-bottom:14px;font-weight:600}}
.section{{background:white;border-radius:10px;padding:22px 24px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.section-title{{font-size:15px;font-weight:700;margin-bottom:14px}}
.subsection{{margin-top:18px}}
.subsection-title{{font-size:12px;font-weight:600;color:#5f6368;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #f1f3f4}}
.tabs{{display:flex;gap:0;border-bottom:1px solid #e8eaed;margin-bottom:14px}}
.tab{{padding:7px 16px;border-bottom:2px solid transparent;font-size:13px;color:#5f6368;font-weight:500}}
.tab.active{{border-bottom-color:#1a1a2e;color:#1a1a2e}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:8px 10px;border-bottom:2px solid #f1f3f4;color:#5f6368;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.3px;white-space:nowrap}}
td{{padding:8px 10px;border-bottom:1px solid #f8f9fa;vertical-align:top;white-space:nowrap}}
tr:hover td{{background:#f8f9fa}}
.empty{{color:#80868b;font-style:italic;padding:12px 0}}
.info-bar{{background:#e8f0fe;border-left:4px solid #1a73e8;padding:10px 14px;border-radius:0 6px 6px 0;margin-bottom:14px;font-size:12px;color:#1a73e8}}
.warn-bar{{background:#fef3e2;border-left:4px solid #e67e22;padding:10px 14px;border-radius:0 6px 6px 0;margin-bottom:14px;font-size:12px;color:#e67e22}}
.footer{{text-align:center;padding:28px;color:#80868b;font-size:11px}}
code{{background:#f1f3f4;padding:1px 5px;border-radius:3px;font-size:11px}}
@media(max-width:700px){{.body{{padding:16px}}.charts{{grid-template-columns:1fr}}.cards{{grid-template-columns:1fr 1fr}}}}
</style>
</head>
<body>

<div class="header">
  <h1>📉 Sales RCA Report
    <span class="badge">{label}</span>
    <span class="badge">{date_str}</span>
  </h1>
  <div class="meta">Generated {generated_at} &nbsp;·&nbsp; {_n(meta.get('total_rows'))} rows analysed &nbsp;·&nbsp; Window sizes: L30D = 29 days · L7D = 6 days</div>
</div>

<div class="toc">
  <a href="#summary">📊 Summary</a>
  <a href="#rules">📋 Rules</a>
  <a href="#actionables">🎯 Actionables</a>
  <a href="#price">💸 Price Hike</a>
  <a href="#oos">📦 OOS</a>
  <a href="#inactive">🚫 Inactive</a>
  <a href="#insights">🔬 Insights</a>
  <a href="#recs">💡 Recommendations</a>
</div>

<div class="body">

  <div id="summary">{cards}</div>

  <div class="charts">
    <div class="chart-box">
      <h3>Order Loss by Rule — L7D vs L30D (orders/day)</h3>
      <div style="position:relative;height:220px"><canvas id="ruleChart"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Top 8 Actionables — Loss vs Recovery (orders/day)</h3>
      <div style="position:relative;height:220px"><canvas id="actionChart"></canvas></div>
    </div>
  </div>

  {_section("📋","Rules Causing Order Loss — Ranked by Impact", """
    <div class="info-bar">Sorted by orders lost yesterday vs L7D run rate. Loss shown as 0 where that window was not triggered.</div>
    {rules_html}
    <div class="subsection">
      <div class="subsection-title">Rule Definitions</div>
      {rule_explainers}
    </div>
  """, "rules")}

  {_section("🎯","Priority Actionables", """
    <div class="info-bar">Precedence: Deactivated by FK → Seller Switched Off → Out of Stock → TP Price Hike → ASP Price Hike. Each listing assigned one rule.</div>
    {actions_html}
  """, "actionables")}

  {_section("💸","Price Hike Analysis", """
    <div class="subsection">
      <div class="subsection-title">🏷️ TP Price Hike (Transaction Price) — {_n(meta.get('tp_hike_count'))} listings</div>
      <div class="info-bar">L1D TP &gt; L7D/L30D TP × 1.05 AND orders dropped. Loss = 0 where window was not triggered.</div>
      {tp_html}
    </div>
    <div class="subsection" style="margin-top:24px">
      <div class="subsection-title">💸 ASP Price Hike (Avg Selling Price) — {_n(meta.get('asp_hike_count'))} listings</div>
      <div class="info-bar">L1D ASP &gt; L7D/L30D ASP × 1.05 AND orders dropped.</div>
      {asp_html}
    </div>
  """, "price")}

  {_section("📦","Out of Stock (ATP = 0, Active)", """
    <div class="info-bar">Both L7D and L30D losses shown — OOS impacts both windows equally.</div>
    {oos_html}
  """, "oos")}

  {_section("🚫","Inactive Listings", """
    <div class="subsection">
      <div class="subsection-title">🔴 Deactivated by Flipkart — {_n(fk.get('total_count'))} listings · L7D loss: {_n(fk.get('total_loss_vs_l7d'))}/day · L30D loss: {_n(fk.get('total_loss_vs_l30d'))}/day</div>
      <div style="margin-bottom:10px">{fk_reason_html}</div>
      <div class="subsection-title" style="margin-top:14px">Top impacted listings</div>
      {fk_listing_html}
    </div>
    <div class="subsection" style="margin-top:24px">
      <div class="subsection-title">🟡 Seller Switched Off — {_n(sel.get('total_count'))} listings · L7D loss: {_n(sel.get('total_loss_vs_l7d'))}/day · L30D loss: {_n(sel.get('total_loss_vs_l30d'))}/day</div>
      {sel_html}
    </div>
  """, "inactive")}

  {_section("🔬","Additional Insights", """
    <div class="subsection">
      <div class="subsection-title">⚡ Multiple Problems at Once — {_n(mp.get('count'))} listings · {_n(mp.get('total_loss_vs_l7d'))} orders/day L7D loss</div>
      <div class="warn-bar">These listings have 2+ issues simultaneously — losing orders from multiple directions. Highest priority.</div>
      {mp_html}
    </div>
    <div class="subsection" style="margin-top:20px">
      <div class="subsection-title">🌊 High Dependency Sellers</div>
      <div class="info-bar">If these sellers go inactive, you lose their full order run rate in one shot.</div>
      {hds_html}
    </div>
    <div class="subsection" style="margin-top:20px">
      <div class="subsection-title">📉 Sudden Order Drop — {_n(sod.get('count'))} listings</div>
      <div class="warn-bar">L1D orders fell to &lt;15% of 30-day DRR — something acute happened yesterday.</div>
      {sod_html}
    </div>
    <div class="subsection" style="margin-top:20px">
      <div class="subsection-title">🧊 Dead Stock — No Demand — {_n(ds.get('count'))} listings</div>
      <div class="info-bar">Stock available, but fewer than 0.1 orders/day over L7D. Trapped working capital.</div>
      {ds_html}
    </div>
  """, "insights")}

  {_section("💡","Recommendations", """
    <div class="subsection">
      <div class="subsection-title">⬆️ UM → KAM Upgrade Candidates</div>
      <div class="info-bar">Qualification: high L30D volume, positive growth trend. Growth % = (DRR change / base DRR) × 100. ✅ = L1D DRR &gt; L7D DRR &gt; L30D DRR continuously.</div>
      {um_html}
    </div>
    <div class="subsection" style="margin-top:20px">
      <div class="subsection-title">🔍 Google Search Trends vs Order Trends</div>
      <div class="info-bar">Trend score 0–100 (India, last 7 days). ⚠️ = High search interest but low stock — restock urgently.</div>
      {trends_html}
    </div>
  """, "recs")}

</div>

<div class="footer">RCA Report · {label} · {date_str} · Generated {generated_at}</div>

<script>
const d = {chart_data};
new Chart(document.getElementById('ruleChart'),{{
  type:'bar',
  data:{{
    labels: d.rule_labels,
    datasets:[
      {{label:'Loss vs L7D',  data:d.loss_l7d,  backgroundColor:'rgba(192,57,43,.7)',  borderWidth:0}},
      {{label:'Loss vs L30D', data:d.loss_l30d, backgroundColor:'rgba(230,126,34,.7)', borderWidth:0}},
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    plugins:{{legend:{{position:'top',labels:{{font:{{size:11}}}}}}}},
    scales:{{x:{{ticks:{{font:{{size:9}}}}}},y:{{ticks:{{font:{{size:10}}}}}}}}
  }}
}});
new Chart(document.getElementById('actionChart'),{{
  type:'bar',
  data:{{
    labels: d.action_labels,
    datasets:[
      {{label:'Loss vs L7D',  data:d.action_l7d,  backgroundColor:'rgba(192,57,43,.7)',  borderWidth:0}},
      {{label:'Loss vs L30D', data:d.action_l30d, backgroundColor:'rgba(230,126,34,.7)', borderWidth:0}},
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,indexAxis:'y',
    plugins:{{legend:{{position:'top',labels:{{font:{{size:11}}}}}}}},
    scales:{{x:{{ticks:{{font:{{size:10}}}}}},y:{{ticks:{{font:{{size:9}}}}}}}}
  }}
}});
</script>
</body>
</html>"""
