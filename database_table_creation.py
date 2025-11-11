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

# Default insight types data - easy to update
# Note: insight_categories is now a list (array) that will be stored as JSONB
DEFAULT_INSIGHT_TYPES = [
    {
        "insight_type": "PI Sync",
        "insight_description": "Insights from the PI Sync (data + transcipts)",
        "insight_categories": ["PI Sync"],
        "active": True,
        "requires_pi": True,
        "requires_team": False,
        "cron_config": {"day_of_week": "sun,tue,thu", "hour": 6, "minute": 0}
    },
    {
        "insight_type": "Daily Progress",
        "insight_description": "Analysis of team progress in the sprint",
        "insight_categories": ["Daily"],
        "active": True,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Sprint Goal",
        "insight_description": "Assesses the team progress towards the defined sprint goal",
        "insight_categories": ["Daily", "Planning", "Retrospective", "Sprint Review", "Backlog Refinement", "PI Sync"],
        "active": True,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Team Retro Topics",
        "insight_description": "Suggests focus topics for the next retrospective",
        "insight_categories": ["Retrospective"],
        "active": True,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Team PI Insight",
        "insight_description": "Evaluates progress toward sprint goals",
        "insight_categories": ["Daily", "Planning", "Retrospective", "Sprint Review", "Backlog Refinement", "PI Sync"],
        "active": True,
        "requires_pi": True,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Conversation Analysis",
        "insight_description": "Analyzes team discussions for themes",
        "insight_categories": ["Daily", "Retrospective"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Team Blockers' Last Days",
        "insight_description": "Lists recent blockers",
        "insight_categories": ["Daily"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "WIP Level",
        "insight_description": "Monitors active work items",
        "insight_categories": ["Daily"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Stories Aging (In Progress)",
        "insight_description": "Tracks story age in progress",
        "insight_categories": ["Daily"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Defects Trend",
        "insight_description": "Shows trend of bugs and quality issues",
        "insight_categories": ["Retrospective", "Sprint Review"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Team Metrics Insight",
        "insight_description": "Summarizes key velocity and flow metrics",
        "insight_categories": ["Retrospective"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "DORA Lite",
        "insight_description": "Displays core DORA metrics for the sprint",
        "insight_categories": ["Retrospective"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Sprint Summary",
        "insight_description": "Provides overall sprint summary and outcomes",
        "insight_categories": ["Retrospective", "Sprint Review", "Planning", "PI Sync"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Stories Readiness Gaps",
        "insight_description": "Lists stories not ready for execution",
        "insight_categories": ["Planning", "Backlog Refinement"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Forecast (Velocity-Based)",
        "insight_description": "Compares planned vs. forecasted work",
        "insight_categories": ["Planning", "Backlog Refinement", "PI Sync"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Epic Risk Scanner",
        "insight_description": "Detects epics at delivery risk",
        "insight_categories": ["Planning", "Backlog Refinement", "Sprint Review", "PI Sync"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Sprint planning Suggestions Coaching",
        "insight_description": "Provides AI-driven planning tips",
        "insight_categories": ["Planning"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Sprint Goal Suggestions",
        "insight_description": "Suggests possible sprint goals from stories",
        "insight_categories": ["Planning", "Backlog Refinement"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"hour": 5, "minute": 30}
    },
    {
        "insight_type": "Demo Suggestions",
        "insight_description": "Suggests highlights for the sprint demo",
        "insight_categories": ["Sprint Review"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"day_of_week": "mon", "hour": 6, "minute": 0}
    },
    {
        "insight_type": "Dependency Radar",
        "insight_description": "Highlights critical delivery dependencies",
        "insight_categories": ["Planning", "Retrospective", "Sprint Review", "Backlog Refinement", "PI Sync"],
        "active": False,
        "requires_pi": False,
        "requires_team": True,
        "cron_config": {"day_of_week": "sun,tue,thu", "hour": 6, "minute": 0}
    }


]

DEFAULT_REPORT_DEFINITIONS = [
    {
        "report_id": "team-sprint-burndown",
        "report_name": "Sprint Burndown",
        "chart_type": "burn_down",
        "data_source": "team_sprint_burndown",
        "description": "Tracks remaining work across a sprint for a given team.",
        "default_filters": {
            "team_name": None,
            "issue_type": "all",
            "sprint_name": None
        },
        "meta_schema": {
            "required_filters": ["team_name"],
            "optional_filters": ["issue_type", "sprint_name"],
            "parameters": {
                "team_name": {"type": "string", "description": "Team identifier"},
                "issue_type": {"type": "string", "description": "Issue type filter such as 'all', 'Bug', 'Story'"},
                "sprint_name": {"type": "string", "description": "Explicit sprint name override"}
            },
            "allowed_views": ["team-dashboard"]
        }
    },
    {
        "report_id": "team-current-sprint-progress",
        "report_name": "Current Sprint Progress",
        "chart_type": "summary",
        "data_source": "team_current_sprint_progress",
        "description": "Displays the progress of the current sprint for a given team.",
        "default_filters": {
            "team_name": None
        },
        "meta_schema": {
            "required_filters": ["team_name"],
            "optional_filters": [],
            "parameters": {
                "team_name": {"type": "string", "description": "Team identifier"}
            },
            "allowed_views": ["team-dashboard"]
        }
    },
    {
        "report_id": "pi-burndown",
        "report_name": "PI Burndown",
        "chart_type": "burn_down",
        "data_source": "pi_burndown",
        "description": "Displays program increment burndown for epics and features.",
        "default_filters": {
            "pi": None,
            "issue_type": "Epic",
            "project": None,
            "team": None
        },
        "meta_schema": {
            "required_filters": ["pi"],
            "optional_filters": ["issue_type", "project", "team"],
            "parameters": {
                "pi": {"type": "string", "description": "Program increment name"},
                "issue_type": {"type": "string", "description": "Issue type filter (default 'Epic')"},
                "project": {"type": "string", "description": "Project key filter"},
                "team": {"type": "string", "description": "Team filter"}
            },
            "allowed_views": ["pi-dashboard", "team-dashboard"]
        }
    },
    {
        "report_id": "team-closed-sprints",
        "report_name": "Closed Sprints",
        "chart_type": "table",
        "data_source": "team_closed_sprints",
        "description": "Displays completed sprint metrics for a given team across recent months.",
        "default_filters": {
            "team_name": None,
            "months": 3
        },
        "meta_schema": {
            "required_filters": ["team_name"],
            "optional_filters": ["months"],
            "parameters": {
                "team_name": {"type": "string", "description": "Team identifier"},
                "months": {"type": "integer", "description": "Number of months to look back (1, 2, 3, 4, 6, 9)"}
            },
            "allowed_views": ["team-dashboard"]
        }
    },
    {
        "report_id": "team-issues-trend",
        "report_name": "Bugs Created and Resolved Over Time",
        "chart_type": "trend",
        "data_source": "team_issues_trend",
        "description": "Shows monthly counts of issues created, resolved, and remaining open.",
        "default_filters": {
            "team_name": None,
            "issue_type": "Bug",
            "months": 6
        },
        "meta_schema": {
            "required_filters": ["team_name"],
            "optional_filters": ["issue_type", "months"],
            "parameters": {
                "team_name": {"type": "string", "description": "Team identifier"},
                "issue_type": {"type": "string", "description": "Issue type filter (e.g., 'Bug', 'Story', 'all')"},
                "months": {"type": "integer", "description": "Number of months to look back (1-12)"}
            },
            "allowed_views": ["team-dashboard"]
        }
    },
    {
        "report_id": "pi-predictability",
        "report_name": "PI Predictability",
        "chart_type": "table",
        "data_source": "pi_predictability",
        "description": "Summarizes predictability metrics for program increments.",
        "default_filters": {
            "pi_names": [],
            "team_name": None
        },
        "meta_schema": {
            "required_filters": ["pi_names"],
            "optional_filters": ["team_name"],
            "parameters": {
                "pi_names": {"type": "array", "description": "List of PI names to include"},
                "team_name": {"type": "string", "description": "Optional team filter"}
            },
            "allowed_views": ["pi-dashboard", "team-dashboard"]
        }
    },
    {
        "report_id": "epic-scope-changes",
        "report_name": "Epic Scope Changes",
        "chart_type": "stacked_bar",
        "data_source": "epic_scope_changes",
        "description": "Compares epic scope adjustments across selected PI quarters.",
        "default_filters": {
            "quarters": []
        },
        "meta_schema": {
            "required_filters": ["quarters"],
            "optional_filters": [],
            "parameters": {
                "quarters": {"type": "array", "description": "List of PI or quarter names (e.g., '2025-Q1')"}
            },
            "allowed_views": ["every-dashboard"]
        }
    },
    {
        "report_id": "issues-bugs-by-priority",
        "report_name": "Bugs by Priority",
        "chart_type": "composite",
        "data_source": "issues_bugs_by_priority",
        "description": "Visualizes open issues by priority and by team to highlight where attention is needed.",
        "default_filters": {
            "issue_type": "Bug",
            "team_name": None,
            "status_category": None,
            "include_done": False
        },
        "meta_schema": {
            "required_filters": [],
            "optional_filters": ["issue_type", "team_name", "status_category", "include_done"],
            "parameters": {
                "issue_type": {"type": "string", "description": "Issue type filter (default 'Bug')"},
                "team_name": {"type": "string", "description": "Optional team name filter"},
                "status_category": {"type": "string", "description": "Optional status category filter"},
                "include_done": {"type": "boolean", "description": "Include completed issues (defaults to false)"}
            },
            "allowed_views": ["team-dashboard"]
        }
    },
    {
        "report_id": "issues-flow-status-duration",
        "report_name": "Flow Status Duration",
        "chart_type": "bar",
        "data_source": "issues_flow_status_duration",
        "description": "Shows average time spent in each workflow status, with optional monthly breakdowns and drill-down.",
        "default_filters": {
            "issue_type": None,
            "team_name": None,
            "months": 3,
            "view_mode": "total"
        },
        "meta_schema": {
            "required_filters": [],
            "optional_filters": ["issue_type", "team_name", "months", "view_mode"],
            "parameters": {
                "issue_type": {"type": "string", "description": "Issue type filter"},
                "team_name": {"type": "string", "description": "Team name filter"},
                "months": {"type": "integer", "description": "Number of months to evaluate (1, 2, 3, 4, 6, 9)"},
                "view_mode": {"type": "string", "description": "Chart view mode ('total' or 'monthly')"}
            },
            "allowed_views": ["team-dashboard"]
        }
    },
    {
        "report_id": "issues-epics-hierarchy",
        "report_name": "Epics Hierarchy",
        "chart_type": "table",
        "data_source": "issues_epics_hierarchy",
        "description": "Displays the hierarchy of epics with status and dependency information.",
        "default_filters": {
            "pi": None,
            "team_name": None,
            "limit": 500
        },
        "meta_schema": {
            "required_filters": [],
            "optional_filters": ["pi", "team_name", "limit"],
            "parameters": {
                "pi": {"type": "string", "description": "PI name filter"},
                "team_name": {"type": "string", "description": "Team name filter"},
                "limit": {"type": "integer", "description": "Maximum number of records to return (up to 1000)"}
            },
            "allowed_views": ["pi-dashboard", "team-dashboard"]
        }
    },
    {
        "report_id": "issues-epic-dependencies",
        "report_name": "Epic Dependencies",
        "chart_type": "table",
        "data_source": "issues_epic_dependencies",
        "description": "Summarizes inbound and outbound epic dependencies for a PI.",
        "default_filters": {
            "pi": None
        },
        "meta_schema": {
            "required_filters": [],
            "optional_filters": ["pi"],
            "parameters": {
                "pi": {"type": "string", "description": "PI name filter"}
            },
            "allowed_views": ["pi-dashboard"]
        }
    },
    {
        "report_id": "issues-release-predictability",
        "report_name": "Release Predictability",
        "chart_type": "table",
        "data_source": "issues_release_predictability",
        "description": "Highlights release progress across epics and other issues over recent months.",
        "default_filters": {
            "months": 3
        },
        "meta_schema": {
            "required_filters": [],
            "optional_filters": ["months"],
            "parameters": {
                "months": {"type": "integer", "description": "Number of months to look back"}
            },
            "allowed_views": ["every-dashboard"]
        }
    },
    {
        "report_id": "sprint-predictability",
        "report_name": "Sprint Predictability",
        "chart_type": "table",
        "data_source": "sprint_predictability",
        "description": "Provides sprint predictability metrics, cycle time, and completion breakdown.",
        "default_filters": {
            "months": 3
        },
        "meta_schema": {
            "required_filters": [],
            "optional_filters": ["months"],
            "parameters": {
                "months": {"type": "integer", "description": "Number of months to look back"}
            },
            "allowed_views": ["team-dashboard", "pi-dashboard"]
        }
    },
    {
        "report_id": "pi-metrics-summary",
        "report_name": "PI Metrics Summary",
        "chart_type": "summary",
        "data_source": "pi_metrics_summary",
        "description": "Aggregates PI closure progress and WIP metrics for leadership review.",
        "default_filters": {
            "pi": None,
            "project": None,
            "issue_type": "Epic",
            "team_name": None,
            "plan_grace_period": 5
        },
        "meta_schema": {
            "required_filters": [],
            "optional_filters": ["pi", "project", "issue_type", "team_name", "plan_grace_period"],
            "parameters": {
                "pi": {"type": "string", "description": "PI name filter"},
                "project": {"type": "string", "description": "Project key filter"},
                "issue_type": {"type": "string", "description": "Issue type filter (default 'Epic')"},
                "team_name": {"type": "string", "description": "Team name filter"},
                "plan_grace_period": {"type": "integer", "description": "Grace period in days (default 5)"}
            },
            "allowed_views": ["pi-dashboard"]
        }
    }
]



def create_users_table_if_not_exists(engine=None) -> bool:
    """Create users table if it doesn't exist"""
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
                    ai_insight BOOLEAN DEFAULT FALSE,
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
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
                    email_address VARCHAR(255) NOT NULL,
                    prompt_name VARCHAR(255) NOT NULL,
                    prompt_description TEXT NULL,
                    prompt_type VARCHAR(100) NOT NULL,
                    prompt_active BOOLEAN DEFAULT TRUE NULL,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NULL,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NULL,
                    CONSTRAINT prompts_pkey PRIMARY KEY (email_address, prompt_name)
                );
                
                -- Intentionally no foreign key on email_address
                
                CREATE INDEX idx_prompts_type ON public.prompts(prompt_type);
                CREATE INDEX idx_prompts_active ON public.prompts(prompt_active);
                CREATE INDEX idx_prompts_created ON public.prompts(created_at DESC);
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
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
                CREATE INDEX idx_agent_jobs_status_created_jobid ON public.agent_jobs(status, created_at ASC, job_id ASC);
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
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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
                    source_job_id INTEGER,
                    source_ai_summary_id INTEGER,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX idx_recommendations_team_date ON public.recommendations(team_name, date DESC);
                CREATE INDEX idx_recommendations_priority ON public.recommendations(priority);
                CREATE INDEX idx_recommendations_status ON public.recommendations(status);
                CREATE INDEX idx_recommendations_source_ai_summary_id ON public.recommendations(source_ai_summary_id);
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


def create_chat_history_table_if_not_exists(engine=None) -> bool:
    """Create chat_history table if it doesn't exist"""
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create chat_history table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'chat_history'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating chat_history table...")
                create_table_sql = """
                CREATE TABLE public.chat_history (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(255) NOT NULL,
                    team VARCHAR(255) NOT NULL,
                    pi VARCHAR(255) NOT NULL,
                    chat_type VARCHAR(50) NOT NULL,
                    start_timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    history_json JSONB
                );
                
                CREATE INDEX idx_chat_history_username ON public.chat_history(username);
                CREATE INDEX idx_chat_history_team ON public.chat_history(team);
                CREATE INDEX idx_chat_history_pi ON public.chat_history(pi);
                CREATE INDEX idx_chat_history_chat_type ON public.chat_history(chat_type);
                CREATE INDEX idx_chat_history_timestamp ON public.chat_history(start_timestamp DESC);
                CREATE INDEX idx_chat_history_username_timestamp ON public.chat_history(username, start_timestamp DESC);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("Chat history table created successfully")
            else:
                print("Chat history table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating chat_history table: {e}")
        traceback.print_exc()
        return False


def add_input_sent_column_to_agent_jobs(engine=None) -> bool:
    """Temporary function to add input_sent column to agent_jobs table"""
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
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


def create_teams_and_team_groups_tables_if_not_exists(engine=None) -> bool:
    """Create team_groups and teams tables if they don't exist"""
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create teams and team_groups tables")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if team_groups table exists
            check_team_groups_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'team_groups'
            );
            """
            result = conn.execute(text(check_team_groups_sql))
            team_groups_exists = result.scalar()
            
            # Check if teams table exists
            check_teams_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'teams'
            );
            """
            result = conn.execute(text(check_teams_sql))
            teams_exists = result.scalar()
            
            if not team_groups_exists or not teams_exists:
                print("Creating team_groups and teams tables...")
                create_tables_sql = """
                -- 1. Table for Group Hierarchy
                CREATE TABLE IF NOT EXISTS public.team_groups (
                    -- Primary Key
                    group_key INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    group_name TEXT NOT NULL UNIQUE,
                    
                    -- Self-referencing FK for hierarchy (NULL means it's a root group)
                    parent_group_key INT REFERENCES public.team_groups(group_key)
                );

                -- 2. Table for Teams (with one-to-many relationship)
                CREATE TABLE IF NOT EXISTS public.teams (
                    -- Primary Key
                    team_key INT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    team_name TEXT NOT NULL UNIQUE,
                    
                    -- New field for team size
                    number_of_team_members INT NOT NULL DEFAULT 0,
                    
                    -- Foreign Key linking the team to exactly one group
                    group_key INT REFERENCES public.team_groups(group_key),
                    
                    -- AI Insight flag
                    ai_insight BOOLEAN DEFAULT FALSE
                );

                -- Index for fast lookup/joining on the foreign key
                CREATE INDEX IF NOT EXISTS idx_teams_group_key
                ON public.teams (group_key);
                """
                conn.execute(text(create_tables_sql))
                conn.commit()
                print("team_groups and teams tables created successfully")
            else:
                print("team_groups and teams tables already exist")
            
            return True
            
    except Exception as e:
        print(f"Error creating teams and team_groups tables: {e}")
        traceback.print_exc()
        return False


# Test data insertion functions (copied from original)
def create_insight_types_table_if_not_exists(engine=None) -> bool:
    """Create insight_types table if it doesn't exist"""
    # Skip if tables are already initialized (no need to check again)
    global _tables_initialized
    if _tables_initialized:
        return True
    
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create insight_types table")
        return False
    
    try:
        with engine.connect() as conn:
            # Check if table exists
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'insight_types'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating insight_types table...")
                create_table_sql = """
                CREATE TABLE public.insight_types (
                    id SERIAL PRIMARY KEY,
                    insight_type VARCHAR(255) NOT NULL,
                    insight_description TEXT,
                    insight_categories JSONB NOT NULL DEFAULT '[]'::jsonb,
                    active BOOLEAN DEFAULT TRUE NOT NULL,
                    requires_pi BOOLEAN DEFAULT FALSE NOT NULL,
                    requires_team BOOLEAN DEFAULT TRUE NOT NULL,
                    cron_config JSONB DEFAULT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
                );
                
                CREATE INDEX idx_insight_types_type ON public.insight_types(insight_type);
                CREATE INDEX idx_insight_types_categories ON public.insight_types USING GIN (insight_categories);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("Insight types table created successfully")
                
                # Insert default insight types data
                insert_default_insight_types(engine)
            else:
                print("Insight types table already exists")
            
            return True
            
    except Exception as e:
        print(f"Error creating insight_types table: {e}")
        traceback.print_exc()
        return False


def create_report_definitions_table_if_not_exists(engine=None) -> bool:
    """Create report_definitions table if it doesn't exist."""
    global _tables_initialized
    if _tables_initialized:
        return True

    import database_connection

    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot create report_definitions table")
        return False

    try:
        with engine.connect() as conn:
            check_table_sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'report_definitions'
            );
            """
            result = conn.execute(text(check_table_sql))
            table_exists = result.scalar()

            if not table_exists:
                print("Creating report_definitions table...")
                create_table_sql = """
                CREATE TABLE public.report_definitions (
                    report_id VARCHAR(100) PRIMARY KEY,
                    report_name VARCHAR(255) NOT NULL,
                    chart_type VARCHAR(100) NOT NULL,
                    data_source VARCHAR(100) NOT NULL,
                    description TEXT,
                    default_filters JSONB NOT NULL DEFAULT '{}'::jsonb,
                    meta_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX idx_report_definitions_data_source ON public.report_definitions(data_source);
                """
                conn.execute(text(create_table_sql))
                conn.commit()
                print("report_definitions table created successfully")
            else:
                print("report_definitions table already exists")

            insert_default_report_definitions(engine)

            return True

    except Exception as e:
        print(f"Error creating report_definitions table: {e}")
        traceback.print_exc()
        return False


def insert_test_data_for_users():
    """Insert test data for users table"""
    import database_connection
    
    engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot insert test user data")
        return
    
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
    
    engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot insert test prompt data")
        return
    
    try:
        with engine.connect() as conn:
            insert_sql = """
            INSERT INTO public.prompts (email_address, prompt_name, prompt_description, prompt_type, prompt_active) 
            VALUES ('admin@example.com', 'test_prompt', 'This is a test prompt', 'general', TRUE)
            ON CONFLICT (email_address, prompt_name) DO NOTHING;
            """
            conn.execute(text(insert_sql))
            conn.commit()
            print("Test prompt data inserted")
    except Exception as e:
        print(f"Error inserting test prompt data: {e}")


def insert_test_data_for_team_ai_summary_cards():
    """Insert test data for team_ai_summary_cards table"""
    import database_connection
    
    engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot insert test AI card data")
        return
    
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
    
    engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot insert default global settings")
        return
    
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


def insert_default_insight_types(engine=None):
    """Insert default insight types from DEFAULT_INSIGHT_TYPES array"""
    import database_connection
    
    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot insert default insight types")
        return
    
    try:
        with engine.connect() as conn:
            if not DEFAULT_INSIGHT_TYPES:
                print("No default insight types to insert")
                return
            
            # Insert each insight type using parameterized queries
            inserted_count = 0
            import json
            for insight_type_data in DEFAULT_INSIGHT_TYPES:
                try:
                    insight_type = insight_type_data.get("insight_type", "")
                    insight_description = insight_type_data.get("insight_description")
                    insight_categories = insight_type_data.get("insight_categories", [])
                    active = insight_type_data.get("active", True)
                    requires_pi = insight_type_data.get("requires_pi", False)
                    requires_team = insight_type_data.get("requires_team", True)
                    cron_config = insight_type_data.get("cron_config")
                    
                    # Validate categories is a list
                    if not isinstance(insight_categories, list):
                        print(f"Warning: insight_categories must be a list for '{insight_type}', skipping")
                        continue
                    
                    # Check if it already exists to avoid duplicates
                    check_sql = """
                    SELECT EXISTS (
                        SELECT 1 FROM public.insight_types 
                        WHERE insight_type = :insight_type
                    );
                    """
                    result = conn.execute(text(check_sql), {"insight_type": insight_type})
                    exists = result.scalar()
                    
                    if not exists:
                        # Insert using parameterized query with JSONB
                        # Convert list to JSON string for JSONB column
                        insert_sql = """
                        INSERT INTO public.insight_types 
                        (insight_type, insight_description, insight_categories, active, requires_pi, requires_team, cron_config) 
                        VALUES (:insight_type, :insight_description, CAST(:insight_categories AS jsonb), :active, :requires_pi, :requires_team, CAST(:cron_config AS jsonb))
                        """
                        conn.execute(text(insert_sql), {
                            "insight_type": insight_type,
                            "insight_description": insight_description,
                            "insight_categories": json.dumps(insight_categories),
                            "active": active,
                            "requires_pi": requires_pi,
                            "requires_team": requires_team,
                            "cron_config": json.dumps(cron_config) if cron_config else None
                        })
                        inserted_count += 1
                except Exception as e:
                    print(f"Error inserting insight type '{insight_type_data.get('insight_type', 'unknown')}': {e}")
                    continue
            
            if inserted_count > 0:
                conn.commit()
                print(f"Inserted {inserted_count} default insight types")
            else:
                print("No new insight types inserted (all already exist)")
    except Exception as e:
        print(f"Error inserting default insight types: {e}")
        traceback.print_exc()


def insert_default_report_definitions(engine=None):
    """Insert default report definitions."""
    import database_connection
    import json

    if engine is None:
        engine = database_connection.get_db_engine()
    if engine is None:
        print("Warning: Database engine not available, cannot insert default report definitions")
        return

    if not DEFAULT_REPORT_DEFINITIONS:
        print("No default report definitions to insert")
        return

    try:
        with engine.connect() as conn:
            inserted_count = 0
            for definition in DEFAULT_REPORT_DEFINITIONS:
                report_id = definition.get("report_id")
                if not report_id:
                    print("Skipping report definition with missing report_id")
                    continue

                upsert_sql = """
                INSERT INTO public.report_definitions (
                    report_id,
                    report_name,
                    chart_type,
                    data_source,
                    description,
                    default_filters,
                    meta_schema,
                    updated_at
                ) VALUES (
                    :report_id,
                    :report_name,
                    :chart_type,
                    :data_source,
                    :description,
                    CAST(:default_filters AS jsonb),
                    CAST(:meta_schema AS jsonb),
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (report_id) DO UPDATE SET
                    report_name = EXCLUDED.report_name,
                    chart_type = EXCLUDED.chart_type,
                    data_source = EXCLUDED.data_source,
                    description = EXCLUDED.description,
                    default_filters = EXCLUDED.default_filters,
                    meta_schema = EXCLUDED.meta_schema,
                    updated_at = CURRENT_TIMESTAMP
                """

                conn.execute(
                    text(upsert_sql),
                    {
                        "report_id": report_id,
                        "report_name": definition.get("report_name"),
                        "chart_type": definition.get("chart_type"),
                        "data_source": definition.get("data_source"),
                        "description": definition.get("description"),
                        "default_filters": json.dumps(definition.get("default_filters", {})),
                        "meta_schema": json.dumps(definition.get("meta_schema", {})),
                    },
                )
                inserted_count += 1

            conn.commit()
            print(f"Upserted {inserted_count} report definitions")
    except Exception as e:
        print(f"Error inserting default report definitions: {e}")
        traceback.print_exc()


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
    create_teams_and_team_groups_tables_if_not_exists(engine)
    create_team_ai_summary_cards_table_if_not_exists(engine)
    create_pi_ai_summary_cards_table_if_not_exists(engine)
    create_ai_summary_table_if_not_exists(engine)
    create_agent_jobs_table_if_not_exists(engine)
    add_input_sent_column_to_agent_jobs(engine)
    create_transcripts_table_if_not_exists(engine)
    create_recommendations_table_if_not_exists(engine)
    create_chat_history_table_if_not_exists(engine)
    create_insight_types_table_if_not_exists(engine)
    create_report_definitions_table_if_not_exists(engine)
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
    create_teams_and_team_groups_tables_if_not_exists()
    create_team_ai_summary_cards_table_if_not_exists()
    create_pi_ai_summary_cards_table_if_not_exists()
    create_ai_summary_table_if_not_exists()
    create_agent_jobs_table_if_not_exists()
    add_input_sent_column_to_agent_jobs()  # Temporary function to add input_sent column
    create_transcripts_table_if_not_exists()
    create_recommendations_table_if_not_exists()
    create_chat_history_table_if_not_exists()
    create_insight_types_table_if_not_exists()
    create_report_definitions_table_if_not_exists()
    _tables_initialized = True
    print("=== DATABASE TABLES INITIALIZATION COMPLETE ===")
