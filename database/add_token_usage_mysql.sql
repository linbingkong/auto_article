-- 上线前以 MySQL 管理员执行；应用账号只需 SELECT/INSERT/UPDATE/DELETE。
USE wechat_agent;
CREATE TABLE IF NOT EXISTS llm_token_usage (
 id VARCHAR(64) NOT NULL, task_id VARCHAR(64) NOT NULL, article_id VARCHAR(64) NULL,
 user_id VARCHAR(64) NOT NULL, username VARCHAR(64) NOT NULL DEFAULT '',
 generation_mode VARCHAR(24) NOT NULL DEFAULT 'platform', stage VARCHAR(64) NOT NULL DEFAULT 'unknown',
 provider VARCHAR(128) NOT NULL DEFAULT '', model VARCHAR(128) NOT NULL DEFAULT '',
 prompt_tokens BIGINT NOT NULL DEFAULT 0, completion_tokens BIGINT NOT NULL DEFAULT 0,
 total_tokens BIGINT NOT NULL DEFAULT 0, cached_tokens BIGINT NOT NULL DEFAULT 0,
 reasoning_tokens BIGINT NOT NULL DEFAULT 0, estimated TINYINT NOT NULL DEFAULT 0,
 input_price_per_million DECIMAL(12,4) NOT NULL DEFAULT 0,
 output_price_per_million DECIMAL(12,4) NOT NULL DEFAULT 0,
 estimated_cost_micro_yuan BIGINT NOT NULL DEFAULT 0, created_at DATETIME NOT NULL,
 PRIMARY KEY (id), KEY idx_llm_token_usage_task (task_id),
 KEY idx_llm_token_usage_created (created_at),
 KEY idx_llm_token_usage_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
GRANT SELECT, INSERT, UPDATE, DELETE ON wechat_agent.llm_token_usage TO 'wechat_agent_app'@'localhost';
FLUSH PRIVILEGES;
