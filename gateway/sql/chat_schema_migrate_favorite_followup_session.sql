-- Store the latest Hermes session id for favorite-based follow-up chat (conversation_type=2).

ALTER TABLE chat_sql_favorite
  ADD COLUMN followup_hermes_session_id VARCHAR(128) DEFAULT NULL
    COMMENT 'Latest hermes_session_id for chats started from this favorite'
    AFTER hermes_response_id;

ALTER TABLE chat_sql_favorite
  ADD KEY idx_user_followup_session (user_id, followup_hermes_session_id);
