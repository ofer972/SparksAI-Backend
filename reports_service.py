"""
Reports Service - REST API endpoints for report metadata and resolved datasets.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, List
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.engine import Connection

from database_connection import get_db_connection
from database_reports import (
    get_all_report_definitions,
    get_report_definition_by_id,
    resolve_report_data,
)
from cache_utils import (
    generate_cache_key,
    get_cached_report,
    set_cached_report,
    get_report_cache_ttl,
    invalidate_report_cache,
    get_redis_client,
)
import config
from config import get_jira_url

reports_router = APIRouter()


def _normalize_filter_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, list):
        normalized_list: List[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    normalized_list.append(stripped)
            else:
                normalized_list.append(item)
        return normalized_list

    if isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped != "" else None

    return value


def _merge_filters(default_filters: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(default_filters or {})
    for key, value in overrides.items():
        normalized = _normalize_filter_value(value)
        merged[key] = normalized
    return merged


def _validate_required_filters(definition: Dict[str, Any], filters: Dict[str, Any]) -> None:
    meta_schema = definition.get("meta_schema") or {}
    required_filters = meta_schema.get("required_filters") or []

    missing = []
    for filter_key in required_filters:
        value = filters.get(filter_key)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            missing.append(filter_key)

    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Missing required filters",
                "missing_filters": missing,
            },
        )


def _normalize_multi_value(values: Optional[List[str] | str]) -> Optional[List[str]]:
    if values is None:
        return None
    if isinstance(values, list):
        normalized: List[str] = []
        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                parts = [part.strip() for part in value.split(",") if part.strip()]
                normalized.extend(parts)
            else:
                normalized.append(str(value))
        return normalized if normalized else None
    if isinstance(values, str):
        parts = [part.strip() for part in values.split(",") if part.strip()]
        return parts if parts else None
    return [str(values)]


@reports_router.get("/reports")
async def list_reports(
    conn: Connection = Depends(get_db_connection),
    bypass_cache: Optional[bool] = Query(False, description="Skip cache lookup"),
):
    """
    Return all available report definitions.
    """
    # Try cache first (definitions change rarely, so use a long TTL)
    cache_key = "report:definitions:all"
    
    if not bypass_cache:
        cached_data = get_cached_report(cache_key)
        if cached_data:
            return {
                "success": True,
                "data": cached_data.get("data", []),
                "count": cached_data.get("count", 0),
                "message": cached_data.get("message", "Retrieved report definitions (cached)"),
                "cached": True,
            }
    
    definitions = get_all_report_definitions(conn)
    summaries = [
        {
            "report_id": definition["report_id"],
            "report_name": definition["report_name"],
            "chart_type": definition["chart_type"],
            "description": definition.get("description"),
            "data_source": definition.get("data_source"),
            "default_filters": definition.get("default_filters"),
            "meta_schema": definition.get("meta_schema"),
        }
        for definition in definitions
    ]

    response_data = {
        "data": summaries,
        "count": len(summaries),
        "message": f"Retrieved {len(summaries)} report definitions",
    }
    
    # Cache the result with definitions TTL
    set_cached_report(cache_key, response_data, ttl=config.CACHE_TTL_DEFINITIONS)

    return {
        "success": True,
        "data": summaries,
        "count": len(summaries),
        "message": f"Retrieved {len(summaries)} report definitions",
        "cached": False,
    }


@reports_router.get("/reports/{report_id}", response_model=Dict[str, Any])
async def get_report_instance(
    report_id: str,
    request: Request,
    conn: Connection = Depends(get_db_connection),
    # Cache control parameters
    cache_ttl: Optional[int] = Query(None, description="Cache TTL in seconds (overrides default)"),
    bypass_cache: Optional[bool] = Query(False, description="Skip cache lookup"),
    # Dynamically accept all possible filters
    team_name: Optional[str] = Query(None),
    issue_type: Optional[str] = Query(None),
    sprint_name: Optional[str] = Query(None),
    pi: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    months: Optional[int] = Query(None),
    pi_names: Optional[List[str]] = Query(None),
    quarters: Optional[List[str]] = Query(None),
    status_category: Optional[str] = Query(None), # New filter
    include_done: Optional[bool] = Query(None), # New filter
    view_mode: Optional[str] = Query(None), # New filter
    limit: Optional[int] = Query(None), # New filter
    detail_status: Optional[str] = Query(None), # New filter
    detail_year_month: Optional[str] = Query(None), # New filter
    detail_months: Optional[int] = Query(None), # New filter
    plan_grace_period: Optional[int] = Query(None), # New filter
    isGroup: Optional[bool] = Query(None), # New filter for group support
):
    """
    Resolve a specific report by ID, merging defaults with provided filters.
    
    Available report IDs (copy/paste for testing):
    - team-sprint-burndown
    - team-current-sprint-progress
    - pi-burndown
    - team-closed-sprints
    - sprint-velocity-advanced
    - team-issues-trend
    - pi-predictability
    - epic-scope-changes
    - issues-bugs-by-priority
    - issues-bugs-by-team
    - issues-flow-status-duration
    - issues-epics-hierarchy
    - issues-epic-dependencies
    - issues-release-predictability
    - sprint-predictability
    - pi-metrics-summary
    - pi-metrics-summary-by-team
    - active-sprint-summary
    - wip-over-time
    - cycle-time-over-time
    """
    definition = get_report_definition_by_id(report_id, conn)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")

    default_filters = definition.get("default_filters") or {}
    override_filters: Dict[str, Any] = {}

    # Handle boolean parameter directly (FastAPI converts it)
    if isGroup is not None:
        override_filters["isGroup"] = isGroup

    # Gather all values for each query parameter
    # FastAPI automatically decodes URL-encoded values from query strings
    # Values like "AutoDesign%20Dev%2BTest" are automatically decoded to "AutoDesign Dev+Test"
    raw_params: Dict[str, List[str]] = {}
    for key, value in request.query_params.multi_items():
        # Skip isGroup as it's already handled above
        if key.lower() == "isgroup":
            continue
        # FastAPI's request.query_params already decodes URL-encoded values
        # Special characters like + (encoded as %2B) are properly decoded
        raw_params.setdefault(key, []).append(value)

    # Normalize multi-value parameters and aliases
    def _assign_multi(target_key: str, *source_keys: str) -> None:
        collected: List[str] = []
        for source_key in source_keys:
            collected.extend(raw_params.pop(source_key, []))
        normalized = _normalize_multi_value(collected or None)
        if normalized:
            override_filters[target_key] = normalized

    _assign_multi("quarters", "quarters", "quarter")
    _assign_multi("pi_names", "pi_names", "pi_name")

    # Remaining parameters: collapse repeated values, trim whitespace
    for key, values in raw_params.items():
        normalized_values = _normalize_multi_value(values)
        if not normalized_values:
            continue
        if len(normalized_values) == 1:
            override_filters[key] = normalized_values[0]
        else:
            override_filters[key] = normalized_values

    merged_filters = _merge_filters(default_filters, override_filters)

    # Ensure required filters present
    _validate_required_filters(definition, merged_filters)

    # Generate cache key from report_id and merged filters
    cache_key = generate_cache_key(report_id, merged_filters)
    
    # Try cache first (unless bypassed)
    if not bypass_cache:
        cached_data = get_cached_report(cache_key)
        if cached_data:
            # Add JIRA URL to cached response metadata (will retry if null)
            jira_settings = get_jira_url(conn=conn)
            if jira_settings.get("url") and "meta" in cached_data:
                cached_data["meta"]["jira_url"] = jira_settings["url"]
            
            return {
                "success": True,
                "data": cached_data,
                "message": f"Retrieved report '{report_id}' (cached)",
                "cached": True,
            }

    try:
        resolved_payload = resolve_report_data(definition["data_source"], merged_filters, conn)
    except KeyError as err:
        raise HTTPException(
            status_code=500,
            detail=f"Report '{report_id}' has unsupported data source: {err}",
        ) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except HTTPException:
        # Re-raise HTTP exceptions as-is (they already have proper status codes)
        raise
    except Exception as err:
        # Log the full error for debugging, especially for URL encoding issues
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to resolve report '{report_id}': {type(err).__name__}: {err}", exc_info=True)
        logger.error(f"Filters used: {merged_filters}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resolve report '{report_id}': {type(err).__name__}: {str(err)}",
        ) from err

    response_payload = {
        "definition": {
            "report_id": definition["report_id"],
            "report_name": definition["report_name"],
            "chart_type": definition["chart_type"],
            "description": definition.get("description"),
            "data_source": definition.get("data_source"),
            "default_filters": default_filters,
            "meta_schema": definition.get("meta_schema"),
        },
        "filters": merged_filters,
        "result": resolved_payload.get("data"),
        "meta": resolved_payload.get("meta", {}),
    }

    # Add JIRA URL to metadata (will retry from DB if null)
    jira_settings = get_jira_url(conn=conn)
    if jira_settings.get("url"):
        response_payload["meta"]["jira_url"] = jira_settings["url"]

    # Determine TTL: use custom if provided, otherwise use smart default
    ttl = cache_ttl if cache_ttl is not None else get_report_cache_ttl(report_id)
    
    # Cache the result before returning
    set_cached_report(cache_key, response_payload, ttl=ttl)

    return {
        "success": True,
        "data": response_payload,
        "message": f"Retrieved report '{report_id}'",
        "cached": False,
    }


@reports_router.post("/reports/cache/invalidate")
async def invalidate_cache(report_id: Optional[str] = Query(None)):
    """
    Invalidate cached reports.
    
    Args:
        report_id: If provided, clears only that report's caches.
                   If None, clears all report caches.
    
    Returns:
        Success status and count of invalidated entries.
    
    Examples:
        - POST /reports/cache/invalidate?report_id=team-sprint-burndown
        - POST /reports/cache/invalidate (clears all)
    """
    count = invalidate_report_cache(report_id)
    
    if report_id:
        message = f"Invalidated {count} cache entries for report '{report_id}'"
    else:
        message = f"Invalidated {count} cache entries for all reports"
    
    return {
        "success": True,
        "message": message,
        "count": count,
        "report_id": report_id,
    }


@reports_router.get("/reports/cache/stats")
async def get_cache_stats():
    """
    Get Redis cache statistics and health information.
    
    Returns:
        Cache statistics including:
        - Whether Redis is enabled
        - Number of cached report keys
        - Total commands processed
        - Keyspace hits/misses (for hit rate calculation)
    """
    try:
        client = get_redis_client()
        if not client:
            return {
                "success": False,
                "message": "Redis is not enabled or not available",
                "data": {
                    "enabled": config.REDIS_ENABLED,
                    "available": False,
                }
            }
        
        # Get Redis stats
        info = client.info("stats")
        
        # Count report cache keys (use scan_iter for efficiency)
        keys_count = len(list(client.scan_iter(match="report:*", count=1000)))
        
        # Calculate hit rate
        hits = info.get("keyspace_hits", 0)
        misses = info.get("keyspace_misses", 0)
        total_requests = hits + misses
        hit_rate = (hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "success": True,
            "data": {
                "enabled": config.REDIS_ENABLED,
                "available": True,
                "report_cache_keys": keys_count,
                "total_commands": info.get("total_commands_processed", 0),
                "keyspace_hits": hits,
                "keyspace_misses": misses,
                "hit_rate_percentage": round(hit_rate, 2),
                "redis_version": client.info("server").get("redis_version", "unknown"),
            },
            "message": "Cache statistics retrieved successfully"
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to retrieve cache stats: {str(e)}",
            "data": {
                "enabled": config.REDIS_ENABLED,
                "available": False,
                "error": str(e),
            }
    }


