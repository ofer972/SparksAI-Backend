"""
Validation thresholds - central configuration.
Modify these constants to change validation behavior.
"""

# Old Bugs Validation
OLD_BUGS_THRESHOLD_DAYS = 90

# Stuck In Progress Validation (different for stories vs epics)
STUCK_STORIES_THRESHOLD_DAYS = 30  # Hierarchy level 0 issues
STUCK_EPICS_THRESHOLD_DAYS = 90    # Epic issue type

# Dragged Sprints Validation
DRAGGED_SPRINTS_THRESHOLD = 3

# Epic Health Validation
EPIC_MAX_CHILDREN_THRESHOLD = 25


