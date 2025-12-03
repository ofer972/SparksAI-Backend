# Null vs Zero Comparison - Original vs Current

## Original Logic:
```python
'remaining_issues': int(row[3]) if row[3] else 0,
```

**Behavior:**
- If `row[3]` is `None` → `None` is falsy → returns `0`
- If `row[3]` is `0` → `0` is falsy → returns `0`
- If `row[3]` is any other number → converts to `int` and returns it

**BUT:** If `row[3]` is `None` and we want to preserve `None` (for UI to stop the line), the original logic would still return `0` because `None` is falsy.

## Current Logic:
```python
'remaining_issues': int(row_dict.get('remaining_issues', 0)) if row_dict.get('remaining_issues') else 0,
```

**Behavior:**
- If `row_dict.get('remaining_issues')` is `None` → `None` is falsy → returns `0`
- If `row_dict.get('remaining_issues')` is `0` → `0` is falsy → returns `0`
- If `row_dict.get('remaining_issues')` is any other number → converts to `int` and returns it

**BUT:** `row_dict.get('remaining_issues', 0)` has a default of `0`, so if the column doesn't exist, it returns `0` instead of `None`.

## The Issue:

The current logic has a problem:
```python
int(row_dict.get('remaining_issues', 0)) if row_dict.get('remaining_issues') else 0
```

This calls `row_dict.get('remaining_issues')` TWICE, and the default value `0` means:
- If column is missing → returns `0` (not `None`)
- If column is `None` → still returns `0` (because `None` is falsy)

**The original might have returned `None` in some edge cases that the current code doesn't.**

## Solution:

If we want to preserve `None` when the database returns `None` (so UI can stop the line), we should use:

```python
'remaining_issues': int(row_dict.get('remaining_issues')) if row_dict.get('remaining_issues') is not None else None,
```

Or simpler:
```python
value = row_dict.get('remaining_issues')
'remaining_issues': int(value) if value is not None else None,
```

This way:
- If database returns `None` → Python returns `None` → JSON returns `null` → UI stops the line
- If database returns `0` → Python returns `0` → JSON returns `0` → UI continues the line
- If database returns any number → Python returns that number → UI continues the line

