"""
Database General - Database access functions for general services.

This module contains database access functions for recommendations and team AI cards.
Uses FastAPI dependencies for clean connection management and SQL injection protection.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from typing import List, Dict, Any
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
                               CASE priority 
                                   WHEN 'Critical' THEN 1 
                                   WHEN 'High' THEN 2 
                                   WHEN 'Medium' THEN 3 
                               END
                       ) as rn
                FROM public.team_ai_summary_cards
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


