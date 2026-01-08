-- ALTER TABLE command to remove goal_number from unique constraint
-- This allows user goals (ai=false) to have duplicates
-- goal_number is still used in application logic for generate endpoint matching

-- Drop the existing unique index
DROP INDEX IF EXISTS public.idx_pi_goals_unique;

-- Create new unique index without goal_number
CREATE UNIQUE INDEX idx_pi_goals_unique ON public.pi_goals(
    pi_name, 
    goal_type, 
    COALESCE(team_name, ''), 
    COALESCE(group_name, ''),
    ai
);

