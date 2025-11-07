"""
Vanna AI Service - Natural language to SQL conversion using LLM service.

This service provides functionality to convert natural language questions
to SQL queries using the LLM service, then execute them on the database.
"""

from fastapi import HTTPException
from sqlalchemy.engine import Connection
from sqlalchemy import text
from typing import Optional, Dict, Any, List
import logging
import json
import agent_llm_service
import config

logger = logging.getLogger(__name__)

# Vanna AI trigger constant
VANNA_AI_TRIGGER = "XXX"


def get_database_schema(conn: Connection) -> List[Dict[str, Any]]:
    """
    Get database schema information (tables and columns).
    
    Args:
        conn: Database connection
        
    Returns:
        List of table dictionaries with column information
    """
    try:
        # Get table information
        tables_query = text("""
            SELECT table_name, table_schema 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        tables_result = conn.execute(tables_query)
        tables = [dict(row._mapping) for row in tables_result]
        
        # Get column information for each table
        for table in tables:
            columns_query = text("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns 
                WHERE table_schema = :schema 
                AND table_name = :table_name
                ORDER BY ordinal_position;
            """)
            columns_result = conn.execute(
                columns_query,
                {"schema": table['table_schema'], "table_name": table['table_name']}
            )
            columns = [dict(row._mapping) for row in columns_result]
            table['columns'] = columns
        
        logger.info(f"Fetched schema for {len(tables)} tables")
        return tables
        
    except Exception as e:
        logger.error(f"Error fetching database schema: {e}")
        raise


def format_schema_text(schema_info: List[Dict[str, Any]]) -> str:
    """
    Format database schema information as text for LLM prompt.
    
    Args:
        schema_info: List of table dictionaries with columns
        
    Returns:
        Formatted schema text string
    """
    schema_text = "Database Schema:\n"
    for table in schema_info:
        schema_text += f"\nTable: {table['table_name']}\n"
        for column in table.get('columns', []):
            nullable = "NULL" if column.get('is_nullable') == 'YES' else "NOT NULL"
            default = f" DEFAULT {column.get('column_default')}" if column.get('column_default') else ""
            schema_text += f"  - {column['column_name']} ({column['data_type']}) {nullable}{default}\n"
    
    return schema_text


def convert_history_to_vanna_format(history_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert chat history_json format to Vanna conversation history format.
    
    Args:
        history_json: Chat history in format {'messages': [{'role': str, 'content': str}, ...]}
        
    Returns:
        List of conversation exchanges in format [{'question': str, 'sql': str, 'answer': str}]
    """
    vanna_history = []
    
    if not history_json or 'messages' not in history_json:
        return vanna_history
    
    messages = history_json.get('messages', [])
    
    # Extract last few exchanges (user/assistant pairs) that contain Vanna queries
    i = 0
    while i < len(messages):
        if messages[i].get('role') == 'user' and VANNA_AI_TRIGGER in messages[i].get('content', ''):
            # Found a Vanna question
            question = messages[i].get('content', '')
            
            # Look for assistant response
            if i + 1 < len(messages) and messages[i + 1].get('role') == 'assistant':
                answer = messages[i + 1].get('content', '')
                
                # Try to extract SQL from answer (look for code blocks)
                sql = None
                if '```sql' in answer:
                    sql_start = answer.find('```sql') + 6
                    sql_end = answer.find('```', sql_start)
                    if sql_end > sql_start:
                        sql = answer[sql_start:sql_end].strip()
                elif '```' in answer:
                    sql_start = answer.find('```') + 3
                    sql_end = answer.find('```', sql_start)
                    if sql_end > sql_start:
                        sql = answer[sql_start:sql_end].strip()
                
                vanna_history.append({
                    'question': question,
                    'sql': sql,
                    'answer': answer[:200] if answer else None  # Summarize to avoid token bloat
                })
            
            i += 2
        else:
            i += 1
    
    # Return last 3 exchanges to keep token count reasonable
    return vanna_history[-3:] if len(vanna_history) > 3 else vanna_history


def build_sql_generation_prompt(
    question: str,
    schema_text: str,
    conversation_history: List[Dict[str, Any]] = None
) -> str:
    """
    Build prompt for SQL generation from natural language question.
    
    Args:
        question: Natural language question
        schema_text: Formatted database schema text
        conversation_history: Previous conversation exchanges
        
    Returns:
        Complete prompt for LLM
    """
    # Build conversation context
    context_text = ""
    if conversation_history and len(conversation_history) > 0:
        context_text = "\n\nPrevious Conversation:\n"
        for i, exchange in enumerate(conversation_history, 1):
            context_text += f"\n{i}. User asked: {exchange.get('question', '')}\n"
            if exchange.get('sql'):
                context_text += f"   Generated SQL: {exchange.get('sql', '')}\n"
            if exchange.get('answer'):
                answer_summary = exchange.get('answer', '')[:200]
                context_text += f"   Answer summary: {answer_summary}...\n"
    
    prompt = f"""
{schema_text}
{context_text}

Based on the above database schema{"and conversation history" if context_text else ""}, convert this natural language question to SQL:
{question}

Important:
- If this is a follow-up question, consider modifying the previous SQL query
- If the user asks to "add" or "filter" or "modify", build upon the last SQL query
- Return only the SQL query, no explanations
- Do not include markdown code blocks (no ```sql or ```)
- Return clean SQL that can be executed directly
"""
    
    return prompt.strip()


async def call_vanna_ai(
    question: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    conn: Connection = None
) -> Dict[str, Any]:
    """
    Call Vanna AI to generate SQL from natural language question and execute it.
    
    Args:
        question: Natural language question (should contain trigger "XXX")
        conversation_history: Previous conversation exchanges (optional)
        conn: Database connection
        
    Returns:
        Dictionary with 'sql', 'results', 'status', 'error' keys
    """
    try:
        # Remove trigger from question
        clean_question = question.replace(VANNA_AI_TRIGGER, "").strip()
        # Remove any remaining standalone "X" at the start (in case user typed "XXXX" or "XXX X")
        if clean_question.startswith("X "):
            clean_question = clean_question[2:].strip()
        elif clean_question.startswith("X"):
            clean_question = clean_question[1:].strip()
        if not clean_question:
            return {
                'sql': None,
                'results': None,
                'error': 'Question is empty after removing trigger',
                'status': 'error'
            }
        
        # Get database schema
        logger.info("Fetching database schema for Vanna AI")
        schema_info = get_database_schema(conn)
        if not schema_info:
            return {
                'sql': None,
                'results': None,
                'error': 'Failed to fetch database schema',
                'status': 'error'
            }
        
        # Format schema text
        schema_text = format_schema_text(schema_info)
        
        # Build prompt
        prompt = build_sql_generation_prompt(clean_question, schema_text, conversation_history)
        
        # Call LLM service to generate SQL
        logger.info(f"Calling LLM service to generate SQL for question: {clean_question[:100]}...")
        system_prompt = "You are a SQL expert. Generate valid PostgreSQL SQL queries based on natural language questions and database schema."
        
        llm_response = await agent_llm_service.call_llm_service_process_single(
            prompt=prompt,
            system_prompt=system_prompt,
            metadata={"vanna_ai": True, "question": clean_question[:200]}
        )
        
        # Extract SQL from LLM response
        if not llm_response.get("success"):
            error_data = llm_response.get("error", {})
            error_message = error_data.get("message", "Unknown error from LLM service")
            return {
                'sql': None,
                'results': None,
                'error': f'LLM service error: {error_message}',
                'status': 'error'
            }
        
        response_data = llm_response.get("data", {})
        sql = response_data.get("response", "").strip()
        
        if not sql:
            return {
                'sql': None,
                'results': None,
                'error': 'LLM service returned empty SQL',
                'status': 'error'
            }
        
        # Strip markdown code blocks if present
        if sql.startswith('```'):
            lines = sql.split('\n')
            if lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            sql = '\n'.join(lines).strip()
        
        logger.info(f"Generated SQL: {sql[:200]}...")
        logger.info(f"=== VANNA AI SQL STATEMENT ===")
        logger.info(f"{sql}")
        logger.info(f"=== END VANNA AI SQL STATEMENT ===")
        
        # Execute SQL query
        try:
            result = conn.execute(text(sql))
            columns = result.keys()
            rows = result.fetchall()
            results = [dict(zip(columns, row)) for row in rows]
            
            logger.info(f"SQL executed successfully, returned {len(results)} rows")
            
            return {
                'sql': sql,
                'results': results,
                'status': 'success',
                'error': None
            }
            
        except Exception as sql_error:
            error_msg = str(sql_error)
            logger.error(f"SQL execution error: {error_msg}")
            
            # Provide helpful suggestions
            suggestions = []
            if "does not exist" in error_msg.lower() or "relation" in error_msg.lower():
                suggestions = [
                    "Check if the table name is correct",
                    "Try: 'Show me available tables'",
                    "Try: 'Show me active sprints' or 'Show me teams'"
                ]
            elif "column" in error_msg.lower():
                suggestions = [
                    "Check if the column name is correct",
                    "Try asking about different data"
                ]
            else:
                suggestions = [
                    "Try rephrasing your question",
                    "Be more specific about what data you want to see"
                ]
            
            return {
                'sql': sql,
                'results': None,
                'error': error_msg,
                'suggestions': suggestions,
                'status': 'error'
            }
        
    except Exception as e:
        error_msg = f"Error processing Vanna AI question: {str(e)}"
        logger.error(error_msg)
        import traceback
        traceback.print_exc()
        
        return {
            'sql': None,
            'results': None,
            'error': error_msg,
            'status': 'error'
        }


def format_vanna_results_for_llm(vanna_result: Dict[str, Any], question: Optional[str] = None) -> str:
    """
    Format Vanna AI results as text to include in LLM conversation context.
    Explicitly pairs the question with the answer so LLM understands the relationship.
    
    Args:
        vanna_result: Result dictionary from call_vanna_ai()
        question: Optional cleaned question (without trigger) to pair with results
        
    Returns:
        Formatted text string for LLM context
    """
    if vanna_result.get('status') != 'success':
        error = vanna_result.get('error', 'Unknown error')
        sql = vanna_result.get('sql', 'N/A')
        if question:
            return f"""=== DATABASE QUERY ATTEMPT ===
Question: {question}
SQL Query: {sql}
Error: {error}
=== END DATABASE QUERY ATTEMPT ==="""
        else:
            return f"""=== DATABASE QUERY ATTEMPT ===
SQL Query: {sql}
Error: {error}
=== END DATABASE QUERY ATTEMPT ==="""
    
    sql = vanna_result.get('sql', '')
    results = vanna_result.get('results', [])
    row_count = len(results)
    
    # Format results as JSON string (limit to first 100 rows to avoid token bloat)
    results_to_show = results[:100]
    results_json = json.dumps(results_to_show, indent=2, default=str)
    
    # Format with explicit Question/Answer pairing
    if question:
        formatted_text = f"""=== DATABASE QUERY ===
Question: {question}
Answer:
SQL Query:
{sql}

Results ({row_count} row{'s' if row_count != 1 else ''}):"""
    else:
        formatted_text = f"""=== DATABASE QUERY RESULTS ===
SQL Query:
{sql}

Results ({row_count} row{'s' if row_count != 1 else ''}):"""
    
    if row_count > 0:
        formatted_text += f"\n{results_json}"
        if row_count > 100:
            formatted_text += f"\n\n(Showing first 100 of {row_count} rows)"
    else:
        formatted_text += "\nNo data returned"
    
    if question:
        formatted_text += "\n=== END DATABASE QUERY ==="
    else:
        formatted_text += "\n=== END DATABASE QUERY RESULTS ==="
    
    return formatted_text

