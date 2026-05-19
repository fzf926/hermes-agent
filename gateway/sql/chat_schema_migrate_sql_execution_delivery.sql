-- Extend chat_sql_execution for volume routing and generation_reason.
-- Run once on existing MySQL chat databases.

ALTER TABLE chat_sql_execution
  ADD COLUMN total_row_count INT NULL COMMENT 'COUNT(*) before delivery routing' AFTER row_count,
  ADD COLUMN delivery_mode VARCHAR(32) NULL COMMENT 'inline|excel|dbops_export|sql_only' AFTER total_row_count,
  ADD COLUMN download_url VARCHAR(2048) NULL AFTER delivery_mode,
  ADD COLUMN generation_reason TEXT NULL AFTER download_url,
  ADD COLUMN export_uid VARCHAR(64) NULL AFTER generation_reason,
  ADD COLUMN dbops_export_task_id VARCHAR(64) NULL AFTER export_uid;

CREATE INDEX idx_chat_sql_export_uid ON chat_sql_execution (export_uid);
