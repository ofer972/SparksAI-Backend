# Sprint Issues with Epic for LLM Endpoint - Usage Example

## Endpoint
```
GET /api/v1/sprints/sprint-issues-with-epic-for-llm
```

## Request

### Query Parameters
- `sprint_id` (required, integer): The ID of the sprint to get issues for
- `team_name` (required, string): The name of the team to get issues for

### Example Request

#### Using curl:
```bash
curl -X GET "http://localhost:8000/api/v1/sprints/sprint-issues-with-epic-for-llm?sprint_id=123&team_name=TeamAlpha" \
  -H "Content-Type: application/json"
```

#### Using Python requests:
```python
import requests

response = requests.get(
    "http://localhost:8000/api/v1/sprints/sprint-issues-with-epic-for-llm",
    params={"sprint_id": 123, "team_name": "TeamAlpha"}
)

data = response.json()
print(data)
```

#### Using JavaScript fetch:
```javascript
fetch('http://localhost:8000/api/v1/sprints/sprint-issues-with-epic-for-llm?sprint_id=123&team_name=TeamAlpha')
  .then(response => response.json())
  .then(data => console.log(data));
```

## Response

### Success Response (200 OK)

```json
{
  "success": true,
  "data": {
    "sprint_issues": [
      {
        "issue_key": "IDPSCAN-449",
        "issue_summary": "FRD Review - IDPS_09 Vehicle [NULL]",
        "issue_description": null,
        "issue_type": "Task",
        "status_category": "To Do",
        "description": null,
        "flagged": [],
        "dependency": [],
        "sprint_ids": [123, 124],
        "epic_summary": "IDPS_09 Vehicle State Aware Diagr",
        "addpoint_name": "Sprint Planning Point A"
      },
      {
        "issue_key": "IDPSCAN-474",
        "issue_summary": "HLR Review - IDPS_09 Vehicle [NULL]",
        "issue_description": null,
        "issue_type": "Task",
        "status_category": "To Do",
        "description": null,
        "flagged": [],
        "dependency": [],
        "sprint_ids": [123],
        "epic_summary": "IDPS_09 Vehicle State Aware Diagr",
        "addpoint_name": "Sprint Planning Point B"
      }
    ],
    "count": 2,
    "sprint_id": 123,
    "team_name": "TeamAlpha"
  },
  "message": "Retrieved 2 sprint issues with epic data for sprint_id 123 and team 'TeamAlpha'"
}
```

### Empty Result Response (200 OK)

```json
{
  "success": true,
  "data": {
    "sprint_issues": [],
    "count": 0,
    "sprint_id": 123,
    "team_name": "TeamAlpha"
  },
  "message": "Retrieved 0 sprint issues with epic data for sprint_id 123 and team 'TeamAlpha'"
}
```

### Error Response - Missing Parameter (422 Unprocessable Entity)

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["query", "sprint_id"],
      "msg": "Field required",
      "input": null
    },
    {
      "type": "missing",
      "loc": ["query", "team_name"],
      "msg": "Field required",
      "input": null
    }
  ]
}
```

### Error Response - Invalid Parameter Type (422 Unprocessable Entity)

```json
{
  "detail": [
    {
      "type": "int_parsing",
      "loc": ["query", "sprint_id"],
      "msg": "Input should be a valid integer",
      "input": "invalid"
    }
  ]
}
```

### Error Response - Database Error (500 Internal Server Error)

```json
{
  "detail": "Failed to fetch sprint issues with epic for LLM: [database error message]"
}
```

## Response Fields

The endpoint returns **ALL fields** from the `sprint_issues_with_epic_for_llm` view. Based on the view structure, fields include:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `issue_key` | string | Unique identifier for the issue | "IDPSCAN-449" |
| `issue_summary` | string | Brief summary or title of the issue | "FRD Review - IDPS_09 Vehicle" |
| `issue_description` | string/null | Detailed description of the issue | null or text |
| `issue_type` | string | Category of the issue | "Task", "Story", "Bug" |
| `status_category` | string | Current status category | "To Do", "In Progress", "Done" |
| `description` | string/null | Additional description field | null or text |
| `flagged` | array | Array of flagged items | [] or ["urgent"] |
| `dependency` | array | Array of dependencies | [] or ["IDPSCAN-450"] |
| `sprint_ids` | array | Array of sprint IDs associated with the issue | [123, 124] |
| `epic_summary` | string | Summary or title of the related epic | "IDPS_09 Vehicle State Aware Diagr" |
| `addpoint_name` | string/null | Name of the addpoint associated with the sprint | "Sprint Planning Point A" |

**Note**: Date/timestamp fields (if present in the view) will be formatted as `YYYY-MM-DD` or ISO 8601 format.

## Response Structure

```typescript
{
  success: boolean;           // Always true for successful responses
  data: {
    sprint_issues: Array<{    // Array of all issues for the sprint
      // All fields from sprint_issues_with_epic_for_llm view
      [key: string]: any;
    }>;
    count: number;            // Number of issues returned
    sprint_id: number;        // The sprint_id that was queried
  };
  message: string;            // Human-readable success message
}
```

