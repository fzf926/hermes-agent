-- Add chat_sql_execution for DBOps SQL audit (run after chat_schema.sql)

CREATE TABLE IF NOT EXISTS chat_sql_execution (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_id BIGINT UNSIGNED NOT NULL,
  turn_id BIGINT UNSIGNED DEFAULT NULL,
  user_id VARCHAR(64) NOT NULL,
  tool_call_id VARCHAR(128) DEFAULT NULL,
  sql_content MEDIUMTEXT NOT NULL,
  db_name VARCHAR(128) NOT NULL,
  instance_name VARCHAR(128) NOT NULL,
  status ENUM('success','error') NOT NULL DEFAULT 'success',
  error_message VARCHAR(512) DEFAULT NULL,
  query_time_ms DECIMAL(12,3) DEFAULT NULL,
  row_count INT UNSIGNED DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_session_turn (session_id, turn_id),
  KEY idx_user_time (user_id, created_at DESC),
  CONSTRAINT fk_sql_session FOREIGN KEY (session_id) REFERENCES chat_session(id),
  CONSTRAINT fk_sql_turn FOREIGN KEY (turn_id) REFERENCES chat_turn(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
