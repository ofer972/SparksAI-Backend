"""
Database table creation module.

This module contains all table creation functions and the initialization logic
that runs when the database engine is created (new connection, not from pool).
Copied from JiraDashboard-NEWUI project without any logic changes.
"""

import sys
import traceback
from datetime import date, timedelta
from sqlalchemy import text
from typing import Optional


# Global flag to ensure tables are created only once
_tables_initialized = False


def create_users_table_if_not_exists(engine=None) -> bool:
    """Create users table if it doesn't exist"""
    # Use the same connection pattern as other functions for consistency
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create users table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'users'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating users table...")
                create_table_sql = """
                CREATE TABLE public.users (
                    email_address VARCHAR(255) PRIMARY KEY,
                    display_name VARCHAR(255) NOT NULL,
                    user_role VARCHAR(50) DEFAULT 'User',
                    last_login TIMESTAMP WITH TIME ZONE,
                    active BOOLEAN DEFAULT TRUE,
                    ai_model_preference VARCHAR(100) DEFAULT 'gemini-2.5-flash',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX idx_users_email ON public.users(email_address);
                CREATE INDEX idx_users_role ON public.users(user_role);
                CREATE INDEX idx_users_active ON public.users(active);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("Users table created successfully")
                
                # Insert test data
                insert_test_data_for_users()
            else:
                print("Users table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating users table: {e}")
        traceback.print_exc()
        return False


def create_prompts_table_if_not_exists(engine=None) -> bool:
    """Create prompts table if it doesn't exist"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create prompts table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'prompts'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating prompts table...")
                create_table_sql = """
                CREATE TABLE public.prompts (
                    id SERIAL PRIMARY KEY,
                    prompt_name VARCHAR(255) NOT NULL UNIQUE,
                    prompt_text TEXT NOT NULL,
                    prompt_type VARCHAR(100) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_by VARCHAR(255),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (created_by) REFERENCES public.users(email_address)
                );
                
                CREATE INDEX idx_prompts_name ON public.prompts(prompt_name);
                CREATE INDEX idx_prompts_type ON public.prompts(prompt_type);
                CREATE INDEX idx_prompts_active ON public.prompts(is_active);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("Prompts table created successfully")
                
                # Insert test data
                insert_test_data_for_prompts()
            else:
                print("Prompts table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating prompts table: {e}")
        traceback.print_exc()
        return False


def create_security_logs_table_if_not_exists(engine=None) -> bool:
    """Create security_logs table if it doesn't exist"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create security_logs table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'security_logs'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating security_logs table...")
                create_table_sql = """
                CREATE TABLE public.security_logs (
                    id SERIAL PRIMARY KEY,
                    user_email VARCHAR(255),
                    action VARCHAR(255) NOT NULL,
                    resource VARCHAR(255),
                    ip_address INET,
                    user_agent TEXT,
                    success BOOLEAN NOT NULL,
                    error_message TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_email) REFERENCES public.users(email_address)
                );
                
                CREATE INDEX idx_security_logs_user ON public.security_logs(user_email);
                CREATE INDEX idx_security_logs_action ON public.security_logs(action);
                CREATE INDEX idx_security_logs_created ON public.security_logs(created_at DESC);
                CREATE INDEX idx_security_logs_success ON public.security_logs(success);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("Security logs table created successfully")
            else:
                print("Security logs table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating security_logs table: {e}")
        traceback.print_exc()
        return False


def create_global_settings_table_if_not_exists(engine=None) -> bool:
    """Create global_settings table if it doesn't exist"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create global_settings table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'global_settings'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating global_settings table...")
                create_table_sql = """
                CREATE TABLE public.global_settings (
                    setting_key VARCHAR(255) PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    setting_type VARCHAR(50) NOT NULL,
                    description TEXT,
                    is_encrypted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX idx_global_settings_type ON public.global_settings(setting_type);
                CREATE INDEX idx_global_settings_encrypted ON public.global_settings(is_encrypted);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("Global settings table created successfully")
                
                # Insert default settings
                insert_default_global_settings()
            else:
                print("Global settings table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating global_settings table: {e}")
        traceback.print_exc()
        return False


def create_agent_jobs_table_if_not_exists(engine=None) -> bool:
    """Create agent_jobs table if it doesn't exist"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create agent_jobs table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'agent_jobs'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating agent_jobs table...")
                create_table_sql = """
                CREATE TABLE public.agent_jobs (
                    id SERIAL PRIMARY KEY,
                    job_name VARCHAR(255) NOT NULL,
                    job_type VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    team_name VARCHAR(255),
                    pi VARCHAR(50),
                    parameters JSONB,
                    result_data JSONB,
                    error_message TEXT,
                    started_at TIMESTAMP WITH TIME ZONE,
                    completed_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    created_by VARCHAR(255),
                    FOREIGN KEY (created_by) REFERENCES public.users(email_address)
                );
                
                CREATE INDEX idx_agent_jobs_status ON public.agent_jobs(status);
                CREATE INDEX idx_agent_jobs_type ON public.agent_jobs(job_type);
                CREATE INDEX idx_agent_jobs_team ON public.agent_jobs(team_name);
                CREATE INDEX idx_agent_jobs_created ON public.agent_jobs(created_at DESC);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("Agent jobs table created successfully")
            else:
                print("Agent jobs table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating agent_jobs table: {e}")
        traceback.print_exc()
        return False


def create_team_ai_summary_cards_table_if_not_exists(engine=None) -> bool:
    """Create team_ai_summary_cards table if it doesn't exist"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create team_ai_summary_cards table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'team_ai_summary_cards'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating team_ai_summary_cards table...")
                create_table_sql = """
                CREATE TABLE public.team_ai_summary_cards (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    team_name VARCHAR(255) NOT NULL,
                    card_name VARCHAR(255) NOT NULL,
                    card_type VARCHAR(100) NOT NULL,
                    priority VARCHAR(50) NOT NULL,
                    source VARCHAR(255),
                    source_job_id INTEGER,
                    description TEXT NOT NULL,
                    full_information TEXT,
                    information_json TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (date, team_name, card_name)
                );
                
                CREATE INDEX idx_team_ai_summary_cards_team_date ON public.team_ai_summary_cards(team_name, date DESC);
                CREATE INDEX idx_team_ai_summary_cards_priority ON public.team_ai_summary_cards(priority);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("team_ai_summary_cards table created successfully")
                
                # Insert test data
                insert_test_data_for_team_ai_summary_cards()
            else:
                print("team_ai_summary_cards table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating team_ai_summary_cards table: {e}")
        traceback.print_exc()
        return False


def create_pi_ai_summary_cards_table_if_not_exists(engine=None) -> bool:
    """Create pi_ai_summary_cards table if it doesn't exist"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create pi_ai_summary_cards table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'pi_ai_summary_cards'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating pi_ai_summary_cards table...")
                create_table_sql = """
                CREATE TABLE public.pi_ai_summary_cards (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    team_name VARCHAR(255) NOT NULL,
                    quarter VARCHAR(100) NOT NULL,
                    card_type VARCHAR(100) NOT NULL,
                    priority VARCHAR(50) NOT NULL,
                    source VARCHAR(255),
                    description TEXT NOT NULL,
                    full_information TEXT,
                    information_json TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX idx_pi_ai_summary_cards_team_date ON public.pi_ai_summary_cards(team_name, date DESC);
                CREATE INDEX idx_pi_ai_summary_cards_priority ON public.pi_ai_summary_cards(priority);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("pi_ai_summary_cards table created successfully")
            else:
                print("pi_ai_summary_cards table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating pi_ai_summary_cards table: {e}")
        traceback.print_exc()
        return False


def create_ai_summary_table_if_not_exists(engine=None) -> bool:
    """Create ai_summary table if it doesn't exist"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create ai_summary table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'ai_summary'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating ai_summary table...")
                create_table_sql = """
                CREATE TABLE public.ai_summary (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    team_name VARCHAR(255) NOT NULL,
                    card_name VARCHAR(255) NOT NULL,
                    card_type VARCHAR(100) NOT NULL,
                    priority VARCHAR(50) NOT NULL,
                    source VARCHAR(255),
                    source_job_id INTEGER,
                    description TEXT NOT NULL,
                    full_information TEXT,
                    information_json TEXT,
                    pi VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (date, team_name, card_name, pi)
                );
                
                CREATE INDEX idx_ai_summary_team_date ON public.ai_summary(team_name, date DESC);
                CREATE INDEX idx_ai_summary_priority ON public.ai_summary(priority);
                CREATE INDEX idx_ai_summary_pi ON public.ai_summary(pi);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("ai_summary table created successfully")
            else:
                print("ai_summary table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating ai_summary table: {e}")
        return False


def create_transcripts_table_if_not_exists(engine=None) -> bool:
    """Create transcripts table if it doesn't exist"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create transcripts table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'transcripts'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating transcripts table...")
                create_table_sql = """
                CREATE TABLE public.transcripts (
                    id SERIAL PRIMARY KEY,
                    transcript_date DATE,
                    team_name VARCHAR(255),
                    type VARCHAR(50),
                    file_name VARCHAR(255),
                    raw_text TEXT,
                    origin VARCHAR(500),
                    pi VARCHAR(255),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT unique_team_transcript UNIQUE (transcript_date, team_name),
                    CONSTRAINT unique_pi_transcript UNIQUE (transcript_date, pi)
                );
                
                CREATE INDEX idx_transcripts_type ON public.transcripts(type);
                CREATE INDEX idx_transcripts_team ON public.transcripts(team_name);
                CREATE INDEX idx_transcripts_pi ON public.transcripts(pi);
                CREATE INDEX idx_transcripts_date ON public.transcripts(transcript_date DESC);
                CREATE INDEX idx_transcripts_created ON public.transcripts(created_at DESC);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("Transcripts table created successfully")
            else:
                print("Transcripts table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating transcripts table: {e}")
        traceback.print_exc()
        return False


def create_recommendations_table_if_not_exists(engine=None) -> bool:
    """Create recommendations table if it doesn't exist"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create recommendations table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'recommendations'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating recommendations table...")
                create_table_sql = """
                CREATE TABLE public.recommendations (
                    id SERIAL PRIMARY KEY,
                    team_name VARCHAR(255) NOT NULL,
                    date TIMESTAMP WITH TIME ZONE NOT NULL,
                    action_text TEXT NOT NULL,
                    rational TEXT,
                    full_information TEXT,
                    information_json TEXT,
                    priority VARCHAR(50) NOT NULL,
                    status VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (team_name, date)
                );
                
                CREATE INDEX idx_recommendations_team_date ON public.recommendations(team_name, date DESC);
                CREATE INDEX idx_recommendations_priority ON public.recommendations(priority);
                CREATE INDEX idx_recommendations_status ON public.recommendations(status);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("Recommendations table created successfully")
            else:
                print("Recommendations table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating recommendations table: {e}")
        traceback.print_exc()
        return False


def add_input_sent_column_to_agent_jobs(engine=None) -> bool:
    """Temporary function to add input_sent column to agent_jobs table"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot add input_sent column")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if column exists
            check_column_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name = 'agent_jobs'
                AND column_name = 'input_sent'
            );
            """
            result = conn.execute(text(check_column_sql))
            column_exists = result.scalar()
            
            if not column_exists:
                print("Adding input_sent column to agent_jobs table...")
                alter_table_sql = """
                ALTER TABLE public.agent_jobs 
                ADD COLUMN input_sent TEXT;
                """
                conn.execute(text(alter_table_sql))
                conn.commit()
                print("input_sent column added successfully")
            else:
                print("input_sent column already exists")
            
            return True
            
    except Exception as e:
        print(f"Error adding input_sent column: {e}")
        traceback.print_exc()
        return False


# Test data insertion functions (copied from original)
def insert_test_data_for_users():
    """Insert test data for users table"""
    import database_connection
    
    try:
        with engine.connect() as conn:
            insert_sql = """
            INSERT INTO public.users (email_address, display_name, user_role, active) 
            VALUES ('admin@example.com', 'Admin User', 'Admin', TRUE)
            ON CONFLICT (email_address) DO NOTHING;
            """
            conn.execute(text(insert_sql))
            conn.commit()
            print("Test user data inserted")
    except Exception as e:
        print(f"Error inserting test user data: {e}")


def insert_test_data_for_prompts():
    """Insert test data for prompts table"""
    import database_connection
    
    try:
        with engine.connect() as conn:
            insert_sql = """
            INSERT INTO public.prompts (prompt_name, prompt_text, prompt_type, is_active) 
            VALUES ('test_prompt', 'This is a test prompt', 'general', TRUE)
            ON CONFLICT (prompt_name) DO NOTHING;
            """
            conn.execute(text(insert_sql))
            conn.commit()
            print("Test prompt data inserted")
    except Exception as e:
        print(f"Error inserting test prompt data: {e}")


def insert_test_data_for_team_ai_summary_cards():
    """Insert test data for team_ai_summary_cards table"""
    import database_connection
    
    try:
        with engine.connect() as conn:
            insert_sql = """
            INSERT INTO public.team_ai_summary_cards (date, team_name, card_name, card_type, priority, description) 
            VALUES (CURRENT_DATE, 'TestTeam', 'Test Card', 'performance', 'High', 'This is a test AI card')
            ON CONFLICT (date, team_name, card_name) DO NOTHING;
            """
            conn.execute(text(insert_sql))
            conn.commit()
            print("Test AI card data inserted")
    except Exception as e:
        print(f"Error inserting test AI card data: {e}")


def insert_default_global_settings():
    """Insert default global settings"""
    import database_connection
    
    try:
        with engine.connect() as conn:
            insert_sql = """
            INSERT INTO public.global_settings (setting_key, setting_value, setting_type, description) 
            VALUES 
                ('default_ai_model', 'gemini-2.5-flash', 'string', 'Default AI model to use'),
                ('max_ai_cards_per_team', '10', 'integer', 'Maximum AI cards per team'),
                ('enable_ai_insights', 'true', 'boolean', 'Enable AI insights feature')
            ON CONFLICT (setting_key) DO NOTHING;
            """
            conn.execute(text(insert_sql))
            conn.commit()
            print("Default global settings inserted")
    except Exception as e:
        print(f"Error inserting default global settings: {e}")


def initialize_database_tables_with_engine(engine) -> None:
    """Initialize all database tables using provided engine - should be called only once on startup"""
    global _tables_initialized
    if _tables_initialized:
        return
    
    print("=== INITIALIZING DATABASE TABLES ===")
    create_users_table_if_not_exists(engine)
    create_prompts_table_if_not_exists(engine)
    create_security_logs_table_if_not_exists(engine)
    create_global_settings_table_if_not_exists(engine)
    create_team_ai_summary_cards_table_if_not_exists(engine)
    create_pi_ai_summary_cards_table_if_not_exists(engine)
    create_ai_summary_table_if_not_exists(engine)
    create_agent_jobs_table_if_not_exists(engine)
    add_input_sent_column_to_agent_jobs(engine)
    create_transcripts_table_if_not_exists(engine)
    create_recommendations_table_if_not_exists(engine)
    _tables_initialized = True
    print("=== DATABASE TABLES INITIALIZATION COMPLETE ===")


def initialize_database_tables() -> None:
    """Initialize all database tables - should be called only once on startup"""
    global _tables_initialized
    if _tables_initialized:
        return
    
    print("=== INITIALIZING DATABASE TABLES ===")
    create_users_table_if_not_exists()
    create_prompts_table_if_not_exists()
    create_security_logs_table_if_not_exists()
    create_global_settings_table_if_not_exists()
    create_team_ai_summary_cards_table_if_not_exists()
    create_pi_ai_summary_cards_table_if_not_exists()
    create_ai_summary_table_if_not_exists()
    create_agent_jobs_table_if_not_exists()
    add_input_sent_column_to_agent_jobs()  # Temporary function to add input_sent column
    create_transcripts_table_if_not_exists()
    create_recommendations_table_if_not_exists()
    _tables_initialized = True
    print("=== DATABASE TABLES INITIALIZATION COMPLETE ===")
