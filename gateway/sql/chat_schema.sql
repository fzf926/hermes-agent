-- Hermes Agent chat persistence (MySQL 8+)
-- Database: hermes_agent

CREATE TABLE IF NOT EXISTS chat_session (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_uid VARCHAR(64) NOT NULL COMMENT '业务侧会话ID',
  hermes_session_id VARCHAR(128) DEFAULT NULL COMMENT 'Hermes session_id',
  user_id VARCHAR(64) NOT NULL COMMENT '业务用户ID',
  tenant_id VARCHAR(64) DEFAULT NULL,
  channel VARCHAR(32) DEFAULT 'api_server',
  conversation_type TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1=history 2=favorite 3=direct',
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
  KEY idx_user_started (user_id, started_at DESC),
  KEY idx_user_conv_updated (user_id, conversation_type, updated_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chat_message (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  message_uid VARCHAR(64) NOT NULL,
  session_id BIGINT UNSIGNED NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  tenant_id VARCHAR(64) DEFAULT NULL,
  turn_no INT UNSIGNED NOT NULL,
  role ENUM('system','user','assistant','tool') NOT NULL,
  conversation_type TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1=history 2=favorite 3=direct',
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
  fulfillment_status ENUM('satisfied','partial','unsatisfied','unknown') DEFAULT NULL
    COMMENT 'LLM judge: whether user intent was met',
  fulfillment_reason VARCHAR(512) DEFAULT NULL COMMENT 'LLM judge explanation',
  is_final TINYINT(1) DEFAULT NULL COMMENT '1=no follow-up needed for this ask',
  feedback_score TINYINT DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  UNIQUE KEY uk_session_turn (session_id, turn_no),
  KEY idx_user_created (user_id, created_at DESC),
  CONSTRAINT fk_turn_session FOREIGN KEY (session_id) REFERENCES chat_session(id),
  CONSTRAINT fk_turn_qmsg FOREIGN KEY (question_message_id) REFERENCES chat_message(id),
  CONSTRAINT fk_turn_amsg FOREIGN KEY (answer_message_id) REFERENCES chat_message(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chat_tool_call (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  session_id BIGINT UNSIGNED NOT NULL,
  turn_id BIGINT UNSIGNED DEFAULT NULL,
  message_id BIGINT UNSIGNED DEFAULT NULL COMMENT '关联assistant/tool消息',
  user_id VARCHAR(64) NOT NULL,
  tool_name VARCHAR(128) NOT NULL,
  tool_args JSON DEFAULT NULL,
  tool_result JSON DEFAULT NULL,
  status ENUM('success','failed','timeout') NOT NULL,
  latency_ms INT UNSIGNED DEFAULT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (id),
  KEY idx_session_time (session_id, created_at DESC),
  KEY idx_user_time (user_id, created_at DESC),
  KEY idx_tool_time (tool_name, created_at DESC),
  CONSTRAINT fk_tool_session FOREIGN KEY (session_id) REFERENCES chat_session(id),
  CONSTRAINT fk_tool_turn FOREIGN KEY (turn_id) REFERENCES chat_turn(id),
  CONSTRAINT fk_tool_msg FOREIGN KEY (message_id) REFERENCES chat_message(id)
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
