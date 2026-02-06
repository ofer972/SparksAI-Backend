"""
Terminology replacement constants for user-facing text.

Provides constants for replacing "PI" terminology with value from PI_TERMINOLOGY_REPLACEMENT environment variable.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Read replacement term from environment variable
_REPLACEMENT_TERM = os.getenv("PI_TERMINOLOGY_REPLACEMENT")

# Define constants
PI_TERM = _REPLACEMENT_TERM if _REPLACEMENT_TERM else "PI"
PI_TERM_PLURAL = f"{PI_TERM}s" if _REPLACEMENT_TERM else "PIs"

if _REPLACEMENT_TERM:
    logger.info(f"Terminology replacement enabled: PI_TERM='{PI_TERM}', PI_TERM_PLURAL='{PI_TERM_PLURAL}'")
else:
    logger.debug("Terminology replacement disabled: using default 'PI'/'PIs'")

