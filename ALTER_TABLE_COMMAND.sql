-- ALTER TABLE command to add group_name column to ai_summary table
-- Run this command manually on your existing database

ALTER TABLE public.ai_summary 
ADD COLUMN group_name VARCHAR(255);

-- Verify the column was added
-- SELECT column_name, data_type, is_nullable 
-- FROM information_schema.columns 
-- WHERE table_name = 'ai_summary' AND column_name = 'group_name';

