"""
rca_engine.py
Pure pandas/numpy RCA engine. Zero external APIs. Zero Anthropic.
Columns match the exact SQL dump output.
"""

import numpy as np
import pandas as pd

# ── exact column aliases (from SQL dump) ──────────────────────────────────────
COL_ALIASES = {
    "sell_bu":                    ["sell_bu", "mapping"],
    "analytic_business_unit":     ["analytic_business_unit"],
    "analytic_super_category":    ["analytic_super_category"],
    "analytic_category":          ["analytic_category"],
    "cms_vertical":               ["cms_vertical"],
    "analytic_vertical":          ["analytic_vertical"],
    "owner":                      ["owner", "owner_type"],
    "name":                       ["name", "owner_name", "kam_name", "manager_name"],
    "seller_id":                  ["seller_id"],
    "display_name":               ["display_name", "seller_name", "seller"],
    "product_id":                 ["product_id"],
    "product_title":              ["product_title", "title"],
    "listing_id":                 ["listing_id", "sku_id", "fsn"],
    "listing_status":             ["listing_status", "status"],
    "listing_internal_state":     ["listing_internal_state", "internal_state"],
    "latest_deactivation_reason": ["latest_deactivation_reason",
                                   "listing_deactivation_reason", "deactivation_reason"],
    "yesterday_atp":              ["yesterday_atp", "atp", "current_atp", "available_qty"],
    "l30d_orders": ["l30d_orders", "orders_l30d"],
    "l7d_orders":  ["l7d_orders",  "orders_l7d"],
    "l1d_orders":  ["l1d_orders",  "orders_l1d"],
    "l30d_units":  ["l30d_units",  "units_l30d"],
    "l7d_units":   ["l7d_units",   "units_l7d"],
    "l1d_units":   ["l1d_units",   "units_l1d"],
    "l30d_gmv":    ["l30d_gmv",  "gmv_l30d"],
    "l7d_gmv":     ["l7d_gmv",   "gmv_l7d"],
    "l1d_gmv":     ["l1d_gmv",   "gmv_l1d"],
    "l30d_asp":    ["l30d_asp",  "asp_l30d"],
    "l7d_asp":     ["l7d_asp",   "asp_l7d"],
    "l1d_asp":     ["l1d_asp",   "asp_l1d"],
    "l30d_tp":     ["l30d_tp",   "tp_l30d"],
    "l7d_tp":      ["l7d_tp",    "tp_l7d"],
    "l1d_tp":      ["l1d_tp",    "tp_l1d"],
}

DIMENSION_COLS = [
    "sell_bu", "analytic_business_unit", "analytic_super_category",
    "analytic_category", "cms_vertical", "analytic_vertical",
    "owner", "name", "seller_id", "display_name",
]

DIMENSION_LABELS = {
    "sell_bu":                 "sell_bu (BU)",
    "analytic_business_unit":  "Analytic Business Unit",
    "analytic_super_category": "Super Category",
    "analytic_category":       "Category",
    "cms_vertical":            "CMS Vertical",
    "analytic_vertical":       "Analytic Vertical",
    "owner":                   "Owner Type (KAM / UM)",
    "name":                    "Owner Name (person)",
    "seller_id":               "Seller ID",
    "display_name":            "Seller Display Name",
}

FK_KEYWORDS = [
    "flipkart", "fk", "policy", "quality", "violation", "content",
    "catalog", "price", "image", "blocked", "restricted", "compliance",
    "counterfeit", "prohibited",
]

# ── sales-friendly tag names ───────────────────────────────────────────────────
TAG_FRIENDLY = {
    "OOS":             "Out of Stock",
    "ASP Hike":        "Price Hike Hurting Demand",
    "FK Inactive":     "Deactivated by Flipkart",
    "Seller Inactive": "Seller Switched Off Listing",
    "Stranded Stock":  "Stock Available — Zero Demand",
    "Velocity Cliff":  "Sharp Drop in Orders Yesterday",
    "Healthy":         "Active & Selling Well",
}

# ── rule definitions (shown in dashboard explainer) ───────────────────────────
RULE_DEFINITIONS = {
    "Out of Stock": {
        "icon": "📦", "recovery": 0.90,
        "what": "Listing is live on Flipkart but seller has zero stock (ATP=0). Every buyer who lands on the page leaves empty-handed.",
        "how":  "listing_status = ACTIVE  AND  yesterday_atp = 0",
        "fix":  "Seller must replenish stock immediately. Until then, all demand is lost.",
    },
    "Price Hike Hurting Demand": {
        "icon": "💸", "recovery": 0.75,
        "what": "Seller raised the selling price by more than 5% in the last 7 days vs the 30-day average, and orders dropped in the same window.",
        "how":  "l7d_asp > l30d_asp × 1.05  AND  l1d_orders < l7d_orders / 7",
        "fix":  "Bring ASP back to the L30D level (the price at which orders were highest). Recovery is typically fast — within 1–2 days.",
    },
    "Deactivated by Flipkart": {
        "icon": "🔴", "recovery": 0.40,
        "what": "Flipkart has taken down the listing due to a policy, quality, catalog, or compliance issue.",
        "how":  "listing_status = INACTIVE  AND  deactivation reason contains FK/policy/quality/violation keywords",
        "fix":  "Raise a reinstatement ticket with Flipkart Seller Support. Harder to recover quickly — depends on the specific violation.",
    },
    "Seller Switched Off Listing": {
        "icon": "🟡", "recovery": 0.65,
        "what": "The seller manually deactivated their own listing — usually due to stock issues, pricing disputes, or inactivity.",
        "how":  "listing_status = INACTIVE  AND  not deactivated by Flipkart",
        "fix":  "Call or message the seller to reactivate. Offer account management support. Usually quick to fix.",
    },
    "Stock Available — Zero Demand": {
        "icon": "🧊", "recovery": 0.30,
        "what": "The listing is active and the seller has stock, but fewer than 0.1 orders/day in the last 7 days. Working capital is trapped with no buyers.",
        "how":  "listing_status = ACTIVE  AND  yesterday_atp > 0  AND  l7d_orders / 7 < 0.1",
        "fix":  "Investigate: is pricing too high vs category? Poor catalog images? Run a promotional push or visibility boost.",
    },
    "Sharp Drop in Orders Yesterday": {
        "icon": "📉", "recovery": 0.55,
        "what": "Yesterday's orders fell to less than 15% of the 30-day daily average. Something acute happened — a price change, stock running out, or a listing edit.",
        "how":  "l1d_orders < l30d_orders / 30 × 0.15",
        "fix":  "Investigate same-day: check ASP change, ATP, listing status, any recent catalog edits. Often reversible quickly.",
    },
    "Active & Selling Well": {
        "icon": "✅", "recovery": 0.0,
        "what": "No issues detected. Orders are at or above recent averages.",
        "how":  "None of the above conditions met",
        "fix":  "No action needed.",
    },
}

RECOVERY_PROB = {k: v["recovery"] for k, v in RULE_DEFINITIONS.items()}


# ── helpers ────────────────────────────────────────────────────────────────────
def normalise_cols(df: pd.DataFrame) -> pd.DataFrame:
    actual = {c.lower().strip(): c for c in df.columns}
    rename = {}
    for std, aliases in COL_ALIASES.items():
        for a in aliases:
            if a.lower() in actual and std not in rename.values():
                rename[actual[a.lower()]] = std
                break
    return df.rename(columns=rename)


def to_num(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


def safe(df, col, default=0):
    return df[col] if col in df.columns else pd.Series(default, index=df.index, dtype=float if isinstance(default, (int,float)) else object)


def get_dimension_values(df: pd.DataFrame, dimension: str) -> list:
    df2 = normalise_cols(df.copy())
    if dimension not in df2.columns:
        return []
    return sorted(
        df2[dimension].dropna().astype(str).str.strip()
        .replace("", pd.NA).dropna().unique().tolist()
    )


def filter_df(df: pd.DataFrame, dimension: str, value: str) -> pd.DataFrame:
    df = normalise_cols(df.copy())
    if value.upper() == "OVERALL" or value == "" or dimension not in df.columns:
        return df
    mask = df[dimension].astype(str).str.strip().str.lower() == value.strip().lower()
    return df[mask].copy()


# ── prepare: compute all derived fields ───────────────────────────────────────
def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = normalise_cols(df.copy())
    df = to_num(df, [
        "l30d_orders","l7d_orders","l1d_orders",
        "l30d_units","l7d_units","l1d_units",
        "l30d_gmv","l7d_gmv","l1d_gmv",
        "l30d_asp","l7d_asp","l1d_asp",
        "l30d_tp","l7d_tp","l1d_tp",
        "yesterday_atp",
    ])
    for c in ["listing_status","listing_internal_state",
              "latest_deactivation_reason","owner","name"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip()

    # daily rates
    df["rate_l30d"] = df["l30d_orders"] / 30
    df["rate_l7d"]  = df["l7d_orders"]  / 7
    df["rate_l1d"]  = df["l1d_orders"].astype(float)

    # order loss: L1D vs L7D and L1D vs L30D separately
    df["loss_vs_l7d"]  = (df["rate_l7d"]  - df["rate_l1d"]).clip(lower=0)
    df["loss_vs_l30d"] = (df["rate_l30d"] - df["rate_l1d"]).clip(lower=0)
    df["order_loss_per_day"] = df["loss_vs_l7d"]   # primary sort key

    # ASP change l30d → l7d
    df["asp_change_pct"] = np.where(
        df["l30d_asp"] > 0,
        (df["l7d_asp"] - df["l30d_asp"]) / df["l30d_asp"] * 100, 0
    )

    # price elasticity
    pct_ord = np.where(df["rate_l30d"]>0,
                       (df["rate_l7d"]-df["rate_l30d"])/df["rate_l30d"]*100, 0)
    df["price_elasticity"] = pd.Series(
        pct_ord / df["asp_change_pct"].replace(0, np.nan),
        index=df.index).clip(-5, 5).fillna(0)

    # momentum
    df["momentum"] = (df["rate_l7d"] / df["rate_l30d"].replace(0,np.nan)).fillna(0).clip(0,3)

    # velocity cliff: yesterday < 15% of 30d average
    df["velocity_cliff"] = (
        (df["rate_l1d"] / df["rate_l30d"].replace(0,np.nan)).fillna(1) < 0.15
    )

    # FK inactivation
    def is_fk(row):
        for c in ["listing_internal_state","latest_deactivation_reason"]:
            if c in row.index and any(k in str(row[c]).lower() for k in FK_KEYWORDS):
                return True
        return False

    status = safe(df,"listing_status","").str.upper()
    inactive_mask = status == "INACTIVE"
    df["inactivated_by_fk"] = False
    if inactive_mask.any():
        fk_result = df[inactive_mask].apply(is_fk, axis=1)
        df.loc[inactive_mask, "inactivated_by_fk"] = fk_result.to_numpy()

    return df.reset_index(drop=True)


def get_masks(df: pd.DataFrame) -> dict:
    status = safe(df,"listing_status","").str.upper()
    atp    = safe(df,"yesterday_atp", 1)
    return {
        "active":          status == "ACTIVE",
        "inactive":        status == "INACTIVE",
        "oos":             (status == "ACTIVE") & (atp == 0),
        "fk_inactive":     (status == "INACTIVE") & df["inactivated_by_fk"],
        "seller_inactive": (status == "INACTIVE") & (~df["inactivated_by_fk"]),
        "asp_hiked":       (df["asp_change_pct"] > 5) & (df["order_loss_per_day"] > 0),
        "stranded":        (status == "ACTIVE") & (atp > 0) & (df["rate_l7d"] < 0.1),
    }


def health_score(df: pd.DataFrame, masks: dict) -> pd.Series:
    s = pd.Series(100.0, index=df.index)
    s -= masks["oos"].astype(float)      * 30
    s -= masks["asp_hiked"].astype(float) * (df["asp_change_pct"].clip(0,50)/50) * 25
    s -= masks["inactive"].astype(float) * 25
    s -= df["velocity_cliff"].astype(float) * 15
    s -= masks["stranded"].astype(float) * 5
    return s.clip(0, 100).round(1)


# ── tag every raw row (for enriched CSV download) ─────────────────────────────
def tag_rows(df: pd.DataFrame) -> pd.DataFrame:
    df = prepare(df)
    masks = get_masks(df)
    df["health_score"] = health_score(df, masks)

    tags, actions, rec_l7d, rec_l30d = [], [], [], []

    for idx, row in df.iterrows():
        # classify
        if   masks["oos"].loc[idx]:             tag = "Out of Stock"
        elif masks["fk_inactive"].loc[idx]:     tag = "Deactivated by Flipkart"
        elif masks["seller_inactive"].loc[idx]: tag = "Seller Switched Off Listing"
        elif masks["asp_hiked"].loc[idx]:       tag = "Price Hike Hurting Demand"
        elif masks["stranded"].loc[idx]:        tag = "Stock Available — Zero Demand"
        elif df.loc[idx,"velocity_cliff"]:      tag = "Sharp Drop in Orders Yesterday"
        else:                                   tag = "Active & Selling Well"

        tags.append(tag)

        l7  = round(float(row.get("loss_vs_l7d",  0)), 1)
        l30 = round(float(row.get("loss_vs_l30d", 0)), 1)
        rec_l7d.append(l7)
        rec_l30d.append(l30)

        prob   = RULE_DEFINITIONS.get(tag, {}).get("recovery", 0)
        asp7   = float(row.get("l7d_asp",  0))
        asp30  = float(row.get("l30d_asp", 0))
        seller = str(row.get("display_name", row.get("seller_id", "")))
        reason = str(row.get("latest_deactivation_reason", ""))

        if tag == "Out of Stock":
            actions.append(f"Restock now. Seller {seller} has 0 units. Losing {l7:.1f} orders/day vs L7D avg.")
        elif tag == "Price Hike Hurting Demand":
            rec = round(asp30, 0) if asp30 > 0 else round(asp7 * 0.92, 0)
            actions.append(f"Lower price from ₹{asp7:,.0f} → ₹{rec:,.0f} (best-sales price). Losing {l7:.1f} orders/day vs L7D.")
        elif tag == "Deactivated by Flipkart":
            actions.append(f"Raise FK reinstatement ticket. Reason: '{reason or 'check FK portal'}'. Losing {l7:.1f} orders/day.")
        elif tag == "Seller Switched Off Listing":
            actions.append(f"Call seller {seller} — they turned this listing off. Ask them to reactivate. Loss: {l7:.1f}/day.")
        elif tag == "Stock Available — Zero Demand":
            actions.append(f"Listing has stock but no buyers. Check price, catalog quality, run a promo.")
        elif tag == "Sharp Drop in Orders Yesterday":
            actions.append(f"Yesterday orders crashed. Urgently check: ASP change? ATP hit 0? Any listing edits?")
        else:
            actions.append("")

    df["rca_tag"]              = tags
    df["order_loss_vs_l7d"]   = rec_l7d
    df["order_loss_vs_l30d"]  = rec_l30d
    df["recommended_action"]   = actions
    df["health_score"]         = df["health_score"]

    # priority rank
    df["priority_rank"] = 0
    non_h = df[df["rca_tag"] != "Active & Selling Well"].sort_values("order_loss_vs_l7d", ascending=False)
    df.loc[non_h.index, "priority_rank"] = range(1, len(non_h)+1)

    return df


# ── full RCA engine ────────────────────────────────────────────────────────────
def run_rca(df_raw: pd.DataFrame, dimension: str, value: str, date_str: str) -> dict:
    df = filter_df(df_raw, dimension, value)
    if df.empty:
        return {"error": f"No rows found for {dimension} = '{value}'"}

    df = prepare(df)
    masks = get_masks(df)
    df["health_score"] = health_score(df, masks)

    def top(subset, n=20):
        return subset.sort_values("order_loss_per_day", ascending=False).head(n)

    def base_dict(r):
        return {
            "listing_id":        str(r.get("listing_id","")),
            "product_title":     str(r.get("product_title",""))[:70],
            "seller_id":         str(r.get("seller_id","")),
            "display_name":      str(r.get("display_name","")),
            "owner":             str(r.get("owner","")),
            "name":              str(r.get("name","")),
            "sell_bu":           str(r.get("sell_bu","")),
            "analytic_category": str(r.get("analytic_category","")),
            "loss_vs_l7d":       round(float(r.get("loss_vs_l7d",0)),1),
            "loss_vs_l30d":      round(float(r.get("loss_vs_l30d",0)),1),
            "order_loss_per_day":round(float(r.get("order_loss_per_day",0)),1),
            "health_score":      round(float(r.get("health_score",0)),0),
        }

    # ── ASP issues ─────────────────────────────────────────────────────────────
    asp_issues = []
    for _, r in top(df[masks["asp_hiked"]]).iterrows():
        rates = {"L30D":float(r.get("rate_l30d",0)),
                 "L7D": float(r.get("rate_l7d",0)),
                 "L1D": float(r.get("rate_l1d",0))}
        best   = max(rates, key=rates.get)
        rec    = float(r.get({"L30D":"l30d_asp","L7D":"l7d_asp","L1D":"l1d_asp"}[best],0))
        d = base_dict(r)
        d.update({
            "l30d_asp":round(float(r.get("l30d_asp",0)),0),
            "l7d_asp": round(float(r.get("l7d_asp",0)),0),
            "l1d_asp": round(float(r.get("l1d_asp",0)),0),
            "asp_change_pct":round(float(r.get("asp_change_pct",0)),1),
            "l7d_orders": round(float(r.get("l7d_orders",0)),0),
            "l30d_orders":round(float(r.get("l30d_orders",0)),0),
            "best_period":best, "recommended_asp":round(rec,0),
            "price_elasticity":round(float(r.get("price_elasticity",0)),2),
            "action": f"Lower price ₹{round(float(r.get('l7d_asp',0)),0):,.0f} → ₹{round(rec,0):,.0f}. Recovery: {round(float(r.get('loss_vs_l7d',0))*0.75,1)} orders/day.",
        })
        asp_issues.append(d)

    # ── OOS ────────────────────────────────────────────────────────────────────
    oos_issues = []
    for _, r in top(df[masks["oos"]]).iterrows():
        d = base_dict(r)
        loss = round(float(r.get("loss_vs_l7d",0)),1)
        d.update({
            "yesterday_atp":int(r.get("yesterday_atp",0)),
            "l7d_orders": round(float(r.get("l7d_orders",0)),0),
            "l30d_orders":round(float(r.get("l30d_orders",0)),0),
            "l7d_gmv":    round(float(r.get("l7d_gmv",0)),0),
            "action": f"Restock now — seller {r.get('display_name',r.get('seller_id',''))} has 0 units. {loss:.1f} orders/day lost vs L7D.",
        })
        oos_issues.append(d)

    # ── inactive ───────────────────────────────────────────────────────────────
    def build_inactive(subset, grp_col, itype):
        total = float(subset["order_loss_per_day"].sum())
        by_grp = []
        if grp_col and grp_col in subset.columns:
            g = (subset.groupby(grp_col, dropna=False, observed=True)
                 .agg(count=("order_loss_per_day","count"),
                      loss=("order_loss_per_day","sum"))
                 .reset_index().sort_values("loss", ascending=False))
            by_grp = [{("reason" if itype=="fk" else "seller"):str(r[grp_col]),
                       "count":int(r["count"]),
                       "order_loss_per_day":round(float(r["loss"]),1)}
                      for _,r in g.head(15).iterrows()]
        top_l = []
        for _,r in top(subset,15).iterrows():
            d = base_dict(r)
            d["latest_deactivation_reason"] = str(r.get("latest_deactivation_reason",""))
            d["action"] = (
                f"Raise FK ticket — reason: {r.get('latest_deactivation_reason','unknown')}"
                if itype=="fk"
                else f"Contact seller {r.get('display_name',r.get('seller_id',''))} to reactivate"
            )
            top_l.append(d)
        key = "by_reason" if itype=="fk" else "by_seller"
        return {"total_count":int(len(subset)),"total_order_loss_per_day":round(total,1),
                key:by_grp,"top_listings":top_l}

    inactive_issues = {
        "fk_inactivated":     build_inactive(df[masks["fk_inactive"]],     "latest_deactivation_reason","fk"),
        "seller_inactivated": build_inactive(df[masks["seller_inactive"]], "display_name","seller"),
    }

    # ── stranded ───────────────────────────────────────────────────────────────
    strand = []
    for _,r in df[masks["stranded"]].sort_values("yesterday_atp",ascending=False).head(15).iterrows():
        d = base_dict(r)
        d.update({"yesterday_atp":int(r.get("yesterday_atp",0)),
                  "l7d_asp":round(float(r.get("l7d_asp",0)),0),
                  "action":"Price down or investigate visibility. Stock exists but zero demand."})
        strand.append(d)

    # ── master actionables list ────────────────────────────────────────────────
    all_issues = (
        [("Out of Stock",                i, "Out of Stock")               for i in oos_issues] +
        [("Price Hike Hurting Demand",   i, "Price Hike Hurting Demand")  for i in asp_issues] +
        [("Deactivated by Flipkart",     i, "Deactivated by Flipkart")    for i in inactive_issues["fk_inactivated"]["top_listings"]] +
        [("Seller Switched Off Listing", i, "Seller Switched Off Listing") for i in inactive_issues["seller_inactivated"]["top_listings"]] +
        [("Stock Available — Zero Demand",i,"Stock Available — Zero Demand") for i in strand]
    )
    all_issues.sort(key=lambda x: x[1].get("loss_vs_l7d",0), reverse=True)

    actionables = []
    for pri,(cat,item,tag) in enumerate(all_issues, 1):
        l7  = float(item.get("loss_vs_l7d", item.get("order_loss_per_day",0)))
        l30 = float(item.get("loss_vs_l30d", item.get("order_loss_per_day",0)))
        rec = RULE_DEFINITIONS.get(tag,{}).get("recovery",0.5)
        actionables.append({
            "priority":           pri,
            "rule":               cat,
            "product_title":      item.get("product_title",""),
            "listing_id":         item.get("listing_id",""),
            "seller_id":          item.get("seller_id",""),
            "display_name":       item.get("display_name",""),
            "owner":              item.get("owner",""),
            "name":               item.get("name",""),
            "sell_bu":            item.get("sell_bu",""),
            "analytic_category":  item.get("analytic_category",""),
            "loss_vs_l7d":        round(l7,1),
            "loss_vs_l30d":       round(l30,1),
            "monthly_impact":     round(l7*30,0),
            "recovery_per_day":   round(l7*rec,1),
            "action":             item.get("action",""),
            "health_score":       item.get("health_score",""),
        })

    # ── rules summary ──────────────────────────────────────────────────────────
    rules_summary = []
    rule_data = [
        ("Out of Stock",                 masks["oos"]),
        ("Price Hike Hurting Demand",    masks["asp_hiked"]),
        ("Deactivated by Flipkart",      masks["fk_inactive"]),
        ("Seller Switched Off Listing",  masks["seller_inactive"]),
        ("Stock Available — Zero Demand",masks["stranded"]),
        ("Sharp Drop in Orders Yesterday",
         pd.Series(df["velocity_cliff"], index=df.index).fillna(False)),
    ]
    for rule_name, mask in rule_data:
        sub = df[mask]
        if sub.empty:
            continue
        defn = RULE_DEFINITIONS.get(rule_name, {})
        top_owners = (list(sub["name"].value_counts().head(3).index.tolist())
                      if "name" in sub.columns else [])
        rules_summary.append({
            "rule":             rule_name,
            "icon":             defn.get("icon",""),
            "what":             defn.get("what",""),
            "how":              defn.get("how",""),
            "fix":              defn.get("fix",""),
            "recovery_pct":     int(defn.get("recovery",0)*100),
            "listings_affected":int(len(sub)),
            "loss_vs_l7d":      round(float(sub["loss_vs_l7d"].sum()),1),
            "loss_vs_l30d":     round(float(sub["loss_vs_l30d"].sum()),1),
            "top_owner_names":  top_owners,
        })
    rules_summary.sort(key=lambda x: x["loss_vs_l7d"], reverse=True)

    # ── innovative insights ────────────────────────────────────────────────────
    issue_flags = pd.DataFrame({
        "oos":     masks["oos"],"asp":masks["asp_hiked"],
        "inactive":masks["inactive"],"cliff":df["velocity_cliff"],
        "stranded":masks["stranded"],
    })
    df["issue_count"] = issue_flags.sum(axis=1)

    compound = df[df["issue_count"]>=2].sort_values("order_loss_per_day", ascending=False)
    compound_list = [{
        "product_title":str(r.get("product_title",""))[:55],
        "display_name": str(r.get("display_name","")),
        "name":         str(r.get("name","")),
        "issue_count":  int(r.get("issue_count",0)),
        "problems": ", ".join(filter(None,[
            "OOS"       if masks["oos"].loc[i]       else "",
            "ASP Hike"  if masks["asp_hiked"].loc[i]  else "",
            "Inactive"  if masks["inactive"].loc[i]   else "",
            "Cliff"     if df["velocity_cliff"].loc[i] else "",
            "Stranded"  if masks["stranded"].loc[i]   else "",
        ])),
        "loss_vs_l7d":  round(float(r.get("loss_vs_l7d",0)),1),
        "health_score": round(float(r.get("health_score",0)),0),
    } for i,r in compound.head(15).iterrows()]

    grp_col = "display_name" if "display_name" in df.columns else "seller_id"
    cascade = []
    if grp_col in df.columns:
        cdf = df.groupby(grp_col, observed=True).agg(
            total_listings    =("listing_id","count") if "listing_id" in df.columns else (grp_col,"count"),
            total_orders_daily=("rate_l7d","sum"),
            oos_count         =("yesterday_atp", lambda x:(x==0).sum()),
        ).reset_index()
        cdf["risk_score"] = (cdf["total_orders_daily"]*1.2).round(1)
        for _,r in cdf.sort_values("risk_score",ascending=False).head(10).iterrows():
            cascade.append({"seller":str(r[grp_col]),"listings":int(r["total_listings"]),
                            "orders_per_day":round(float(r["total_orders_daily"]),1),
                            "oos_count":int(r["oos_count"]),"risk_score":float(r["risk_score"])})

    # UM→KAM
    um_kam = []
    if "owner" in df.columns and grp_col in df.columns:
        um = df[df["owner"].str.upper().str.contains("UM",na=False)]
        if not um.empty:
            ss = um.groupby(grp_col, observed=True).agg(
                l30d_orders=("l30d_orders","sum"),l7d_orders=("l7d_orders","sum"),
                avg_asp=("l7d_asp","mean"),avg_health=("health_score","mean"),
            ).reset_index()
            ss["growth"] = np.where(ss["l30d_orders"]/30>0,
                (ss["l7d_orders"]/7-ss["l30d_orders"]/30)/(ss["l30d_orders"]/30)*100,0)
            ss["score"] = (ss["l30d_orders"].rank(pct=True)*50+
                           ss["avg_health"].rank(pct=True)*30+
                           ss["growth"].clip(-100,100).rank(pct=True)*20)
            for _,r in ss.sort_values("score",ascending=False).head(10).iterrows():
                trend = "📈 Growing" if r["growth"]>10 else "📉 Declining" if r["growth"]<-10 else "➡️ Stable"
                um_kam.append({"seller":str(r[grp_col]),"l30d_orders":round(float(r["l30d_orders"]),0),
                               "avg_asp":round(float(r["avg_asp"]),0),"health":round(float(r["avg_health"]),0),
                               "growth_pct":round(float(r["growth"]),1),"trend":trend})

    # ── summary ────────────────────────────────────────────────────────────────
    oos_loss   = float(df[masks["oos"]]["loss_vs_l7d"].sum())
    asp_loss   = float(df[masks["asp_hiked"]]["loss_vs_l7d"].sum())
    inact_loss = float(df[masks["inactive"]]["loss_vs_l7d"].sum())
    total_loss = oos_loss + asp_loss + inact_loss
    top_cat = max([("Out of Stock",oos_loss),
                   ("Price Hike",asp_loss),
                   ("Inactive",inact_loss)], key=lambda x:x[1])[0]

    return {
        "meta":{
            "dimension":dimension,"value":value,"date":date_str,
            "total_rows":int(len(df)),"active":int(masks["active"].sum()),
            "inactive":int(masks["inactive"].sum()),"oos_count":int(masks["oos"].sum()),
        },
        "summary":{
            "total_loss_vs_l7d":   round(total_loss,1),
            "total_loss_vs_l30d":  round(float(df[masks["oos"]]["loss_vs_l30d"].sum()+
                                               df[masks["asp_hiked"]]["loss_vs_l30d"].sum()+
                                               df[masks["inactive"]]["loss_vs_l30d"].sum()),1),
            "total_l1d_orders":    round(float(df["l1d_orders"].sum()),0),
            "monthly_impact":      round(total_loss*30,0),
            "top_issue":           top_cat,
            "avg_health_score":    round(float(df["health_score"].mean()),1),
            "velocity_cliff_count":int(df["velocity_cliff"].sum()),
            "stranded_count":      int(masks["stranded"].sum()),
        },
        "rules_summary":   rules_summary,
        "actionables":     actionables,
        "asp_issues":      asp_issues,
        "oos_issues":      oos_issues,
        "inactive_issues": inactive_issues,
        "innovative_insights":{
            "compounding_issues":{"count":len(compound_list),
                "total_loss_vs_l7d":round(float(compound["loss_vs_l7d"].sum()),1),
                "listings":compound_list},
            "seller_cascade_risk":{"top_sellers":cascade},
            "velocity_cliff_listings":{"count":int(df["velocity_cliff"].sum()),
                "listings":[{"product_title":str(r.get("product_title",""))[:50],
                    "display_name":str(r.get("display_name","")),"name":str(r.get("name","")),
                    "rate_l1d":round(float(r.get("rate_l1d",0)),2),
                    "rate_l30d":round(float(r.get("rate_l30d",0)),2),
                    "health_score":round(float(r.get("health_score",0)),0)}
                  for _,r in df[df["velocity_cliff"]].sort_values("rate_l30d",ascending=False).head(12).iterrows()]},
            "stranded_inventory":{"count":int(masks["stranded"].sum()),"listings":strand},
        },
        "recommendations":{"um_to_kam":um_kam},
    }
