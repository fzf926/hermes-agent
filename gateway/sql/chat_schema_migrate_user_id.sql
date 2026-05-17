-- Migrate user_id from BIGINT to VARCHAR(64) for flexible business IDs
ALTER TABLE chat_session MODIFY user_id VARCHAR(64) NOT NULL COMMENT '业务用户ID';
ALTER TABLE chat_session MODIFY tenant_id VARCHAR(64) DEFAULT NULL;
ALTER TABLE chat_message MODIFY user_id VARCHAR(64) NOT NULL;
ALTER TABLE chat_message MODIFY tenant_id VARCHAR(64) DEFAULT NULL;
ALTER TABLE chat_turn MODIFY user_id VARCHAR(64) NOT NULL;
ALTER TABLE chat_turn MODIFY tenant_id VARCHAR(64) DEFAULT NULL;
