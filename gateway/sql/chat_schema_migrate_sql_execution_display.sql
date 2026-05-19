-- Persist SQL display payloads for turns API parity with /v1/responses.

ALTER TABLE chat_sql_execution
  ADD COLUMN user_display MEDIUMTEXT NULL COMMENT 'Markdown display block for one SQL execution' AFTER dbops_export_task_id,
  ADD COLUMN result_table MEDIUMTEXT NULL COMMENT 'Markdown table only' AFTER user_display;
