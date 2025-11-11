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

