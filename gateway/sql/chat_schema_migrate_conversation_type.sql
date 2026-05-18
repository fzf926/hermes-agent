-- Add conversation_type to chat_session and chat_message.
-- 1=history, 2=favorite, 3=direct SQL. One session has a single type for its lifetime.

ALTER TABLE chat_session
  ADD COLUMN conversation_type TINYINT UNSIGNED NOT NULL DEFAULT 1
    COMMENT '1=history 2=favorite 3=direct' AFTER channel;

ALTER TABLE chat_message
  ADD COLUMN conversation_type TINYINT UNSIGNED NOT NULL DEFAULT 1
    COMMENT '1=history 2=favorite 3=direct' AFTER role;

ALTER TABLE chat_session
  ADD KEY idx_user_conv_updated (user_id, conversation_type, updated_at DESC);
