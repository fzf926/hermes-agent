-- SQL favorites: bookmark satisfied turns with their chat_sql_execution rows.
-- Run after chat_schema.sql, chat_schema_migrate_sql_execution.sql, chat_schema_migrate_fulfillment.sql

CREATE TABLE IF NOT EXISTS chat_sql_favorite (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  favorite_uid VARCHAR(64) NOT NULL COMMENT 'Public favorite id for API',
  user_id VARCHAR(64) NOT NULL,
  session_id BIGINT UNSIGNED NOT NULL,
  turn_id BIGINT UNSIGNED NOT NULL,
  turn_no INT UNSIGNED NOT NULL,
  hermes_response_id VARCHAR(128) NOT NULL COMMENT 'Assistant response id from chat_message',
  question_summary VARCHAR(512) DEFAULT NULL COMMENT 'AI summary of user question',
  answer_summary VARCHAR(512) DEFAULT NULL COMMENT 'AI summary of assistant answer',
  fulfillment_status VARCHAR(32) DEFAULT NULL,
  fulfillment_reason VARCHAR(512) DEFAULT NULL,
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1=active 0=removed',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_favorite_uid (favorite_uid),
  UNIQUE KEY uk_user_response (user_id, hermes_response_id),
  KEY idx_user_created (user_id, created_at DESC),
  CONSTRAINT fk_fav_session FOREIGN KEY (session_id) REFERENCES chat_session(id),
  CONSTRAINT fk_fav_turn FOREIGN KEY (turn_id) REFERENCES chat_turn(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chat_sql_favorite_item (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  favorite_id BIGINT UNSIGNED NOT NULL,
  sql_execution_id BIGINT UNSIGNED NOT NULL,
  sort_order INT UNSIGNED NOT NULL DEFAULT 0,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_favorite_sql (favorite_id, sql_execution_id),
  KEY idx_sql_execution (sql_execution_id),
  CONSTRAINT fk_fav_item_favorite FOREIGN KEY (favorite_id) REFERENCES chat_sql_favorite(id) ON DELETE CASCADE,
  CONSTRAINT fk_fav_item_sql FOREIGN KEY (sql_execution_id) REFERENCES chat_sql_execution(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Speed up favorite creation lookup by hermes_response_id
ALTER TABLE chat_message ADD KEY idx_hermes_response (hermes_response_id);
