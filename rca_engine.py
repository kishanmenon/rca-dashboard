"""
rca_engine.py — Pure pandas RCA engine. Zero external APIs.
Exact detection rules as specified, with correct DRR calculations.

Window sizes (from actual SQL):
  l30d = 29 days  (date_sub(31) to date_sub(2))
  l7d  = 6 days   (date_sub(8)  to date_sub(2))
  l1d  = 1 day    (date_sub(1))
"""

import difflib
import numpy as np
import pandas as pd

# ── column aliases ─────────────────────────────────────────────────────────────
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
    "flipkart", "fk_", "policy", "quality", "violation", "content",
    "catalog", "price", "image", "blocked", "restricted", "compliance",
    "counterfeit", "prohibited", "inactivated_by_flipkart",
]

INACTIVE_SELLER_STATES = ["inactive", "archived", "seller_inactive"]

# ── sales-friendly rule names ──────────────────────────────────────────────────
# Precedence order: INACTIVE > OOS > PRICE HIKE > DEMAND ISSUES
RULE_PRIORITY = {
    "Deactivated by Flipkart":       1,
    "Seller Switched Off Listing":   2,
    "Out of Stock":                  3,
    "TP Price Hike":                 4,
    "ASP Price Hike":                5,
    "Sudden Order Drop":             6,   # was Velocity Cliff
    "Dead Stock — No Demand":        7,   # was Stranded Stock
    "Active & Selling Well":         99,
}

RULE_DEFINITIONS = {
    "Deactivated by Flipkart": {
        "icon": "🔴", "recovery": 0.40,
        "what": "Flipkart has removed the listing due to a policy, quality, catalog, or compliance issue.",
        "how":  "listing_status = INACTIVE AND latest_deactivation_reason IS NOT NULL AND internal state contains FK/policy/violation keywords",
        "fix":  "Raise a reinstatement ticket with Flipkart Seller Support citing the specific deactivation reason.",
    },
    "Seller Switched Off Listing": {
        "icon": "🟡", "recovery": 0.65,
        "what": "The seller manually switched off their listing — deactivation reason is blank or the internal state shows seller/archived.",
        "how":  "listing_status = INACTIVE AND (latest_deactivation_reason IS NULL OR listing_internal_state in [inactive, archived])",
        "fix":  "Call or message the seller to reactivate. Usually quick to fix.",
    },
    "Out of Stock": {
        "icon": "📦", "recovery": 0.90,
        "what": "Listing is live but the seller has zero units. Every buyer who lands on the page leaves empty-handed.",
        "how":  "listing_status = ACTIVE AND yesterday_atp = 0",
        "fix":  "Seller must replenish stock immediately.",
    },
    "TP Price Hike": {
        "icon": "🏷️", "recovery": 0.70,
        "what": "The listed transaction price (MRP / base price) rose by more than 5% compared to the L7D or L30D average, and orders dropped. Buyers are seeing a higher sticker price.",
        "how":  "l1d_tp > l7d_tp × 1.05 AND l1d_orders < l7d_orders/6   OR   l1d_tp > l30d_tp × 1.05 AND l1d_orders < l30d_orders/29",
        "fix":  "Seller should revert transaction price to the L30D or L7D level.",
    },
    "ASP Price Hike": {
        "icon": "💸", "recovery": 0.75,
        "what": "The average selling price (after discounts) rose by more than 5% compared to the L7D or L30D average, and orders dropped. Buyers are getting a worse deal.",
        "how":  "l1d_asp > l7d_asp × 1.05 AND l1d_orders < l7d_orders/6   OR   l1d_asp > l30d_asp × 1.05 AND l1d_orders < l30d_orders/29",
        "fix":  "Bring ASP back to the best-sales-period level.",
    },
    "Sudden Order Drop": {
        "icon": "📉", "recovery": 0.55,
        "what": "Yesterday's orders fell to less than 15% of the 30-day daily run rate. Something acute happened — check ASP change, stock, listing edits.",
        "how":  "l1d_orders < l30d_orders/29 × 0.15",
        "fix":  "Investigate same-day: ASP change? ATP hit 0? Any listing edits?",
    },
    "Dead Stock — No Demand": {
        "icon": "🧊", "recovery": 0.30,
        "what": "The listing is active and the seller has stock, but fewer than 0.1 orders per day over the last 7 days. Working capital is trapped.",
        "how":  "listing_status = ACTIVE AND yesterday_atp > 0 AND l7d_orders/6 < 0.1",
        "fix":  "Investigate: price too high vs category? Poor catalog? Run a promo.",
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
    return df[col] if col in df.columns else pd.Series(default, index=df.index, dtype=float)


def filter_df(df: pd.DataFrame, dimension: str, value: str) -> pd.DataFrame:
    df = normalise_cols(df.copy())
    if value.upper() == "OVERALL" or value == "" or dimension not in df.columns:
        return df
    mask = df[dimension].astype(str).str.strip().str.lower() == value.strip().lower()
    return df[mask].copy()


def get_dimension_values(df: pd.DataFrame, dimension: str) -> list:
    df2 = normalise_cols(df.copy())
    if dimension not in df2.columns:
        return []
    raw = df2[dimension].dropna().astype(str).str.strip()
    return sorted([v for v in raw.unique().tolist() if v != ""])


# ── DRR helper (uses correct window sizes from SQL) ────────────────────────────
W_L30D = 29   # actual days in l30d window
W_L7D  = 6    # actual days in l7d window
W_L1D  = 1


# ── prepare ────────────────────────────────────────────────────────────────────
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

    # ── correct DRRs using actual window sizes ─────────────────────────────────
    df["drr_l30d"] = df["l30d_orders"] / W_L30D
    df["drr_l7d"]  = df["l7d_orders"]  / W_L7D
    df["drr_l1d"]  = df["l1d_orders"].astype(float)

    # ── order loss: only where L1D DRR is actually lower ──────────────────────
    # loss_vs_l7d  = how many orders/day we lost vs the L7D run rate
    # loss_vs_l30d = how many orders/day we lost vs the L30D run rate
    df["loss_vs_l7d"]  = (df["drr_l7d"]  - df["drr_l1d"]).clip(lower=0)
    df["loss_vs_l30d"] = (df["drr_l30d"] - df["drr_l1d"]).clip(lower=0)

    # gate: only consider order loss if L1D DRR is genuinely lower than at least one window
    df["order_loss_exists"] = (
        (df["drr_l1d"] < df["drr_l7d"]) |
        (df["drr_l1d"] < df["drr_l30d"])
    )

    # primary sort key for actionables
    df["order_loss_per_day"] = df["loss_vs_l7d"]

    # ── price change % for TP and ASP, both windows ───────────────────────────
    for price_col, base_l7d, base_l30d in [
        ("tp",  "l7d_tp",  "l30d_tp"),
        ("asp", "l7d_asp", "l30d_asp"),
    ]:
        l1d = safe(df, f"l1d_{price_col}", 0)
        b7  = safe(df, base_l7d,  0)
        b30 = safe(df, base_l30d, 0)
        df[f"{price_col}_chg_vs_l7d"]  = np.where(b7  > 0, (l1d - b7)  / b7  * 100, 0)
        df[f"{price_col}_chg_vs_l30d"] = np.where(b30 > 0, (l1d - b30) / b30 * 100, 0)

    # ── FK inactivation flag ───────────────────────────────────────────────────
    def is_fk(row):
        # FK inactivated if deactivation_reason is NOT null AND contains FK keywords
        reason = str(row.get("latest_deactivation_reason","")).lower()
        state  = str(row.get("listing_internal_state","")).lower()
        if not reason and not state:
            return False
        # Must have a reason (not null/empty) to be FK
        if not reason:
            return False
        return any(k in reason or k in state for k in FK_KEYWORDS)

    def is_seller_inactive(row):
        reason = str(row.get("latest_deactivation_reason","")).lower().strip()
        state  = str(row.get("listing_internal_state","")).lower().strip()
        # Seller inactive: reason is blank/null OR state is seller/archived
        reason_blank = (reason == "" or reason == "none" or reason == "nan")
        state_seller = any(s in state for s in INACTIVE_SELLER_STATES)
        return reason_blank or state_seller

    status = safe(df,"listing_status","").str.upper()
    inactive_mask = status == "INACTIVE"
    df["inactivated_by_fk"]     = False
    df["inactivated_by_seller"] = False
    if inactive_mask.any():
        df.loc[inactive_mask, "inactivated_by_fk"] = (
            df[inactive_mask].apply(is_fk, axis=1).to_numpy()
        )
        df.loc[inactive_mask, "inactivated_by_seller"] = (
            df[inactive_mask].apply(is_seller_inactive, axis=1).to_numpy()
        )

    # ── velocity / momentum ───────────────────────────────────────────────────
    df["sudden_drop"] = (
        (df["drr_l1d"] / df["drr_l30d"].replace(0, np.nan)).fillna(1) < 0.15
    )

    return df.reset_index(drop=True)


# ── masks ──────────────────────────────────────────────────────────────────────
def get_masks(df: pd.DataFrame) -> dict:
    status = safe(df,"listing_status","").str.upper()
    atp    = safe(df,"yesterday_atp", 1)
    oe     = df.get("order_loss_exists", pd.Series(True, index=df.index))

    # ── price hike masks — separate for TP/ASP × L7D/L30D window ─────────────
    # Rule: price rose >5% vs that window AND orders fell vs that window
    tp_h_l7d  = (df.get("tp_chg_vs_l7d",  pd.Series(0, index=df.index)) > 5) & (df["drr_l1d"] < df["drr_l7d"])
    tp_h_l30d = (df.get("tp_chg_vs_l30d", pd.Series(0, index=df.index)) > 5) & (df["drr_l1d"] < df["drr_l30d"])
    asp_h_l7d  = (df.get("asp_chg_vs_l7d",  pd.Series(0, index=df.index)) > 5) & (df["drr_l1d"] < df["drr_l7d"])
    asp_h_l30d = (df.get("asp_chg_vs_l30d", pd.Series(0, index=df.index)) > 5) & (df["drr_l1d"] < df["drr_l30d"])

    return {
        "active":           status == "ACTIVE",
        "inactive":         status == "INACTIVE",
        "fk_inactive":      (status == "INACTIVE") & df.get("inactivated_by_fk",     pd.Series(False, index=df.index)),
        "seller_inactive":  (status == "INACTIVE") & df.get("inactivated_by_seller", pd.Series(False, index=df.index)),
        "oos":              (status == "ACTIVE") & (atp == 0),
        "tp_hike_l7d":      tp_h_l7d,
        "tp_hike_l30d":     tp_h_l30d,
        "tp_hike":          tp_h_l7d | tp_h_l30d,
        "asp_hike_l7d":     asp_h_l7d,
        "asp_hike_l30d":    asp_h_l30d,
        "asp_hike":         asp_h_l7d | asp_h_l30d,
        "sudden_drop":      df.get("sudden_drop", pd.Series(False, index=df.index)),
        "dead_stock":       (status == "ACTIVE") & (atp > 0) & (df["drr_l7d"] < 0.1),
        "order_loss":       oe,
    }


# ── health score ───────────────────────────────────────────────────────────────
def health_score(df: pd.DataFrame, masks: dict) -> pd.Series:
    s = pd.Series(100.0, index=df.index)
    s -= masks["oos"].astype(float)           * 30
    s -= masks["fk_inactive"].astype(float)   * 28
    s -= masks["seller_inactive"].astype(float)* 22
    asp_f = df.get("asp_chg_vs_l7d", pd.Series(0, index=df.index)).clip(0,50)/50
    s -= masks["asp_hike"].astype(float) * asp_f * 20
    tp_f  = df.get("tp_chg_vs_l7d", pd.Series(0, index=df.index)).clip(0,50)/50
    s -= masks["tp_hike"].astype(float)  * tp_f  * 18
    s -= masks["sudden_drop"].astype(float) * 15
    s -= masks["dead_stock"].astype(float) * 5
    return s.clip(0,100).round(1)


# ── single-rule tagger (precedence: INACTIVE > OOS > PRICE HIKE > DEMAND) ─────
def classify_row(masks, idx, df):
    if masks["fk_inactive"].iloc[idx]:       return "Deactivated by Flipkart"
    if masks["seller_inactive"].iloc[idx]:   return "Seller Switched Off Listing"
    if masks["oos"].iloc[idx]:               return "Out of Stock"
    if masks["tp_hike"].iloc[idx]:           return "TP Price Hike"
    if masks["asp_hike"].iloc[idx]:          return "ASP Price Hike"
    if masks["sudden_drop"].iloc[idx]:       return "Sudden Order Drop"
    if masks["dead_stock"].iloc[idx]:        return "Dead Stock — No Demand"
    return "Active & Selling Well"


# ── tag every raw row for enriched download ───────────────────────────────────
def tag_rows(df: pd.DataFrame) -> pd.DataFrame:
    df = prepare(df)
    masks = get_masks(df)
    df["health_score"] = health_score(df, masks)

    tags, actions = [], []
    loss_l7d_out, loss_l30d_out = [], []

    for pos in range(len(df)):
        row = df.iloc[pos]
        tag = classify_row(masks, pos, df)
        tags.append(tag)

        # per-rule loss amounts (0 where window not triggered)
        if tag == "TP Price Hike":
            l7  = float(row["loss_vs_l7d"])  if masks["tp_hike_l7d"].iloc[pos]  else 0.0
            l30 = float(row["loss_vs_l30d"]) if masks["tp_hike_l30d"].iloc[pos] else 0.0
        elif tag == "ASP Price Hike":
            l7  = float(row["loss_vs_l7d"])  if masks["asp_hike_l7d"].iloc[pos]  else 0.0
            l30 = float(row["loss_vs_l30d"]) if masks["asp_hike_l30d"].iloc[pos] else 0.0
        else:
            l7  = round(float(row.get("loss_vs_l7d",  0)), 1)
            l30 = round(float(row.get("loss_vs_l30d", 0)), 1)

        loss_l7d_out.append(round(l7, 1))
        loss_l30d_out.append(round(l30, 1))

        # action text
        seller = str(row.get("display_name", row.get("seller_id", "")))
        reason = str(row.get("latest_deactivation_reason", ""))
        asp1   = float(row.get("l1d_asp", 0))
        asp30  = float(row.get("l30d_asp", 0))
        tp1    = float(row.get("l1d_tp",  0))
        tp30   = float(row.get("l30d_tp",  0))

        if tag == "Deactivated by Flipkart":
            actions.append(f"Raise FK reinstatement ticket. Reason: '{reason or 'check FK portal'}'. Loss: {l7:.1f} orders/day vs L7D.")
        elif tag == "Seller Switched Off Listing":
            actions.append(f"Call seller {seller} to reactivate listing. Loss: {l7:.1f} orders/day vs L7D.")
        elif tag == "Out of Stock":
            actions.append(f"Restock now — {seller} has 0 units. Loss: {l7:.1f}/day vs L7D, {l30:.1f}/day vs L30D.")
        elif tag == "TP Price Hike":
            rec = round(tp30, 0) if tp30 > 0 else round(tp1*0.95, 0)
            actions.append(f"Revert transaction price from ₹{tp1:,.0f} → ₹{rec:,.0f}. Loss: L7D={l7:.1f}/day, L30D={l30:.1f}/day.")
        elif tag == "ASP Price Hike":
            rec = round(asp30, 0) if asp30 > 0 else round(asp1*0.95, 0)
            actions.append(f"Lower selling price from ₹{asp1:,.0f} → ₹{rec:,.0f}. Loss: L7D={l7:.1f}/day, L30D={l30:.1f}/day.")
        elif tag == "Sudden Order Drop":
            actions.append(f"Orders dropped >85% yesterday vs 30D avg. Investigate: ASP? Stock? Listing edit?")
        elif tag == "Dead Stock — No Demand":
            actions.append(f"Stock exists but no buyers. Check pricing vs category, catalog quality, visibility.")
        else:
            actions.append("")

    df["rca_tag"]           = tags
    df["order_loss_vs_l7d"]  = loss_l7d_out
    df["order_loss_vs_l30d"] = loss_l30d_out
    df["recommended_action"] = actions
    df["health_score"]       = df["health_score"]

    non_h = df[df["rca_tag"] != "Active & Selling Well"].sort_values("order_loss_vs_l7d", ascending=False)
    df["priority_rank"] = 0
    df.loc[non_h.index, "priority_rank"] = range(1, len(non_h)+1)

    return df


# ── base dict helper ───────────────────────────────────────────────────────────
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
        "drr_l30d":          round(float(r.get("drr_l30d",0)),2),
        "drr_l7d":           round(float(r.get("drr_l7d", 0)),2),
        "drr_l1d":           round(float(r.get("drr_l1d", 0)),2),
        "loss_vs_l7d":       round(float(r.get("loss_vs_l7d",  0)),1),
        "loss_vs_l30d":      round(float(r.get("loss_vs_l30d", 0)),1),
        "order_loss_per_day":round(float(r.get("order_loss_per_day",0)),1),
        "health_score":      round(float(r.get("health_score",0)),0),
    }


# ── build issue sections ───────────────────────────────────────────────────────
def build_price_hike_issues(df, masks, price_type):
    """price_type = 'tp' or 'asp'"""
    hike_mask  = masks[f"{price_type}_hike"]
    hike_l7d   = masks[f"{price_type}_hike_l7d"]
    hike_l30d  = masks[f"{price_type}_hike_l30d"]

    result = []
    subset = df[hike_mask].copy()
    subset["_sort"] = subset["loss_vs_l7d"]
    subset = subset.sort_values("_sort", ascending=False).head(30)

    for _, r in subset.iterrows():
        idx = r.name
        d = base_dict(r)
        triggered_l7d  = hike_l7d.loc[idx]  if idx in hike_l7d.index  else False
        triggered_l30d = hike_l30d.loc[idx] if idx in hike_l30d.index else False

        d.update({
            f"l30d_{price_type}":   round(float(r.get(f"l30d_{price_type}",0)),0),
            f"l7d_{price_type}":    round(float(r.get(f"l7d_{price_type}",0)),0),
            f"l1d_{price_type}":    round(float(r.get(f"l1d_{price_type}",0)),0),
            f"{price_type}_chg_vs_l7d":  round(float(r.get(f"{price_type}_chg_vs_l7d",0)),1),
            f"{price_type}_chg_vs_l30d": round(float(r.get(f"{price_type}_chg_vs_l30d",0)),1),
            "triggered_vs_l7d":    bool(triggered_l7d),
            "triggered_vs_l30d":   bool(triggered_l30d),
            # plot 0 for windows not triggered
            "plot_loss_vs_l7d":    round(float(r.get("loss_vs_l7d",0)),1)  if triggered_l7d  else 0,
            "plot_loss_vs_l30d":   round(float(r.get("loss_vs_l30d",0)),1) if triggered_l30d else 0,
            "l7d_orders":          round(float(r.get("l7d_orders",0)),0),
            "l30d_orders":         round(float(r.get("l30d_orders",0)),0),
            "l1d_orders":          round(float(r.get("l1d_orders",0)),0),
            "recommended_asp":     round(float(r.get(f"l30d_{price_type}",0)),0),
            "action": (
                f"Revert {'transaction price' if price_type=='tp' else 'selling price'} "
                f"from ₹{round(float(r.get(f'l1d_{price_type}',0)),0):,.0f} "
                f"→ ₹{round(float(r.get(f'l30d_{price_type}',0)),0):,.0f}. "
                f"{'L7D loss: ' + str(round(float(r.get('loss_vs_l7d',0)),1)) + '/day. ' if triggered_l7d else ''}"
                f"{'L30D loss: ' + str(round(float(r.get('loss_vs_l30d',0)),1)) + '/day.' if triggered_l30d else ''}"
            ),
        })
        result.append(d)
    return result


def build_oos_issues(df, masks):
    result = []
    for _, r in df[masks["oos"]].sort_values("loss_vs_l7d", ascending=False).head(30).iterrows():
        d = base_dict(r)
        d.update({
            "yesterday_atp": int(r.get("yesterday_atp",0)),
            "l7d_orders":    round(float(r.get("l7d_orders",0)),0),
            "l30d_orders":   round(float(r.get("l30d_orders",0)),0),
            "l1d_orders":    round(float(r.get("l1d_orders",0)),0),
            "l7d_gmv":       round(float(r.get("l7d_gmv",0)),0),
            # OOS: plot BOTH loss columns
            "plot_loss_vs_l7d":  round(float(r.get("loss_vs_l7d",0)),1),
            "plot_loss_vs_l30d": round(float(r.get("loss_vs_l30d",0)),1),
            "action": (
                f"Restock now — {r.get('display_name',r.get('seller_id',''))} has 0 units. "
                f"Loss: {round(float(r.get('loss_vs_l7d',0)),1)}/day vs L7D, "
                f"{round(float(r.get('loss_vs_l30d',0)),1)}/day vs L30D."
            ),
        })
        result.append(d)
    return result


def build_inactive_issues(df, masks):
    def section(subset, grp_col, itype):
        total_l7d  = float(subset["loss_vs_l7d"].sum())
        total_l30d = float(subset["loss_vs_l30d"].sum())
        by_grp = []
        if grp_col and grp_col in subset.columns:
            g = (subset.groupby(grp_col, dropna=False, observed=True)
                 .agg(count=("loss_vs_l7d","count"),
                      loss_l7d=("loss_vs_l7d","sum"),
                      loss_l30d=("loss_vs_l30d","sum"))
                 .reset_index().sort_values("loss_l7d", ascending=False))
            by_grp = [{
                ("reason" if itype=="fk" else "seller"): str(r[grp_col]),
                "count": int(r["count"]),
                "loss_vs_l7d":  round(float(r["loss_l7d"]),1),
                "loss_vs_l30d": round(float(r["loss_l30d"]),1),
            } for _, r in g.head(15).iterrows()]
        top_l = []
        for _, r in subset.sort_values("loss_vs_l7d", ascending=False).head(20).iterrows():
            d = base_dict(r)
            d["latest_deactivation_reason"] = str(r.get("latest_deactivation_reason",""))
            d["listing_internal_state"]     = str(r.get("listing_internal_state",""))
            d["l7d_orders"]   = round(float(r.get("l7d_orders",0)),0)
            d["l30d_orders"]  = round(float(r.get("l30d_orders",0)),0)
            d["l1d_orders"]   = round(float(r.get("l1d_orders",0)),0)
            d["plot_loss_vs_l7d"]  = round(float(r.get("loss_vs_l7d",0)),1)
            d["plot_loss_vs_l30d"] = round(float(r.get("loss_vs_l30d",0)),1)
            d["action"] = (
                f"Raise FK reinstatement ticket — reason: {r.get('latest_deactivation_reason','unknown')}"
                if itype=="fk"
                else f"Contact seller {r.get('display_name',r.get('seller_id',''))} to reactivate."
            )
            top_l.append(d)
        key = "by_reason" if itype=="fk" else "by_seller"
        return {
            "total_count": int(len(subset)),
            "total_loss_vs_l7d":  round(total_l7d,1),
            "total_loss_vs_l30d": round(total_l30d,1),
            key: by_grp,
            "top_listings": top_l,
        }

    return {
        "fk_inactivated":     section(df[masks["fk_inactive"]],     "latest_deactivation_reason","fk"),
        "seller_inactivated": section(df[masks["seller_inactive"]], "display_name","seller"),
    }


# ── UM → KAM: qualify on L1D DRR > L7D DRR > L30D DRR ────────────────────────
def build_um_to_kam(df):
    if "owner" not in df.columns:
        return []
    grp_col = "display_name" if "display_name" in df.columns else "seller_id"
    if grp_col not in df.columns:
        return []

    um_df = df[df["owner"].str.upper().str.contains("UM", na=False)]
    if um_df.empty:
        return []

    ss = um_df.groupby(grp_col, observed=True).agg(
        l30d_orders=("l30d_orders","sum"),
        l7d_orders =("l7d_orders", "sum"),
        l1d_orders =("l1d_orders", "sum"),
        avg_asp    =("l7d_asp","mean"),
        avg_health =("health_score","mean"),
    ).reset_index()

    ss["drr_l30d"]      = ss["l30d_orders"] / W_L30D
    ss["drr_l7d"]       = ss["l7d_orders"]  / W_L7D
    ss["drr_l1d"]       = ss["l1d_orders"]

    # Growth %
    ss["growth_l30d_to_l1d"] = np.where(
        ss["drr_l30d"] > 0,
        (ss["drr_l1d"] - ss["drr_l30d"]) / ss["drr_l30d"] * 100, 0
    )
    ss["growth_l7d_to_l1d"] = np.where(
        ss["drr_l7d"] > 0,
        (ss["drr_l1d"] - ss["drr_l7d"]) / ss["drr_l7d"] * 100, 0
    )

    # Continuously growing: L1D DRR > L7D DRR > L30D DRR
    ss["continuously_growing"] = (
        (ss["drr_l1d"] > ss["drr_l7d"]) &
        (ss["drr_l7d"] > ss["drr_l30d"])
    )

    # Score
    ss["score"] = (
        ss["l30d_orders"].rank(pct=True) * 40 +
        ss["avg_health"].rank(pct=True)  * 30 +
        ss["growth_l30d_to_l1d"].clip(-100,100).rank(pct=True) * 30
    )
    ss = ss.sort_values("score", ascending=False)

    result = []
    for _, r in ss.head(10).iterrows():
        cg = bool(r["continuously_growing"])
        trend = "📈 Growing (L30D→L7D→L1D)" if cg else (
            "📈 Growing vs L30D" if r["growth_l30d_to_l1d"] > 10 else
            "📉 Declining" if r["growth_l30d_to_l1d"] < -10 else "➡️ Stable"
        )
        result.append({
            "seller":              str(r[grp_col]),
            "drr_l30d":            round(float(r["drr_l30d"]),1),
            "drr_l7d":             round(float(r["drr_l7d"]),1),
            "drr_l1d":             round(float(r["drr_l1d"]),1),
            "l30d_orders":         round(float(r["l30d_orders"]),0),
            "avg_asp":             round(float(r["avg_asp"]),0),
            "health":              round(float(r["avg_health"]),0),
            "growth_l30d_to_l1d":  round(float(r["growth_l30d_to_l1d"]),1),
            "growth_l7d_to_l1d":   round(float(r["growth_l7d_to_l1d"]),1),
            "continuously_growing": cg,
            "trend":               trend,
        })
    return result


# ── Google Trends fuzzy match ──────────────────────────────────────────────────
def get_search_trend_alignment(df, top_n=10):
    """
    Fuzzy-match product titles against Google Trends.
    Returns list of matches with trend data.
    Requires: pip install pytrends
    """
    try:
        from pytrends.request import TrendReq

        # Get top product titles by order volume
        if "product_title" not in df.columns or "l7d_orders" not in df.columns:
            return []

        top_products = (
            df.groupby("product_title", observed=True)["l7d_orders"]
            .sum().sort_values(ascending=False).head(top_n).index.tolist()
        )
        if not top_products:
            return []

        # Extract short search-friendly keywords (first 3 words)
        keywords = []
        keyword_to_product = {}
        for title in top_products:
            words = str(title).split()[:4]
            kw = " ".join(words).strip()
            if kw and kw not in keywords:
                keywords.append(kw)
                keyword_to_product[kw] = title

        # Google Trends API (India, last 7 days)
        pt = TrendReq(hl="en-IN", tz=330, timeout=(10, 25), retries=2)
        results = []

        # Process in batches of 5 (API limit)
        for i in range(0, min(len(keywords), 15), 5):
            batch = keywords[i:i+5]
            try:
                pt.build_payload(batch, cat=0, timeframe="now 7-d", geo="IN")
                interest = pt.interest_over_time()
                if interest.empty:
                    continue
                avg_interest = interest[batch].mean()

                for kw in batch:
                    if kw not in avg_interest:
                        continue
                    trend_score = float(avg_interest[kw])
                    original_title = keyword_to_product.get(kw, kw)

                    # match back to product data
                    prod_data = df[df["product_title"] == original_title]
                    drr_l7d  = float(prod_data["drr_l7d"].sum())  if "drr_l7d"  in prod_data.columns else 0
                    drr_l30d = float(prod_data["drr_l30d"].sum()) if "drr_l30d" in prod_data.columns else 0
                    drr_l1d  = float(prod_data["drr_l1d"].sum())  if "drr_l1d"  in prod_data.columns else 0
                    atp      = float(prod_data["yesterday_atp"].sum()) if "yesterday_atp" in prod_data.columns else 0

                    # Demand-supply gap: high search interest but low stock
                    gap_flag = trend_score > 50 and atp < 10

                    results.append({
                        "search_keyword":    kw,
                        "product_title":     original_title[:60],
                        "google_trend_score": round(trend_score, 0),
                        "drr_l1d":           round(drr_l1d, 1),
                        "drr_l7d":           round(drr_l7d, 1),
                        "drr_l30d":          round(drr_l30d, 1),
                        "total_atp":         round(atp, 0),
                        "demand_supply_gap": gap_flag,
                        "action": (
                            f"⚠️ High search interest (score {trend_score:.0f}/100) but only {atp:.0f} units in stock. Restock urgently."
                            if gap_flag
                            else f"Search interest {trend_score:.0f}/100. Orders tracking at {drr_l7d:.1f}/day (L7D DRR)."
                        ),
                    })
            except Exception:
                continue

        return sorted(results, key=lambda x: x["google_trend_score"], reverse=True)

    except ImportError:
        return [{"search_keyword": "pytrends not installed",
                 "product_title": "Add pytrends to requirements.txt to enable Google Trends",
                 "google_trend_score": 0, "drr_l1d": 0, "drr_l7d": 0, "drr_l30d": 0,
                 "total_atp": 0, "demand_supply_gap": False,
                 "action": "pip install pytrends"}]
    except Exception as e:
        return [{"search_keyword": "Error fetching trends",
                 "product_title": str(e)[:80],
                 "google_trend_score": 0, "drr_l1d": 0, "drr_l7d": 0, "drr_l30d": 0,
                 "total_atp": 0, "demand_supply_gap": False, "action": "Check internet / API limits"}]


# ── master actionables ─────────────────────────────────────────────────────────
def build_actionables(oos, tp_hike, asp_hike, inactive):
    all_issues = (
        [("Deactivated by Flipkart",     i, "Deactivated by Flipkart")
         for i in inactive["fk_inactivated"]["top_listings"]] +
        [("Seller Switched Off Listing", i, "Seller Switched Off Listing")
         for i in inactive["seller_inactivated"]["top_listings"]] +
        [("Out of Stock",                i, "Out of Stock")    for i in oos]   +
        [("TP Price Hike",               i, "TP Price Hike")   for i in tp_hike] +
        [("ASP Price Hike",              i, "ASP Price Hike")  for i in asp_hike]
    )
    # Sort by precedence first, then by loss_vs_l7d within same rule
    all_issues.sort(key=lambda x: (
        RULE_PRIORITY.get(x[0], 99),
        -x[1].get("plot_loss_vs_l7d", x[1].get("loss_vs_l7d", 0))
    ))

    actionables = []
    for pri, (rule, item, tag) in enumerate(all_issues, 1):
        l7  = float(item.get("plot_loss_vs_l7d",  item.get("loss_vs_l7d",  0)))
        l30 = float(item.get("plot_loss_vs_l30d", item.get("loss_vs_l30d", 0)))
        rec = RECOVERY_PROB.get(tag, 0.5)
        actionables.append({
            "priority":           pri,
            "rule":               rule,
            "product_title":      item.get("product_title",""),
            "listing_id":         item.get("listing_id",""),
            "seller_id":          item.get("seller_id",""),
            "display_name":       item.get("display_name",""),
            "owner":              item.get("owner",""),
            "name":               item.get("name",""),
            "sell_bu":            item.get("sell_bu",""),
            "analytic_category":  item.get("analytic_category",""),
            "drr_l30d":           item.get("drr_l30d",""),
            "drr_l7d":            item.get("drr_l7d",""),
            "drr_l1d":            item.get("drr_l1d",""),
            "loss_vs_l7d":        round(l7, 1),
            "loss_vs_l30d":       round(l30, 1),
            "monthly_impact":     round(l7 * 30, 0),
            "recovery_per_day":   round(l7 * rec, 1),
            "action":             item.get("action",""),
            "health_score":       item.get("health_score",""),
        })
    return actionables


# ── rules summary ──────────────────────────────────────────────────────────────
def build_rules_summary(df, masks):
    rule_data = [
        ("Deactivated by Flipkart",      masks["fk_inactive"]),
        ("Seller Switched Off Listing",  masks["seller_inactive"]),
        ("Out of Stock",                 masks["oos"]),
        ("TP Price Hike",                masks["tp_hike"]),
        ("ASP Price Hike",               masks["asp_hike"]),
        ("Sudden Order Drop",            masks["sudden_drop"]),
        ("Dead Stock — No Demand",       masks["dead_stock"]),
    ]
    summary = []
    for rule_name, mask in rule_data:
        sub = df[mask]
        if sub.empty:
            continue
        defn = RULE_DEFINITIONS.get(rule_name, {})
        top_owners = list(sub["name"].value_counts().head(3).index.tolist()) if "name" in sub.columns else []
        summary.append({
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
    summary.sort(key=lambda x: x["loss_vs_l7d"], reverse=True)
    return summary


# ── innovative insights (with plain English names) ─────────────────────────────
def build_innovative_insights(df, masks):
    # Compounding = Multiple Problems at Once
    issue_flags = pd.DataFrame({
        "inactive": masks["inactive"],
        "oos":      masks["oos"],
        "tp_hike":  masks["tp_hike"],
        "asp_hike": masks["asp_hike"],
        "drop":     masks["sudden_drop"],
        "dead":     masks["dead_stock"],
    })
    df["issue_count"] = issue_flags.sum(axis=1)
    compound = df[df["issue_count"] >= 2].sort_values("loss_vs_l7d", ascending=False)
    compound_list = [{
        "product_title": str(r.get("product_title",""))[:55],
        "display_name":  str(r.get("display_name","")),
        "name":          str(r.get("name","")),
        "issue_count":   int(r.get("issue_count",0)),
        "drr_l1d":       round(float(r.get("drr_l1d",0)),2),
        "drr_l7d":       round(float(r.get("drr_l7d",0)),2),
        "drr_l30d":      round(float(r.get("drr_l30d",0)),2),
        "problems": ", ".join(filter(None,[
            "Inactive"     if masks["inactive"].loc[i]   else "",
            "OOS"          if masks["oos"].loc[i]        else "",
            "TP Hike"      if masks["tp_hike"].loc[i]    else "",
            "ASP Hike"     if masks["asp_hike"].loc[i]   else "",
            "Order Drop"   if masks["sudden_drop"].loc[i] else "",
            "Dead Stock"   if masks["dead_stock"].loc[i]  else "",
        ])),
        "loss_vs_l7d":  round(float(r.get("loss_vs_l7d",0)),1),
        "health_score": round(float(r.get("health_score",0)),0),
    } for i, r in compound.head(15).iterrows()]

    # High Dependency Sellers (was Cascade Risk)
    grp_col = "display_name" if "display_name" in df.columns else "seller_id"
    cascade = []
    if grp_col in df.columns:
        cdf = df.groupby(grp_col, observed=True).agg(
            listings      =(grp_col,"count"),
            orders_per_day=("drr_l7d","sum"),
            oos_count     =("yesterday_atp", lambda x:(x==0).sum()),
        ).reset_index()
        cdf["risk_score"] = (cdf["orders_per_day"] * 1.2).round(1)
        for _, r in cdf.sort_values("risk_score", ascending=False).head(10).iterrows():
            cascade.append({"seller":str(r[grp_col]),"listings":int(r["listings"]),
                            "orders_per_day":round(float(r["orders_per_day"]),1),
                            "oos_count":int(r["oos_count"]),"risk_score":float(r["risk_score"])})

    # Sudden Order Drop (was Velocity Cliff)
    drop_list = [{
        "product_title": str(r.get("product_title",""))[:50],
        "display_name":  str(r.get("display_name","")),
        "name":          str(r.get("name","")),
        "drr_l1d":       round(float(r.get("drr_l1d",0)),2),
        "drr_l7d":       round(float(r.get("drr_l7d",0)),2),
        "drr_l30d":      round(float(r.get("drr_l30d",0)),2),
        "health_score":  round(float(r.get("health_score",0)),0),
    } for _, r in df[masks["sudden_drop"]].sort_values("drr_l30d", ascending=False).head(12).iterrows()]

    # Dead Stock
    dead_list = [{
        "product_title": str(r.get("product_title",""))[:50],
        "display_name":  str(r.get("display_name","")),
        "yesterday_atp": int(r.get("yesterday_atp",0)),
        "drr_l1d":       round(float(r.get("drr_l1d",0)),2),
        "drr_l7d":       round(float(r.get("drr_l7d",0)),2),
        "drr_l30d":      round(float(r.get("drr_l30d",0)),2),
        "l7d_asp":       round(float(r.get("l7d_asp",0)),0),
    } for _, r in df[masks["dead_stock"]].sort_values("yesterday_atp", ascending=False).head(12).iterrows()]

    return {
        "multiple_problems_at_once": {
            "label":   "⚡ Multiple Problems at Once",
            "desc":    "Listings with 2+ issues simultaneously — losing orders from multiple directions",
            "count":   len(compound_list),
            "total_loss_vs_l7d": round(float(compound["loss_vs_l7d"].sum()),1) if not compound.empty else 0,
            "listings": compound_list,
        },
        "high_dependency_sellers": {
            "label":   "🌊 High Dependency Sellers",
            "desc":    "If these sellers go inactive, you lose their full daily order run rate in one shot",
            "top_sellers": cascade,
        },
        "sudden_order_drop": {
            "label":   "📉 Sudden Order Drop",
            "desc":    "Orders fell to <15% of 30-day run rate yesterday — something acute happened",
            "count":   int(df["sudden_drop"].sum()),
            "listings": drop_list,
        },
        "dead_stock_no_demand": {
            "label":   "🧊 Dead Stock — No Demand",
            "desc":    "Stock available but barely any orders — trapped working capital",
            "count":   int(masks["dead_stock"].sum()),
            "listings": dead_list,
        },
    }


# ── MAIN RCA FUNCTION ──────────────────────────────────────────────────────────
def run_rca(df_raw: pd.DataFrame, dimension: str, value: str, date_str: str) -> dict:
    df = filter_df(df_raw, dimension, value)
    if df.empty:
        return {"error": f"No rows found for {dimension} = '{value}'"}

    df = prepare(df)
    masks = get_masks(df)
    df["health_score"] = health_score(df, masks)

    # build sections
    tp_issues   = build_price_hike_issues(df, masks, "tp")
    asp_issues  = build_price_hike_issues(df, masks, "asp")
    oos_issues  = build_oos_issues(df, masks)
    inact       = build_inactive_issues(df, masks)
    actionables = build_actionables(oos_issues, tp_issues, asp_issues, inact)
    rules_sum   = build_rules_summary(df, masks)
    innov       = build_innovative_insights(df, masks)
    um_kam      = build_um_to_kam(df)
    trends      = get_search_trend_alignment(df)

    # summary
    fk_l7  = float(df[masks["fk_inactive"]]["loss_vs_l7d"].sum())
    sel_l7 = float(df[masks["seller_inactive"]]["loss_vs_l7d"].sum())
    oos_l7 = float(df[masks["oos"]]["loss_vs_l7d"].sum())
    ph_l7  = float(df[masks["tp_hike"] | masks["asp_hike"]]["loss_vs_l7d"].sum())
    total_l7  = fk_l7 + sel_l7 + oos_l7 + ph_l7
    total_l30 = float(df[masks["fk_inactive"] | masks["seller_inactive"] |
                         masks["oos"] | masks["tp_hike"] | masks["asp_hike"]]["loss_vs_l30d"].sum())

    top_issue = max([
        ("Deactivated by Flipkart",     fk_l7),
        ("Seller Switched Off Listing", sel_l7),
        ("Out of Stock",                oos_l7),
        ("Price Hike",                  ph_l7),
    ], key=lambda x: x[1])[0]

    return {
        "meta": {
            "dimension": dimension, "value": value, "date": date_str,
            "total_rows": int(len(df)),
            "active":     int(masks["active"].sum()),
            "inactive":   int(masks["inactive"].sum()),
            "oos_count":  int(masks["oos"].sum()),
            "tp_hike_count":  int(masks["tp_hike"].sum()),
            "asp_hike_count": int(masks["asp_hike"].sum()),
        },
        "summary": {
            "total_loss_vs_l7d":       round(total_l7, 1),
            "total_loss_vs_l30d":      round(total_l30, 1),
            "total_l1d_orders":        round(float(df["l1d_orders"].sum()), 0),
            "total_drr_l7d":           round(float(df["drr_l7d"].sum()), 1),
            "total_drr_l30d":          round(float(df["drr_l30d"].sum()), 1),
            "monthly_impact":          round(total_l7 * 30, 0),
            "top_issue":               top_issue,
            "avg_health_score":        round(float(df["health_score"].mean()), 1),
            "sudden_drop_count":       int(df["sudden_drop"].sum()),
            "dead_stock_count":        int(masks["dead_stock"].sum()),
        },
        "rules_summary":   rules_sum,
        "actionables":     actionables,
        "tp_hike_issues":  tp_issues,
        "asp_hike_issues": asp_issues,
        "oos_issues":      oos_issues,
        "inactive_issues": inact,
        "innovative_insights": innov,
        "recommendations": {
            "um_to_kam":     um_kam,
            "search_trends": trends,
        },
    }
