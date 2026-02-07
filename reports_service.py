"""
Reports Service - REST API endpoints for report metadata and resolved datasets.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, List
import logging
import os
import httpx

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
from global_settings_loader import settings

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


async def forward_to_github_service(report_id: str, filters: Dict[str, Any], definition: Dict[str, Any]) -> Dict[str, Any]:
    """
    Forward report request to GitHub service.
    Returns data in the same format as resolve_report_data.
    GitHub service returns only the data, backend adds definition.
    """
    github_service_url = os.getenv("GITHUB_SERVICE_URL", "http://github-service:8084")
    endpoint = f"/api/v1/github-service/reports/{report_id}"
    
    # Build query parameters from filters
    params: Dict[str, Any] = {}
    if filters.get("github_repo_ids"):
        if isinstance(filters["github_repo_ids"], list):
            # Convert list to comma-separated string
            params["github_repo_ids"] = ",".join(map(str, filters["github_repo_ids"]))
        else:
            params["github_repo_ids"] = str(filters["github_repo_ids"])
    if filters.get("environment"):
        params["environment"] = filters["environment"]
    if filters.get("months"):
        params["months"] = filters["months"]
    if filters.get("pr_state"):
        params["pr_state"] = filters["pr_state"]
    if filters.get("lookback_days"):
        params["lookback_days"] = filters["lookback_days"]
    if filters.get("team_name"):
        params["team_name"] = filters["team_name"]
    if filters.get("isGroup") is not None:
        params["isGroup"] = str(filters["isGroup"]).lower() if filters["isGroup"] else "false"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{github_service_url}{endpoint}",
                params=params
            )
            response.raise_for_status()
            # GitHub service returns only the data (result)
            result_data = response.json()
            
            # Format response with definition from database (single source of truth)
            return {
                "data": result_data,
                "meta": {
                    "service": "github-service"
                }
            }
    except httpx.HTTPStatusError as e:
        logger = logging.getLogger(__name__)
        logger.error(f"GitHub service returned error: {e.response.status_code} - {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"GitHub service error: {e.response.text}"
        )
    except httpx.RequestError as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to connect to GitHub service: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to connect to GitHub service: {str(e)}"
        )


async def forward_to_audit_service(report_id: str, filters: Dict[str, Any], definition: Dict[str, Any]) -> Dict[str, Any]:
    """
    Forward report request to Audit service.
    Returns data in the same format as resolve_report_data.
    Audit service returns only the data, backend adds definition.
    """
    audit_service_url = os.getenv("AUDIT_SERVICE_URL", "http://audit-service:8083")
    endpoint = f"/api/v1/audit-service/reports/{report_id}"
    
    # Build query parameters from filters
    params: Dict[str, Any] = {}
    if filters.get("months"):
        params["months"] = filters["months"]
    if filters.get("month"):
        params["month"] = filters["month"]
    if filters.get("user_id"):
        params["user_id"] = filters["user_id"]
    if filters.get("http_method"):
        params["http_method"] = filters["http_method"]
    if filters.get("action"):
        params["action"] = filters["action"]
    if filters.get("min_tokens"):
        params["min_tokens"] = filters["min_tokens"]
    if filters.get("min_response_time"):
        params["min_response_time"] = filters["min_response_time"]
    if filters.get("status_code"):
        params["status_code"] = filters["status_code"]
    if filters.get("status_code_min"):
        params["status_code_min"] = filters["status_code_min"]
    if filters.get("status_code_max"):
        params["status_code_max"] = filters["status_code_max"]
    if filters.get("search_query"):
        params["search_query"] = filters["search_query"]
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{audit_service_url}{endpoint}",
                params=params
            )
            response.raise_for_status()
            result_data = response.json()
            
            return {
                "data": result_data,
                "meta": {
                    "service": "audit-service"
                }
            }
    except httpx.HTTPStatusError as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Audit service returned error: {e.response.status_code} - {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Audit service error: {e.response.text}"
        )
    except httpx.RequestError as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to connect to audit service: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to connect to audit service: {str(e)}"
        )


@reports_router.get("/reports")
async def list_reports(
    conn: Connection = Depends(get_db_connection),
    bypass_cache: Optional[bool] = Query(False, description="Skip cache lookup"),
    include_audit: Optional[bool] = Query(False, description="Include audit reports in results"),
    audit_only: Optional[bool] = Query(False, description="Return only audit reports"),
):
    """
    Return all available report definitions.
    Filtering by naming convention: audit reports have report_id starting with "audit-"
    """
    # Build cache key with filter parameters
    cache_key = f"report:definitions:all:include_audit={include_audit}:audit_only={audit_only}"
    
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
    
    # Filter by naming convention
    if audit_only:
        definitions = [d for d in definitions if d["report_id"].startswith("audit-")]
    elif not include_audit:
        definitions = [d for d in definitions if not d["report_id"].startswith("audit-")]
    
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
    set_cached_report(cache_key, response_data, ttl=settings.CACHE_TTL_DEFINITIONS)

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
    pi: Optional[str] = Query(None, description="PI name filter. For pi-roadmap report, supports multiple PIs: use comma-separated format '2026-Q1,2026-Q2' OR repeat the parameter '?pi=2026-Q1&pi=2026-Q2'. In Swagger UI: Enter comma-separated values like '2026-Q1,2026-Q2' in the pi field."),
    release: Optional[str] = Query(None, description="Release name filter (for release-burndown report)"),
    project: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    months: Optional[int] = Query(None),
    month: Optional[str] = Query(None, description="Specific month in YYYY-MM format (e.g., '2026-01')"),
    pi_names: Optional[List[str]] = Query(None, description="PI name(s) filter as a list. For multiple PIs, use comma-separated format: '2026-Q1,2026-Q2' OR repeat the parameter: '?pi_names=2026-Q1&pi_names=2026-Q2'. In Swagger UI: Enter comma-separated values like '2026-Q1,2026-Q2' in a single input field."),
    status_category: Optional[List[str]] = Query(None), # Status category filter (array)
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
    - team-sprint-burndown - Tracks remaining work across a sprint for a given team
    - team-current-sprint-progress - Displays the progress of the current sprint for a given team
    - pi-burndown - Displays program increment burndown for epics and features
    - release-burndown - Displays release burndown for issues that are part of a defined release
    - team-closed-sprints - Displays completed sprint metrics for a given team across recent months
    - sprint-velocity-advanced - Displays sprint velocity chart with planned, added, completed, and removed issues
    - team-issues-trend - Shows monthly counts of issues created, resolved, and remaining open
    - pi-predictability - Summarizes predictability metrics for program increments
    - epic-scope-changes - Compares epic scope adjustments across selected PI quarters
    - issues-bugs-by-priority - Visualizes open bugs by priority level
    - issues-bugs-by-team - Visualizes open bugs grouped by team with priority breakdown
    - issues-flow-status-duration - Shows average time spent in each workflow status
    - issues-epics-hierarchy - Displays the hierarchy of issues with status and dependency information
    - issues-epic-dependencies - Summarizes inbound and outbound epic dependencies for a PI
    - issues-release-predictability - Highlights release progress across epics and other issues over recent months
    - sprint-predictability - Provides sprint predictability metrics, cycle time, and completion breakdown
    - pi-metrics-summary - Aggregates PI closure progress and WIP metrics for leadership review
    - pi-metrics-summary-by-team - Displays PI closure progress and WIP metrics broken down by team
    - pi-roadmap - PI Roadmap with Initiative/Epic Hierarchy (supports multiple PIs - see pi parameter documentation)
    - dependency-heatmap - Visualize team-to-team dependencies in a heatmap format. Shows which teams are blocking others and completion status
    - active-sprint-summary - Displays active sprint summary by team with progress metrics and completion status
    - wip-over-time - Displays work in progress metrics over time by issue type
    - cycle-time-over-time - Displays average cycle time and issue count over time by issue type
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

    _assign_multi("pi_names", "pi_names", "pi_name")

    # Handle pi parameter specially to support both single-PI and multi-PI reports
    # Single-PI reports (pi-burndown, pi-metrics-summary) expect pi as a string
    # Multi-PI reports (pi-roadmap) can use pi_names (list) or pi (string/list)
    if "pi" in raw_params:
        pi_values = raw_params.pop("pi")
        normalized_pi = _normalize_multi_value(pi_values)
        if normalized_pi:
            if len(normalized_pi) == 1:
                # Single PI: keep as string for single-PI reports, also add to pi_names for multi-PI reports
                override_filters["pi"] = normalized_pi[0]
                # Only add to pi_names if it wasn't already set by _assign_multi above
                if "pi_names" not in override_filters:
                    override_filters["pi_names"] = normalized_pi
            else:
                # Multiple PIs: convert to pi_names (list) for multi-PI reports
                # Set pi to first value for backward compatibility with single-PI reports
                override_filters["pi_names"] = normalized_pi
                override_filters["pi"] = normalized_pi[0]  # First PI as string for single-PI reports

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
        # Check if report should be forwarded to external service
        if definition["data_source"].startswith("github_service_"):
            resolved_payload = await forward_to_github_service(report_id, merged_filters, definition)
        elif definition["data_source"].startswith("audit_"):
            resolved_payload = await forward_to_audit_service(report_id, merged_filters, definition)
        else:
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
                   If None, clears all report caches (including definitions).
    
    Returns:
        Success status and count of invalidated entries.
    
    Examples:
        - POST /reports/cache/invalidate?report_id=team-sprint-burndown
        - POST /reports/cache/invalidate (clears all, including definitions)
    """
    from cache_utils import invalidate_report_cache, invalidate_report_definitions_cache
    
    count = invalidate_report_cache(report_id)
    
    # If clearing all reports, also clear definitions cache
    if not report_id:
        definitions_count = invalidate_report_definitions_cache()
        count += definitions_count
    
    if report_id:
        message = f"Invalidated {count} cache entries for report '{report_id}'"
    else:
        message = f"Invalidated {count} cache entries for all reports (including definitions)"
    
    return {
        "success": True,
        "message": message,
        "count": count,
        "report_id": report_id,
    }


@reports_router.post("/reports/cache/invalidate-definitions")
async def invalidate_definitions_cache():
    """
    Invalidate cached report definitions (the list of available reports).
    Use this when you've added/removed reports and need to see them immediately.
    
    Returns:
        Success status and count of invalidated entries.
    
    Example:
        POST /reports/cache/invalidate-definitions
    """
    from cache_utils import invalidate_report_definitions_cache
    
    count = invalidate_report_definitions_cache()
    
    return {
        "success": True,
        "message": f"Invalidated {count} report definitions cache entries",
        "count": count,
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


