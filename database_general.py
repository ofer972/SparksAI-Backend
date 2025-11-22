"""
Database General - Database access functions for general services.

This module contains database access functions for recommendations and team AI cards.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any, Optional
import logging
import config

logger = logging.getLogger(__name__)


def get_top_ai_recommendations(team_name: str, limit: int = 4, source_ai_summary_id: Optional[int] = None, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get top AI recommendations for a specific team.
    
    Returns recommendations ordered by:
    1. Date (newest first)
    2. Priority (Critical > High > Important)
    3. ID (descending)
    
    Args:
        team_name (str): Team name
        limit (int): Number of recommendations to return (default: 4)
        source_ai_summary_id (Optional[int]): Optional filter by source AI summary ID
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of recommendation dictionaries
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            SELECT *
            FROM public.recommendations
            WHERE team_name = :team_name
              AND (:source_ai_summary_id IS NULL OR source_ai_summary_id = :source_ai_summary_id)
            ORDER BY 
                DATE(date) DESC,
                CASE priority 
                    WHEN 'Critical' THEN 1
                    WHEN 'High' THEN 2
                    WHEN 'Important' THEN 3
                    ELSE 4
                END,
                id DESC
            LIMIT :limit
        """
        
        logger.info(f"Executing query to get top AI recommendations for team: {team_name}")
        logger.info(f"Parameters: team_name={team_name}, limit={limit}, source_ai_summary_id={source_ai_summary_id}")
        
        result = conn.execute(text(sql_query), {
            'team_name': team_name, 
            'limit': limit,
            'source_ai_summary_id': source_ai_summary_id
        })
        
        # Convert rows to list of dictionaries
        recommendations = []
        for row in result:
            # Convert row to dictionary dynamically since we're using SELECT *
            recommendations.append(dict(row._mapping))
        
        return recommendations
            
    except Exception as e:
        logger.error(f"Error fetching top AI recommendations for team {team_name}: {e}")
        raise e


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
        filter_column (str): Column to filter by (must be 'team_name' or 'pi' for security)
        filter_value (str): Value to filter by
        limit (int): Number of AI cards to return (default: 4)
        categories (Optional[List[str]]): Optional category filter - only return cards with card_type matching insight types for any of these categories
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of AI card dictionaries with all columns
    """
    # Security: Only allow specific columns to prevent SQL injection
    allowed_columns = {'team_name', 'pi'}
    if filter_column not in allowed_columns:
        raise ValueError(f"filter_column must be one of {allowed_columns}, got: {filter_column}")
    
    try:
        import json
        
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
            # Build IN clause for card_type filtering
            # Use parameterized query with array
            sql_query = f"""
                WITH ranked_cards AS (
                    SELECT *,
                        ROW_NUMBER() OVER (
                            PARTITION BY card_type 
                            ORDER BY 
                                CASE priority 
                                    WHEN 'Critical' THEN 1 
                                    WHEN 'High' THEN 2 
                                    WHEN 'Important' THEN 3 
                                    ELSE 4 
                                END,
                                created_at DESC
                        ) as rn
                    FROM public.ai_summary
                    WHERE {filter_column} = :filter_value
                      AND card_type = ANY(:card_types)
                )
                SELECT *
                FROM ranked_cards
                WHERE rn = 1
                ORDER BY 
                    CASE priority 
                        WHEN 'Critical' THEN 1 
                        WHEN 'High' THEN 2 
                        WHEN 'Important' THEN 3 
                        ELSE 4 
                    END,
                    created_at DESC
                LIMIT :limit
            """
            
            params = {
                'filter_value': filter_value,
                'card_types': insight_types_list,
                'limit': limit
            }
        else:
            # No category filter - original query
            sql_query = f"""
                WITH ranked_cards AS (
                    SELECT *,
                        ROW_NUMBER() OVER (
                            PARTITION BY card_type 
                            ORDER BY 
                                CASE priority 
                                    WHEN 'Critical' THEN 1 
                                    WHEN 'High' THEN 2 
                                    WHEN 'Important' THEN 3 
                                    ELSE 4 
                                END,
                                created_at DESC
                        ) as rn
                    FROM public.ai_summary
                    WHERE {filter_column} = :filter_value
                )
                SELECT *
                FROM ranked_cards
                WHERE rn = 1
                ORDER BY 
                    CASE priority 
                        WHEN 'Critical' THEN 1 
                        WHEN 'High' THEN 2 
                        WHEN 'Important' THEN 3 
                        ELSE 4 
                    END,
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
        
        # Convert rows to list of dictionaries
        ai_cards = []
        for row in result:
            ai_cards.append(dict(row._mapping))
        
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
                CASE priority 
                    WHEN 'Critical' THEN 1
                    WHEN 'High' THEN 2
                    WHEN 'Important' THEN 3
                    ELSE 4
                END,
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
        filter_column (str): Column to filter by ('team_name' or 'pi')
        filter_value (str): Value to filter by
        limit (int): Number of AI cards to return (default: 4)
        recommendations_limit (int): Maximum recommendations per card (default: 5)
        categories (Optional[List[str]]): Optional category filter - only return cards with card_type matching insight types for any of these categories
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of AI card dictionaries, each with 'recommendations' array and 'recommendations_count'
    """
    try:
        # Get top AI cards using existing function
        ai_cards = get_top_ai_cards_filtered(filter_column, filter_value, limit, categories=categories, conn=conn)
        
        # For each card, fetch and attach recommendations
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


def get_team_ai_card_by_id(card_id: int, conn: Connection = None) -> Optional[Dict[str, Any]]:
    """
    Get a single team AI summary card by ID from ai_summary table.
    Uses parameterized queries to prevent SQL injection.
    Returns all fields including source_job_id.
    
    Args:
        card_id (int): The ID of the team AI card to retrieve
        conn (Connection): Database connection from FastAPI dependency
        
    Returns:
        dict: Team AI card dictionary (includes source_job_id) or None if not found
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        query = text(f"""
            SELECT * 
            FROM {config.TEAM_AI_CARDS_TABLE} 
            WHERE id = :id
        """)
        
        logger.info(f"Executing query to get team AI card with ID {card_id} from {config.TEAM_AI_CARDS_TABLE}")
        
        result = conn.execute(query, {"id": card_id})
        row = result.fetchone()
        
        if not row:
            return None
        
        # Convert row to dictionary - get all fields from database
        return dict(row._mapping)
        
    except Exception as e:
        logger.error(f"Error fetching team AI card {card_id}: {e}")
        raise e


def get_prompt_by_email_and_name(
    email_address: str,
    prompt_name: str,
    conn: Connection = None,
    active: Optional[bool] = None
) -> Optional[Dict[str, Any]]:
    """
    Get a single prompt by email_address and prompt_name from the prompts table.
    Optionally filter by prompt_active.

    Args:
        email_address: Owner email (e.g., 'admin')
        prompt_name: Prompt name (chat type string)
        conn: Database connection
        active: If True, require prompt_active=TRUE; if False, require prompt_active=FALSE; if None, no filter

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

        return {
            "email_address": row[0],
            "prompt_name": row[1],
            "prompt_description": row[2],
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


def get_pi_ai_card_by_id(card_id: int, conn: Connection = None) -> Optional[Dict[str, Any]]:
    """
    Get a single PI AI summary card by ID from ai_summary table.
    Uses parameterized queries to prevent SQL injection.
    Returns all fields including source_job_id.

    Args:
        card_id (int): The ID of the PI AI card to retrieve
        conn (Connection): Database connection from FastAPI dependency

    Returns:
        dict: PI AI card dictionary (includes source_job_id) or None if not found
    """
    try:
        query = text(f"""
            SELECT *
            FROM {config.PI_AI_CARDS_TABLE}
            WHERE id = :id
        """)

        logger.info(f"Executing query to get PI AI card with ID {card_id} from {config.PI_AI_CARDS_TABLE}")

        result = conn.execute(query, {"id": card_id})
        row = result.fetchone()
        if not row:
            return None

        return dict(row._mapping)
    except Exception as e:
        logger.error(f"Error fetching PI AI card {card_id}: {e}")
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
            "date", "team_name", "card_name", "card_type", "priority", "source",
            "source_job_id", "description", "full_information", "information_json", "pi"
        }

        filtered = {k: v for k, v in data.items() if k in allowed_columns}
        if not filtered:
            raise ValueError("No valid fields provided for ai_summary insert")

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
            "date", "team_name", "card_name", "card_type", "priority", "source",
            "source_job_id", "description", "full_information", "information_json", "pi"
        }
        filtered = {k: v for k, v in updates.items() if k in allowed_columns}
        if not filtered:
            raise ValueError("No valid fields provided for ai_summary update")

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

        query = text(f"""
            INSERT INTO {config.RECOMMENDATIONS_TABLE} ({columns_sql})
            VALUES ({values_sql})
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
    Copied exact logic from JiraDashboard-NEWUI project.
    
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
        upsert_sql = """
        INSERT INTO public.global_settings 
        (setting_key, setting_value, updated_at, updated_by)
        VALUES (:key, :value, CURRENT_TIMESTAMP, :updated_by)
        ON CONFLICT (setting_key) 
        DO UPDATE SET 
            setting_value = EXCLUDED.setting_value,
            updated_at = CURRENT_TIMESTAMP,
            updated_by = EXCLUDED.updated_by
        """
        
        logger.info(f"Setting global setting '{setting_key}' by {updated_by}")
        
        result = conn.execute(text(upsert_sql), {
            'key': setting_key,
            'value': setting_value,
            'updated_by': updated_by
        })
        conn.commit()
        
        logger.info(f"Successfully set global setting '{setting_key}'")
        return True
        
    except Exception as e:
        logger.error(f"Error setting global setting '{setting_key}': {e}")
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
        upsert_sql = """
        INSERT INTO public.global_settings 
        (setting_key, setting_value, updated_at, updated_by)
        VALUES (:key, :value, CURRENT_TIMESTAMP, :updated_by)
        ON CONFLICT (setting_key) 
        DO UPDATE SET 
            setting_value = EXCLUDED.setting_value,
            updated_at = CURRENT_TIMESTAMP,
            updated_by = EXCLUDED.updated_by
        """
        
        logger.info(f"Setting {len(settings)} global settings by {updated_by}")
        
        for setting_key, setting_value in settings.items():
            try:
                conn.execute(text(upsert_sql), {
                    'key': setting_key,
                    'value': setting_value,
                    'updated_by': updated_by
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
    offset: int = 0,
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
        offset (int): Number of results to skip
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
            LIMIT :limit OFFSET :offset
        """)
        
        params["limit"] = limit
        params["offset"] = offset
        
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
            "insight_type", "insight_description", "insight_categories", "active", "cron_config"
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
            "insight_type", "insight_description", "insight_categories", "active", "cron_config"
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


