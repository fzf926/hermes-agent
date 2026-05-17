-- Hermes Agent chat persistence (MySQL 8+)
-- Database: hermes_agent

CREATE TABLE IF NOT EXISTS chat_session (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_uid VARCHAR(64) NOT NULL COMMENT '业务侧会话ID',
  hermes_session_id VARCHAR(128) DEFAULT NULL COMMENT 'Hermes session_id',
  user_id VARCHAR(64) NOT NULL COMMENT '业务用户ID',
  tenant_id VARCHAR(64) DEFAULT NULL,
  channel VARCHAR(32) DEFAULT 'api_server',
  title VARCHAR(255) DEFAULT NULL,
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1-active 2-closed 3-archived',
  started_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  ended_at DATETIME(3) DEFAULT NULL,
  ext JSON DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_session_uid (session_uid),
  KEY idx_hermes_session_user (hermes_session_id, user_id),
  KEY idx_user_started (user_id, started_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chat_message (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  message_uid VARCHAR(64) NOT NULL,
  session_id BIGINT UNSIGNED NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(64) DEFAULT NULL,
  turn_no INT UNSIGNED NOT NULL,
  role ENUM('system','user','assistant','tool') NOT NULL,
  content LONGTEXT NOT NULL,
  content_type VARCHAR(32) DEFAULT 'text',
  provider VARCHAR(64) DEFAULT NULL,
  model VARCHAR(128) DEFAULT NULL,
  prompt_tokens INT UNSIGNED DEFAULT 0,
  completion_tokens INT UNSIGNED DEFAULT 0,
  total_tokens INT UNSIGNED DEFAULT 0,
  latency_ms INT UNSIGNED DEFAULT NULL,
  hermes_response_id VARCHAR(128) DEFAULT NULL,
  hermes_run_id VARCHAR(128) DEFAULT NULL,
  parent_message_id BIGINT UNSIGNED DEFAULT NULL,
  is_final TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_message_uid (message_uid),
  KEY idx_session_turn (session_id, turn_no, id),
  KEY idx_user_time (user_id, created_at DESC),
  CONSTRAINT fk_msg_session FOREIGN KEY (session_id) REFERENCES chat_session(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chat_turn (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_id BIGINT UNSIGNED NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(64) DEFAULT NULL,
  turn_no INT UNSIGNED NOT NULL,
  question_message_id BIGINT UNSIGNED NOT NULL,
  answer_message_id BIGINT UNSIGNED DEFAULT NULL,
  question_text MEDIUMTEXT NOT NULL,
  answer_text MEDIUMTEXT DEFAULT NULL,
  status ENUM('answered','timeout','error','interrupted') NOT NULL DEFAULT 'answered',
  error_code VARCHAR(64) DEFAULT NULL,
  error_message VARCHAR(512) DEFAULT NULL,
  feedback_score TINYINT DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_session_turn (session_id, turn_no),
  KEY idx_user_created (user_id, created_at DESC),
  CONSTRAINT fk_turn_session FOREIGN KEY (session_id) REFERENCES chat_session(id),
  CONSTRAINT fk_turn_qmsg FOREIGN KEY (question_message_id) REFERENCES chat_message(id),
  CONSTRAINT fk_turn_amsg FOREIGN KEY (answer_message_id) REFERENCES chat_message(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
