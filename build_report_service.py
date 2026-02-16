"""
Build Report Service - REST API endpoints for building generic reports.
Supports table, bar chart, and pie chart report types with dynamic field selection and filtering.
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
import logging
import uuid
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.engine import Connection
from sqlalchemy import text
from pydantic import BaseModel, model_validator, ConfigDict

from database_connection import get_db_connection
import config
from config import get_jira_url
from global_settings_loader import settings

build_report_router = APIRouter()
logger = logging.getLogger(__name__)


@build_report_router.get("/reports/fields")
async def get_report_fields(
    conn: Connection = Depends(get_db_connection)
):
    """
    Get all available fields from jira_issues table for report building.
    Returns displayable fields and filterable fields (without dropdown values).
    Use /reports/filters/dropdown-values to get dropdown values for specific fields.
    """
    try:
        # Standard dropdown fields (known fields that should have dropdowns)
        STANDARD_DROPDOWN_FIELDS = {
            'issue_type', 'quarter_pi', 'status', 'priority', 'status_category',
            'resolution', 'project_key', 'project_name',
            'reporter_name', 'assignee_name', 'labels', 'fix_version_ids', 'planned_or_added'
        }
        
        # 1. Get all columns from jira_issues table
        columns_query = text("""
            SELECT 
                column_name,
                data_type,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' 
            AND table_name = :table_name
            ORDER BY ordinal_position
        """)
        
        result = conn.execute(columns_query, {"table_name": config.WORK_ITEMS_TABLE})
        all_columns = result.fetchall()
        
        # 2. Get custom field metadata from jira_custom_fields table
        custom_fields_query = text("""
            SELECT column_name, name, field_type
            FROM jira_custom_fields
            ORDER BY name
        """)
        
        custom_fields_result = conn.execute(custom_fields_query)
        custom_fields_map = {}
        for row in custom_fields_result:
            custom_fields_map[row[0]] = {
                "name": row[1],
                "field_type": row[2]
            }
        
        # 3. Build displayable fields list
        displayable_fields = []
        filterable_fields = []
        
        for col in all_columns:
            column_name = col[0]
            data_type = col[1]
            
            # Skip internal/system columns
            if column_name in ['issue_id']:  # Skip issue_id, keep issue_key
                continue
            
            # Get display name
            if column_name in custom_fields_map:
                display_name = custom_fields_map[column_name]["name"]
                field_type = custom_fields_map[column_name].get("field_type", "text")
            else:
                # Format standard field names
                display_name = column_name.replace('_', ' ').title()
                field_type = "text"
            
            # Determine field type for display
            if data_type in ['timestamp without time zone', 'timestamp with time zone', 'date']:
                type_str = "date"
            elif data_type in ['integer', 'bigint', 'numeric', 'double precision', 'real']:
                type_str = "number"
            elif data_type == 'boolean':
                type_str = "boolean"
            else:
                type_str = "string"
            
            # Add to displayable fields
            displayable_fields.append({
                "column_name": column_name,
                "display_name": display_name,
                "type": type_str
            })
            
            # Determine if field is filterable and what filter type
            # Exclude non-filterable fields (e.g., large text, or internal like current_sprint_id)
            NON_FILTERABLE_FIELDS = {'description', 'current_sprint_id'}
            if column_name in NON_FILTERABLE_FIELDS:
                continue  # Skip this field - don't add to filterable_fields
            
            # Handle boolean fields first
            if type_str == "boolean":
                filterable_fields.append({
                    "column_name": column_name,
                    "display_name": display_name,
                    "type": type_str,
                    "filter_type": "boolean",
                    "operator": ["equals"],
                    "values": ["true", "false"]  # For display purposes
                })
                continue  # Skip to next field
            
            # Handle date fields
            elif type_str == "date":
                filterable_fields.append({
                    "column_name": column_name,
                    "display_name": display_name,
                    "type": type_str,
                    "filter_type": "date",
                    "operator": ["greater_than", "less_than"]
                })
                continue  # Skip to next field
            
            # Handle number fields
            elif type_str == "number":
                filterable_fields.append({
                    "column_name": column_name,
                    "display_name": display_name,
                    "type": type_str,
                    "filter_type": "number",
                    "operator": ["equals", "greater_than", "less_than"]
                })
                continue  # Skip to next field
            
            # Check if field is a dropdown
            is_dropdown = False
            if column_name in STANDARD_DROPDOWN_FIELDS:
                is_dropdown = True
            elif column_name in custom_fields_map:
                # Check if custom field is a select/dropdown
                if custom_fields_map[column_name].get("field_type") == "select":
                    is_dropdown = True
            
            if is_dropdown:
                # Don't fetch dropdown values here - use dedicated endpoint /reports/filters/dropdown-values
                filterable_fields.append({
                    "column_name": column_name,
                    "display_name": display_name,
                    "type": type_str,
                    "filter_type": "dropdown"
                    # No "values" field - fetch via /reports/filters/dropdown-values
                })
            else:
                # Text field - support equals and contains
                filterable_fields.append({
                    "column_name": column_name,
                    "display_name": display_name,
                    "type": type_str,
                    "filter_type": "text",
                    "operator": ["equals", "contains"]
                })
        
        return {
            "success": True,
            "data": {
                "displayable_fields": displayable_fields,
                "filterable_fields": filterable_fields
            }
        }
    
    except Exception as e:
        logger.error(f"Error fetching report fields: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch report fields: {str(e)}"
        )


@build_report_router.get("/reports/filters/dropdown-values")
async def get_filter_dropdown_values(
    field: List[str] = Query(..., description="Field names to get dropdown values for"),
    conn: Connection = Depends(get_db_connection)
):
    """
    Get dropdown values for specified filter fields.
    Returns distinct values for each requested field (limit 100 per field).
    """
    try:
        if not field or len(field) == 0:
            raise HTTPException(
                status_code=400,
                detail="At least one field name must be provided"
            )
        
        # Get all valid columns
        columns_query = text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' 
            AND table_name = :table_name
        """)
        result = conn.execute(columns_query, {"table_name": config.WORK_ITEMS_TABLE})
        valid_columns = {row[0] for row in result}
        
        # Validate all requested fields exist
        invalid_fields = [f for f in field if f not in valid_columns]
        if invalid_fields:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid fields: {', '.join(invalid_fields)}"
            )
        
        # Fetch dropdown values for each field
        dropdown_values = {}
        for field_name in field:
            try:
                values_query = text(f"""
                    SELECT DISTINCT "{field_name}"
                    FROM {config.WORK_ITEMS_TABLE}
                    WHERE "{field_name}" IS NOT NULL
                    ORDER BY "{field_name}"
                    LIMIT 100
                """)
                values_result = conn.execute(values_query)
                values = [str(row[0]) for row in values_result if row[0] is not None]
                dropdown_values[field_name] = values
            except Exception as e:
                logger.warning(f"Could not get dropdown values for {field_name}: {e}")
                dropdown_values[field_name] = []
        
        return {
            "success": True,
            "data": dropdown_values
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching filter dropdown values: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch filter dropdown values: {str(e)}"
        )


class BuildReportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    
    report_type: str
    selected_fields: Optional[List[str]] = None
    filters: List[Dict[str, Any]] = []
    # Use Any for x_axis to allow both string and list, validate manually
    x_axis: Optional[Any] = None
    y_axis: Optional[str] = None
    team_name: Optional[str] = None
    isGroup: Optional[bool] = False
    # Bar chart (optional stack by and bar color)
    bar_color: Optional[str] = None  # hex color for bar chart bar/segments
    # Multi-bar (time-based, two metrics; optional stack by a dimension)
    period: Optional[str] = None  # "month" | "week" | "day"
    lookback_months: Optional[int] = None
    bar_1_metric: Optional[str] = None
    bar_2_metric: Optional[str] = None
    stack_by: Optional[str] = None  # column name to stack bars by (bar_chart or multi_bar)
    
    @model_validator(mode='before')
    @classmethod
    def validate_x_axis_before(cls, data: Any) -> Any:
        # Handle x_axis before type validation - accept both string and list
        # This runs BEFORE Pydantic's type validation, so we can accept any type
        if isinstance(data, dict) and 'x_axis' in data:
            x_axis_value = data['x_axis']
            # Accept both string and list - no conversion needed, just pass through
            # Pydantic will accept it because we declared it as Any
            if x_axis_value is not None and not isinstance(x_axis_value, (str, list)):
                # Try to convert to string if it's a single value
                data['x_axis'] = str(x_axis_value) if x_axis_value else None
        return data
    
    @model_validator(mode='after')
    def validate_x_axis_after(self):
        # Additional validation after model creation
        if self.x_axis is not None:
            if not isinstance(self.x_axis, (str, list)):
                raise ValueError(f"x_axis must be a string or list of strings, got {type(self.x_axis)}")
            if isinstance(self.x_axis, list):
                if not all(isinstance(item, str) for item in self.x_axis):
                    raise ValueError("x_axis list must contain only strings")
        return self


def _merge_custom_report_filters(
    definition: Dict[str, Any],
    filters: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    """
    Merge a custom report definition with request filters.
    Returns (all_filters, merged_defaults, build_config).
    merged_defaults has pi, team_name, isGroup; build_config is from meta_schema.
    Shared by execute_custom_report (reports_service) and get_build_report_issues.
    """
    default_filters = definition.get("default_filters") or {}
    if isinstance(default_filters, str):
        try:
            default_filters = json.loads(default_filters)
        except (ValueError, TypeError):
            default_filters = {}
    meta_schema = definition.get("meta_schema") or {}
    if isinstance(meta_schema, str):
        try:
            meta_schema = json.loads(meta_schema)
        except (ValueError, TypeError):
            meta_schema = {}
    build_config = meta_schema.get("build_report_config") or {}

    merged = {
        "pi": filters.get("pi") or default_filters.get("pi"),
        "team_name": filters.get("team_name") or default_filters.get("team_name"),
        "isGroup": filters.get("isGroup") if "isGroup" in filters else default_filters.get("isGroup", False),
    }
    all_filters = list(build_config.get("filters", []))

    filter_overrides = filters.get("filter_overrides", [])
    if isinstance(filter_overrides, str):
        try:
            filter_overrides = json.loads(filter_overrides)
        except (ValueError, TypeError):
            filter_overrides = []
    if isinstance(filter_overrides, list):
        for override in filter_overrides:
            if not isinstance(override, dict) or "field" not in override:
                continue
            field_name = override.get("field")
            existing_index = next((i for i, f in enumerate(all_filters) if f.get("field") == field_name), None)
            if existing_index is not None:
                all_filters[existing_index] = {
                    "field": field_name,
                    "operator": override.get("operator", all_filters[existing_index].get("operator", "equals")),
                    "values": override.get("values", all_filters[existing_index].get("values", [])),
                }
            elif field_name not in ("quarter_pi", "team_name"):
                all_filters.append({
                    "field": field_name,
                    "operator": override.get("operator", "equals"),
                    "values": override.get("values", []),
                })

    has_pi_filter = any(f.get("field") == "quarter_pi" for f in all_filters)
    if merged.get("pi"):
        if not has_pi_filter:
            all_filters.append({"field": "quarter_pi", "operator": "equals", "values": [merged["pi"]]})
        else:
            for f in all_filters:
                if f.get("field") == "quarter_pi":
                    f["values"] = [merged["pi"]]
                    break

    all_filters = [f for f in all_filters if f.get("field") != "team_name"]
    return all_filters, merged, build_config


def _build_where_from_request(
    request: BuildReportRequest,
    conn: Connection,
) -> Tuple[str, Dict[str, Any], Set[str]]:
    """
    Build WHERE clause and params from BuildReportRequest (filters + team_name).
    Returns (where_clause, base_params, valid_columns). Shared by report execution and issues fetch.
    """
    columns_query = text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :table_name
    """)
    result = conn.execute(columns_query, {"table_name": config.WORK_ITEMS_TABLE})
    valid_columns = {row[0] for row in result}

    where_conditions = []
    base_params: Dict[str, Any] = {}

    for idx, filter_item in enumerate(request.filters):
        field = filter_item.get("field")
        operator = filter_item.get("operator", "equals")
        values = filter_item.get("values", [])

        if not field or field not in valid_columns:
            continue
        if not values or (isinstance(values, list) and len(values) == 0):
            continue
        if isinstance(values, str) and values.strip() == "":
            continue
        if isinstance(values, list):
            non_empty = [v for v in values if v is not None and (str(v).strip() if isinstance(v, str) else True)]
            if not non_empty:
                continue
            values = non_empty
        if not operator or not isinstance(operator, str):
            operator = "equals"

        field_sql = f'"{field}"'
        if operator == "equals":
            if isinstance(values, list) and len(values) > 1:
                placeholders = ", ".join([f":filter_{idx}_val_{i}" for i in range(len(values))])
                where_conditions.append(f"{field_sql} IN ({placeholders})")
                for i, val in enumerate(values):
                    if isinstance(val, str) and val.lower() in ("true", "false"):
                        base_params[f"filter_{idx}_val_{i}"] = val.lower() == "true"
                    else:
                        base_params[f"filter_{idx}_val_{i}"] = val
            else:
                single_val = values[0] if isinstance(values, list) else values
                if isinstance(single_val, str) and single_val.lower() in ("true", "false"):
                    base_params[f"filter_{idx}_val"] = single_val.lower() == "true"
                else:
                    base_params[f"filter_{idx}_val"] = single_val
                where_conditions.append(f"{field_sql} = :filter_{idx}_val")
        elif operator == "contains":
            single_val = values[0] if isinstance(values, list) else values
            where_conditions.append(f"{field_sql} ILIKE :filter_{idx}_val")
            base_params[f"filter_{idx}_val"] = f"%{single_val}%"
        elif operator == "greater_than":
            single_val = values[0] if isinstance(values, list) else values
            where_conditions.append(f"{field_sql} > :filter_{idx}_val")
            base_params[f"filter_{idx}_val"] = single_val
        elif operator == "less_than":
            single_val = values[0] if isinstance(values, list) else values
            where_conditions.append(f"{field_sql} < :filter_{idx}_val")
            base_params[f"filter_{idx}_val"] = single_val

    if request.team_name:
        try:
            from database_team_metrics import resolve_team_names_from_filter
            team_names_list = resolve_team_names_from_filter(request.team_name, request.isGroup or False, conn)
            if team_names_list:
                placeholders = ", ".join([f":team_name_{i}" for i in range(len(team_names_list))])
                where_conditions.append(f"team_name IN ({placeholders})")
                for i, name in enumerate(team_names_list):
                    base_params[f"team_name_{i}"] = name
        except Exception as e:
            logger.warning(f"Failed to resolve team names: {e}")

    where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
    return where_clause, base_params, valid_columns


def _build_report_run_grouped_count(
    conn: Connection,
    table_name: str,
    where_clause: str,
    params: Dict[str, Any],
    bucket_column: str,
    stack_by_column: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Shared helper for Build Report: grouped count with optional stack-by dimension.
    Used by bar_chart (single metric) and by multi_bar stacked path (called twice).
    Returns list of {bucket, count} or {bucket, stacked: {segment: count}}.
    """
    bucket_sql = f'"{bucket_column}"'
    if not stack_by_column:
        q = text(
            f'SELECT {bucket_sql}, COUNT(*)::int FROM {table_name} WHERE {where_clause} '
            f'GROUP BY {bucket_sql} ORDER BY {bucket_sql}'
        )
        r = conn.execute(q, params)
        return [{"bucket": row[0], "count": row[1]} for row in r]
    seg_expr = f'COALESCE("{stack_by_column}"::text, \'(Blank)\')'
    q = text(
        f'SELECT {bucket_sql}, {seg_expr}, COUNT(*)::int FROM {table_name} WHERE {where_clause} '
        f'GROUP BY {bucket_sql}, {seg_expr} ORDER BY {bucket_sql}, {seg_expr}'
    )
    r = conn.execute(q, params)
    out: Dict[Any, Dict[str, int]] = {}
    bucket_order: List[Any] = []
    for row in r:
        b, seg, cnt = row[0], row[1], row[2]
        if b not in out:
            out[b] = {}
            bucket_order.append(b)
        out[b][seg] = cnt
    return [{"bucket": b, "stacked": out[b]} for b in bucket_order]


async def _execute_build_report_logic(
    request: BuildReportRequest,
    conn: Connection
) -> Dict[str, Any]:
    """
    Core logic for building reports. Can be called from both:
    - POST /reports/build (direct user request)
    - execute_custom_report() (when custom report is executed)
    
    Returns:
        Dict with 'data', 'count', 'columns', and optionally 'meta'
    """
    try:
        # Validate report type
        if request.report_type not in ["table", "bar_chart", "pie_chart", "multi_bar"]:
            raise HTTPException(
                status_code=400,
                detail=f"Report type '{request.report_type}' is not supported. Only 'table', 'bar_chart', 'pie_chart', and 'multi_bar' are supported."
            )
        
        # Multi-bar validation
        MULTI_BAR_METRICS = {"created", "resolved", "updated"}
        if request.report_type == "multi_bar":
            if not request.period or request.period not in ("month", "week", "day"):
                raise HTTPException(status_code=400, detail="Multi-bar requires period 'month', 'week', or 'day'")
            valid_lookback = {1, 2, 3, 4, 6, 9, 12}
            if request.lookback_months is None or request.lookback_months not in valid_lookback:
                raise HTTPException(status_code=400, detail="Multi-bar lookback_months must be one of: 1, 2, 3, 4, 6, 9, 12")
            if not request.bar_1_metric or request.bar_1_metric not in MULTI_BAR_METRICS:
                raise HTTPException(status_code=400, detail=f"Multi-bar bar_1_metric must be one of: {sorted(MULTI_BAR_METRICS)}")
            if not request.bar_2_metric or request.bar_2_metric not in MULTI_BAR_METRICS:
                raise HTTPException(status_code=400, detail=f"Multi-bar bar_2_metric must be one of: {sorted(MULTI_BAR_METRICS)}")
        
        # Validate based on report type
        if request.report_type == "table":
            if not request.selected_fields:
                raise HTTPException(
                    status_code=400,
                    detail="At least one field must be selected"
                )
        elif request.report_type in ["bar_chart", "pie_chart"]:
            # Validate x_axis - handle both string and list
            if request.report_type == "pie_chart":
                # For pie charts, x_axis can be a list
                if not request.x_axis or (isinstance(request.x_axis, list) and len(request.x_axis) == 0):
                    raise HTTPException(
                        status_code=400,
                        detail="At least one Group By field must be selected"
                    )
            else:  # bar_chart
                # For bar charts, x_axis must be a string
                if not request.x_axis or (isinstance(request.x_axis, list) and len(request.x_axis) == 0):
                    raise HTTPException(
                        status_code=400,
                        detail="X-axis field must be selected"
                    )
            # Only validate y_axis for bar charts (pie charts don't use y_axis)
            if request.report_type == "bar_chart" and request.y_axis != "count":
                raise HTTPException(
                    status_code=400,
                    detail=f"Y-axis '{request.y_axis}' is not supported. Only 'count' is currently supported."
                )
        
        # 1. Build WHERE from filters and get valid columns (shared helper)
        where_clause, base_params, valid_columns = _build_where_from_request(request, conn)

        # 2. Validate all fields exist in the table (skip for multi_bar; it uses metric keys)
        if request.report_type == "table":
            invalid_fields = [f for f in request.selected_fields if f not in valid_columns]
            if invalid_fields:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid fields: {', '.join(invalid_fields)}"
                )
        elif request.report_type == "multi_bar":
            if request.stack_by and request.stack_by not in valid_columns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid stack_by field '{request.stack_by}' for multi-bar."
                )
        elif request.report_type == "bar_chart":
            if request.stack_by and request.stack_by not in valid_columns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid stack_by field '{request.stack_by}' for bar chart."
                )
        if request.report_type in ["bar_chart", "pie_chart"]:
            if request.report_type == "pie_chart":
                x_axis_list = [request.x_axis] if isinstance(request.x_axis, str) else request.x_axis
                invalid_fields = [f for f in x_axis_list if f not in valid_columns]
                if invalid_fields:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid Group By fields: {', '.join(invalid_fields)}"
                    )
            else:
                if request.x_axis not in valid_columns:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid X-axis field: {request.x_axis}"
                    )

        # 3. Handle different report types
        if request.report_type == "table":
            # Table: SELECT selected fields
            selected_fields_sql = ", ".join([f'"{field}"' for field in request.selected_fields])
            limit = min(settings.DEFAULT_QUERY_LIMIT, 500)
            base_params["limit"] = limit
            
            query = text(f"""
                SELECT {selected_fields_sql}
                FROM {config.WORK_ITEMS_TABLE}
                WHERE {where_clause}
                ORDER BY issue_key
                LIMIT :limit
            """)
            
            logger.info(f"Executing build report query with {len(request.selected_fields)} fields and {len(request.filters)} filters")
            result = conn.execute(query, base_params)
            rows = result.fetchall()
            
            # Convert rows to list of dictionaries
            data = []
            for row in rows:
                row_dict = {}
                for i, field in enumerate(request.selected_fields):
                    row_dict[field] = row[i]
                data.append(row_dict)
            
            response = {
                "success": True,
                "data": {
                    "data": data,
                    "count": len(data),
                    "columns": request.selected_fields
                },
                "message": f"Retrieved {len(data)} rows"
            }
            
        elif request.report_type == "bar_chart":
            # Bar chart: single metric, optional stack_by (shared helper)
            if request.stack_by:
                rows = _build_report_run_grouped_count(
                    conn, config.WORK_ITEMS_TABLE, where_clause, base_params,
                    request.x_axis, request.stack_by,
                )
                data = [{"x_value": r["bucket"], "stacked": r["stacked"]} for r in rows]
                response = {
                    "success": True,
                    "data": {
                        "data": data,
                        "count": len(data),
                        "columns": ["x_value", "stacked"]
                    },
                    "message": f"Retrieved {len(data)} stacked bar chart data points"
                }
            else:
                x_axis_sql = f'"{request.x_axis}"'
                selected_fields_sql = f'{x_axis_sql}, COUNT(*) as count'
                query = text(f"""
                    SELECT {selected_fields_sql}
                    FROM {config.WORK_ITEMS_TABLE}
                    WHERE {where_clause}
                    GROUP BY {x_axis_sql}
                    ORDER BY {x_axis_sql}
                """)
                logger.info(f"Executing bar chart query with X-axis: {request.x_axis}, Y-axis: {request.y_axis}, filters: {len(request.filters)}")
                result = conn.execute(query, base_params)
                rows = result.fetchall()
                data = [{"x_value": row[0], "y_value": row[1]} for row in rows]
                response = {
                    "success": True,
                    "data": {
                        "data": data,
                        "count": len(data),
                        "columns": ["x_value", "y_value"]
                    },
                    "message": f"Retrieved {len(data)} chart data points"
                }
        
        elif request.report_type == "multi_bar":
            # Time-based multi-bar: two independent metrics (e.g. issues created, issues resolved) per period
            metric_to_column = {"created": "created_at", "resolved": "resolved_at", "updated": "updated_at"}
            period_type = request.period
            lookback = request.lookback_months
            end_date = date.today()
            start_date = end_date - timedelta(days=lookback * 31)
            periods_list: List[Tuple[date, str]] = []
            if period_type == "month":
                current = date(start_date.year, start_date.month, 1)
                end_month = date(end_date.year, end_date.month, 1)
                while current <= end_month:
                    periods_list.append((current, current.strftime("%b %Y")))
                    if current.month == 12:
                        current = date(current.year + 1, 1, 1)
                    else:
                        current = date(current.year, current.month + 1, 1)
            elif period_type == "day":
                current = start_date
                while current <= end_date:
                    periods_list.append((current, current.strftime("%Y-%m-%d")))
                    current += timedelta(days=1)
            else:
                current = start_date
                seen_weeks: Set[date] = set()
                while current <= end_date:
                    week_start = current - timedelta(days=current.weekday())
                    if week_start not in seen_weeks:
                        seen_weeks.add(week_start)
                        periods_list.append((week_start, week_start.strftime("%Y-%m-%d")))
                    current += timedelta(days=1)
                periods_list.sort(key=lambda x: x[0])
            if not periods_list:
                fmt = "%b %Y" if period_type == "month" else "%Y-%m-%d"
                periods_list = [(start_date, start_date.strftime(fmt))]
            period_dates = [p[0] for p in periods_list]
            period_labels = [p[1] for p in periods_list]
            start_ts = datetime.combine(start_date, datetime.min.time())
            if period_type == "month":
                next_month = date(end_date.year + 1, 1, 1) if end_date.month == 12 else date(end_date.year, end_date.month + 1, 1)
                end_ts = datetime.combine(next_month, datetime.min.time())
            else:
                end_ts = datetime.combine(end_date, datetime.max.time())
            mb_params = {**base_params, "mb_start": start_ts, "mb_end": end_ts}

            def run_metric_query(col: str) -> Dict[date, int]:
                if period_type == "month":
                    date_cond = f'"{col}" >= :mb_start AND "{col}" < :mb_end'
                    trunc_sql = "month"
                elif period_type == "day":
                    date_cond = f'"{col}" >= :mb_start AND "{col}" <= :mb_end'
                    trunc_sql = "day"
                else:
                    date_cond = f'"{col}" >= :mb_start AND "{col}" <= :mb_end'
                    trunc_sql = "week"
                where_parts = [where_clause, f'"{col}" IS NOT NULL', date_cond]
                full_where = " AND ".join(where_parts)
                q = text(f"""
                    SELECT date_trunc('{trunc_sql}', "{col}")::date AS p, COUNT(*)::int
                    FROM {config.WORK_ITEMS_TABLE}
                    WHERE {full_where}
                    GROUP BY 1
                    ORDER BY 1
                """)
                r = conn.execute(q, mb_params)
                return {row[0]: row[1] for row in r if row[0]}

            stack_by_col = request.stack_by
            col1 = metric_to_column[request.bar_1_metric]
            col2 = metric_to_column[request.bar_2_metric]

            if not stack_by_col:
                counts1 = run_metric_query(col1)
                counts2 = run_metric_query(col2)
                data = []
                for i, pd in enumerate(period_dates):
                    data.append({
                        "x_value": period_labels[i],
                        "bar_1_value": counts1.get(pd, 0),
                        "bar_2_value": counts2.get(pd, 0),
                    })
                response = {
                    "success": True,
                    "data": {
                        "data": data,
                        "count": len(data),
                        "columns": ["x_value", "bar_1_value", "bar_2_value"]
                    },
                    "message": f"Retrieved {len(data)} multi-bar data points"
                }
            else:
                # Stacked: group by period and stack_by column; return bar_1_stacked / bar_2_stacked per period
                def run_metric_query_stacked(col: str) -> Dict[date, Dict[str, int]]:
                    if period_type == "month":
                        date_cond = f'"{col}" >= :mb_start AND "{col}" < :mb_end'
                        trunc_sql = "month"
                    elif period_type == "day":
                        date_cond = f'"{col}" >= :mb_start AND "{col}" <= :mb_end'
                        trunc_sql = "day"
                    else:
                        date_cond = f'"{col}" >= :mb_start AND "{col}" <= :mb_end'
                        trunc_sql = "week"
                    where_parts = [where_clause, f'"{col}" IS NOT NULL', date_cond]
                    full_where = " AND ".join(where_parts)
                    seg_expr = f'COALESCE("{stack_by_col}"::text, \'(Blank)\')'
                    q = text(f"""
                        SELECT date_trunc('{trunc_sql}', "{col}")::date AS p, {seg_expr} AS seg, COUNT(*)::int
                        FROM {config.WORK_ITEMS_TABLE}
                        WHERE {full_where}
                        GROUP BY 1, 2
                        ORDER BY 1, 2
                    """)
                    r = conn.execute(q, mb_params)
                    out: Dict[date, Dict[str, int]] = {}
                    for row in r:
                        p, seg, cnt = row[0], row[1], row[2]
                        if p not in out:
                            out[p] = {}
                        out[p][seg] = cnt
                    return out

                stacked1 = run_metric_query_stacked(col1)
                stacked2 = run_metric_query_stacked(col2)
                data = []
                for i, pd in enumerate(period_dates):
                    data.append({
                        "x_value": period_labels[i],
                        "bar_1_stacked": stacked1.get(pd, {}),
                        "bar_2_stacked": stacked2.get(pd, {}),
                    })
                response = {
                    "success": True,
                    "data": {
                        "data": data,
                        "count": len(data),
                        "columns": ["x_value", "bar_1_stacked", "bar_2_stacked"]
                    },
                    "message": f"Retrieved {len(data)} stacked multi-bar data points"
                }
            
        else:  # pie_chart
            # Pie chart: multiple queries (one per x_axis field)
            x_axis_list = [request.x_axis] if isinstance(request.x_axis, str) else request.x_axis
            chart_data = {}
            
            for x_axis_field in x_axis_list:
                x_axis_sql = f'"{x_axis_field}"'
                selected_fields_sql = f'{x_axis_sql}, COUNT(*) as count'
                
                query = text(f"""
                    SELECT {selected_fields_sql}
                    FROM {config.WORK_ITEMS_TABLE}
                    WHERE {where_clause}
                    GROUP BY {x_axis_sql}
                    ORDER BY {x_axis_sql}
                """)
                
                result = conn.execute(query, base_params)
                rows = result.fetchall()
                
                # Convert rows to list of dictionaries
                field_data = []
                for row in rows:
                    field_data.append({
                        "x_value": row[0],
                        "y_value": row[1]  # count
                    })
                
                chart_data[x_axis_field] = field_data
            
            logger.info(f"Executing pie chart query with Group By fields: {x_axis_list}, filters: {len(request.filters)}")
            
            # Return data in format: {field1: [{x_value, y_value}], field2: [...]}
            response = {
                "success": True,
                "data": {
                    "data": chart_data,  # Object with field names as keys
                    "count": sum(len(v) for v in chart_data.values()),
                    "columns": ["x_value", "y_value"]
                },
                "message": f"Retrieved pie chart data for {len(x_axis_list)} field(s)"
            }
        
        # Get Jira URL for issue key links
        jira_settings = get_jira_url(conn=conn)
        
        # Build meta dict
        meta = {}
        if jira_settings.get("url"):
            meta["jira_url"] = jira_settings["url"]
        
        # Return in standard format expected by get_report_instance
        # Format: {"data": actual_data, "meta": {...}}
        return {
            "data": response["data"]["data"],
            "count": response["data"]["count"],
            "columns": response["data"]["columns"],
            "meta": meta
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error building report: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to build report: {str(e)}"
        )


@build_report_router.post("/reports/build")
async def build_report(
    request: BuildReportRequest = Body(...),
    conn: Connection = Depends(get_db_connection)
):
    """
    Build a report based on selected fields and filters.
    Supports 'table', 'bar_chart', and 'pie_chart' report types.
    """
    result = await _execute_build_report_logic(request, conn)
    
    # Return in API response format
    return {
        "success": True,
        "data": {
            "data": result["data"],
            "count": result["count"],
            "columns": result["columns"]
        },
        "meta": result.get("meta", {}),
        "message": f"Retrieved {result['count']} rows"
    }


class BuildReportIssuesRequest(BaseModel):
    """Request body for fetching issues for a chart segment (bar or pie slice)."""
    report_id: str
    filters: Dict[str, Any] = {}
    segment: Dict[str, Any]  # x_value (required), group_by_field (optional, for pie)


async def _fetch_build_report_issues(
    request: BuildReportRequest,
    x_value: Any,
    group_by_field: Optional[str],
    conn: Connection,
) -> List[Dict[str, Any]]:
    """
    Fetch issue rows for a chart segment using the same filters as the report.
    Returns list of dicts with: issue_key, issue_type, status, summary, created_at, updated_at, assignee_name.
    """
    where_clause, base_params, valid_columns = _build_where_from_request(request, conn)

    # Add segment filter: bar_chart -> x_axis = x_value; pie_chart -> group_by_field = x_value
    # Treat "Unknown", null, or empty string as "not set" -> filter by IS NULL or empty string
    def _is_unknown_or_empty(val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, str) and (val.strip().lower() == "unknown" or val.strip() == ""):
            return True
        return False

    segment_field = None
    if request.report_type == "bar_chart" and request.x_axis and request.x_axis in valid_columns:
        segment_field = request.x_axis
    elif request.report_type == "pie_chart" and group_by_field and group_by_field in valid_columns:
        segment_field = group_by_field
    if segment_field:
        if _is_unknown_or_empty(x_value):
            segment_cond = f'("{segment_field}" IS NULL OR "{segment_field}" = \'\')'
        else:
            base_params = {**base_params, "segment_x_value": x_value}
            segment_cond = f'"{segment_field}" = :segment_x_value'
        final_where = f"{where_clause} AND {segment_cond}" if where_clause != "1=1" else segment_cond
    else:
        final_where = where_clause
    issue_columns = ["issue_key", "issue_type", "status", "summary", "created_at", "updated_at", "assignee_name"]
    # Only select columns that exist
    existing_issue_cols = [c for c in issue_columns if c in valid_columns]
    if not existing_issue_cols:
        return []
    selected_sql = ", ".join([f'"{c}"' for c in existing_issue_cols])
    limit = min(getattr(settings, "DEFAULT_QUERY_LIMIT", 1000), 500)
    base_params["limit"] = limit

    query = text(f"""
        SELECT {selected_sql}
        FROM {config.WORK_ITEMS_TABLE}
        WHERE {final_where}
        ORDER BY issue_key
        LIMIT :limit
    """)
    result = conn.execute(query, base_params)
    rows = result.fetchall()
    issues = []
    for row in rows:
        issues.append(dict(zip(existing_issue_cols, row)))
    return issues


@build_report_router.post("/reports/build/issues")
async def get_build_report_issues(
    body: BuildReportIssuesRequest = Body(...),
    conn: Connection = Depends(get_db_connection),
):
    """
    Return issues for a chart segment (bar or pie slice) of a custom build report.
    Uses report_id to load definition, merges filters, and returns issues matching the segment.
    """
    try:
        # Load report definition
        query = text(f"""
            SELECT report_id, chart_type, default_filters, meta_schema
            FROM {config.REPORT_DEFINITIONS_TABLE}
            WHERE report_id = :report_id AND report_type = 'custom'
        """)
        result = conn.execute(query, {"report_id": body.report_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Custom report '{body.report_id}' not found")

        row_dict = dict(row._mapping)
        all_filters, merged, build_config = _merge_custom_report_filters(row_dict, body.filters or {})
        if not build_config:
            raise HTTPException(status_code=400, detail="Report has no build_report_config")

        report_type = build_config.get("report_type") or row_dict.get("chart_type")
        if report_type not in ("bar_chart", "pie_chart"):
            raise HTTPException(status_code=400, detail="Drill-down issues only supported for bar_chart and pie_chart")

        x_value = body.segment.get("x_value")
        group_by_field = body.segment.get("group_by_field")

        request_data = {
            "report_type": report_type,
            "filters": all_filters,
            "x_axis": build_config.get("x_axis"),
            "y_axis": build_config.get("y_axis", "count"),
            "team_name": merged.get("team_name"),
            "isGroup": merged.get("isGroup", False),
        }
        request_data = {k: v for k, v in request_data.items() if v is not None}
        build_req = BuildReportRequest(**request_data)

        issues = await _fetch_build_report_issues(build_req, x_value, group_by_field, conn)
        return {
            "success": True,
            "data": {"issues": issues},
            "message": f"Retrieved {len(issues)} issues",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching build report issues: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


class DefaultSortConfig(BaseModel):
    key: str
    direction: str = "asc"  # 'asc' | 'desc'


class SaveCustomReportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    
    report_name: str
    description: Optional[str] = None
    report_type: str
    selected_fields: Optional[List[str]] = None
    x_axis: Optional[Any] = None
    y_axis: Optional[str] = None
    bar_color: Optional[str] = None
    stack_by: Optional[str] = None
    filters: List[Dict[str, Any]] = []
    team_name: Optional[str] = None
    isGroup: Optional[bool] = False
    default_sort: Optional[DefaultSortConfig] = None
    period: Optional[str] = None
    lookback_months: Optional[int] = None
    bar_1_metric: Optional[str] = None
    bar_2_metric: Optional[str] = None
    bar_1_color: Optional[str] = None
    bar_2_color: Optional[str] = None
    stack_by: Optional[str] = None


@build_report_router.post("/reports/build/save")
async def save_custom_report(
    request: SaveCustomReportRequest = Body(...),
    conn: Connection = Depends(get_db_connection)
):
    """
    Save a custom report definition.
    """
    try:
        # Validate report name length
        if len(request.report_name) > 100:
            raise HTTPException(
                status_code=400,
                detail="Report name must be 100 characters or less"
            )
        
        # Validate report name is not empty
        if not request.report_name or not request.report_name.strip():
            raise HTTPException(
                status_code=400,
                detail="Report name is required"
            )
        
        # Check for duplicate report name (globally among custom reports)
        check_duplicate_query = text("""
            SELECT report_id
            FROM public.report_definitions
            WHERE report_type = 'custom' AND LOWER(report_name) = LOWER(:report_name)
        """)
        duplicate_result = conn.execute(check_duplicate_query, {"report_name": request.report_name.strip()})
        if duplicate_result.fetchone():
            raise HTTPException(
                status_code=409,
                detail="Duplicate report name. Please choose a different name."
            )
        
        # Generate unique report_id
        report_id = f"custom-{uuid.uuid4()}"
        
        # Extract default filters (PI and Team/Group) from request
        # These will be saved to the default_filters column (like system reports)
        default_filters = {
            "pi": None,
            "team_name": None,
            "isGroup": False
        }
        
        # Extract PI from filters array
        regular_filters = []
        for f in request.filters:
            if f.get("field") == "quarter_pi":
                # Extract PI value
                values = f.get("values", [])
                if values and len(values) > 0:
                    default_filters["pi"] = values[0] if isinstance(values, list) else values
            elif f.get("field") != "team_name":
                # Keep regular filters (exclude PI and team_name)
                regular_filters.append(f)
        
        # Extract Team/Group from separate fields
        if request.team_name:
            default_filters["team_name"] = request.team_name
            default_filters["isGroup"] = request.isGroup or False
        
        # Build meta_schema with build_report_config (only regular filters, no PI or team_name)
        build_config = {
            "report_type": request.report_type,
            "filters": regular_filters  # Only regular filters
        }
        
        if request.report_type == "table" and request.selected_fields:
            build_config["selected_fields"] = request.selected_fields
            if request.default_sort and request.default_sort.key:
                build_config["default_sort"] = {
                    "key": request.default_sort.key,
                    "direction": request.default_sort.direction or "asc",
                }
        elif request.report_type in ["bar_chart", "pie_chart"] and request.x_axis:
            build_config["x_axis"] = request.x_axis
            if request.report_type == "bar_chart":
                if request.y_axis:
                    build_config["y_axis"] = request.y_axis
                if request.stack_by:
                    build_config["stack_by"] = request.stack_by
                if request.bar_color:
                    build_config["bar_color"] = request.bar_color
        elif request.report_type == "multi_bar" and request.period and request.bar_1_metric and request.bar_2_metric:
            build_config["period"] = request.period
            build_config["lookback_months"] = request.lookback_months or 6
            build_config["bar_1_metric"] = request.bar_1_metric
            build_config["bar_2_metric"] = request.bar_2_metric
            if request.bar_1_color:
                build_config["bar_1_color"] = request.bar_1_color
            if request.bar_2_color:
                build_config["bar_2_color"] = request.bar_2_color
            if request.stack_by:
                build_config["stack_by"] = request.stack_by
        
        meta_schema = {
            "build_report_config": build_config
        }
        
        # Insert into database with default_filters column populated
        insert_sql = text("""
            INSERT INTO public.report_definitions (
                report_id,
                report_name,
                chart_type,
                data_source,
                description,
                report_type,
                default_filters,
                meta_schema,
                created_at,
                updated_at
            ) VALUES (
                :report_id,
                :report_name,
                :chart_type,
                'build_report',
                :description,
                'custom',
                CAST(:default_filters AS jsonb),
                CAST(:meta_schema AS jsonb),
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
        """)
        
        conn.execute(
            insert_sql,
            {
                "report_id": report_id,
                "report_name": request.report_name.strip(),
                "chart_type": request.report_type,
                "description": request.description.strip() if request.description else None,
                "default_filters": json.dumps(default_filters),
                "meta_schema": json.dumps(meta_schema),
            }
        )
        conn.commit()
        
        # Fetch the created report
        get_report_query = text("""
            SELECT
                report_id,
                report_name,
                chart_type,
                data_source,
                description,
                report_type,
                default_filters,
                meta_schema,
                created_at,
                updated_at
            FROM public.report_definitions
            WHERE report_id = :report_id
        """)
        
        result = conn.execute(get_report_query, {"report_id": report_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve saved report")
        
        row_dict = dict(row._mapping)
        # Ensure JSON fields are parsed
        if isinstance(row_dict.get("default_filters"), str):
            row_dict["default_filters"] = json.loads(row_dict["default_filters"])
        if isinstance(row_dict.get("meta_schema"), str):
            row_dict["meta_schema"] = json.loads(row_dict["meta_schema"])
        
        return {
            "success": True,
            "data": row_dict,
            "message": f"Report '{request.report_name}' saved successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving custom report: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save report: {str(e)}"
        )


@build_report_router.get("/reports/custom")
async def get_custom_reports(
    conn: Connection = Depends(get_db_connection)
):
    """
    Get all custom reports.
    """
    try:
        limit = settings.DEFAULT_QUERY_LIMIT
        
        query = text(f"""
            SELECT
                report_id,
                report_name,
                chart_type,
                data_source,
                description,
                report_type,
                default_filters,
                meta_schema,
                created_at,
                updated_at
            FROM {config.REPORT_DEFINITIONS_TABLE}
            WHERE report_type = 'custom'
            ORDER BY report_name
            LIMIT :limit
        """)
        
        result = conn.execute(query, {"limit": limit})
        reports = []
        
        for row in result:
            row_dict = dict(row._mapping)
            # Ensure JSON fields are parsed
            if isinstance(row_dict.get("default_filters"), str):
                row_dict["default_filters"] = json.loads(row_dict["default_filters"])
            if isinstance(row_dict.get("meta_schema"), str):
                row_dict["meta_schema"] = json.loads(row_dict["meta_schema"])
            reports.append(row_dict)
        
        return {
            "success": True,
            "data": reports,
            "count": len(reports),
            "message": f"Retrieved {len(reports)} custom reports"
        }
    
    except Exception as e:
        logger.error(f"Error fetching custom reports: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch custom reports: {str(e)}"
        )


@build_report_router.get("/reports/custom/{report_id}")
async def get_custom_report(
    report_id: str,
    conn: Connection = Depends(get_db_connection)
):
    """
    Get a specific custom report by ID.
    """
    try:
        query = text(f"""
            SELECT
                report_id,
                report_name,
                chart_type,
                data_source,
                description,
                report_type,
                default_filters,
                meta_schema,
                created_at,
                updated_at
            FROM {config.REPORT_DEFINITIONS_TABLE}
            WHERE report_id = :report_id AND report_type = 'custom'
        """)
        
        result = conn.execute(query, {"report_id": report_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Custom report with ID '{report_id}' not found"
            )
        
        row_dict = dict(row._mapping)
        # Ensure JSON fields are parsed
        if isinstance(row_dict.get("default_filters"), str):
            row_dict["default_filters"] = json.loads(row_dict["default_filters"])
        if isinstance(row_dict.get("meta_schema"), str):
            row_dict["meta_schema"] = json.loads(row_dict["meta_schema"])
        
        return {
            "success": True,
            "data": row_dict,
            "message": "Report retrieved successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching custom report: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch report: {str(e)}"
        )


@build_report_router.put("/reports/custom/{report_id}")
async def update_custom_report(
    report_id: str,
    request: SaveCustomReportRequest = Body(...),
    conn: Connection = Depends(get_db_connection)
):
    """
    Update a custom report.
    """
    try:
        # Validate report name length
        if len(request.report_name) > 100:
            raise HTTPException(
                status_code=400,
                detail="Report name must be 100 characters or less"
            )
        
        # Validate report name is not empty
        if not request.report_name or not request.report_name.strip():
            raise HTTPException(
                status_code=400,
                detail="Report name is required"
            )
        
        # Check if report exists and is custom
        check_query = text("""
            SELECT report_id
            FROM public.report_definitions
            WHERE report_id = :report_id AND report_type = 'custom'
        """)
        check_result = conn.execute(check_query, {"report_id": report_id})
        if not check_result.fetchone():
            raise HTTPException(
                status_code=404,
                detail=f"Custom report with ID '{report_id}' not found"
            )
        
        # Check for duplicate report name (excluding current report)
        check_duplicate_query = text("""
            SELECT report_id
            FROM public.report_definitions
            WHERE report_type = 'custom' 
            AND LOWER(report_name) = LOWER(:report_name)
            AND report_id != :report_id
        """)
        duplicate_result = conn.execute(check_duplicate_query, {
            "report_name": request.report_name.strip(),
            "report_id": report_id
        })
        if duplicate_result.fetchone():
            raise HTTPException(
                status_code=409,
                detail="Duplicate report name. Please choose a different name."
            )
        
        # Extract default filters (PI and Team/Group) from request
        # These will be saved to the default_filters column (like system reports)
        default_filters = {
            "pi": None,
            "team_name": None,
            "isGroup": False
        }
        
        # Extract PI from filters array
        regular_filters = []
        for f in request.filters:
            if f.get("field") == "quarter_pi":
                # Extract PI value
                values = f.get("values", [])
                if values and len(values) > 0:
                    default_filters["pi"] = values[0] if isinstance(values, list) else values
            elif f.get("field") != "team_name":
                # Keep regular filters (exclude PI and team_name)
                regular_filters.append(f)
        
        # Extract Team/Group from separate fields
        if request.team_name:
            default_filters["team_name"] = request.team_name
            default_filters["isGroup"] = request.isGroup or False
        
        # Build meta_schema with build_report_config (only regular filters, no PI or team_name)
        build_config = {
            "report_type": request.report_type,
            "filters": regular_filters  # Only regular filters
        }
        
        if request.report_type == "table" and request.selected_fields:
            build_config["selected_fields"] = request.selected_fields
            if request.default_sort and request.default_sort.key:
                build_config["default_sort"] = {
                    "key": request.default_sort.key,
                    "direction": request.default_sort.direction or "asc",
                }
        elif request.report_type in ["bar_chart", "pie_chart"] and request.x_axis:
            build_config["x_axis"] = request.x_axis
            if request.report_type == "bar_chart":
                if request.y_axis:
                    build_config["y_axis"] = request.y_axis
                if request.stack_by:
                    build_config["stack_by"] = request.stack_by
                if request.bar_color:
                    build_config["bar_color"] = request.bar_color
        elif request.report_type == "multi_bar" and request.period and request.bar_1_metric and request.bar_2_metric:
            build_config["period"] = request.period
            build_config["lookback_months"] = request.lookback_months or 6
            build_config["bar_1_metric"] = request.bar_1_metric
            build_config["bar_2_metric"] = request.bar_2_metric
            if request.bar_1_color:
                build_config["bar_1_color"] = request.bar_1_color
            if request.bar_2_color:
                build_config["bar_2_color"] = request.bar_2_color
            if request.stack_by:
                build_config["stack_by"] = request.stack_by
            elif "stack_by" in build_config:
                del build_config["stack_by"]
        
        meta_schema = {
            "build_report_config": build_config
        }
        
        # Update database with default_filters column
        update_sql = text("""
            UPDATE public.report_definitions
            SET
                report_name = :report_name,
                chart_type = :chart_type,
                description = :description,
                default_filters = CAST(:default_filters AS jsonb),
                meta_schema = CAST(:meta_schema AS jsonb),
                updated_at = CURRENT_TIMESTAMP
            WHERE report_id = :report_id AND report_type = 'custom'
        """)
        
        conn.execute(
            update_sql,
            {
                "report_id": report_id,
                "report_name": request.report_name.strip(),
                "chart_type": request.report_type,
                "description": request.description.strip() if request.description else None,
                "default_filters": json.dumps(default_filters),
                "meta_schema": json.dumps(meta_schema),
            }
        )
        conn.commit()
        
        # Fetch the updated report
        get_report_query = text("""
            SELECT
                report_id,
                report_name,
                chart_type,
                data_source,
                description,
                report_type,
                default_filters,
                meta_schema,
                created_at,
                updated_at
            FROM public.report_definitions
            WHERE report_id = :report_id
        """)
        
        result = conn.execute(get_report_query, {"report_id": report_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated report")
        
        row_dict = dict(row._mapping)
        # Ensure JSON fields are parsed
        if isinstance(row_dict.get("default_filters"), str):
            row_dict["default_filters"] = json.loads(row_dict["default_filters"])
        if isinstance(row_dict.get("meta_schema"), str):
            row_dict["meta_schema"] = json.loads(row_dict["meta_schema"])
        
        return {
            "success": True,
            "data": row_dict,
            "message": f"Report '{request.report_name}' updated successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating custom report: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update report: {str(e)}"
        )


@build_report_router.delete("/reports/custom/{report_id}")
async def delete_custom_report(
    report_id: str,
    conn: Connection = Depends(get_db_connection)
):
    """
    Delete a custom report.
    """
    try:
        # Check if report exists and is custom
        check_query = text("""
            SELECT report_id, report_name
            FROM public.report_definitions
            WHERE report_id = :report_id AND report_type = 'custom'
        """)
        check_result = conn.execute(check_query, {"report_id": report_id})
        row = check_result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Custom report with ID '{report_id}' not found"
            )
        
        report_name = row[1]
        
        # Delete the report
        delete_sql = text("""
            DELETE FROM public.report_definitions
            WHERE report_id = :report_id AND report_type = 'custom'
        """)
        
        conn.execute(delete_sql, {"report_id": report_id})
        conn.commit()
        
        return {
            "success": True,
            "message": f"Report '{report_name}' deleted successfully"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting custom report: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete report: {str(e)}"
        )

