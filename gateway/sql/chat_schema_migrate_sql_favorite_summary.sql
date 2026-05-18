-- Rename favorite Q&A columns to summaries (if you ran an earlier favorite migration).
-- Safe to run: skips when columns already renamed.

-- question_text -> question_summary
SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'chat_sql_favorite'
    AND COLUMN_NAME = 'question_text'
);
SET @sql := IF(
  @col_exists > 0,
  'ALTER TABLE chat_sql_favorite CHANGE COLUMN question_text question_summary VARCHAR(512) DEFAULT NULL COMMENT ''AI summary of user question''',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- answer_text -> answer_summary
SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'chat_sql_favorite'
    AND COLUMN_NAME = 'answer_text'
);
SET @sql := IF(
  @col_exists > 0,
  'ALTER TABLE chat_sql_favorite CHANGE COLUMN answer_text answer_summary VARCHAR(512) DEFAULT NULL COMMENT ''AI summary of assistant answer''',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
