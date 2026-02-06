#!/usr/bin/env python3
"""
Script to update all insight types' cron_config to hour: 20, minute: 0
This updates the database directly to match the changes in database_table_creation.py
"""
import sys
import os
import json

# Add the backend directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database_connection import get_db_engine
from sqlalchemy import text

def update_insight_cron_configs():
    """Update all insight types' cron_config to hour: 20, minute: 0"""
    engine = get_db_engine()
    if engine is None:
        print("Error: Could not get database engine")
        return False
    
    try:
        with engine.connect() as conn:
            # Get all insight types
            result = conn.execute(text("SELECT id, insight_type, cron_config FROM public.insight_types"))
            rows = result.fetchall()
            
            updated_count = 0
            for row in rows:
                insight_id = row[0]
                insight_type = row[1]
                current_cron = row[2]
                
                # Determine new cron_config based on insight type
                if insight_type == "PI Sync":
                    new_cron = {"day_of_week": "sun,mon,tue,wed,thu", "hour": 20, "minute": 0}
                else:
                    new_cron = {"hour": 20, "minute": 0}
                
                # Update the cron_config
                update_sql = text("""
                    UPDATE public.insight_types 
                    SET cron_config = CAST(:cron_config AS jsonb),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                """)
                
                conn.execute(update_sql, {
                    "id": insight_id,
                    "cron_config": json.dumps(new_cron)
                })
                updated_count += 1
                print(f"Updated {insight_type} (id: {insight_id}) cron_config to {new_cron}")
            
            conn.commit()
            print(f"\n✅ Successfully updated {updated_count} insight types")
            return True
            
    except Exception as e:
        print(f"❌ Error updating insight types: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🔄 Updating insight types cron_config to hour: 20, minute: 0...")
    success = update_insight_cron_configs()
    sys.exit(0 if success else 1)

