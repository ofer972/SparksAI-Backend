"""
Database General - Database access functions for general services.

This module contains database access functions for recommendations and team AI cards.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging
import os
import config

logger = logging.getLogger(__name__)

# Import priority helper from config (no circular dependency)
from config import build_priority_case_sql, PRIORITY_COLOR_MAP


def add_priority_color_to_card(card: Dict[str, Any]) -> None:
    """Add priority_color field to a card dictionary based on its priority."""
    if isinstance(card, dict):
        priority = card.get('priority')
        card['priority_color'] = PRIORITY_COLOR_MAP.get(priority, 'Gray')


def get_insight_types_by_categories(categories: List[str], conn: Connection = None) -> List[str]:
    """
    Get all insight type names that match ANY of the specified categories.
    Returns a deduplicated, sorted list of insight types.
    
    Uses a single SQL query with jsonb_array_elements_text to expand categories
    and check if insight_categories contains any of them using @> operator.
    
    Args:
        categories (List[str]): List of category names (e.g., ["Daily", "Planning"])
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: Deduplicated, sorted list of insight_type strings that match any of the categories
    """
    if not categories:
        return []
    
    try:
        import json
        # SECURE: Parameterized query prevents SQL injection
        # Use jsonb_array_elements_text to expand categories array and check
        # if insight_categories contains any of the provided categories using @> operator
        query = text("""
            SELECT DISTINCT insight_type
            FROM public.insight_types
            WHERE active = TRUE
              AND EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text(CAST(:categories AS jsonb)) AS cat
                WHERE insight_categories @> jsonb_build_array(cat)
              )
        """)
        
        logger.info(f"Executing query to get insight types for categories: {categories}")
        
        result = conn.execute(query, {"categories": json.dumps(categories)})
        rows = result.fetchall()
        
        # Extract insight_type values
        insight_types = [row[0] for row in rows]
        
        # Sort for consistency (DISTINCT already handles deduplication)
        result = sorted(insight_types)
        
        logger.info(f"Found {len(result)} unique insight types for categories {categories}: {result}")
        return result
            
    except Exception as e:
        logger.error(f"Error fetching insight types for categories {categories}: {e}")
        raise e


def get_top_ai_cards_filtered(filter_column: str, filter_value: str, limit: int = 4, categories: Optional[List[str]] = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get top AI cards filtered by a specific column (e.g., team_name or pi).
    
    Returns the most recent + highest priority card for each type (max 1 per type).
    Cards are ordered by:
    1. Priority (Critical > High > Important)
    2. Created_at (newest first)
    
    Args:
        filter_column (str): Column to filter by (must be 'team_name', 'pi', or 'group_name' for security)
        filter_value (str): Value to filter by
        limit (int): Number of AI cards to return (default: 4)
        categories (Optional[List[str]]): Optional category filter - only return cards with insight_type matching insight types for any of these categories
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of AI card dictionaries with all columns
    """
    # Security: Only allow specific columns to prevent SQL injection
    allowed_columns = {'team_name', 'pi', 'group_name'}
    if filter_column not in allowed_columns:
        raise ValueError(f"filter_column must be one of {allowed_columns}, got: {filter_column}")
    
    try:
        import json
        
        # Build additional WHERE condition based on filter_column
        # Team/Group cards: exclude cards where both team_name and group_name are NULL
        # PI cards: only return cards where pi is NOT NULL
        if filter_column in ('team_name', 'group_name'):
            additional_filter = "AND (team_name IS NOT NULL OR group_name IS NOT NULL)"
        elif filter_column == 'pi':
            additional_filter = "AND pi IS NOT NULL"
        else:
            additional_filter = ""
        
        # If categories provided, get insight types for those categories
        insight_types_list = []
        if categories:
            insight_types_list = get_insight_types_by_categories(categories, conn)
            if not insight_types_list:
                # No insight types found for categories, return empty result
                logger.info(f"No insight types found for categories {categories}, returning empty result")
                return []
        
        # Build SQL query with optional category filter
        if categories and insight_types_list:
            # Build IN clause for insight_type filtering
            # Use parameterized query with array
            sql_query = f"""
                WITH ranked_cards AS (
                    SELECT *,
                        ROW_NUMBER() OVER (
                            PARTITION BY insight_type 
                            ORDER BY 
                                {build_priority_case_sql()},
                                created_at DESC
                        ) as rn
                    FROM public.ai_summary
                    WHERE {filter_column} = :filter_value
                      AND insight_type = ANY(:insight_types)
                      {additional_filter}
                )
                SELECT *
                FROM ranked_cards
                WHERE rn = 1
                ORDER BY 
                    {build_priority_case_sql()},
                    created_at DESC
                LIMIT :limit
            """
            
            params = {
                'filter_value': filter_value,
                'insight_types': insight_types_list,
                'limit': limit
            }
        else:
            # No category filter - original query
            sql_query = f"""
                WITH ranked_cards AS (
                    SELECT *,
                        ROW_NUMBER() OVER (
                            PARTITION BY insight_type 
                            ORDER BY 
                                {build_priority_case_sql()},
                                created_at DESC
                        ) as rn
                    FROM public.ai_summary
                    WHERE {filter_column} = :filter_value
                      {additional_filter}
                )
                SELECT *
                FROM ranked_cards
                WHERE rn = 1
                ORDER BY 
                    {build_priority_case_sql()},
                    created_at DESC
                LIMIT :limit
            """
            
            params = {
                'filter_value': filter_value,
                'limit': limit
            }
        
        logger.info(f"Executing query to get top AI cards filtered by {filter_column}: {filter_value}, categories: {categories}")
        logger.info(f"Parameters: filter_column={filter_column}, filter_value={filter_value}, limit={limit}, categories={categories}")
        
        result = conn.execute(text(sql_query), params)
        
        # Convert rows to list of dictionaries and add priority_color
        ai_cards = []
        for row in result:
            card = dict(row._mapping)
            add_priority_color_to_card(card)
            ai_cards.append(card)
        
        return ai_cards
            
    except Exception as e:
        logger.error(f"Error fetching top AI cards filtered by {filter_column}={filter_value}: {e}")
        raise e


def get_top_ai_cards(team_name: str, limit: int = 4, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get top AI cards for a specific team.
    
    Returns the most recent + highest priority card for each type (max 1 per type).
    Cards are ordered by:
    1. Priority (Critical > High > Important)
    2. Date (newest first)
    
    Args:
        team_name (str): Team name
        limit (int): Number of AI cards to return (default: 4)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of AI card dictionaries
    """
    # Use the generic function for backward compatibility
    return get_top_ai_cards_filtered('team_name', team_name, limit, category=None, conn=conn)


def get_recommendations_by_ai_summary_id(
    ai_summary_id: int,
    limit: int = 4,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Get recommendations linked to a specific AI summary card.
    
    Returns recommendations ordered by:
    1. Date (newest first)
    2. Priority (Critical > High > Important)
    3. ID (descending)
    
    Args:
        ai_summary_id (int): The ID of the AI summary card
        limit (int): Maximum number of recommendations to return (default: 4)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of recommendation dictionaries
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT *
            FROM public.recommendations
            WHERE source_ai_summary_id = :ai_summary_id
            ORDER BY 
                DATE(date) DESC,
                {build_priority_case_sql()},
                id DESC
            LIMIT :limit
        """
        
        logger.info(f"Executing query to get recommendations for AI summary card: {ai_summary_id}")
        logger.info(f"Parameters: ai_summary_id={ai_summary_id}, limit={limit}")
        
        result = conn.execute(text(sql_query), {
            'ai_summary_id': ai_summary_id,
            'limit': limit
        })
        
        # Convert rows to list of dictionaries
        recommendations = []
        for row in result:
            recommendations.append(dict(row._mapping))
        
        return recommendations
            
    except Exception as e:
        logger.error(f"Error fetching recommendations for AI summary card {ai_summary_id}: {e}")
        raise e


def get_top_ai_cards_multi_filtered(
    pi: Optional[str] = None,
    team_name: Optional[str] = None,
    group_name: Optional[str] = None,
    limit: int = 4,
    categories: Optional[List[str]] = None,
    insight_type: Optional[str] = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Get top AI cards filtered by multiple criteria (PI, team, group).
    
    Supports combinations:
    - PI only: Returns insights with exact PI match
    - PI + Team: Returns insights for the team where PI matches OR PI is NULL/empty
    - PI + Group: Returns insights for the group where PI matches OR PI is NULL/empty
    - Team only: Returns insights for the team (any PI)
    - Group only: Returns insights for the group (any PI)
    
    Special behavior: When both PI and (team_name OR group_name) are provided,
    the query returns insights that either match the specified PI or have no PI
    assigned (NULL or empty string). This allows viewing all insights for a
    team/group regardless of PI assignment.
    
    Returns the most recent + highest priority card for each type (max 1 per type).
    Cards are ordered by:
    1. Priority (Critical > High > Important)
    2. Created_at (newest first)
    """
    from sqlalchemy import text
    import json
    
    # Build WHERE conditions dynamically
    where_conditions = []
    params = {'limit': limit}
    
    # Check if team_name or group_name is provided along with pi
    has_team_or_group = team_name is not None or group_name is not None
    
    if pi:
        # If pi is provided along with team_name or group_name, include NULL/empty PI values
        # Otherwise, use exact match
        if has_team_or_group:
            where_conditions.append('(pi = :pi OR pi IS NULL OR pi = \'\')')
        else:
            where_conditions.append('pi = :pi')
        params['pi'] = pi
    
    if team_name:
        where_conditions.append('team_name = :team_name')
        params['team_name'] = team_name
    
    if group_name:
        where_conditions.append('group_name = :group_name')
        params['group_name'] = group_name
    
    # Validate at least one filter is provided
    if not where_conditions:
        raise ValueError("At least one filter (pi, team_name, or group_name) must be provided")
    
    # Build additional filter for data integrity
    if team_name or group_name:
        additional_filter = "AND (team_name IS NOT NULL OR group_name IS NOT NULL)"
    elif pi:
        additional_filter = "AND pi IS NOT NULL"
    else:
        additional_filter = ""
    
    # Handle insight_type and categories (mutually exclusive)
    insight_types_list = []
    if categories:
        # get_insight_types_by_categories is defined in this same file (database_general.py)
        insight_types_list = get_insight_types_by_categories(categories, conn)
        if not insight_types_list:
            return []
    
    # Build SQL query
    where_clause = " AND ".join(where_conditions)
    
    if insight_type:
        # Branch 1: Filter by single insight_type
        sql_query = f"""
            WITH ranked_cards AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY insight_type 
                        ORDER BY 
                            date DESC,
                            {build_priority_case_sql()}
                    ) as rn
                FROM public.ai_summary
                WHERE {where_clause}
                  AND insight_type = :insight_type
                  {additional_filter}
            )
            SELECT *
            FROM ranked_cards
            WHERE rn = 1
            ORDER BY 
                date DESC,
                {build_priority_case_sql()}
            LIMIT :limit
        """
        params['insight_type'] = insight_type
    elif categories and insight_types_list:
        # Branch 2: Filter by categories (existing logic)
        sql_query = f"""
            WITH ranked_cards AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY insight_type 
                        ORDER BY 
                            date DESC,
                            {build_priority_case_sql()}
                    ) as rn
                FROM public.ai_summary
                WHERE {where_clause}
                  AND insight_type = ANY(:insight_types)
                  {additional_filter}
            )
            SELECT *
            FROM ranked_cards
            WHERE rn = 1
            ORDER BY 
                date DESC,
                {build_priority_case_sql()}
            LIMIT :limit
        """
        params['insight_types'] = insight_types_list
    else:
        # Branch 3: No insight_type filter (existing logic)
        sql_query = f"""
            WITH ranked_cards AS (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY insight_type 
                        ORDER BY 
                            date DESC,
                            {build_priority_case_sql()}
                    ) as rn
                FROM public.ai_summary
                WHERE {where_clause}
                  {additional_filter}
            )
            SELECT *
            FROM ranked_cards
            WHERE rn = 1
            ORDER BY 
                date DESC,
                {build_priority_case_sql()}
            LIMIT :limit
        """
    
    logger.info(f"Executing multi-filter query: pi={pi}, team_name={team_name}, group_name={group_name}, categories={categories}, insight_type={insight_type}")
    result = conn.execute(text(sql_query), params)
    cards = [dict(row._mapping) for row in result]
    
    # Add priority_color to each card
    for card in cards:
        add_priority_color_to_card(card)
    
    return cards


def get_top_ai_cards_with_recommendations_from_json(
    insight_type: Optional[str] = None,
    filter_value: Optional[str] = None,
    pi: Optional[str] = None,
    team_name: Optional[str] = None,
    group_name: Optional[str] = None,
    limit: int = 4,
    recommendations_limit: int = 3,
    categories: Optional[List[str]] = None,
    insight_type_name: Optional[str] = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Get top AI cards with recommendations extracted from information_json field.
    Supports both legacy single-filter mode and new multi-filter mode.
    
    Returns the most recent + highest priority card for each type (max 1 per type),
    with recommendations parsed from the card's information_json field.
    
    Args:
        insight_type (Optional[str]): Type of insight - 'team', 'group', or 'pi' (legacy mode)
        filter_value (Optional[str]): Value to filter by (legacy mode)
        pi (Optional[str]): PI name (new multi-filter mode)
        team_name (Optional[str]): Team name (new multi-filter mode)
        group_name (Optional[str]): Group name (new multi-filter mode)
        limit (int): Number of AI cards to return (default: 4)
        recommendations_limit (int): Maximum recommendations per card (default: 3, max: 3)
        categories (Optional[List[str]]): Optional category filter
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of AI card dictionaries, each with 'recommendations' array and 'recommendations_count'
    """
    import json
    
    try:
        # Determine which mode to use
        if pi is not None or team_name is not None or group_name is not None:
            # New multi-filter mode
            ai_cards = get_top_ai_cards_multi_filtered(
                pi=pi,
                team_name=team_name,
                group_name=group_name,
                limit=limit,
                categories=categories,
                insight_type=insight_type_name,
                conn=conn
            )
        elif insight_type and filter_value:
            # Legacy single-filter mode (backward compatibility)
            filter_column_map = {
                'team': 'team_name',
                'group': 'group_name',
                'pi': 'pi'
            }
            
            if insight_type not in filter_column_map:
                raise ValueError(f"Invalid insight_type: {insight_type}. Must be 'team', 'group', or 'pi'")
            
            filter_column = filter_column_map[insight_type]
            ai_cards = get_top_ai_cards_filtered(filter_column, filter_value, limit, categories=categories, conn=conn)
        else:
            raise ValueError("Either (insight_type and filter_value) or (pi/team_name/group_name) must be provided")
        
        # For each card, add priority_color and parse recommendations from information_json
        for card in ai_cards:
            # Add priority_color to each card (needed here because cards are modified in this function)
            add_priority_color_to_card(card)
            
            card_id = card.get('id')
            information_json_str = card.get('information_json')
            
            recommendations = []
            
            if card_id and information_json_str:
                try:
                    # Parse the JSON string
                    information_json = json.loads(information_json_str) if isinstance(information_json_str, str) else information_json_str
                    
                    # Extract Recommendations array
                    if isinstance(information_json, dict) and 'Recommendations' in information_json:
                        json_recommendations = information_json['Recommendations']
                        
                        if isinstance(json_recommendations, list):
                            # Take first N recommendations (no sorting, as they appear in JSON)
                            for index, json_rec in enumerate(json_recommendations[:recommendations_limit]):
                                if isinstance(json_rec, dict):
                                    # Map fields from JSON to recommendation structure
                                    recommendation = {
                                        'id': f"{card_id}_{index + 1}",  # Format: {card_id}_{index} starting from 1
                                        'team_name': card.get('team_name'),
                                        'date': card.get('date'),
                                        'action_text': json_rec.get('text', ''),  # text -> action_text
                                        'rational': json_rec.get('header', ''),  # header -> rational
                                        'priority': 'Important',  # Always "Important"
                                        'status': 'Pending',  # Always "Pending"
                                        'source_job_id': card.get('source_job_id'),
                                        'source_ai_summary_id': card_id,  # Same as card ID
                                        'created_at': card.get('created_at'),
                                        'updated_at': card.get('updated_at')
                                    }
                                    recommendations.append(recommendation)
                    
                except (json.JSONDecodeError, TypeError, KeyError) as e:
                    # Log error but continue - return empty recommendations array
                    logger.warning(f"Error parsing information_json for card {card_id}: {e}")
            
            card['recommendations'] = recommendations
            card['recommendations_count'] = len(recommendations)
        
        return ai_cards
            
    except Exception as e:
        logger.error(f"Error fetching top AI cards with recommendations: {e}")
        raise e




def get_top_ai_cards_with_recommendations_filtered(
    filter_column: str,
    filter_value: str,
    limit: int = 4,
    recommendations_limit: int = 5,
    categories: Optional[List[str]] = None,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Get top AI cards with their recommendations attached.
    
    Returns the most recent + highest priority card for each type (max 1 per type),
    with recommendations linked via source_ai_summary_id.
    
    Args:
        filter_column (str): Column to filter by ('team_name', 'pi', or 'group_name')
        filter_value (str): Value to filter by
        limit (int): Number of AI cards to return (default: 4)
        recommendations_limit (int): Maximum recommendations per card (default: 5)
        categories (Optional[List[str]]): Optional category filter - only return cards with insight_type matching insight types for any of these categories
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of AI card dictionaries, each with 'recommendations' array and 'recommendations_count'
    """
    try:
        # Get top AI cards using existing function
        ai_cards = get_top_ai_cards_filtered(filter_column, filter_value, limit, categories=categories, conn=conn)
        
        # For each card, fetch and attach recommendations from database
        for card in ai_cards:
            card_id = card.get('id')
            if card_id:
                recommendations = get_recommendations_by_ai_summary_id(
                    card_id,
                    recommendations_limit,
                    conn
                )
                # Remove full_information and information_json from each recommendation
                filtered_recommendations = []
                for rec in recommendations:
                    filtered_rec = {k: v for k, v in rec.items() if k not in ['full_information', 'information_json']}
                    filtered_recommendations.append(filtered_rec)
                card['recommendations'] = filtered_recommendations
                card['recommendations_count'] = len(filtered_recommendations)
            else:
                card['recommendations'] = []
                card['recommendations_count'] = 0
        
        return ai_cards
            
    except Exception as e:
        logger.error(f"Error fetching top AI cards with recommendations filtered by {filter_column}={filter_value}: {e}")
        raise e


def get_ai_card_by_id(card_id: int, conn: Connection = None) -> Optional[Dict[str, Any]]:
    """
    Get a single AI summary card by ID from ai_summary table.
    Unified function for team, group, and PI AI cards.
    Uses parameterized queries to prevent SQL injection.
    Returns all fields including source_job_id.
    
    Args:
        card_id (int): The ID of the AI card to retrieve
        conn (Connection): Database connection from FastAPI dependency
        
    Returns:
        dict: AI card dictionary (includes source_job_id) or None if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT * 
            FROM {config.AI_INSIGHTS_TABLE} 
            WHERE id = :id
        """)
        
        logger.info(f"Executing query to get AI card with ID {card_id} from ai_summary")
        
        result = conn.execute(query, {"id": card_id})
        row = result.fetchone()
        
        if not row:
            return None
        
        # Convert row to dictionary - get all fields from database
        card = dict(row._mapping)
        add_priority_color_to_card(card)
        return card
        
    except Exception as e:
        logger.error(f"Error fetching AI card {card_id}: {e}")
        raise e




def replace_prompt_placeholders(prompt_text: str, conn: Optional[Connection] = None) -> str:
    """
    Replace placeholders in prompt text with values from database.
    
    Currently replaces:
    - {{JIRA_URL}} with the value from ETL settings
    
    Args:
        prompt_text: The prompt text that may contain placeholders
        conn: Optional database connection for lazy loading JIRA URL
    
    Returns:
        str: Prompt text with placeholders replaced
    """
    if not prompt_text:
        return prompt_text
    
    # Get JIRA URL (will retry from DB if null and conn provided)
    from config import get_jira_url
    jira_settings = get_jira_url(conn=conn)
    jira_url = jira_settings.get("url") or ""
    prompt_text = prompt_text.replace("{{JIRA_URL}}", jira_url)
    
    return prompt_text


def get_prompt_by_email_and_name(
    email_address: str,
    prompt_name: str,
    conn: Connection = None,
    active: Optional[bool] = None,
    replace_placeholders: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Get a single prompt by email_address and prompt_name from the prompts table.
    Optionally filter by prompt_active.

    Args:
        email_address: Owner email (e.g., 'admin')
        prompt_name: Prompt name (chat type string)
        conn: Database connection
        active: If True, require prompt_active=TRUE; if False, require prompt_active=FALSE; if None, no filter
        replace_placeholders: If True, replace {{JIRA_URL}} with value from ETL settings (default: False)

    Returns:
        dict: Prompt row as dictionary or None if not found
    """
    try:
        where_clauses = ["email_address = :email_address", "prompt_name = :prompt_name"]
        params: Dict[str, Any] = {
            "email_address": email_address,
            "prompt_name": prompt_name
        }

        if active is True:
            where_clauses.append("prompt_active = TRUE")
        elif active is False:
            where_clauses.append("prompt_active = FALSE")

        query = text(f"""
            SELECT 
                email_address,
                prompt_name,
                prompt_description,
                prompt_type,
                prompt_active,
                created_at,
                updated_at
            FROM {config.PROMPTS_TABLE}
            WHERE {' AND '.join(where_clauses)}
        """)

        logger.info(
            f"Executing query to get prompt '{prompt_name}' for '{email_address}' from {config.PROMPTS_TABLE} (active={active})"
        )

        result = conn.execute(query, params)
        row = result.fetchone()
        if not row:
            return None

        # Replace placeholders in prompt_description only if requested
        prompt_description = row[2]
        if prompt_description and replace_placeholders:
            # Pass conn for lazy loading if needed
            prompt_description = replace_prompt_placeholders(str(prompt_description), conn=conn)
        elif prompt_description:
            prompt_description = str(prompt_description)

        return {
            "email_address": row[0],
            "prompt_name": row[1],
            "prompt_description": prompt_description,
            "prompt_type": row[3],
            "prompt_active": row[4],
            "created_at": row[5],
            "updated_at": row[6]
        }
    except Exception as e:
        logger.error(f"Error fetching prompt '{prompt_name}' for '{email_address}': {e}")
        raise e


def get_recommendation_by_id(recommendation_id: int, conn: Connection = None) -> Optional[Dict[str, Any]]:
    """
    Get a single recommendation by ID from recommendations table.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        recommendation_id (int): The ID of the recommendation to retrieve
        conn (Connection): Database connection from FastAPI dependency
        
    Returns:
        dict: Recommendation dictionary or None if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT * 
            FROM {config.RECOMMENDATIONS_TABLE} 
            WHERE id = :id
        """)
        
        logger.info(f"Executing query to get recommendation with ID {recommendation_id} from {config.RECOMMENDATIONS_TABLE}")
        
        result = conn.execute(query, {"id": recommendation_id})
        row = result.fetchone()
        
        if not row:
            return None
        
        # Convert row to dictionary - get all fields from database
        return dict(row._mapping)
        
    except Exception as e:
        logger.error(f"Error fetching recommendation {recommendation_id}: {e}")
        raise e




def _format_job_data_for_llm(row_dict: Dict[str, Any], entity_type: str, entity_id: int, job_id: int) -> str:
    """
    Shared helper function to format job data for LLM context.
    
    Args:
        row_dict: Dictionary of row data from database
        entity_type: Type of entity ("card" or "recommendation")
        entity_id: ID of the entity (card_id or recommendation_id)
        job_id: Job ID
    
    Returns:
        Formatted string with all non-NULL fields
    """
    # Format all non-NULL fields, excluding job_id
    formatted_parts = []
    for field_name, field_value in row_dict.items():
        # Skip job_id field (not needed in output)
        if field_name.lower() in ['job_id', 'jobid']:
            continue
        
        if field_value is not None:
            # TEMPORARY FIX: Remove prompt from input_sent
            # This is a temporary fix to remove the prompt from the input_sent.
            # The correct solution should be that we keep the original input that was sent to the LLM
            # and the result from the LLM without a prompt.
            if field_name.lower() == 'input_sent':
                field_value_str = str(field_value)
                # Search for "-- Prompt --" and remove everything after it
                prompt_marker = "-- Prompt --"
                if prompt_marker in field_value_str:
                    field_value = field_value_str.split(prompt_marker)[0].rstrip()
                    logger.info(f"Removed prompt from input_sent (temporary fix)")
            
            # Convert field name to readable format (replace underscores with spaces, capitalize)
            readable_field_name = str(field_name).replace('_', ' ').title()
            
            # For recommendation_id and team_name, format on same line
            # For other fields, keep the original format (new line)
            if field_name.lower() in ['recommendation_id', 'recommendationid', 'ai_card_id', 'aicardid', 'team_name', 'teamname']:
                # Format: "Field Name: value"
                formatted_parts.append(f"{readable_field_name}: {field_value}")
            else:
                # Format: "Field Name = \nvalue\n"
                formatted_parts.append(f"{readable_field_name} = \n{field_value}\n")
    
    if not formatted_parts:
        logger.info(f"All fields are NULL for {entity_type}_id={entity_id}, job_id={job_id}")
        return f"No previous chat discussion was found in the previous job ({job_id})"
    
    formatted_data = "\n".join(formatted_parts)
    logger.info(f"Formatted job data (length: {len(formatted_data)} chars, {len(formatted_parts)} fields)")
    return formatted_data


def get_formatted_job_data_for_llm_followup_insight(card_id: int, job_id: Optional[int], conn: Connection = None) -> Optional[str]:
    """
    Get formatted job data from ai_summary table for LLM context (for insights/cards).
    Formats all fields as "Field Name = \nvalue\n" and skips NULL fields.
    
    Args:
        card_id (int): The ID of the card
        job_id (Optional[int]): The source_job_id from the card (can be None)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        str: Formatted string with all non-NULL fields, or None if no data found.
             Returns message string if job_id is None or no data found.
    """
    try:
        # If no job_id, return message indicating no previous job discussion
        if job_id is None:
            return f"No previous chat discussion was found in the previous job (no job ID)"
        
        # Direct SELECT from agent_jobs table - only get input_sent (not full_information or other fields)
        query = text("""
            SELECT aj.input_sent
            FROM ai_summary ai
            JOIN agent_jobs aj ON ai.source_job_id = aj.job_id
            WHERE ai.id = :card_id AND aj.job_id = :job_id
        """)
        
        logger.info(f"Executing query to get input_sent for LLM followup (card_id={card_id}, job_id={job_id})")
        
        result = conn.execute(query, {"card_id": card_id, "job_id": job_id})
        row = result.fetchone()
        
        if not row:
            logger.info(f"No data found for card_id={card_id}, job_id={job_id}")
            return f"No previous chat discussion was found in the previous job ({job_id})"
        
        input_sent = row[0] if row[0] is not None else None
        
        if input_sent is None:
            return f"No previous chat discussion was found in the previous job ({job_id})"
        
        # TEMPORARY FIX: Remove prompt from input_sent
        # This is a temporary fix to remove the prompt from the input_sent.
        # The correct solution should be that we keep the original input that was sent to the LLM
        # and the result from the LLM without a prompt.
        input_sent_str = str(input_sent)
        prompt_marker = "===> Prompt:"
        if prompt_marker in input_sent_str:
            input_sent_str = input_sent_str.split(prompt_marker)[0].rstrip()
            logger.info(f"Removed prompt from input_sent (temporary fix)")
        
        return input_sent_str
        
    except Exception as e:
        logger.error(f"Error fetching formatted job data for LLM followup (card_id={card_id}, job_id={job_id}): {e}")
        # Return message instead of raising error
        return f"No previous chat discussion was found in the previous job ({job_id if job_id else 'unknown'})"


def get_formatted_job_data_for_llm_followup_recommendation(recommendation_id: int, job_id: Optional[int], conn: Connection = None) -> Optional[str]:
    """
    Get formatted job data for LLM context from recommendations table.
    Similar to get_formatted_job_data_for_llm_followup_insight but queries recommendations instead of ai_summary.
    
    Args:
        recommendation_id (int): The ID of the recommendation
        job_id (Optional[int]): The source_job_id from the recommendation (can be None)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        str: Formatted string with all non-NULL fields, or None if no data found.
             Returns message string if job_id is None or no data found.
    """
    try:
        # If no job_id, return message indicating no previous job discussion
        if job_id is None:
            return f"No previous chat discussion was found in the previous job (no job ID)"
        
        # Direct SELECT from agent_jobs table - only get input_sent (not full_information or other fields)
        query = text("""
            SELECT aj.input_sent
            FROM recommendations rec
            JOIN agent_jobs aj ON rec.source_job_id = aj.job_id
            WHERE rec.id = :recommendation_id
        """)
        
        logger.info(f"Executing query to get input_sent for LLM followup (recommendation_id={recommendation_id}, job_id={job_id})")
        
        result = conn.execute(query, {"recommendation_id": recommendation_id})
        row = result.fetchone()
        
        if not row:
            # Check if recommendation exists but has no source_job_id or no matching agent_job
            check_rec = text("SELECT id, source_job_id FROM recommendations WHERE id = :recommendation_id")
            rec_check = conn.execute(check_rec, {"recommendation_id": recommendation_id})
            rec_row = rec_check.fetchone()
            if rec_row:
                rec_data = dict(rec_row._mapping)
                logger.warning(f"Recommendation {recommendation_id} exists but join failed. source_job_id={rec_data.get('source_job_id')}, expected job_id={job_id}")
                if rec_data.get('source_job_id') is None:
                    return f"No previous chat discussion was found (recommendation has no source_job_id)"
                elif rec_data.get('source_job_id') != job_id:
                    logger.warning(f"source_job_id mismatch: rec.source_job_id={rec_data.get('source_job_id')} != job_id={job_id}")
            logger.info(f"No data found for recommendation_id={recommendation_id}, job_id={job_id}")
            return f"No previous chat discussion was found in the previous job ({job_id})"
        
        input_sent = row[0] if row[0] is not None else None
        
        if input_sent is None:
            return f"No previous chat discussion was found in the previous job ({job_id})"
        
        # TEMPORARY FIX: Remove prompt from input_sent
        # This is a temporary fix to remove the prompt from the input_sent.
        # The correct solution should be that we keep the original input that was sent to the LLM
        # and the result from the LLM without a prompt.
        input_sent_str = str(input_sent)
        prompt_marker = "===> Prompt:"
        if prompt_marker in input_sent_str:
            input_sent_str = input_sent_str.split(prompt_marker)[0].rstrip()
            logger.info(f"Removed prompt from input_sent (temporary fix)")
        
        return input_sent_str
            
    except Exception as e:
        logger.error(f"Error fetching formatted job data for LLM followup (recommendation_id={recommendation_id}, job_id={job_id}): {e}")
        # Return message instead of raising error
        return f"No previous chat discussion was found in the previous job ({job_id if job_id else 'unknown'})"


# -------------------------------------------------------------
# Shared CRUD helpers for ai_summary (used by Team/PI AI Cards)
# -------------------------------------------------------------
def create_ai_card(data: Dict[str, Any], conn: Connection = None) -> Dict[str, Any]:
    """
    Insert a new AI card row into ai_summary and return the created row.

    Only allowed columns are inserted; others are ignored if provided.
    """
    try:
        allowed_columns = {
            "date", "team_name", "group_name", "card_name", "insight_type", "priority", "source",
            "source_job_id", "description", "full_information", "information_json", "pi"
        }

        # Filter to only allowed columns, but keep None values (they represent NULL in database)
        filtered = {k: v for k, v in data.items() if k in allowed_columns}
        if not filtered:
            raise ValueError("No valid fields provided for ai_summary insert")
        
        # Convert empty strings to None for nullable columns
        nullable_columns = {"team_name", "group_name", "pi"}
        for col in nullable_columns:
            if col in filtered and filtered[col] == "":
                filtered[col] = None

        columns_sql = ", ".join(filtered.keys())
        values_sql = ", ".join([f":{k}" for k in filtered.keys()])

        query = text(f"""
            INSERT INTO {config.AI_SUMMARY_TABLE} ({columns_sql})
            VALUES ({values_sql})
            RETURNING *
        """)

        result = conn.execute(query, filtered)
        row = result.fetchone()
        conn.commit()
        return dict(row._mapping)
    except Exception as e:
        logger.error(f"Error creating ai card: {e}")
        conn.rollback()
        raise e


def update_ai_card_by_id(card_id: int, updates: Dict[str, Any], conn: Connection = None) -> Optional[Dict[str, Any]]:
    """
    Update an existing AI card row by id and return the updated row, or None if not found.
    """
    try:
        allowed_columns = {
            "date", "team_name", "group_name", "card_name", "insight_type", "priority", "source",
            "source_job_id", "description", "full_information", "information_json", "pi"
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_columns}
        if not filtered:
            raise ValueError("No valid fields provided for ai_summary update")
        
        # Convert empty strings to None for nullable columns
        nullable_columns = {"team_name", "group_name", "pi"}
        for col in nullable_columns:
            if col in filtered and filtered[col] == "":
                filtered[col] = None

        set_clauses = ", ".join([f"{k} = :{k}" for k in filtered.keys()])
        params = dict(filtered)
        params["id"] = card_id

        query = text(f"""
            UPDATE {config.AI_SUMMARY_TABLE}
            SET {set_clauses}, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            RETURNING *
        """)

        result = conn.execute(query, params)
        row = result.fetchone()
        conn.commit()
        if not row:
            return None
        return dict(row._mapping)
    except Exception as e:
        logger.error(f"Error updating ai card {card_id}: {e}")
        conn.rollback()
        raise e


def delete_ai_card_by_id(card_id: int, conn: Connection = None) -> bool:
    """Hard delete an AI card by id. Returns True if deleted, False if not found."""
    try:
        query = text(f"DELETE FROM {config.AI_SUMMARY_TABLE} WHERE id = :id")
        result = conn.execute(query, {"id": card_id})
        conn.commit()
        return result.rowcount > 0
    except Exception as e:
        logger.error(f"Error deleting ai card {card_id}: {e}")
        conn.rollback()
        raise e


# -------------------------------------------------------------
# CRUD helpers for recommendations
# -------------------------------------------------------------
def create_recommendation(data: Dict[str, Any], conn: Connection = None) -> Dict[str, Any]:
    """
    Insert a new recommendation and return the created row.
    """
    try:
        allowed_columns = {
            "team_name", "date", "action_text", "rational", "full_information",
            "priority", "status", "information_json", "source_job_id", "source_ai_summary_id"
        }
        filtered = {k: v for k, v in data.items() if k in allowed_columns}
        if not filtered:
            raise ValueError("No valid fields provided for recommendations insert")

        columns_sql = ", ".join(filtered.keys())
        values_sql = ", ".join([f":{k}" for k in filtered.keys()])
        
        # Build UPDATE clause for ON CONFLICT - update all fields except created_at
        update_fields = [k for k in filtered.keys() if k != "created_at"]
        set_clauses = ", ".join([f"{k} = EXCLUDED.{k}" for k in update_fields])
        set_clauses += ", updated_at = CURRENT_TIMESTAMP"

        query = text(f"""
            INSERT INTO {config.RECOMMENDATIONS_TABLE} ({columns_sql})
            VALUES ({values_sql})
            ON CONFLICT (date, team_name, source_ai_summary_id)
            DO UPDATE SET {set_clauses}
            RETURNING *
        """)

        result = conn.execute(query, filtered)
        row = result.fetchone()
        conn.commit()
        return dict(row._mapping)
    except Exception as e:
        logger.error(f"Error creating recommendation: {e}")
        conn.rollback()
        raise e


def update_recommendation_by_id(recommendation_id: int, updates: Dict[str, Any], conn: Connection = None) -> Optional[Dict[str, Any]]:
    """
    Update an existing recommendation by id and return the updated row, or None if not found.
    """
    try:
        allowed_columns = {
            "team_name", "date", "action_text", "rational", "full_information",
            "priority", "status", "information_json", "source_job_id", "source_ai_summary_id"
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_columns}
        if not filtered:
            raise ValueError("No valid fields provided for recommendations update")

        set_clauses = ", ".join([f"{k} = :{k}" for k in filtered.keys()])
        params = dict(filtered)
        params["id"] = recommendation_id

        query = text(f"""
            UPDATE {config.RECOMMENDATIONS_TABLE}
            SET {set_clauses}, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            RETURNING *
        """)

        result = conn.execute(query, params)
        row = result.fetchone()
        conn.commit()
        if not row:
            return None
        return dict(row._mapping)
    except Exception as e:
        logger.error(f"Error updating recommendation {recommendation_id}: {e}")
        conn.rollback()
        raise e


def delete_recommendation_by_id(recommendation_id: int, conn: Connection = None) -> bool:
    """Hard delete a recommendation by id. Returns True if deleted, False if not found."""
    try:
        query = text(f"DELETE FROM {config.RECOMMENDATIONS_TABLE} WHERE id = :id")
        result = conn.execute(query, {"id": recommendation_id})
        conn.commit()
        return result.rowcount > 0
    except Exception as e:
        logger.error(f"Error deleting recommendation {recommendation_id}: {e}")
        conn.rollback()
        raise e

def get_all_settings_db(conn: Connection = None) -> Dict[str, str]:
    """
    Get all global settings from the database.
    
    Returns dictionary of setting_key: setting_value pairs.
    Includes full API keys as stored in database.
    
    Args:
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        dict: Dictionary of setting_key: setting_value pairs
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT setting_key, setting_value 
            FROM global_settings
        """
        
        logger.info(f"Executing query to get all global settings")
        
        result = conn.execute(text(sql_query))
        
        # Convert rows to dictionary of key-value pairs
        settings = {row[0]: row[1] for row in result}
        
        return settings
            
    except Exception as e:
        logger.error(f"Error fetching all global settings: {e}")
        raise e


def set_setting_db(
    setting_key: str,
    setting_value: str,
    updated_by: str = 'admin',
    conn: Connection = None
) -> bool:
    """
    Set a single global setting in the database using UPSERT.
    
    Args:
        setting_key (str): The setting key to set
        setting_value (str): The value to set
        updated_by (str): Email of user making the change (default: 'admin')
        conn (Connection): Database connection from FastAPI dependency
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        # Use UPSERT (INSERT ... ON CONFLICT UPDATE)
        # setting_type defaults to 'string' for new inserts, preserved on updates
        upsert_sql = """
        INSERT INTO public.global_settings 
        (setting_key, setting_value, setting_type, updated_at)
        VALUES (:key, :value, 'string', CURRENT_TIMESTAMP)
        ON CONFLICT (setting_key) 
        DO UPDATE SET 
            setting_value = EXCLUDED.setting_value,
            updated_at = CURRENT_TIMESTAMP
        """
        
        logger.info(f"Setting global setting '{setting_key}' by {updated_by}")
        
        result = conn.execute(text(upsert_sql), {
            'key': setting_key,
            'value': setting_value
        })
        conn.commit()
        
        logger.info(f"Successfully set global setting '{setting_key}'")
        return True
        
    except Exception as e:
        logger.error(f"Error setting global setting '{setting_key}': {e}")
        conn.rollback()
        raise e


def get_all_llm_settings_db(conn: Connection = None) -> Dict[str, str]:
    """
    Get all LLM settings from the database.
    
    Returns dictionary of setting_key: setting_value pairs.
    Includes full API keys as stored in database.
    
    Args:
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        dict: Dictionary of setting_key: setting_value pairs
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT setting_key, setting_value 
            FROM llm_settings
        """
        
        logger.info(f"Executing query to get all LLM settings")
        
        result = conn.execute(text(sql_query))
        
        # Convert rows to dictionary of key-value pairs
        settings = {row[0]: row[1] for row in result}
        
        return settings
            
    except Exception as e:
        logger.error(f"Error fetching all LLM settings: {e}")
        raise e


def set_llm_setting_db(
    setting_key: str,
    setting_value: str,
    updated_by: str = 'admin',
    conn: Connection = None
) -> bool:
    """
    Set a single LLM setting in the database using UPSERT.
    
    Args:
        setting_key (str): The setting key to set
        setting_value (str): The value to set
        updated_by (str): Email of user making the change (default: 'admin')
        conn (Connection): Database connection from FastAPI dependency
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        # Use UPSERT (INSERT ... ON CONFLICT UPDATE)
        upsert_sql = """
        INSERT INTO public.llm_settings 
        (setting_key, setting_value, updated_at, updated_by)
        VALUES (:key, :value, CURRENT_TIMESTAMP, :updated_by)
        ON CONFLICT (setting_key) 
        DO UPDATE SET 
            setting_value = EXCLUDED.setting_value,
            updated_at = CURRENT_TIMESTAMP,
            updated_by = EXCLUDED.updated_by
        """
        
        logger.info(f"Setting LLM setting '{setting_key}' by {updated_by}")
        
        result = conn.execute(text(upsert_sql), {
            'key': setting_key,
            'value': setting_value,
            'updated_by': updated_by
        })
        conn.commit()
        
        logger.info(f"Successfully set LLM setting '{setting_key}'")
        return True
        
    except Exception as e:
        logger.error(f"Error setting LLM setting '{setting_key}': {e}")
        conn.rollback()
        raise e


def set_llm_settings_batch_db(
    settings: Dict[str, str],
    updated_by: str = 'admin',
    conn: Connection = None
) -> Dict[str, bool]:
    """
    Set multiple LLM settings in a batch using UPSERT.
    
    Args:
        settings (Dict[str, str]): Dictionary of setting_key: setting_value pairs
        updated_by (str): Email of user making the change (default: 'admin')
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        Dict[str, bool]: Dictionary of setting_key: success_status pairs
    """
    results = {}
    
    try:
        # SECURE: Parameterized query prevents SQL injection
        upsert_sql = """
        INSERT INTO public.llm_settings 
        (setting_key, setting_value, updated_at, updated_by)
        VALUES (:key, :value, CURRENT_TIMESTAMP, :updated_by)
        ON CONFLICT (setting_key) 
        DO UPDATE SET 
            setting_value = EXCLUDED.setting_value,
            updated_at = CURRENT_TIMESTAMP,
            updated_by = EXCLUDED.updated_by
        """
        
        logger.info(f"Setting {len(settings)} LLM settings by {updated_by}")
        
        for setting_key, setting_value in settings.items():
            try:
                conn.execute(text(upsert_sql), {
                    'key': setting_key,
                    'value': setting_value,
                    'updated_by': updated_by
                })
                results[setting_key] = True
                logger.debug(f"Successfully set LLM setting '{setting_key}'")
            except Exception as e:
                logger.error(f"Error setting LLM setting '{setting_key}': {e}")
                results[setting_key] = False
        
        conn.commit()
        logger.info(f"Batch update completed: {sum(results.values())}/{len(settings)} successful")
        return results
        
    except Exception as e:
        logger.error(f"Error in batch LLM settings update: {e}")
        conn.rollback()
        raise e


def set_settings_batch_db(
    settings: Dict[str, str],
    updated_by: str = 'admin',
    conn: Connection = None
) -> Dict[str, bool]:
    """
    Set multiple global settings in a batch using UPSERT.
    
    Args:
        settings (Dict[str, str]): Dictionary of setting_key: setting_value pairs
        updated_by (str): Email of user making the change (default: 'admin')
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        Dict[str, bool]: Dictionary of setting_key: success_status pairs
    """
    results = {}
    
    try:
        # SECURE: Parameterized query prevents SQL injection
        # setting_type defaults to 'string' for new inserts, preserved on updates
        upsert_sql = """
        INSERT INTO public.global_settings 
        (setting_key, setting_value, setting_type, updated_at)
        VALUES (:key, :value, 'string', CURRENT_TIMESTAMP)
        ON CONFLICT (setting_key) 
        DO UPDATE SET 
            setting_value = EXCLUDED.setting_value,
            updated_at = CURRENT_TIMESTAMP
        """
        
        logger.info(f"Setting {len(settings)} global settings by {updated_by}")
        
        for setting_key, setting_value in settings.items():
            try:
                conn.execute(text(upsert_sql), {
                    'key': setting_key,
                    'value': setting_value
                })
                results[setting_key] = True
                logger.debug(f"Successfully set global setting '{setting_key}'")
            except Exception as e:
                logger.error(f"Error setting global setting '{setting_key}': {e}")
                results[setting_key] = False
        
        conn.commit()
        logger.info(f"Batch update completed: {sum(results.values())}/{len(settings)} successful")
        return results
        
    except Exception as e:
        logger.error(f"Error in batch settings update: {e}")
        conn.rollback()
        raise e


# -------------------------------------------------------------
# ETL Settings helpers
# -------------------------------------------------------------
def get_etl_setting_from_db(conn: Connection, setting_key: str, default_value: Optional[str] = None) -> Optional[str]:
    """
    Get a single ETL setting value from the database.
    
    Args:
        conn: Database connection
        setting_key: Setting key to retrieve
        default_value: Default value if not found
        
    Returns:
        Setting value or default_value
    """
    try:
        query = text("SELECT setting_value FROM etl_settings WHERE setting_key = :key")
        result = conn.execute(query, {"key": setting_key})
        row = result.fetchone()
        
        if row and row[0] is not None:
            return row[0]
        return default_value
    except Exception as e:
        logger.error(f"Error fetching ETL setting '{setting_key}': {e}")
        return default_value


def get_all_etl_settings_from_db(conn: Connection) -> Dict[str, str]:
    """
    Get all ETL settings from the database.
    
    Args:
        conn: Database connection
        
    Returns:
        Dictionary of setting_key: setting_value pairs
    """
    try:
        query = text("SELECT setting_key, setting_value FROM etl_settings ORDER BY setting_key")
        result = conn.execute(query)
        
        # Convert rows to dictionary of key-value pairs
        settings = {row[0]: row[1] for row in result}
        
        return settings
    except Exception as e:
        logger.error(f"Error fetching all ETL settings: {e}")
        raise e


# -------------------------------------------------------------
# CRUD helpers for insight_types
# -------------------------------------------------------------
def get_insight_type_by_id(insight_type_id: int, conn: Connection = None) -> Optional[Dict[str, Any]]:
    """
    Get a single insight type by ID from insight_types table.
    Uses parameterized queries to prevent SQL injection.
    
    Args:
        insight_type_id (int): The ID of the insight type to retrieve
        conn (Connection): Database connection from FastAPI dependency
        
    Returns:
        dict: Insight type dictionary or None if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT * 
            FROM {config.INSIGHT_TYPES_TABLE} 
            WHERE id = :id
        """)
        
        logger.info(f"Executing query to get insight type with ID {insight_type_id} from {config.INSIGHT_TYPES_TABLE}")
        
        result = conn.execute(query, {"id": insight_type_id})
        row = result.fetchone()
        
        if not row:
            return None
        
        # Convert row to dictionary - get all fields from database
        row_dict = dict(row._mapping)
        # Convert JSONB insight_categories to Python list
        import json
        if 'insight_categories' in row_dict:
            if isinstance(row_dict['insight_categories'], str):
                try:
                    row_dict['insight_categories'] = json.loads(row_dict['insight_categories'])
                except:
                    row_dict['insight_categories'] = []
            elif hasattr(row_dict['insight_categories'], '__iter__') and not isinstance(row_dict['insight_categories'], str):
                row_dict['insight_categories'] = list(row_dict['insight_categories'])
        return row_dict
        
    except Exception as e:
        logger.error(f"Error fetching insight type {insight_type_id}: {e}")
        raise e


def get_insight_types(
    insight_type: Optional[str] = None,
    insight_category: Optional[str] = None,
    active: Optional[bool] = None,
    search: Optional[str] = None,
    limit: int = 100,
    conn: Connection = None
) -> List[Dict[str, Any]]:
    """
    Get collection of insight types with optional filtering.
    
    Args:
        insight_type (Optional[str]): Filter by insight type (exact match)
        insight_category (Optional[str]): Filter by insight category (exact match - checks if category exists in JSONB array)
        active (Optional[bool]): Filter by active status
        search (Optional[str]): Search term for insight_type (ILIKE search)
        limit (int): Maximum number of results
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of insight type dictionaries
    """
    try:
        import json
        # Build WHERE clause dynamically based on filters
        where_conditions = []
        params = {}
        
        if insight_type:
            where_conditions.append("insight_type = :insight_type")
            params["insight_type"] = insight_type
        
        if insight_category:
            # Filter by category in JSONB array using @> operator
            where_conditions.append("insight_categories @> CAST(:insight_category AS jsonb)")
            params["insight_category"] = json.dumps([insight_category])
        
        if active is not None:
            where_conditions.append("active = :active")
            params["active"] = active
        
        if search:
            where_conditions.append("insight_type ILIKE :search")
            params["search"] = f"%{search}%"
        
        where_clause = ""
        if where_conditions:
            where_clause = "WHERE " + " AND ".join(where_conditions)
        
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT *
            FROM {config.INSIGHT_TYPES_TABLE}
            {where_clause}
            ORDER BY updated_at DESC
            LIMIT :limit
        """)
        
        params["limit"] = limit
        
        logger.info(f"Executing query to get insight types from {config.INSIGHT_TYPES_TABLE}")
        
        result = conn.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to list of dictionaries
        # Parse JSONB arrays to Python lists
        import json
        insight_types = []
        for row in rows:
            row_dict = dict(row._mapping)
            # Convert JSONB insight_categories to Python list
            if 'insight_categories' in row_dict:
                if isinstance(row_dict['insight_categories'], str):
                    try:
                        row_dict['insight_categories'] = json.loads(row_dict['insight_categories'])
                    except:
                        row_dict['insight_categories'] = []
                elif hasattr(row_dict['insight_categories'], '__iter__') and not isinstance(row_dict['insight_categories'], str):
                    # Already a list/array
                    row_dict['insight_categories'] = list(row_dict['insight_categories'])
            insight_types.append(row_dict)
        
        return insight_types
            
    except Exception as e:
        logger.error(f"Error fetching insight types: {e}")
        raise e


def create_insight_type(data: Dict[str, Any], conn: Connection = None) -> Dict[str, Any]:
    """
    Insert a new insight type and return the created row.
    
    Args:
        data (Dict[str, Any]): Dictionary containing insight type data
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        dict: Created insight type dictionary
    """
    try:
        import json
        allowed_columns = {
            "insight_type", "insight_description", "insight_categories", "active", "pi_insight", "team_insight", "group_insight", "sprint_insight", "cron_config"
        }
        
        filtered = {k: v for k, v in data.items() if k in allowed_columns}
        if not filtered:
            raise ValueError("No valid fields provided for insight_types insert")
        
        # Convert insight_categories list to JSON string for JSONB
        if "insight_categories" in filtered and isinstance(filtered["insight_categories"], list):
            filtered["insight_categories"] = json.dumps(filtered["insight_categories"])
        
        # Convert cron_config dict to JSON string for JSONB (or None)
        if "cron_config" in filtered:
            if filtered["cron_config"] is None:
                filtered["cron_config"] = None
            elif isinstance(filtered["cron_config"], dict):
                filtered["cron_config"] = json.dumps(filtered["cron_config"])
        
        columns_sql = ", ".join(filtered.keys())
        # Use CAST for JSONB column
        values_sql_parts = []
        for k in filtered.keys():
            if k == "insight_categories":
                values_sql_parts.append("CAST(:insight_categories AS jsonb)")
            elif k == "cron_config":
                values_sql_parts.append("CAST(:cron_config AS jsonb)")
            else:
                values_sql_parts.append(f":{k}")
        values_sql = ", ".join(values_sql_parts)
        
        query = text(f"""
            INSERT INTO {config.INSIGHT_TYPES_TABLE} ({columns_sql})
            VALUES ({values_sql})
            RETURNING *
        """)
        
        result = conn.execute(query, filtered)
        row = result.fetchone()
        conn.commit()
        
        # Convert result to dict and parse JSONB
        row_dict = dict(row._mapping)
        if 'insight_categories' in row_dict:
            if isinstance(row_dict['insight_categories'], str):
                try:
                    row_dict['insight_categories'] = json.loads(row_dict['insight_categories'])
                except:
                    row_dict['insight_categories'] = []
            elif hasattr(row_dict['insight_categories'], '__iter__') and not isinstance(row_dict['insight_categories'], str):
                row_dict['insight_categories'] = list(row_dict['insight_categories'])
        
        if 'cron_config' in row_dict and row_dict['cron_config'] is not None:
            if isinstance(row_dict['cron_config'], str):
                try:
                    row_dict['cron_config'] = json.loads(row_dict['cron_config'])
                except:
                    row_dict['cron_config'] = None
        
        return row_dict
    except Exception as e:
        logger.error(f"Error creating insight type: {e}")
        conn.rollback()
        raise e


def update_insight_type_by_id(insight_type_id: int, updates: Dict[str, Any], conn: Connection = None) -> Optional[Dict[str, Any]]:
    """
    Update an existing insight type by id and return the updated row, or None if not found.
    
    Args:
        insight_type_id (int): The ID of the insight type to update
        updates (Dict[str, Any]): Dictionary containing fields to update
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        dict: Updated insight type dictionary or None if not found
    """
    try:
        import json
        allowed_columns = {
            "insight_type", "insight_description", "insight_categories", "active", "pi_insight", "team_insight", "group_insight", "sprint_insight", "cron_config"
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_columns}
        if not filtered:
            raise ValueError("No valid fields provided for insight_types update")
        
        # Convert insight_categories list to JSON string for JSONB
        if "insight_categories" in filtered and isinstance(filtered["insight_categories"], list):
            filtered["insight_categories"] = json.dumps(filtered["insight_categories"])
        
        # Convert cron_config dict to JSON string for JSONB (or None)
        if "cron_config" in filtered:
            if filtered["cron_config"] is None:
                filtered["cron_config"] = None
            elif isinstance(filtered["cron_config"], dict):
                filtered["cron_config"] = json.dumps(filtered["cron_config"])
        
        # Build SET clause with CAST for JSONB
        set_clauses_parts = []
        for k in filtered.keys():
            if k == "insight_categories":
                set_clauses_parts.append("insight_categories = CAST(:insight_categories AS jsonb)")
            elif k == "cron_config":
                set_clauses_parts.append("cron_config = CAST(:cron_config AS jsonb)")
            else:
                set_clauses_parts.append(f"{k} = :{k}")
        set_clauses = ", ".join(set_clauses_parts)
        
        params = dict(filtered)
        params["id"] = insight_type_id
        
        query = text(f"""
            UPDATE {config.INSIGHT_TYPES_TABLE}
            SET {set_clauses}, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            RETURNING *
        """)
        
        result = conn.execute(query, params)
        row = result.fetchone()
        conn.commit()
        if not row:
            return None
        
        # Convert result to dict and parse JSONB
        row_dict = dict(row._mapping)
        if 'insight_categories' in row_dict:
            if isinstance(row_dict['insight_categories'], str):
                try:
                    row_dict['insight_categories'] = json.loads(row_dict['insight_categories'])
                except:
                    row_dict['insight_categories'] = []
            elif hasattr(row_dict['insight_categories'], '__iter__') and not isinstance(row_dict['insight_categories'], str):
                row_dict['insight_categories'] = list(row_dict['insight_categories'])
        
        if 'cron_config' in row_dict and row_dict['cron_config'] is not None:
            if isinstance(row_dict['cron_config'], str):
                try:
                    row_dict['cron_config'] = json.loads(row_dict['cron_config'])
                except:
                    row_dict['cron_config'] = None
        
        return row_dict
    except Exception as e:
        logger.error(f"Error updating insight type {insight_type_id}: {e}")
        conn.rollback()
        raise e


def delete_insight_type_by_id(insight_type_id: int, conn: Connection = None) -> bool:
    """
    Hard delete an insight type by id. Returns True if deleted, False if not found.
    
    Args:
        insight_type_id (int): The ID of the insight type to delete
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        bool: True if deleted, False if not found
    """
    try:
        query = text(f"DELETE FROM {config.INSIGHT_TYPES_TABLE} WHERE id = :id")
        result = conn.execute(query, {"id": insight_type_id})
        conn.commit()
        return result.rowcount > 0
    except Exception as e:
        logger.error(f"Error deleting insight type {insight_type_id}: {e}")
        conn.rollback()
        raise e


