# LLM Context Flow Summary

## Overview
This document shows the flow, SELECT statements, and what gets sent to the LLM for Insights and Recommendations.

---

## 1. Team_insights Flow

### Flow:
1. **Fetch AI Card** → `get_team_ai_card_by_id(insights_id)`
2. **Extract** → `source_job_id` from card
3. **Fetch input_sent** → `get_formatted_job_data_for_llm_followup_insight(insights_id, source_job_id)`
4. **Fetch content_intro** → DB prompt `"Team_insights-Content"` (or fallback)
5. **Extract** → `description` from card
6. **Build** → `conversation_context = content_intro + '\n\n' + description + '\n\n' + input_sent`

### SELECT Statements:

#### Step 1: Get AI Card
```sql
SELECT * 
FROM ai_summary 
WHERE id = :id
```
**Returns:** All card fields including `source_job_id`, `description`

#### Step 2: Get input_sent
```sql
SELECT aj.input_sent
FROM ai_summary ai
JOIN agent_jobs aj ON ai.source_job_id = aj.job_id
WHERE ai.id = :card_id AND aj.job_id = :job_id
```
**Returns:** Only `input_sent` (prompt removed: everything after `"-- Prompt --"` is stripped)

### What Gets Sent to LLM:
```
conversation_context = 
  [Content Intro from DB prompt "Team_insights-Content" OR fallback text]
  
  [Description from ai_summary.description]
  
  [input_sent from agent_jobs.input_sent (with "-- Prompt --" removed)]
```

---

## 2. PI_insights Flow

### Flow:
1. **Fetch PI AI Card** → `get_pi_ai_card_by_id(insights_id)`
2. **Extract** → `source_job_id` from card
3. **Fetch input_sent** → `get_formatted_job_data_for_llm_followup_insight(insights_id, source_job_id)`
4. **Fetch content_intro** → DB prompt `"PI_insights-Content"` (or fallback)
5. **Extract** → `description` from card
6. **Build** → `conversation_context = content_intro + '\n\n' + description + '\n\n' + input_sent`

### SELECT Statements:

#### Step 1: Get PI AI Card
```sql
SELECT * 
FROM ai_summary 
WHERE id = :id
```
**Returns:** All card fields including `source_job_id`, `description`

#### Step 2: Get input_sent
```sql
SELECT aj.input_sent
FROM ai_summary ai
JOIN agent_jobs aj ON ai.source_job_id = aj.job_id
WHERE ai.id = :card_id AND aj.job_id = :job_id
```
**Returns:** Only `input_sent` (prompt removed: everything after `"-- Prompt --"` is stripped)

### What Gets Sent to LLM:
```
conversation_context = 
  [Content Intro from DB prompt "PI_insights-Content" OR fallback text]
  
  [Description from ai_summary.description]
  
  [input_sent from agent_jobs.input_sent (with "-- Prompt --" removed)]
```

---

## 3. Recommendation_reason Flow

### Flow:
1. **Fetch Recommendation** → `get_recommendation_by_id(recommendation_id)`
2. **Extract** → `source_job_id` from recommendation
3. **Fetch input_sent** → `get_formatted_job_data_for_llm_followup_recommendation(recommendation_id, source_job_id)`
4. **Fetch content_intro** → DB prompt `"Recommendation_reason-Content"` (or fallback)
5. **Extract** → `action_text` from recommendation
6. **Build** → `conversation_context = content_intro + '\n\n' + action_text + '\n\n' + input_sent`

### SELECT Statements:

#### Step 1: Get Recommendation
```sql
SELECT * 
FROM recommendations 
WHERE id = :id
```
**Returns:** All recommendation fields including `source_job_id`, `action_text`

#### Step 2: Get input_sent
```sql
SELECT aj.input_sent
FROM recommendations rec
JOIN agent_jobs aj ON rec.source_job_id = aj.job_id
WHERE rec.id = :recommendation_id
```
**Returns:** Only `input_sent` (prompt removed: everything after `"-- Prompt --"` is stripped)

### What Gets Sent to LLM:
```
conversation_context = 
  [Content Intro from DB prompt "Recommendation_reason-Content" OR fallback text]
  
  [Action Text from recommendations.action_text]
  
  [input_sent from agent_jobs.input_sent (with "-- Prompt --" removed)]
```

---

## Final Payload Sent to LLM Service

```json
{
  "conversation_id": <int>,
  "question": "<user's question>",
  "history_json": { ... },
  "username": "<user_id>",
  "selected_team": "<team_name>",
  "selected_pi": "<pi_name>",
  "chat_type": "<Team_insights|PI_insights|Recommendation_reason>",
  "conversation_context": "<built string from above flows>",
  "system_message": "<from DB prompt '{chat_type}-System' or default>"
}
```

---

## Key Points:

1. **No full_information** is sent anymore - only `input_sent`
2. **Description/action_text** is extracted from the card/recommendation object (already fetched)
3. **input_sent** has `"-- Prompt --"` and everything after removed (temporary fix)
4. **conversation_context** structure: `content_intro + '\n\n' + description/action_text + '\n\n' + input_sent`
5. All data comes from:
   - `ai_summary` table (for insights)
   - `recommendations` table (for recommendations)
   - `agent_jobs` table (for input_sent)
   - `prompts` table (for content_intro and system_message)

