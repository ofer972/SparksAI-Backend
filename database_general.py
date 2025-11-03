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


def get_top_ai_recommendations(team_name: str, limit: int = 4, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get top AI recommendations for a specific team.
    
    Returns recommendations ordered by:
    1. Date (newest first)
    2. Priority (High > Medium > Low)
    3. ID (descending)
    
    Args:
        team_name (str): Team name
        limit (int): Number of recommendations to return (default: 4)
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
            ORDER BY 
                DATE(date) DESC,
                CASE priority 
                    WHEN 'High' THEN 1
                    WHEN 'Medium' THEN 2
                    WHEN 'Low' THEN 3
                    ELSE 4
                END,
                id DESC
            LIMIT :limit
        """
        
        logger.info(f"Executing query to get top AI recommendations for team: {team_name}")
        logger.info(f"Parameters: team_name={team_name}, limit={limit}")
        
        result = conn.execute(text(sql_query), {
            'team_name': team_name, 
            'limit': limit
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


def get_top_ai_cards(team_name: str, limit: int = 4, conn: Connection = None) -> List[Dict[str, Any]]:
    """
    Get top AI cards for a specific team.
    
    Returns the most recent + highest priority card for each type (max 1 per type).
    Cards are ordered by:
    1. Priority (Critical > High > Medium)
    2. Date (newest first)
    
    Args:
        team_name (str): Team name
        limit (int): Number of AI cards to return (default: 4)
        conn (Connection): Database connection from FastAPI dependency
    
    Returns:
        list: List of AI card dictionaries
    """
    try:
        # SECURE: Parameterized query prevents SQL injection
        sql_query = """
            WITH ranked_cards AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY card_type 
                           ORDER BY 
                               date DESC,
                               updated_at DESC,
                               CASE priority 
                                   WHEN 'Critical' THEN 1 
                                   WHEN 'High' THEN 2 
                                   WHEN 'Medium' THEN 3 
                               END
                       ) as rn
                FROM public.ai_summary
                WHERE team_name = :team_name
            )
            SELECT id, date, team_name, card_name, card_type, priority, source, description, full_information, information_json
            FROM ranked_cards
            WHERE rn = 1
            ORDER BY 
                CASE priority 
                    WHEN 'Critical' THEN 1 
                    WHEN 'High' THEN 2 
                    WHEN 'Medium' THEN 3 
                END,
                date DESC
            LIMIT :limit
        """
        
        logger.info(f"Executing query to get top AI cards for team: {team_name}")
        logger.info(f"Parameters: team_name={team_name}, limit={limit}")
        
        result = conn.execute(text(sql_query), {
            'team_name': team_name, 
            'limit': limit
        })
        
        # Convert rows to list of dictionaries
        ai_cards = []
        for row in result:
            ai_cards.append({
                'id': row[0],
                'date': row[1],
                'team_name': row[2],
                'card_name': row[3],
                'card_type': row[4],
                'priority': row[5],
                'source': row[6],
                'description': row[7],
                'full_information': row[8],
                'information_json': row[9]
            })
        
        return ai_cards
            
    except Exception as e:
        logger.error(f"Error fetching top AI cards for team {team_name}: {e}")
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
        
        # Direct SELECT from ai_summary table (no view)
        query = text("""
            SELECT ai.id AS ai_card_id,
                aj.job_id,
                ai.team_name,
                ai.full_information,
                aj.input_sent
            FROM ai_summary ai
            JOIN agent_jobs aj ON ai.source_job_id = aj.job_id
            WHERE ai.id = :card_id AND aj.job_id = :job_id
        """)
        
        logger.info(f"Executing query to get formatted job data for LLM followup (card_id={card_id}, job_id={job_id})")
        
        result = conn.execute(query, {"card_id": card_id, "job_id": job_id})
        row = result.fetchone()
        
        if not row:
            logger.info(f"No data found for card_id={card_id}, job_id={job_id}")
            return f"No previous chat discussion was found in the previous job ({job_id})"
        
        # Convert row to dictionary
        row_dict = dict(row._mapping)
        
        # Use shared formatting helper
        return _format_job_data_for_llm(row_dict, "card", card_id, job_id)
        
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
        
        # Direct SELECT from recommendations table (no view)
        # Only filter by recommendation_id since the JOIN already connects via source_job_id
        query = text("""
            SELECT rec.id AS recommendation_id,
                aj.job_id,
                rec.team_name,
                rec.full_information,
                aj.input_sent
            FROM recommendations rec
            JOIN agent_jobs aj ON rec.source_job_id = aj.job_id
            WHERE rec.id = :recommendation_id
        """)
        
        logger.info(f"Executing query to get formatted job data for LLM followup (recommendation_id={recommendation_id}, job_id={job_id})")
        
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
        
        # Convert row to dictionary
        row_dict = dict(row._mapping)
        
        # Use shared formatting helper
        return _format_job_data_for_llm(row_dict, "recommendation", recommendation_id, job_id)
            
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
            "priority", "status", "information_json", "source_job_id"
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
            "priority", "status", "information_json", "source_job_id"
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


