-- Store every Hermes tool call linked to the persisted chat turn.
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
