-- ALTER TABLE command to add group_name column to agent_jobs table
-- Run this command manually on your existing database

ALTER TABLE public.agent_jobs 
ADD COLUMN group_name VARCHAR(255);

-- Verify the column was added
-- SELECT column_name, data_type, is_nullable 
-- FROM information_schema.columns 
-- WHERE table_name = 'agent_jobs' AND column_name = 'group_name';

