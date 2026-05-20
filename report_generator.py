"""
report_generator.py
────────────────────────────────────────────────────────────────────────────────
Converts an rca_engine result dict into a fully self-contained HTML report
that can be shared as a Google Drive link — no server, no login needed.
"""

import json
from datetime import datetime


def _fmt(n, default="—"):
    try:
        return f"{round(float(n)):,}" if n not in (None, "", "—") else default
    except Exception:
        return default


def _inr(n):
    try:
        return f"₹{round(float(n)):,}" if n not in (None, "", "—") else "—"
    except Exception:
        return "—"


def _pct(n):
    try:
        v = float(n)
        return f"{'+'if v>0 else ''}{v:.1f}%"
    except Exception:
        return "—"


def _row(*cells, header=False):
    tag = "th" if header else "td"
    return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"


def _table(headers, rows, color_col=None):
    if not rows:
        return "<p class='empty'>No issues found.</p>"
    head = _row(*headers, header=True)
    body = "".join(_row(*r) for r in rows)
    return f"<table><thead>{head}</thead><tbody>{body}</tbody></table>"


def _badge(text, color="red"):
    colors = {
        "red":    ("#fde8e8", "#c0392b"),
        "orange": ("#fef3e2", "#e67e22"),
        "green":  ("#e8f8f0", "#27ae60"),
        "blue":   ("#e8f0fe", "#1a73e8"),
        "gray":   ("#f1f3f4", "#5f6368"),
    }
    bg, fg = colors.get(color, colors["gray"])
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600">{text}</span>'


def _section(title, icon, content, collapsed=False):
    return f"""
<div class="section">
  <div class="section-title">{icon} {title}</div>
  {content}
</div>"""


def generate_html(result: dict, bu: str, date_str: str, generated_at: str = None) -> str:
    if generated_at is None:
        generated_at = datetime.now().strftime("%d %b %Y, %I:%M %p")

    meta    = result.get("meta", {})
    summary = result.get("summary", {})
    asp     = result.get("asp_issues", [])
    oos     = result.get("oos_issues", [])
    inact   = result.get("inactive_issues", {})
    actions = result.get("actionables", [])
    innov   = result.get("innovative_insights", {})
    recs    = result.get("recommendations", {})

    fk_inact  = inact.get("fk_inactivated", {})
    sel_inact = inact.get("seller_inactivated", {})

    # ── summary cards ──────────────────────────────────────────────────────────
    cards_html = f"""
<div class="cards">
  <div class="card red">
    <div class="card-label">Order Loss / Day (L7D)</div>
    <div class="card-value">{_fmt(summary.get('total_order_loss_l7d_daily'))}</div>
    <div class="card-sub">orders per day</div>
  </div>
  <div class="card orange">
    <div class="card-label">Yesterday Loss (L1D)</div>
    <div class="card-value">{_fmt(summary.get('total_order_loss_l1d'))}</div>
    <div class="card-sub">orders</div>
  </div>
  <div class="card orange">
    <div class="card-label">Order Loss / Day (L30D)</div>
    <div class="card-value">{_fmt(summary.get('total_order_loss_l30d_daily'))}</div>
    <div class="card-sub">orders per day</div>
  </div>
  <div class="card red">
    <div class="card-label">Monthly Order Impact</div>
    <div class="card-value">{_fmt(summary.get('total_monthly_order_impact'))}</div>
    <div class="card-sub">{summary.get('top_issue_category','—')} is top issue</div>
  </div>
  <div class="card blue">
    <div class="card-label">Listings Analysed</div>
    <div class="card-value">{_fmt(meta.get('total_listings_analyzed'))}</div>
    <div class="card-sub">{_fmt(meta.get('oos_count'))} OOS · {_fmt(meta.get('inactive_listings'))} inactive</div>
  </div>
  <div class="card blue">
    <div class="card-label">Avg Health Score</div>
    <div class="card-value">{_fmt(summary.get('avg_listing_health_score'))} / 100</div>
    <div class="card-sub">{_fmt(summary.get('listings_on_velocity_cliff'))} on velocity cliff</div>
  </div>
</div>"""

    # ── actionables ────────────────────────────────────────────────────────────
    def priority_badge(p):
        p = int(p) if p else 99
        if p <= 3:   return _badge(f"#{p} CRITICAL", "red")
        if p <= 8:   return _badge(f"#{p} High", "orange")
        return _badge(f"#{p}", "gray")

    action_rows = []
    for a in actions[:25]:
        action_rows.append((
            priority_badge(a.get("priority", "")),
            a.get("category", ""),
            a.get("product_title", a.get("listing_or_seller", ""))[:60],
            a.get("seller", a.get("listing_or_seller", ""))[:30],
            f"<strong style='color:#c0392b'>{_fmt(a.get('order_loss_per_day'))}</strong>",
            f"<span style='color:#27ae60'>{_fmt(a.get('expected_recovery_orders_per_day'))}</span>",
            f"<em style='font-size:11px'>{str(a.get('action',''))[:120]}</em>",
        ))

    actionables_html = _table(
        ["Priority", "Category", "Listing", "Seller", "Loss/Day", "Recovery/Day", "Action"],
        action_rows
    )

    # ── ASP issues ─────────────────────────────────────────────────────────────
    asp_rows = [(
        r.get("product_title","")[:50],
        r.get("seller","")[:25],
        _inr(r.get("asp_l30d")),
        f"<span style='color:#e67e22'>{_inr(r.get('asp_l7d'))}</span>",
        f"<span style='color:#c0392b'>{_pct(r.get('asp_change_pct'))}</span>",
        _fmt(r.get("orders_l7d")),
        f"<strong>{_fmt(r.get('order_loss_per_day'))}</strong>",
        f"<span style='color:#27ae60'>{_inr(r.get('recommended_asp'))}</span>",
    ) for r in asp[:20]]
    asp_html = _table(["Listing", "Seller", "L30D ASP", "L7D ASP", "Δ ASP", "L7D Orders", "Loss/Day", "Rec. ASP"], asp_rows)

    # ── OOS ────────────────────────────────────────────────────────────────────
    oos_rows = [(
        r.get("product_title","")[:50],
        r.get("seller","")[:25],
        _fmt(r.get("orders_l7d")),
        _fmt(r.get("orders_l30d")),
        f"<strong style='color:#c0392b'>{_fmt(r.get('order_loss_per_day'))}</strong>",
        f"<em style='font-size:11px'>{str(r.get('action',''))[:100]}</em>",
    ) for r in oos[:20]]
    oos_html = _table(["Listing", "Seller", "L7D Orders", "L30D Orders", "Loss/Day", "Action"], oos_rows)

    # ── FK inactive ────────────────────────────────────────────────────────────
    fk_by_reason = fk_inact.get("by_reason", [])
    fk_reason_rows = [(
        r.get("reason","")[:50],
        _fmt(r.get("count")),
        f"<strong style='color:#c0392b'>{_fmt(r.get('order_loss_per_day'))}</strong>",
        f"<em style='font-size:11px'>{r.get('action','')[:100]}</em>",
    ) for r in fk_by_reason[:15]]
    fk_reason_html = _table(["Reason", "Count", "Loss/Day", "Action"], fk_reason_rows)

    fk_listing_rows = [(
        r.get("product_title","")[:50],
        r.get("seller","")[:25],
        r.get("reason","")[:40],
        f"<strong style='color:#c0392b'>{_fmt(r.get('order_loss_per_day'))}</strong>",
        f"<em style='font-size:11px'>{r.get('action','')[:100]}</em>",
    ) for r in fk_inact.get("top_listings", [])[:10]]
    fk_listing_html = _table(["Listing", "Seller", "Reason", "Loss/Day", "Action"], fk_listing_rows)

    # ── Seller inactive ────────────────────────────────────────────────────────
    sel_seller_rows = [(
        s.get("seller","")[:40],
        _fmt(s.get("count")),
        f"<strong style='color:#e67e22'>{_fmt(s.get('order_loss_per_day'))}</strong>",
        f"<em style='font-size:11px'>{s.get('action','')[:100]}</em>",
    ) for s in sel_inact.get("by_seller", [])[:15]]
    sel_seller_html = _table(["Seller", "Listings", "Loss/Day", "Action"], sel_seller_rows)

    # ── Innovative: compounding ────────────────────────────────────────────────
    comp = innov.get("compounding_issues", {})
    comp_rows = [(
        r.get("product_title","")[:50],
        r.get("seller","")[:25],
        _badge(str(r.get("issue_count","")) + " issues", "red"),
        r.get("problems",""),
        f"<strong style='color:#c0392b'>{_fmt(r.get('order_loss_per_day'))}</strong>",
        str(r.get("health_score","")),
    ) for r in comp.get("listings",[])[:15]]
    comp_html = _table(["Listing", "Seller", "# Problems", "Issues", "Loss/Day", "Health"], comp_rows)

    # ── Innovative: cascade risk ───────────────────────────────────────────────
    cascade = innov.get("seller_cascade_risk", {})
    casc_rows = [(
        s.get("seller","")[:40],
        _fmt(s.get("total_listings")),
        f"<strong>{_fmt(s.get('total_orders_daily'))}</strong>",
        _fmt(s.get("oos_listings")),
        f"{_fmt(s.get('oos_pct'))}%",
        f"<strong style='color:#c0392b'>{_fmt(s.get('cascade_risk_score'))}</strong>",
    ) for s in cascade.get("top_sellers",[])[:10]]
    casc_html = _table(["Seller", "Listings", "Orders/Day", "OOS Listings", "OOS %", "Risk Score"], casc_rows)

    # ── Innovative: velocity cliff ─────────────────────────────────────────────
    vc = innov.get("velocity_cliff_listings", {})
    vc_rows = [(
        r.get("product_title","")[:50],
        r.get("seller","")[:25],
        _fmt(r.get("rate_l1d")),
        _fmt(r.get("rate_l30d")),
        f"<strong style='color:#c0392b'>{r.get('health_score','')}</strong>",
    ) for r in vc.get("listings",[])[:15]]
    vc_html = _table(["Listing", "Seller", "L1D Rate", "L30D Rate", "Health"], vc_rows)

    # ── Stranded inventory ─────────────────────────────────────────────────────
    strand = innov.get("stranded_inventory", {})
    strand_rows = [(
        r.get("product_title","")[:50],
        r.get("seller","")[:25],
        _fmt(r.get("atp")),
        _inr(r.get("asp")),
        f"<em style='font-size:11px'>{r.get('action','')[:100]}</em>",
    ) for r in strand.get("listings",[])[:15]]
    strand_html = _table(["Listing", "Seller", "Stock (ATP)", "ASP", "Action"], strand_rows)

    # ── UM→KAM ────────────────────────────────────────────────────────────────
    um_kam_rows = [(
        r.get("seller","")[:40],
        _fmt(r.get("orders_l30d")),
        _fmt(r.get("orders_l7d")),
        _inr(r.get("asp")),
        r.get("trend",""),
        r.get("rationale","")[:80],
    ) for r in recs.get("um_to_kam",[])[:10]]
    um_kam_html = _table(["Seller", "L30D Orders", "L7D Orders", "ASP", "Trend", "Rationale"], um_kam_rows)

    # ── Chart data ─────────────────────────────────────────────────────────────
    issue_chart_data = json.dumps({
        "labels": ["OOS", "ASP Hike", "FK Inactive", "Seller Inactive"],
        "values": [
            round(float(sum(x.get("order_loss_per_day",0) for x in oos)), 1),
            round(float(sum(x.get("order_loss_per_day",0) for x in asp)), 1),
            round(float(fk_inact.get("total_order_loss_per_day", 0)), 1),
            round(float(sel_inact.get("total_order_loss_per_day", 0)), 1),
        ]
    })

    top_action_data = json.dumps({
        "labels": [a.get("product_title","")[:25] for a in actions[:8]],
        "loss":   [round(float(a.get("order_loss_per_day",0)),1) for a in actions[:8]],
        "recovery": [round(float(a.get("expected_recovery_orders_per_day",0)),1) for a in actions[:8]],
    })

    # ── Full HTML ──────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RCA Report — {bu} — {date_str}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f8f9fa; color: #1a1a2e; font-size: 13px; }}
  .header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; padding: 28px 40px; }}
  .header h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 4px; }}
  .header .meta {{ font-size: 12px; opacity: 0.7; margin-top: 6px; }}
  .header .badge {{ display:inline-block; background:rgba(255,255,255,0.15); padding:3px 10px; border-radius:12px; font-size:11px; margin-left:10px; }}
  .toc {{ background: #fff; border-bottom: 1px solid #e8eaed; padding: 10px 40px; display:flex; gap:20px; flex-wrap:wrap; }}
  .toc a {{ color: #1a73e8; text-decoration:none; font-size:12px; white-space:nowrap; }}
  .toc a:hover {{ text-decoration:underline; }}
  .body {{ max-width: 1300px; margin: 0 auto; padding: 28px 40px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 28px; }}
  .card {{ background: white; border-radius: 10px; padding: 16px; border-left: 4px solid #e8eaed; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .card.red    {{ border-color: #c0392b; }}
  .card.orange {{ border-color: #e67e22; }}
  .card.green  {{ border-color: #27ae60; }}
  .card.blue   {{ border-color: #1a73e8; }}
  .card-label {{ font-size: 11px; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}
  .card-value {{ font-size: 26px; font-weight: 700; line-height: 1; margin-bottom: 4px; }}
  .card.red    .card-value {{ color: #c0392b; }}
  .card.orange .card-value {{ color: #e67e22; }}
  .card.green  .card-value {{ color: #27ae60; }}
  .card.blue   .card-value {{ color: #1a73e8; }}
  .card-sub {{ font-size: 11px; color: #80868b; }}
  .charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 28px; }}
  .chart-box {{ background: white; border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .chart-box h3 {{ font-size: 13px; color: #5f6368; margin-bottom: 14px; font-weight: 600; }}
  .section {{ background: white; border-radius: 10px; padding: 22px 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .section-title {{ font-size: 15px; font-weight: 700; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }}
  .subsection {{ margin-top: 18px; }}
  .subsection-title {{ font-size: 12px; font-weight: 600; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; padding-bottom: 6px; border-bottom: 1px solid #f1f3f4; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ text-align: left; padding: 8px 10px; border-bottom: 2px solid #f1f3f4; color: #5f6368; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.3px; white-space: nowrap; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #f8f9fa; vertical-align: top; }}
  tr:hover td {{ background: #f8f9fa; }}
  .empty {{ color: #80868b; font-style: italic; padding: 12px 0; }}
  .footer {{ text-align: center; padding: 28px; color: #80868b; font-size: 11px; }}
  .info-bar {{ background: #e8f0fe; border-left: 4px solid #1a73e8; padding: 10px 14px; border-radius: 0 6px 6px 0; margin-bottom: 14px; font-size: 12px; color: #1a73e8; }}
  @media(max-width:700px) {{ .body {{ padding: 16px; }} .charts-row {{ grid-template-columns: 1fr; }} .cards {{ grid-template-columns: 1fr 1fr; }} }}
</style>
</head>
<body>

<div class="header">
  <h1>📉 Sales RCA Report
    <span class="badge">{bu}</span>
    <span class="badge">{date_str}</span>
  </h1>
  <div class="meta">Generated {generated_at} &nbsp;·&nbsp; {_fmt(meta.get('total_listings_analyzed'))} listings analysed &nbsp;·&nbsp; No-API engine (rca_engine.py)</div>
</div>

<div class="toc">
  <a href="#summary">📊 Summary</a>
  <a href="#actionables">🎯 Actionables</a>
  <a href="#asp">💸 ASP Issues</a>
  <a href="#oos">📦 Out of Stock</a>
  <a href="#inactive">🚫 Inactive</a>
  <a href="#compounding">⚡ Compounding</a>
  <a href="#cascade">🌊 Cascade Risk</a>
  <a href="#cliff">📉 Velocity Cliff</a>
  <a href="#stranded">🧊 Stranded Stock</a>
  <a href="#recommendations">💡 Recommendations</a>
</div>

<div class="body">

  <div id="summary">
    {cards_html}
  </div>

  <div class="charts-row">
    <div class="chart-box">
      <h3>Order Loss by Issue Category (orders/day)</h3>
      <div style="position:relative;height:220px"><canvas id="pieChart" role="img" aria-label="Pie chart of order loss by issue category"></canvas></div>
    </div>
    <div class="chart-box">
      <h3>Top 8 Actionables — Loss vs Recovery (orders/day)</h3>
      <div style="position:relative;height:220px"><canvas id="barChart" role="img" aria-label="Bar chart of top actionable order loss vs expected recovery"></canvas></div>
    </div>
  </div>

  {_section("🎯 Priority Actionables", "", f'<div id="actionables"><div class="info-bar">Ranked by order loss per day · Top 25 shown · Recovery/Day = expected orders recovered after fix</div>{actionables_html}</div>')}

  {_section("💸 ASP-Driven Order Loss", "", f'<div id="asp">{asp_html}</div>')}

  {_section("📦 Out of Stock (atp=0, Active listings)", "", f'<div id="oos">{oos_html}</div>')}

  {_section("🚫 Inactive Listings", "", f'''
    <div id="inactive">
      <div class="subsection">
        <div class="subsection-title">🔴 Flipkart Inactivated — {_fmt(fk_inact.get("total_count"))} listings · {_fmt(fk_inact.get("total_order_loss_per_day"))} orders/day lost</div>
        <div class="subsection-title" style="margin-top:12px">By reason</div>
        {fk_reason_html}
        <div class="subsection-title" style="margin-top:16px">Top impacted listings</div>
        {fk_listing_html}
      </div>
      <div class="subsection" style="margin-top:24px">
        <div class="subsection-title">🟡 Seller Inactivated — {_fmt(sel_inact.get("total_count"))} listings · {_fmt(sel_inact.get("total_order_loss_per_day"))} orders/day lost</div>
        {sel_seller_html}
      </div>
    </div>
  ''')}

  {_section("⚡ Compounding Issues", "", f'''
    <div id="compounding">
      <div class="info-bar">Listings with 2+ simultaneous problems lose orders from multiple directions — fix these first for maximum impact. Total: {comp.get("count",0)} listings · {_fmt(comp.get("total_order_loss_per_day"))} orders/day</div>
      {comp_html}
    </div>
  ''')}

  {_section("🌊 Seller Cascade Risk", "", f'''
    <div id="cascade">
      <div class="info-bar">If any of these sellers go fully inactive, their entire daily order run rate disappears. This is your concentration + dependency exposure.</div>
      {casc_html}
    </div>
  ''')}

  {_section("📉 Velocity Cliff Listings", "", f'''
    <div id="cliff">
      <div class="info-bar">L1D orders fell to less than 15% of L30D average — something acute happened yesterday. Investigate same-day. Total: {vc.get("count",0)} listings</div>
      {vc_html}
    </div>
  ''')}

  {_section("🧊 Stranded Inventory", "", f'''
    <div id="stranded">
      <div class="info-bar">Active listings with stock but near-zero demand. Working capital is trapped. Consider price reductions or channel moves. Total: {strand.get("count",0)} listings</div>
      {strand_html}
    </div>
  ''')}

  {_section("💡 Recommendations", "", f'''
    <div id="recommendations">
      <div class="subsection">
        <div class="subsection-title">⬆️ UM → KAM Upgrade Candidates</div>
        {um_kam_html}
      </div>
    </div>
  ''')}

</div>

<div class="footer">
  RCA Dashboard · {bu} · {date_str} · Generated {generated_at}
</div>

<script>
const issueData = {issue_chart_data};
const actionData = {top_action_data};

const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
const COLORS = ['#c0392b','#e67e22','#8e44ad','#1a73e8'];

new Chart(document.getElementById('pieChart'), {{
  type: 'doughnut',
  data: {{
    labels: issueData.labels,
    datasets: [{{ data: issueData.values, backgroundColor: COLORS, borderWidth: 2, borderColor: '#fff' }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'right', labels: {{ font: {{ size: 11 }}, padding: 12 }} }} }}
  }}
}});

new Chart(document.getElementById('barChart'), {{
  type: 'bar',
  data: {{
    labels: actionData.labels,
    datasets: [
      {{ label: 'Loss/Day', data: actionData.loss, backgroundColor: '#fde8e8', borderColor: '#c0392b', borderWidth: 1.5 }},
      {{ label: 'Recovery/Day', data: actionData.recovery, backgroundColor: '#e8f8f0', borderColor: '#27ae60', borderWidth: 1.5 }}
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false, indexAxis: 'y',
    plugins: {{ legend: {{ position: 'top', labels: {{ font: {{ size: 11 }} }} }} }},
    scales: {{ x: {{ ticks: {{ font: {{ size: 10 }} }} }}, y: {{ ticks: {{ font: {{ size: 10 }} }} }} }}
  }}
}});
</script>
</body>
</html>"""
