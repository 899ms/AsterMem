"""
AI Usage REST API (query side of the unified gateway observability)

Background: After all AI calls are persisted via usage_tracker, the usage page needs
three view layers: overview, aggregation, and detail, plus a write endpoint for
backfilling pricing on unpriced models.
Design intent: Standalone router (/api/usage), reuses verify_session from the main API;
costs are never persisted — aggregated rows retain (provider, model) and costs are
computed on-the-fly using current pricing here, so price changes take immediate effect
on historical records.
Key constraints: Override prices are written to pricing.overrides in config.yaml and
persisted via _save_config.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from memory.pricing import calculate_cost_usd, resolve_pricing, usd_rate
from memory.usage_tracker import get_usage_tracker
from web.api import verify_session

router = APIRouter(prefix="/api/usage", tags=["usage"])

_config: Optional[dict] = None
_save_config = None


def init_usage_api(config: dict, save_config=None):
    global _config, _save_config
    _config = config
    _save_config = save_config


def get_tracker():
    tracker = get_usage_tracker()
    if tracker is None:
        raise HTTPException(status_code=500, detail="Usage tracker not initialized")
    return tracker


def _cost_of(row: Dict[str, Any], rate: float) -> Optional[float]:
    """Compute cost (USD) on-the-fly for a single record or aggregate group; returns None if unpriced"""
    pricing = resolve_pricing(row.get("model") or "", _config, row.get("provider") or "")
    return calculate_cost_usd(
        row.get("prompt_tokens") or 0,
        row.get("completion_tokens") or 0,
        row.get("cached_tokens") or 0,
        pricing,
        rate,
    )


def _merge_groups(rows: List[Dict[str, Any]], key_fields: List[str], rate: float) -> List[Dict[str, Any]]:
    """
    Groups from aggregate() carry (provider, model) for pricing lookup;
    here we compute cost per group first, then merge by the target dimension
    (caller / day / model). When a group is unpriced, its dimension's cost
    is set to None and flagged as unpriced.
    """
    merged: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(f) for f in key_fields)
        bucket = merged.setdefault(key, {
            **{f: row.get(f) for f in key_fields},
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "cached_tokens": 0, "errors": 0,
            "cost_usd": 0.0, "unpriced": False,
        })
        bucket["calls"] += row.get("calls") or 0
        bucket["errors"] += row.get("errors") or 0
        for f in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
            bucket[f] += row.get(f) or 0
        cost = _cost_of(row, rate)
        if cost is None:
            bucket["unpriced"] = True
        else:
            bucket["cost_usd"] += cost
    return list(merged.values())


@router.get("/summary")
async def usage_summary(days: int = 30, admin_id: int = Depends(verify_session)):
    """Overview + aggregation by caller / model / day / kind, with costs computed using current pricing"""
    tracker = get_tracker()
    since = None
    if days > 0:
        since = (datetime.now() - timedelta(days=days)).isoformat()
    agg = tracker.aggregate(since)
    rate = usd_rate(_config)

    by_caller = _merge_groups(agg["by_caller"], ["caller"], rate)
    by_caller.sort(key=lambda x: x["total_tokens"], reverse=True)
    by_kind = _merge_groups(agg["by_caller"], ["kind"], rate)
    by_day = _merge_groups(agg["by_day"], ["day"], rate)
    by_day.sort(key=lambda x: x["day"] or "")
    # day × caller: data source for the frontend daily usage stacked chart
    by_day_caller = _merge_groups(agg["by_day"], ["day", "caller"], rate)
    by_day_caller.sort(key=lambda x: (x["day"] or "", -(x["total_tokens"] or 0)))

    by_model = []
    unpriced_models = []
    for row in agg["by_model"]:
        pricing = resolve_pricing(row.get("model") or "", _config, row.get("provider") or "")
        cost = calculate_cost_usd(row.get("prompt_tokens") or 0, row.get("completion_tokens") or 0,
                                  row.get("cached_tokens") or 0, pricing, rate)
        item = {**row, "cost_usd": cost,
                "pricing_source": pricing["source"] if pricing else None}
        by_model.append(item)
        if pricing is None and row.get("model"):
            unpriced_models.append(row["model"])

    totals = dict(agg["totals"])
    for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens",
                "cached_tokens", "errors"):
        totals[key] = totals.get(key) or 0
    priced = [m["cost_usd"] for m in by_model if m["cost_usd"] is not None]
    totals["cost_usd"] = sum(priced)
    totals["has_unpriced"] = len(unpriced_models) > 0

    return {
        "days": days,
        "totals": totals,
        "by_caller": by_caller,
        "by_kind": by_kind,
        "by_day": by_day,
        "by_day_caller": by_day_caller,
        "by_model": by_model,
        "unpriced_models": sorted(set(unpriced_models)),
    }


@router.get("/logs")
async def usage_logs(limit: int = 50, offset: int = 0,
                     caller: Optional[str] = None, kind: Optional[str] = None,
                     status: Optional[str] = None,
                     admin_id: int = Depends(verify_session)):
    tracker = get_tracker()
    result = tracker.get_logs(limit=min(limit, 200), offset=offset,
                              caller=caller, kind=kind, status=status)
    rate = usd_rate(_config)
    for row in result["logs"]:
        row["cost_usd"] = _cost_of(row, rate)
    return result


@router.delete("/logs")
async def clear_usage_logs(admin_id: int = Depends(verify_session)):
    get_tracker().clear()
    return {"success": True, "message": "AI usage logs cleared"}


# ---------- Pricing ----------

class PricingOverride(BaseModel):
    model: str
    input: Optional[float] = None
    output: Optional[float] = None
    cached_input: Optional[float] = None
    currency: str = "USD"
    # both input/output being None means delete this override entry


@router.get("/pricing")
async def get_pricing(admin_id: int = Depends(verify_session)):
    """All observed models with their effective pricing (including source), plus all user overrides"""
    tracker = get_tracker()
    models = []
    for row in tracker.distinct_models():
        pricing = resolve_pricing(row.get("model") or "", _config, row.get("provider") or "")
        models.append({**row, "pricing": pricing})
    overrides = ((_config or {}).get("pricing") or {}).get("overrides") or {}
    return {"models": models, "overrides": overrides,
            "usd_to_cny": usd_rate(_config)}


@router.put("/pricing")
async def put_pricing(data: PricingOverride, admin_id: int = Depends(verify_session)):
    """Write or delete a single model's pricing override and persist to config.yaml"""
    if _config is None:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    model = (data.model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model cannot be empty")

    pricing_cfg = _config.setdefault("pricing", {})
    overrides = pricing_cfg.setdefault("overrides", {})
    if data.input is None and data.output is None:
        overrides.pop(model, None)
    else:
        entry: Dict[str, Any] = {
            "input": float(data.input or 0),
            "output": float(data.output or 0),
            "currency": data.currency if data.currency in ("CNY", "USD") else "USD",
        }
        if data.cached_input is not None:
            entry["cached_input"] = float(data.cached_input)
        overrides[model] = entry

    if _save_config:
        _save_config()
    return {"success": True, "overrides": overrides}
