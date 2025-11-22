# Reports Metadata Service

The Reports Metadata service provides a single entry point for UI clients to discover
available reports and fetch normalized data payloads. Each report definition describes
the chart type that should be rendered and the filters required to resolve the dataset.

## Endpoints

### `GET /api/v1/reports`

Returns the list of report definitions registered in the backend.

Response structure:

```json
{
  "success": true,
  "data": [
    {
      "report_id": "team-sprint-burndown",
      "report_name": "Sprint Burndown",
      "chart_type": "burn_down",
      "description": "Tracks remaining work across a sprint for a given team.",
      "data_source": "team_sprint_burndown",
      "default_filters": {
        "team_name": null,
        "issue_type": "all",
        "sprint_name": null
      },
      "meta_schema": {
        "required_filters": ["team_name"],
        "optional_filters": ["issue_type", "sprint_name"]
      }
    }
  ],
  "count": 3,
  "message": "Retrieved 3 report definitions"
}
```

### `GET /api/v1/reports/{report_id}`

Resolves a specific report, combining default filters with query parameter overrides.

Example request:

```
GET /api/v1/reports/team-sprint-burndown?team_name=Team%20Rocket
```

Response structure:

```json
{
  "success": true,
  "data": {
    "definition": {
      "report_id": "team-sprint-burndown",
      "report_name": "Sprint Burndown",
      "chart_type": "burn_down",
      "description": "Tracks remaining work across a sprint for a given team.",
      "data_source": "team_sprint_burndown"
    },
    "filters": {
      "team_name": "Team Rocket",
      "issue_type": "all",
      "sprint_name": null
    },
    "result": [
      {
        "snapshot_date": "2025-10-01",
        "start_date": "2025-09-25",
        "end_date": "2025-10-08",
        "remaining_issues": 23,
        "ideal_remaining": 18
      }
      /* ... truncated ... */
    ],
    "meta": {
      "team_name": "Team Rocket",
      "issue_type": "all",
      "sprint_name": "2025 Sprint 10",
      "auto_selected": true,
      "total_issues_in_sprint": 42,
      "start_date": "2025-09-25",
      "end_date": "2025-10-08"
    }
  },
  "message": "Retrieved report 'team-sprint-burndown'"
}
```

### Filter handling

- Default filters defined in the report metadata are merged with query parameters.
- Required filters are validated server-side; missing values return a `400` response.
- Query parameters with empty strings are treated as `null`.

## Caching

The Reports service implements Redis-based caching to improve performance and reduce database load.

### Cache Behavior

All report endpoints (`GET /api/v1/reports` and `GET /api/v1/reports/{report_id}`) support caching with the following characteristics:

- **Automatic caching**: Results are automatically cached based on report type and filters
- **Smart TTL defaults**: Cache expiration times are automatically determined by report type
- **Cache key generation**: Unique cache keys are generated from report IDs and filter combinations
- **Graceful degradation**: If Redis is unavailable, the service continues to work without caching

### Cache TTL Defaults

Default cache durations by report type:

| Report Type | TTL | Examples |
|------------|-----|----------|
| Real-time | 60 seconds | `team-current-sprint-progress`, reports with "current", "progress", "wip" |
| Aggregate | 300 seconds (5 min) | `team-sprint-burndown`, `pi-predictability`, reports with "burndown", "trend" |
| Historical | 1800 seconds (30 min) | `team-closed-sprints`, `pi-metrics-summary`, reports with "closed", "historical" |
| Definitions | 3600 seconds (1 hour) | Report definitions list (`/reports`) |

### Cache Control Parameters

Both report endpoints support cache control via query parameters:

#### `bypass_cache` (boolean, default: false)

Skip cache lookup and fetch fresh data from the database.

Example:
```
GET /api/v1/reports/team-sprint-burndown?team_name=TeamA&bypass_cache=true
```

#### `cache_ttl` (integer, optional)

Override the default cache TTL for this request (in seconds).

Example:
```
GET /api/v1/reports/pi-burndown?pi_names=PI2024-Q4&cache_ttl=600
```

### Cache Response Indicator

All responses include a `cached` field indicating whether the result was served from cache:

```json
{
  "success": true,
  "data": { /* ... */ },
  "message": "Retrieved report 'team-sprint-burndown' (cached)",
  "cached": true
}
```

### Cache Management Endpoints

#### `POST /api/v1/reports/cache/invalidate`

Manually invalidate cached reports.

**Query Parameters:**
- `report_id` (optional): If provided, clears only caches for that specific report. If omitted, clears all report caches.

**Examples:**

Invalidate all caches for a specific report:
```bash
POST /api/v1/reports/cache/invalidate?report_id=team-sprint-burndown
```

Invalidate all report caches:
```bash
POST /api/v1/reports/cache/invalidate
```

**Response:**
```json
{
  "success": true,
  "message": "Invalidated 5 cache entries for report 'team-sprint-burndown'",
  "count": 5,
  "report_id": "team-sprint-burndown"
}
```

#### `GET /api/v1/reports/cache/stats`

Get Redis cache statistics and health information.

**Response:**
```json
{
  "success": true,
  "data": {
    "enabled": true,
    "available": true,
    "report_cache_keys": 42,
    "total_commands": 15234,
    "keyspace_hits": 8450,
    "keyspace_misses": 2103,
    "hit_rate_percentage": 80.09,
    "redis_version": "7.2.0"
  },
  "message": "Cache statistics retrieved successfully"
}
```

### Environment Variables

Configure caching behavior via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `localhost` | Redis server hostname |
| `REDIS_PORT` | `6379` | Redis server port |
| `REDIS_DB` | `0` | Redis database number |
| `REDIS_PASSWORD` | `None` | Redis password (optional) |
| `REDIS_ENABLED` | `true` | Enable/disable caching |
| `CACHE_TTL_REALTIME` | `60` | TTL for real-time reports (seconds) |
| `CACHE_TTL_AGGREGATE` | `300` | TTL for aggregate reports (seconds) |
| `CACHE_TTL_HISTORICAL` | `1800` | TTL for historical reports (seconds) |
| `CACHE_TTL_DEFINITIONS` | `3600` | TTL for report definitions (seconds) |

### Cache Invalidation Strategy

Caches should be invalidated when underlying data changes:

1. **After Jira sync**: Clear all report caches
   ```bash
   POST /api/v1/reports/cache/invalidate
   ```

2. **After specific data updates**: Clear caches for affected reports
   ```bash
   POST /api/v1/reports/cache/invalidate?report_id=team-sprint-burndown
   ```

3. **Scheduled refresh**: Set up periodic cache invalidation via cron/scheduler

### Performance Impact

Expected performance improvements with caching enabled:

- **Cache hit response time**: 10-50ms (vs 500ms-2s+ uncached)
- **Database load reduction**: 50-95% for frequently accessed reports
- **Concurrent request handling**: Significantly improved for popular reports

### Supported data sources

The following data source keys are currently registered:

- `team_sprint_burndown` – resolves sprint burndown data with optional issue type and sprint overrides.
- `team_current_sprint_progress` – returns summarized metrics for the active sprint.
- `pi_burndown` – retrieves PI burndown data for epics and features.
- `team_closed_sprints` – lists completed sprints and completion metrics across a recent time window.
- `team_issues_trend` – supplies the bugs created/resolved time-series for a given team.
- `pi_predictability` – returns PI predictability metrics per PI/team combination.
- `epic_scope_changes` – exposes stacked scope change metrics for selected PI quarters.

Additional report definitions can be added via the `report_definitions` database table.

