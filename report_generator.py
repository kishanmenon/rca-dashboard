"""
report_generator.py — builds self-contained HTML from rca_engine output.
NO nested f-strings anywhere. Every section pre-built as a variable.
"""
import json
from datetime import datetime


def _n(v):
    try:    return f"{round(float(v)):,}" if v not in (None,"","—") else "—"
    except: return "—"

def _inr(v):
    try:    return f"&#8377;{round(float(v)):,}" if v not in (None,"","—") else "—"
    except: return "—"

def _pct(v):
    try:
        x = float(v)
        sign = "+" if x > 0 else ""
        return f"{sign}{x:.1f}%"
    except: return "—"

def _drr(v):
    try:    return f"{float(v):.1f}"
    except: return "—"

def _badge(text, color="red"):
    colors = {
        "red":    ("#fde8e8","#c0392b"),
        "orange": ("#fef3e2","#e67e22"),
        "green":  ("#e8f8f0","#27ae60"),
        "blue":   ("#e8f0fe","#1a73e8"),
        "gray":   ("#f1f3f4","#5f6368"),
    }
    bg, fg = colors.get(color, colors["gray"])
    return (
        '<span style="background:' + bg + ';color:' + fg + ';'
        'padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">'
        + text + '</span>'
    )

def _table(headers, rows, empty="No issues found."):
    if not rows:
        return "<p class='empty'>" + empty + "</p>"
    head = "<tr>" + "".join("<th>" + str(h) + "</th>" for h in headers) + "</tr>"
    body = "".join(
        "<tr>" + "".join("<td>" + str(c) + "</td>" for c in row) + "</tr>"
        for row in rows
    )
    return "<table><thead>" + head + "</thead><tbody>" + body + "</tbody></table>"

def _sec(icon, title, content, anchor=""):
    return (
        '<div class="section" id="' + anchor + '">'
        '<div class="section-title">' + icon + " " + title + "</div>"
        + content +
        "</div>"
    )

def _info(text):
    return '<div class="info-bar">' + text + "</div>"

def _warn(text):
    return '<div class="warn-bar">' + text + "</div>"

def _sub(title):
    return '<div class="subsection-title">' + title + "</div>"


# ── CSS ────────────────────────────────────────────────────────────────────────
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#f8f9fa;color:#1a1a2e;font-size:13px}
.header{background:linear-gradient(135deg,#1a1a2e,#16213e);
        color:white;padding:28px 40px}
.header h1{font-size:22px;font-weight:700;margin-bottom:4px}
.header .meta{font-size:12px;opacity:.7;margin-top:6px}
.badge{display:inline-block;background:rgba(255,255,255,.15);
       padding:3px 10px;border-radius:12px;font-size:11px;margin-left:8px}
.toc{background:#fff;border-bottom:1px solid #e8eaed;
     padding:10px 40px;display:flex;gap:16px;flex-wrap:wrap}
.toc a{color:#1a73e8;text-decoration:none;font-size:12px;white-space:nowrap}
.body{max-width:1400px;margin:0 auto;padding:28px 40px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
       gap:14px;margin-bottom:28px}
.card{background:white;border-radius:10px;padding:16px;
      border-left:4px solid #e8eaed;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card.red{border-color:#c0392b}.card.orange{border-color:#e67e22}
.card.blue{border-color:#1a73e8}
.card-label{font-size:11px;color:#5f6368;text-transform:uppercase;
            letter-spacing:.5px;margin-bottom:6px}
.card-value{font-size:26px;font-weight:700;line-height:1;margin-bottom:4px}
.card.red .card-value{color:#c0392b}
.card.orange .card-value{color:#e67e22}
.card.blue .card-value{color:#1a73e8}
.card-sub{font-size:11px;color:#80868b}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:28px}
.chart-box{background:white;border-radius:10px;padding:20px;
           box-shadow:0 1px 3px rgba(0,0,0,.06)}
.chart-box h3{font-size:13px;color:#5f6368;margin-bottom:14px;font-weight:600}
.section{background:white;border-radius:10px;padding:22px 24px;
         margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.section-title{font-size:15px;font-weight:700;margin-bottom:14px}
.subsection{margin-top:18px}
.subsection-title{font-size:12px;font-weight:600;color:#5f6368;
                  text-transform:uppercase;letter-spacing:.5px;
                  margin-bottom:10px;padding-bottom:6px;
                  border-bottom:1px solid #f1f3f4}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid #f1f3f4;
   color:#5f6368;font-weight:600;font-size:11px;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #f8f9fa;
   vertical-align:top;white-space:nowrap}
tr:hover td{background:#f8f9fa}
.empty{color:#80868b;font-style:italic;padding:12px 0}
.info-bar{background:#e8f0fe;border-left:4px solid #1a73e8;
          padding:10px 14px;border-radius:0 6px 6px 0;
          margin-bottom:14px;font-size:12px;color:#1a73e8}
.warn-bar{background:#fef3e2;border-left:4px solid #e67e22;
          padding:10px 14px;border-radius:0 6px 6px 0;
          margin-bottom:14px;font-size:12px;color:#e67e22}
.rule-card{margin-bottom:14px;padding:12px;background:#f8f9fa;
           border-radius:8px;border-left:3px solid #dadce0}
.footer{text-align:center;padding:28px;color:#80868b;font-size:11px}
code{background:#f1f3f4;padding:1px 5px;border-radius:3px;font-size:11px}
@media(max-width:700px){
  .body{padding:16px}.charts{grid-template-columns:1fr}
  .cards{grid-template-columns:1fr 1fr}
}
"""


def generate_html(result: dict, label: str, date_str: str,
                  generated_at: str = None) -> str:

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
    fk      = inact.get("fk_inactivated", {})
    sel_sec = inact.get("seller_inactivated", {})
    mp      = innov.get("multiple_problems_at_once", {})
    hds     = innov.get("high_dependency_sellers", {})
    sod     = innov.get("sudden_order_drop", {})
    ds      = innov.get("dead_stock_no_demand", {})
    um      = recs.get("um_to_kam", [])
    trends  = recs.get("search_trends", [])

    # ── summary cards ──────────────────────────────────────────────────────────
    cards_html = (
        '<div class="cards">'
        '<div class="card red">'
          '<div class="card-label">Loss vs L7D (orders/day)</div>'
          '<div class="card-value">' + _n(summary.get("total_loss_vs_l7d")) + '</div>'
          '<div class="card-sub">yesterday vs 6-day avg</div>'
        '</div>'
        '<div class="card orange">'
          '<div class="card-label">Loss vs L30D (orders/day)</div>'
          '<div class="card-value">' + _n(summary.get("total_loss_vs_l30d")) + '</div>'
          '<div class="card-sub">yesterday vs 29-day avg</div>'
        '</div>'
        '<div class="card blue">'
          '<div class="card-label">Yesterday Orders (L1D)</div>'
          '<div class="card-value">' + _n(summary.get("total_l1d_orders")) + '</div>'
          '<div class="card-sub">L7D DRR: ' + _drr(summary.get("total_drr_l7d")) +
          ' &nbsp;|&nbsp; L30D DRR: ' + _drr(summary.get("total_drr_l30d")) + '</div>'
        '</div>'
        '<div class="card red">'
          '<div class="card-label">Monthly Order Impact</div>'
          '<div class="card-value">' + _n(summary.get("monthly_impact")) + '</div>'
          '<div class="card-sub">Top issue: ' + str(summary.get("top_issue","—")) + '</div>'
        '</div>'
        '<div class="card blue">'
          '<div class="card-label">Listings Analysed</div>'
          '<div class="card-value">' + _n(meta.get("total_rows")) + '</div>'
          '<div class="card-sub">' + _n(meta.get("oos_count")) +
          ' OOS &nbsp;·&nbsp; ' + _n(meta.get("inactive")) + ' inactive</div>'
        '</div>'
        '<div class="card blue">'
          '<div class="card-label">Avg Health Score</div>'
          '<div class="card-value">' + _n(summary.get("avg_health_score")) + ' /100</div>'
          '<div class="card-sub">' + _n(summary.get("sudden_drop_count")) +
          ' sudden drops today</div>'
        '</div>'
        '</div>'
    )

    # ── rules table ────────────────────────────────────────────────────────────
    rule_rows = []
    for i, r in enumerate(rules):
        rule_rows.append((
            str(i+1),
            r.get("icon","") + " " + r["rule"],
            _n(r.get("listings_affected")),
            '<strong style="color:#c0392b">' + _n(r.get("loss_vs_l7d")) + "</strong>",
            '<strong style="color:#e67e22">' + _n(r.get("loss_vs_l30d")) + "</strong>",
            str(r.get("recovery_pct",0)) + "%",
            ", ".join(r.get("top_owner_names",[])[:2]) or "—",
        ))
    rules_table = _table(
        ["#","Rule","Listings Hit","Loss vs L7D /day","Loss vs L30D /day",
         "Recovery","Top Owners"],
        rule_rows
    )

    rule_explainers = ""
    for r in rules:
        rule_explainers += (
            '<div class="rule-card">'
            '<strong>' + r.get("icon","") + " " + r["rule"] + "</strong><br>"
            '<span style="font-size:12px"><b>What:</b> ' + r.get("what","") + "</span><br>"
            '<span style="font-size:11px;color:#5f6368"><b>Detected by:</b> <code>'
            + r.get("how","") + "</code></span><br>"
            '<span style="font-size:12px;color:#1a73e8"><b>Fix:</b> '
            + r.get("fix","") + "</span>"
            "</div>"
        )

    rules_content = (
        _info("Sorted by orders lost yesterday vs L7D run rate.") +
        rules_table +
        '<div class="subsection">' + _sub("Rule Definitions") + rule_explainers + "</div>"
    )

    # ── actionables ────────────────────────────────────────────────────────────
    def pri_badge(p):
        try: p = int(p)
        except: p = 99
        if p <= 3:  return _badge("#" + str(p) + " Critical", "red")
        if p <= 8:  return _badge("#" + str(p) + " High", "orange")
        return _badge("#" + str(p), "gray")

    action_rows = []
    for a in actions[:30]:
        action_rows.append((
            pri_badge(a.get("priority","")),
            a.get("rule",""),
            str(a.get("product_title",""))[:55],
            str(a.get("display_name",""))[:28],
            a.get("owner",""),
            a.get("name",""),
            a.get("sell_bu",""),
            a.get("analytic_category",""),
            _drr(a.get("drr_l1d")),
            _drr(a.get("drr_l7d")),
            _drr(a.get("drr_l30d")),
            '<strong style="color:#c0392b">' + _n(a.get("loss_vs_l7d")) + "</strong>",
            '<strong style="color:#e67e22">' + _n(a.get("loss_vs_l30d")) + "</strong>",
            '<span style="color:#27ae60">' + _n(a.get("recovery_per_day")) + "</span>",
            '<em style="font-size:11px">' + str(a.get("action",""))[:120] + "</em>",
        ))
    actions_content = (
        _info("Precedence: Deactivated by FK &#8594; Seller Switched Off &#8594; OOS &#8594; TP Hike &#8594; ASP Hike") +
        _table(
            ["#","Rule","Listing","Seller","Owner Type","Owner Name","sell_bu","Category",
             "L1D DRR","L7D DRR","L30D DRR","Loss vs L7D","Loss vs L30D",
             "Recovery/Day","Action"],
            action_rows
        )
    )

    # ── price hike ─────────────────────────────────────────────────────────────
    def price_rows(issues, pt):
        rows = []
        for r in issues[:20]:
            rows.append((
                str(r.get("product_title",""))[:50],
                str(r.get("display_name",""))[:25],
                r.get("owner",""), r.get("name",""),
                _inr(r.get("l30d_" + pt)),
                _inr(r.get("l7d_"  + pt)),
                _inr(r.get("l1d_"  + pt)),
                _pct(r.get(pt + "_chg_vs_l7d")),
                _pct(r.get(pt + "_chg_vs_l30d")),
                _drr(r.get("drr_l1d")),
                _drr(r.get("drr_l7d")),
                _drr(r.get("drr_l30d")),
                '<span style="color:#c0392b">' + _n(r.get("plot_loss_vs_l7d"))  + "</span>",
                '<span style="color:#e67e22">' + _n(r.get("plot_loss_vs_l30d")) + "</span>",
                ("Both" if r.get("triggered_vs_l7d") and r.get("triggered_vs_l30d")
                 else "L7D only" if r.get("triggered_vs_l7d") else "L30D only"),
            ))
        return _table(
            ["Listing","Seller","Owner","Owner Name",
             "L30D","L7D","L1D","&#916; vs L7D","&#916; vs L30D",
             "L1D DRR","L7D DRR","L30D DRR",
             "Loss vs L7D","Loss vs L30D","Triggered"],
            rows
        )

    tp_cnt  = _n(meta.get("tp_hike_count"))
    asp_cnt = _n(meta.get("asp_hike_count"))
    price_content = (
        '<div class="subsection">'
        + _sub("&#127991; TP Price Hike (Transaction Price) — " + tp_cnt + " listings")
        + _info("L1D TP &gt; L7D/L30D TP x 1.05 AND orders dropped. Loss = 0 where window not triggered.")
        + price_rows(tp_iss, "tp")
        + "</div>"
        '<div class="subsection" style="margin-top:24px">'
        + _sub("&#128184; ASP Price Hike (Avg Selling Price) — " + asp_cnt + " listings")
        + _info("L1D ASP &gt; L7D/L30D ASP x 1.05 AND orders dropped.")
        + price_rows(asp_iss, "asp")
        + "</div>"
    )

    # ── OOS ────────────────────────────────────────────────────────────────────
    oos_rows = []
    for r in oos[:20]:
        oos_rows.append((
            str(r.get("product_title",""))[:50],
            str(r.get("display_name",""))[:25],
            r.get("owner",""), r.get("name",""),
            _n(r.get("yesterday_atp")),
            _drr(r.get("drr_l1d")), _drr(r.get("drr_l7d")), _drr(r.get("drr_l30d")),
            _n(r.get("l7d_orders")), _n(r.get("l30d_orders")), _inr(r.get("l7d_gmv")),
            '<strong style="color:#c0392b">' + _n(r.get("plot_loss_vs_l7d"))  + "</strong>",
            '<strong style="color:#e67e22">' + _n(r.get("plot_loss_vs_l30d")) + "</strong>",
        ))
    oos_content = (
        _info("Both L7D and L30D losses shown — OOS impacts both windows equally.") +
        _table(
            ["Listing","Seller","Owner","Owner Name","ATP",
             "L1D DRR","L7D DRR","L30D DRR",
             "L7D Orders","L30D Orders","L7D GMV",
             "Loss vs L7D","Loss vs L30D"],
            oos_rows
        )
    )

    # ── inactive ───────────────────────────────────────────────────────────────
    fk_reason_rows = []
    for r in fk.get("by_reason",[])[:15]:
        fk_reason_rows.append((
            str(r.get("reason",""))[:50], _n(r.get("count")),
            '<span style="color:#c0392b">' + _n(r.get("loss_vs_l7d"))  + "</span>",
            '<span style="color:#e67e22">' + _n(r.get("loss_vs_l30d")) + "</span>",
        ))
    fk_listing_rows = []
    for r in fk.get("top_listings",[])[:10]:
        fk_listing_rows.append((
            str(r.get("product_title",""))[:50],
            str(r.get("display_name",""))[:25],
            str(r.get("latest_deactivation_reason",""))[:40],
            _drr(r.get("drr_l1d")), _drr(r.get("drr_l7d")), _drr(r.get("drr_l30d")),
            '<span style="color:#c0392b">' + _n(r.get("plot_loss_vs_l7d"))  + "</span>",
            '<span style="color:#e67e22">' + _n(r.get("plot_loss_vs_l30d")) + "</span>",
        ))
    sel_rows = []
    for s in sel_sec.get("by_seller",[])[:15]:
        sel_rows.append((
            str(s.get("seller",""))[:40], _n(s.get("count")),
            '<span style="color:#e67e22">' + _n(s.get("loss_vs_l7d"))  + "</span>",
            '<span style="color:#e67e22">' + _n(s.get("loss_vs_l30d")) + "</span>",
        ))

    fk_total_l7d  = _n(fk.get("total_loss_vs_l7d"))
    fk_total_l30d = _n(fk.get("total_loss_vs_l30d"))
    fk_count      = _n(fk.get("total_count"))
    sel_total_l7d  = _n(sel_sec.get("total_loss_vs_l7d"))
    sel_total_l30d = _n(sel_sec.get("total_loss_vs_l30d"))
    sel_count      = _n(sel_sec.get("total_count"))

    inactive_content = (
        '<div class="subsection">'
        + _sub("&#128308; Deactivated by Flipkart — " + fk_count +
               " listings | L7D loss: " + fk_total_l7d +
               "/day | L30D loss: " + fk_total_l30d + "/day")
        + _table(["Reason","Count","Loss vs L7D","Loss vs L30D"], fk_reason_rows)
        + '<div style="margin-top:10px">'
        + _sub("Top impacted listings")
        + _table(["Listing","Seller","Reason","L1D DRR","L7D DRR","L30D DRR",
                  "Loss vs L7D","Loss vs L30D"], fk_listing_rows)
        + "</div></div>"
        '<div class="subsection" style="margin-top:24px">'
        + _sub("&#128993; Seller Switched Off — " + sel_count +
               " listings | L7D loss: " + sel_total_l7d +
               "/day | L30D loss: " + sel_total_l30d + "/day")
        + _table(["Seller","Count","Loss vs L7D","Loss vs L30D"], sel_rows)
        + "</div>"
    )

    # ── innovative insights ────────────────────────────────────────────────────
    mp_rows = []
    for i in mp.get("listings",[])[:15]:
        mp_rows.append((
            str(i["product_title"])[:50], str(i["display_name"])[:25],
            i.get("name",""), i["problems"], str(i["issue_count"]),
            _drr(i.get("drr_l1d")), _drr(i.get("drr_l7d")), _drr(i.get("drr_l30d")),
            '<span style="color:#c0392b">' + _n(i["loss_vs_l7d"]) + "</span>",
            str(i["health_score"]),
        ))
    hds_rows = []
    for s in hds.get("top_sellers",[])[:10]:
        hds_rows.append((
            str(s["seller"])[:35], _n(s["listings"]),
            _drr(s["orders_per_day"]), _n(s["oos_count"]), _n(s["risk_score"]),
        ))
    sod_rows = []
    for i in sod.get("listings",[])[:12]:
        sod_rows.append((
            str(i["product_title"])[:50], str(i["display_name"])[:25],
            i.get("name",""),
            _drr(i.get("drr_l1d")), _drr(i.get("drr_l7d")), _drr(i.get("drr_l30d")),
            str(i["health_score"]),
        ))
    ds_rows = []
    for i in ds.get("listings",[])[:12]:
        ds_rows.append((
            str(i["product_title"])[:50], str(i["display_name"])[:25],
            _n(i["yesterday_atp"]),
            _drr(i.get("drr_l1d")), _drr(i.get("drr_l7d")), _drr(i.get("drr_l30d")),
            _inr(i["l7d_asp"]),
        ))

    insights_content = (
        '<div class="subsection">'
        + _sub("&#9889; Multiple Problems at Once — " +
               _n(mp.get("count")) + " listings | " +
               _n(mp.get("total_loss_vs_l7d")) + " orders/day L7D loss")
        + _warn("2+ issues simultaneously — losing orders from multiple directions. Highest priority.")
        + _table(["Listing","Seller","Owner","Problems","# Issues",
                  "L1D DRR","L7D DRR","L30D DRR","Loss vs L7D","Health"], mp_rows)
        + "</div>"
        '<div class="subsection" style="margin-top:20px">'
        + _sub("&#127754; High Dependency Sellers")
        + _info("If these sellers go inactive, you lose their full order run rate in one shot.")
        + _table(["Seller","Listings","Orders/Day (L7D DRR)","OOS Listings","Risk Score"], hds_rows)
        + "</div>"
        '<div class="subsection" style="margin-top:20px">'
        + _sub("&#128201; Sudden Order Drop — " + _n(sod.get("count")) + " listings")
        + _warn("L1D orders fell to less than 15% of 30-day DRR — something acute happened.")
        + _table(["Listing","Seller","Owner","L1D DRR","L7D DRR","L30D DRR","Health"], sod_rows)
        + "</div>"
        '<div class="subsection" style="margin-top:20px">'
        + _sub("&#129398; Dead Stock — No Demand — " + _n(ds.get("count")) + " listings")
        + _info("Stock available, fewer than 0.1 orders/day over L7D. Trapped working capital.")
        + _table(["Listing","Seller","ATP","L1D DRR","L7D DRR","L30D DRR","ASP"], ds_rows)
        + "</div>"
    )

    # ── recommendations ────────────────────────────────────────────────────────
    um_rows = []
    for s in um[:10]:
        um_rows.append((
            str(s["seller"])[:35],
            _drr(s.get("drr_l30d")), _drr(s.get("drr_l7d")), _drr(s.get("drr_l1d")),
            _pct(s.get("growth_l30d_to_l1d")), _pct(s.get("growth_l7d_to_l1d")),
            s.get("trend",""),
            "&#9989; Yes" if s.get("continuously_growing") else "—",
            _inr(s.get("avg_asp")), str(s.get("health","")),
        ))
    trend_rows = []
    for t in trends[:15]:
        trend_rows.append((
            str(t.get("search_keyword",""))[:30],
            str(t.get("product_title",""))[:45],
            _n(t.get("google_trend_score")),
            _drr(t.get("drr_l1d")), _drr(t.get("drr_l7d")), _drr(t.get("drr_l30d")),
            _n(t.get("total_atp")),
            "&#9888; Restock" if t.get("demand_supply_gap") else "OK",
            str(t.get("action",""))[:80],
        ))
    trend_table = (
        _table(
            ["Keyword","Matched Product","Trend Score","L1D DRR","L7D DRR","L30D DRR",
             "ATP","Gap","Action"],
            trend_rows
        ) if trends else
        "<p class='empty'>Add pytrends to requirements.txt to enable Google Trends.</p>"
    )

    recs_content = (
        '<div class="subsection">'
        + _sub("&#11014;&#65039; UM to KAM Upgrade Candidates")
        + _info("&#9989; = L1D DRR &gt; L7D DRR &gt; L30D DRR continuously growing. Growth % = (DRR change / base) x 100.")
        + _table(
            ["Seller","L30D DRR","L7D DRR","L1D DRR",
             "Growth L30D to L1D","Growth L7D to L1D","Trend",
             "Continuously Growing","ASP","Health"],
            um_rows
        )
        + "</div>"
        '<div class="subsection" style="margin-top:20px">'
        + _sub("&#128269; Google Search Trends vs Your Order Trends")
        + _info("Trend score 0-100 (India, last 7 days). High score + low stock = restock urgently.")
        + trend_table
        + "</div>"
    )

    # ── chart data ─────────────────────────────────────────────────────────────
    chart_json = json.dumps({
        "rule_labels":   [r["rule"] for r in rules[:6]],
        "loss_l7d":      [r.get("loss_vs_l7d",  0) for r in rules[:6]],
        "loss_l30d":     [r.get("loss_vs_l30d", 0) for r in rules[:6]],
        "act_labels":    [str(a.get("product_title",""))[:25] for a in actions[:8]],
        "act_l7d":       [a.get("loss_vs_l7d",  0) for a in actions[:8]],
        "act_l30d":      [a.get("loss_vs_l30d", 0) for a in actions[:8]],
    })

    # ── assemble final HTML using simple concatenation — NO nested f-strings ──
    html = (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        "<title>RCA Report — " + label + " — " + date_str + "</title>"
        "<script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js'></script>"
        "<style>" + CSS + "</style>"
        "</head><body>"

        "<div class='header'>"
          "<h1>&#128201; Sales RCA Report"
            "<span class='badge'>" + label + "</span>"
            "<span class='badge'>" + date_str + "</span>"
          "</h1>"
          "<div class='meta'>Generated " + generated_at +
          " &nbsp;&#183;&nbsp; " + _n(meta.get("total_rows")) +
          " rows &nbsp;&#183;&nbsp; L30D window = 29 days | L7D window = 6 days</div>"
        "</div>"

        "<div class='toc'>"
          "<a href='#summary'>&#128202; Summary</a>"
          "<a href='#rules'>&#128203; Rules</a>"
          "<a href='#actionables'>&#127919; Actionables</a>"
          "<a href='#price'>&#128184; Price Hike</a>"
          "<a href='#oos'>&#128230; OOS</a>"
          "<a href='#inactive'>&#128683; Inactive</a>"
          "<a href='#insights'>&#128300; Insights</a>"
          "<a href='#recs'>&#128161; Recommendations</a>"
        "</div>"

        "<div class='body'>"
        + "<div id='summary'>" + cards_html + "</div>"

        + "<div class='charts'>"
            "<div class='chart-box'><h3>Order Loss by Rule — L7D vs L30D</h3>"
              "<div style='position:relative;height:220px'>"
                "<canvas id='ruleChart'></canvas>"
              "</div>"
            "</div>"
            "<div class='chart-box'><h3>Top 8 Actionables — Loss vs Recovery</h3>"
              "<div style='position:relative;height:220px'>"
                "<canvas id='actChart'></canvas>"
              "</div>"
            "</div>"
          "</div>"

        + _sec("&#128203;", "Rules Causing Order Loss — Ranked by Impact",
               rules_content, "rules")
        + _sec("&#127919;", "Priority Actionables",
               actions_content, "actionables")
        + _sec("&#128184;", "Price Hike Analysis",
               price_content, "price")
        + _sec("&#128230;", "Out of Stock",
               oos_content, "oos")
        + _sec("&#128683;", "Inactive Listings",
               inactive_content, "inactive")
        + _sec("&#128300;", "Additional Insights",
               insights_content, "insights")
        + _sec("&#128161;", "Recommendations",
               recs_content, "recs")

        + "</div>"  # end .body

        + "<div class='footer'>RCA Report &nbsp;&#183;&nbsp; " +
          label + " &nbsp;&#183;&nbsp; " + date_str +
          " &nbsp;&#183;&nbsp; Generated " + generated_at +
          "</div>"

        + "<script>\n"
          "const d = " + chart_json + ";\n"
          "new Chart(document.getElementById('ruleChart'),{"
            "type:'bar',"
            "data:{labels:d.rule_labels,datasets:["
              "{label:'Loss vs L7D',data:d.loss_l7d,backgroundColor:'rgba(192,57,43,.7)',borderWidth:0},"
              "{label:'Loss vs L30D',data:d.loss_l30d,backgroundColor:'rgba(230,126,34,.7)',borderWidth:0}"
            "]},"
            "options:{responsive:true,maintainAspectRatio:false,"
              "plugins:{legend:{position:'top',labels:{font:{size:11}}}},"
              "scales:{x:{ticks:{font:{size:9}}},y:{ticks:{font:{size:10}}}}}"
          "});\n"
          "new Chart(document.getElementById('actChart'),{"
            "type:'bar',"
            "data:{labels:d.act_labels,datasets:["
              "{label:'Loss vs L7D',data:d.act_l7d,backgroundColor:'rgba(192,57,43,.7)',borderWidth:0},"
              "{label:'Loss vs L30D',data:d.act_l30d,backgroundColor:'rgba(230,126,34,.7)',borderWidth:0}"
            "]},"
            "options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',"
              "plugins:{legend:{position:'top',labels:{font:{size:11}}}},"
              "scales:{x:{ticks:{font:{size:10}}},y:{ticks:{font:{size:9}}}}}"
          "});\n"
          "</script>"
        "</body></html>"
    )

    return html
