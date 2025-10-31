-- SQL to drop the unique constraint from existing recommendations table
-- Run this against your database before the code change takes effect

-- Option 1: Drop by constraint name (from error message)
ALTER TABLE public.recommendations
DROP CONSTRAINT IF EXISTS recommendations_team_name_date_key;

-- Option 2: Find and drop by constraint pattern (if name is slightly different)
-- First, find the exact constraint name:
-- SELECT constraint_name 
-- FROM information_schema.table_constraints 
-- WHERE table_schema = 'public' 
--   AND table_name = 'recommendations' 
--   AND constraint_type = 'UNIQUE';

-- Then drop it:
-- ALTER TABLE public.recommendations
-- DROP CONSTRAINT <constraint_name_from_query_above>;

