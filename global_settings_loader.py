"""
Global settings loader - reads from global_settings table with in-memory cache.
Refreshed at startup and every REFRESH_INTERVAL_MINUTES. No Redis; add later if needed.
Usage: from global_settings_loader import settings
       days_back = settings.DEFAULT_VALIDATION_DAYS_BACK
"""

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_MINUTES = 15

# In-memory cache: setting_key -> parsed value. Filled by refresh_globals_from_db().
_cache: Dict[str, Any] = {}

# Defaults when DB is unavailable or key missing (match insert_default_global_settings).
_DEFAULTS: Dict[str, Any] = {
    "backend_default_validation_days_back": 180,
    "backend_aged_bug_threshold_days": 90,
    "backend_stuck_stories_threshold_days": 30,
    "backend_stuck_epics_threshold_days": 90,
    "backend_dragged_sprints_threshold": 3,
    "backend_epic_max_children_threshold": 25,
    "backend_max_sprint_count_for_reports": 20,
    "backend_default_query_limit": 300,
    "backend_default_hierarchy_limit": 500,
    "backend_ai_cards_limit": 20,
    "backend_epic_cycle_time_high": 40,
    "backend_epic_cycle_time_medium": 75,
    "backend_epic_cycle_time_period_days": 90,
    "backend_epic_wip_high_threshold": 30,
    "backend_epic_wip_medium_threshold": 60,
    "backend_min_duration_and_cycle_time_days": 0.01,
    "backend_story_cycle_time_high": 10,
    "backend_story_cycle_time_medium": 30,
    "backend_story_cycle_time_period_days": 30,
    "backend_heatmap_low_volume_threshold": 2,
    "backend_heatmap_medium_max_threshold": 5,
    "backend_heatmap_icon_threshold": 10,
    "backend_pi_completion_high_threshold": 75,
    "backend_pi_completion_medium_threshold": 55,
    "backend_sprint_wip_high_threshold": 30,
    "backend_sprint_wip_medium_threshold": 50,
    "backend_sprint_completion_high_threshold": 80,
    "backend_sprint_completion_medium_threshold": 60,
    "backend_open_bugs_high_per_team": 6,
    "backend_open_bugs_medium_per_team": 15,
    "backend_open_bugs_trend_period_days": 30,
    "backend_bug_issue_types": ["Bug", "Defect"],
    "backend_cache_ttl_realtime": 60,
    "backend_cache_ttl_aggregate": 300,
    "backend_cache_ttl_historical": 1800,
    "backend_cache_ttl_definitions": 3600,
    "backend_cache_ttl_groups_teams": 3600,
    "backend_redis_failure_cooldown_seconds": 1800,
    "backend_ai_chat_max_question_length": 1000,
    "backend_ai_chat_auto_sql_when_data_not_in_report": False,
}


def _parse_value(raw: str, setting_type: str) -> Any:
    if setting_type == "integer":
        return int(raw)
    if setting_type == "float":
        return float(raw)
    if setting_type == "boolean":
        return (raw or "").strip().lower() in ("true", "1", "yes")
    if setting_type == "json":
        return json.loads(raw)
    return raw


def _get(key: str) -> Any:
    if key in _cache:
        return _cache[key]
    return _DEFAULTS.get(key)


def refresh_globals_from_db() -> None:
    """Load all global_settings from DB into _cache. Uses defaults on failure."""
    try:
        import database_connection
        from sqlalchemy import text
        engine = database_connection.get_db_engine()
        if engine is None:
            logger.warning("Global settings: no DB engine, using defaults")
            _cache.clear()
            _cache.update(_DEFAULTS)
            return
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT setting_key, setting_value, setting_type FROM global_settings"
            ))
            new_cache: Dict[str, Any] = {}
            for row in result:
                key, value, stype = row[0], row[1], (row[2] or "string")
                try:
                    new_cache[key] = _parse_value(value, stype)
                except (ValueError, json.JSONDecodeError) as e:
                    logger.warning("Global settings: skip key %s: %s", key, e)
                    if key in _DEFAULTS:
                        new_cache[key] = _DEFAULTS[key]
            # Merge: defaults first, then DB values (so missing table rows still have defaults)
            _cache.clear()
            _cache.update(_DEFAULTS)
            _cache.update(new_cache)
            logger.info("🔄 Global settings refreshed from DB (%d keys)", len(new_cache))
    except Exception as e:
        logger.warning("Global settings: refresh failed (%s), using defaults", e)
        _cache.clear()
        _cache.update(_DEFAULTS)


# ---------------------------------------------------------------------------
# Scheduler (APScheduler) - start/stop for periodic refresh
# ---------------------------------------------------------------------------
_scheduler = None


def start_scheduler() -> None:
    """Start background scheduler to refresh global settings every REFRESH_INTERVAL_MINUTES."""
    global _scheduler
    if _scheduler is not None:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.add_job(
            refresh_globals_from_db,
            "interval",
            minutes=REFRESH_INTERVAL_MINUTES,
            id="global_settings_refresh",
        )
        _scheduler.start()
        logger.info("Global settings: scheduler started (every %s min)", REFRESH_INTERVAL_MINUTES)
    except Exception as e:
        logger.warning("Global settings: could not start scheduler: %s", e)


def stop_scheduler() -> None:
    """Stop the background scheduler (e.g. on app shutdown)."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        logger.info("Global settings: scheduler stopped")
    except Exception as e:
        logger.warning("Global settings: scheduler shutdown error: %s", e)


# ---------------------------------------------------------------------------
# Settings object - read-only properties (backed by _cache; add Redis later here)
# ---------------------------------------------------------------------------
class _Settings:
    """Read-only settings from global_settings table (cached)."""

    # Validation & Reporting
    @property
    def DEFAULT_VALIDATION_DAYS_BACK(self) -> int:
        return _get("backend_default_validation_days_back")

    @property
    def OLD_BUGS_THRESHOLD_DAYS(self) -> int:
        return _get("backend_aged_bug_threshold_days")

    @property
    def STUCK_STORIES_THRESHOLD_DAYS(self) -> int:
        return _get("backend_stuck_stories_threshold_days")

    @property
    def STUCK_EPICS_THRESHOLD_DAYS(self) -> int:
        return _get("backend_stuck_epics_threshold_days")

    @property
    def DRAGGED_SPRINTS_THRESHOLD(self) -> int:
        return _get("backend_dragged_sprints_threshold")

    @property
    def EPIC_MAX_CHILDREN_THRESHOLD(self) -> int:
        return _get("backend_epic_max_children_threshold")

    @property
    def MAX_SPRINT_COUNT_FOR_REPORTS(self) -> int:
        return _get("backend_max_sprint_count_for_reports")

    @property
    def DEFAULT_QUERY_LIMIT(self) -> int:
        return _get("backend_default_query_limit")

    @property
    def DEFAULT_HIERARCHY_LIMIT(self) -> int:
        return _get("backend_default_hierarchy_limit")

    @property
    def AI_CARDS_LIMIT(self) -> int:
        return _get("backend_ai_cards_limit")

    # Epic Metrics and KPI
    @property
    def EPIC_CYCLE_TIME_HIGH(self) -> int:
        return _get("backend_epic_cycle_time_high")

    @property
    def EPIC_CYCLE_TIME_MEDIUM(self) -> int:
        return _get("backend_epic_cycle_time_medium")

    @property
    def EPIC_CYCLE_TIME_PERIOD_DAYS(self) -> int:
        return _get("backend_epic_cycle_time_period_days")

    @property
    def EPIC_WIP_HIGH_THRESHOLD(self) -> int:
        return _get("backend_epic_wip_high_threshold")

    @property
    def EPIC_WIP_MEDIUM_THRESHOLD(self) -> int:
        return _get("backend_epic_wip_medium_threshold")

    # Story Metrics and KPI
    @property
    def MIN_DURATION_AND_CYCLE_TIME_DAYS(self) -> float:
        return _get("backend_min_duration_and_cycle_time_days")

    @property
    def STORY_CYCLE_TIME_HIGH(self) -> int:
        return _get("backend_story_cycle_time_high")

    @property
    def STORY_CYCLE_TIME_MEDIUM(self) -> int:
        return _get("backend_story_cycle_time_medium")

    @property
    def CYCLE_TIME_PERIOD_DAYS(self) -> int:
        return _get("backend_story_cycle_time_period_days")

    # PI Metrics and KPI (heatmap + completion)
    @property
    def HEATMAP_LOW_VOLUME_THRESHOLD(self) -> int:
        return _get("backend_heatmap_low_volume_threshold")

    @property
    def HEATMAP_MEDIUM_MAX_THRESHOLD(self) -> int:
        return _get("backend_heatmap_medium_max_threshold")

    @property
    def HEATMAP_ICON_THRESHOLD(self) -> int:
        return _get("backend_heatmap_icon_threshold")

    @property
    def PI_COMPLETION_HIGH_THRESHOLD(self) -> int:
        return _get("backend_pi_completion_high_threshold")

    @property
    def PI_COMPLETION_MEDIUM_THRESHOLD(self) -> int:
        return _get("backend_pi_completion_medium_threshold")

    # Sprint Metrics and KPI
    @property
    def SPRINT_WIP_HIGH_THRESHOLD(self) -> int:
        return _get("backend_sprint_wip_high_threshold")

    @property
    def SPRINT_WIP_MEDIUM_THRESHOLD(self) -> int:
        return _get("backend_sprint_wip_medium_threshold")

    @property
    def SPRINT_COMPLETION_HIGH_THRESHOLD(self) -> int:
        return _get("backend_sprint_completion_high_threshold")

    @property
    def SPRINT_COMPLETION_MEDIUM_THRESHOLD(self) -> int:
        return _get("backend_sprint_completion_medium_threshold")

    # Bug Tracking
    @property
    def OPEN_BUGS_HIGH_PER_TEAM(self) -> int:
        return _get("backend_open_bugs_high_per_team")

    @property
    def OPEN_BUGS_MEDIUM_PER_TEAM(self) -> int:
        return _get("backend_open_bugs_medium_per_team")

    @property
    def OPEN_BUGS_TREND_PERIOD_DAYS(self) -> int:
        return _get("backend_open_bugs_trend_period_days")

    @property
    def BUG_ISSUE_TYPES(self) -> List[str]:
        return _get("backend_bug_issue_types")

    # Cache Configuration
    @property
    def CACHE_TTL_REALTIME(self) -> int:
        return _get("backend_cache_ttl_realtime")

    @property
    def CACHE_TTL_AGGREGATE(self) -> int:
        return _get("backend_cache_ttl_aggregate")

    @property
    def CACHE_TTL_HISTORICAL(self) -> int:
        return _get("backend_cache_ttl_historical")

    @property
    def CACHE_TTL_DEFINITIONS(self) -> int:
        return _get("backend_cache_ttl_definitions")

    @property
    def CACHE_TTL_GROUPS_TEAMS(self) -> int:
        return _get("backend_cache_ttl_groups_teams")

    @property
    def REDIS_FAILURE_COOLDOWN_SECONDS(self) -> int:
        return _get("backend_redis_failure_cooldown_seconds")

    # AI Chat
    @property
    def AI_CHAT_MAX_QUESTION_LENGTH(self) -> int:
        return _get("backend_ai_chat_max_question_length")

    @property
    def AI_CHAT_AUTO_SQL_WHEN_DATA_NOT_IN_REPORT(self) -> bool:
        return _get("backend_ai_chat_auto_sql_when_data_not_in_report")


settings = _Settings()
