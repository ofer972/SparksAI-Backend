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
            SELECT 
                id,
                team_name,
                date,
                action_text,
                rational,
                full_information,
                priority,
                status
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
        logger.info(f"SQL Query: {sql_query}")
        logger.info(f"Parameters: team_name={team_name}, limit={limit}")
        
        result = conn.execute(text(sql_query), {
            'team_name': team_name, 
            'limit': limit
        })
        
        # Convert rows to list of dictionaries
        recommendations = []
        for row in result:
            recommendations.append({
                'id': row.id,
                'team_name': row.team_name,
                'date': row.date,
                'action_text': row.action_text,
                'rational': row.rational,
                'full_information': row.full_information,
                'priority': row.priority,
                'status': row.status
            })
        
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
            SELECT id, date, team_name, card_name, card_type, priority, source, description, full_information
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
        logger.info(f"SQL Query: {sql_query}")
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
                'full_information': row[8]
            })
        
        return ai_cards
            
    except Exception as e:
        logger.error(f"Error fetching top AI cards for team {team_name}: {e}")
        raise e
