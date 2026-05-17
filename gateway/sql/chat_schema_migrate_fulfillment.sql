-- Add fulfillment judge columns to chat_turn (MySQL 8+)
-- Run after chat_schema.sql on existing databases.

ALTER TABLE chat_turn
  ADD COLUMN fulfillment_status ENUM('satisfied','partial','unsatisfied','unknown') DEFAULT NULL
    COMMENT 'LLM judge: whether user intent was met' AFTER error_message,
  ADD COLUMN fulfillment_reason VARCHAR(512) DEFAULT NULL
    COMMENT 'LLM judge explanation' AFTER fulfillment_status,
  ADD COLUMN is_final TINYINT(1) DEFAULT NULL
    COMMENT '1=no follow-up needed for this ask' AFTER fulfillment_reason;
