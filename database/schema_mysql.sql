-- =====================================================================
-- 观思辩明 · 生产 MySQL 初始化脚本（MySQL 5.7+，MySQL 8.0 推荐）
-- 目标：把用户、额度、支付、审计、文章记录等线上数据从 SQLite
--       （output/auth.db、output/history.db）迁移到外部 MySQL，
--       使代码更新与用户数据彻底分离。
-- 说明：
--   1. 字段与现有 SQLite 结构一一对应，仅做 MySQL 化调整。
--   2. SQLite 的部分唯一索引（WHERE ... <> ''）在 MySQL 中改用
--      生成列 + 唯一索引实现。
--   3. 时间字段统一 DATETIME；SQLite 中的 ISO 字符串（含 T 分隔符）
--      可以直接写入 DATETIME。
-- =====================================================================

CREATE DATABASE IF NOT EXISTS wechat_agent
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
USE wechat_agent;

-- ---------------------------------------------------------------------
-- 1. 用户表（含公开注册申请与人工审批字段）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id                      VARCHAR(64)  NOT NULL,
    username                VARCHAR(64)  NOT NULL,
    password_hash           VARCHAR(128) NOT NULL,              -- bcrypt
    role                    VARCHAR(16)  NOT NULL DEFAULT 'viewer',
    status                  VARCHAR(24)  NOT NULL DEFAULT 'active',
    phone_hash              CHAR(64)     NULL,                  -- HMAC-SHA256 十六进制
    phone_last4             VARCHAR(8)   NULL,
    phone                   VARCHAR(32)  NULL,                  -- 注册审批核对手机号
    registration_token_hash CHAR(64)     NULL,
    applied_at              DATETIME     NULL,
    reviewed_at             DATETIME     NULL,
    reviewed_by             VARCHAR(64)  NULL,
    review_note             VARCHAR(512) NULL,
    created_at              DATETIME     NOT NULL,
    updated_at              DATETIME     NOT NULL,
    -- 部分唯一索引的 MySQL 等价实现：空值/空串归一为 NULL
    phone_hash_uk           CHAR(64) GENERATED ALWAYS AS
                            (IF(phone_hash IS NULL OR phone_hash = '', NULL, phone_hash)) STORED,
    reg_token_uk            CHAR(64) GENERATED ALWAYS AS
                            (IF(registration_token_hash IS NULL OR registration_token_hash = '', NULL, registration_token_hash)) STORED,
    PRIMARY KEY (id),
    UNIQUE KEY uk_users_username (username),
    UNIQUE KEY uk_users_phone_hash (phone_hash_uk),
    UNIQUE KEY uk_users_registration_token (reg_token_uk),
    KEY idx_users_status_applied (status, applied_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 2. 个人 API 配置（LLM / 搜索 / 文生图，Fernet 加密 JSON）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_api_configs (
    user_id           VARCHAR(64) NOT NULL,
    encrypted_payload TEXT        NOT NULL,
    llm_configured    TINYINT     NOT NULL DEFAULT 0,
    search_configured TINYINT     NOT NULL DEFAULT 0,
    image_configured  TINYINT     NOT NULL DEFAULT 0,
    updated_at        DATETIME    NOT NULL,
    PRIMARY KEY (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 3. 生成额度账户（试用 + 付费）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_generation_accounts (
    user_id        VARCHAR(64) NOT NULL,
    trial_total    INT NOT NULL DEFAULT 0,
    trial_used     INT NOT NULL DEFAULT 0,
    trial_reserved INT NOT NULL DEFAULT 0,
    paid_credits   INT NOT NULL DEFAULT 0,
    paid_reserved  INT NOT NULL DEFAULT 0,
    created_at     DATETIME NOT NULL,
    updated_at     DATETIME NOT NULL,
    PRIMARY KEY (user_id),
    CONSTRAINT chk_uga_nonneg CHECK (
        trial_total >= 0 AND trial_used >= 0 AND trial_reserved >= 0
        AND paid_credits >= 0 AND paid_reserved >= 0
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 4. 试用额度身份表（同一手机号只赠送一次）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trial_identities (
    phone_hash    CHAR(64)    NOT NULL,
    user_id       VARCHAR(64) NOT NULL,
    granted_count INT         NOT NULL,
    granted_at    DATETIME    NOT NULL,
    PRIMARY KEY (phone_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 5. 生成用量流水（预留 → 结算/释放）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS generation_usage (
    id             VARCHAR(64) NOT NULL,
    task_id        VARCHAR(64) NOT NULL,
    user_id        VARCHAR(64) NOT NULL,
    article_id     VARCHAR(64) NULL,
    source         ENUM('trial','paid') NOT NULL,
    status         ENUM('reserved','consumed','released') NOT NULL,
    unit_price_fen INT NOT NULL DEFAULT 0,
    failure_reason VARCHAR(1024) NULL,
    created_at     DATETIME NOT NULL,
    settled_at     DATETIME NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_generation_usage_task (task_id),
    KEY idx_generation_usage_user_created (user_id, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 6. 额度人工调整流水
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS credit_adjustments (
    id               VARCHAR(64) NOT NULL,
    user_id          VARCHAR(64) NOT NULL,
    delta            INT         NOT NULL,
    balance_after    INT         NOT NULL,
    reason           VARCHAR(1024) NOT NULL,
    operator_user_id VARCHAR(64) NOT NULL,
    created_at       DATETIME    NOT NULL,
    PRIMARY KEY (id),
    KEY idx_credit_adjustments_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 7. 支付订单（微信 Native）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payment_orders (
    id                      VARCHAR(64) NOT NULL,
    order_no                VARCHAR(64) NOT NULL,
    user_id                 VARCHAR(64) NOT NULL,
    amount_fen              INT NOT NULL,
    credit_count            INT NOT NULL,
    unit_price_fen          INT NOT NULL,
    status                  ENUM('pending','paid','closed','failed','refunded') NOT NULL,
    provider                VARCHAR(32) NOT NULL,
    code_url                VARCHAR(512) NULL,
    provider_transaction_id VARCHAR(128) NULL,
    created_at              DATETIME NOT NULL,
    expires_at              DATETIME NOT NULL,
    paid_at                 DATETIME NULL,
    updated_at              DATETIME NOT NULL,
    transaction_uk          VARCHAR(128) GENERATED ALWAYS AS
                            (IF(provider_transaction_id IS NULL OR provider_transaction_id = '', NULL, provider_transaction_id)) STORED,
    PRIMARY KEY (id),
    UNIQUE KEY uk_payment_orders_no (order_no),
    UNIQUE KEY uk_payment_orders_transaction (transaction_uk),
    KEY idx_payment_orders_user_created (user_id, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 8. 支付回调事件（幂等去重）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payment_events (
    event_id       VARCHAR(128) NOT NULL,
    order_no       VARCHAR(64)  NOT NULL,
    transaction_id VARCHAR(128) NULL,
    payload_hash   CHAR(64)     NOT NULL,
    created_at     DATETIME     NOT NULL,
    PRIMARY KEY (event_id),
    KEY idx_payment_events_order (order_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 9. 文章访问权限（共享设置）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS article_permissions (
    article_id VARCHAR(64) NOT NULL,
    user_id    VARCHAR(64) NOT NULL,
    can_view   TINYINT NOT NULL DEFAULT 1,
    can_edit   TINYINT NOT NULL DEFAULT 0,
    can_push   TINYINT NOT NULL DEFAULT 0,
    granted_by VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (article_id, user_id),
    KEY idx_article_permissions_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 10. 审计日志
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id          VARCHAR(64) NOT NULL,
    user_id     VARCHAR(64) NULL,
    username    VARCHAR(64) NULL,
    action      VARCHAR(128) NOT NULL,
    resource    VARCHAR(64) NULL,
    resource_id VARCHAR(128) NULL,
    detail      TEXT NULL,
    ip          VARCHAR(64) NULL,
    user_agent  VARCHAR(512) NULL,
    created_at  DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_audit_user_time (user_id, created_at),
    KEY idx_audit_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 11. 运行与发布记录（原 output/history.db 的 articles 表）
--     7 天选题去重依赖 (topic_norm, created_at)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS articles (
    id             BIGINT NOT NULL AUTO_INCREMENT,
    topic          VARCHAR(512) NOT NULL,
    topic_norm     VARCHAR(64)  NOT NULL,
    source         VARCHAR(64)  NULL,
    score          DOUBLE       NULL,
    article_title  VARCHAR(256) NULL,
    draft_media_id VARCHAR(128) NULL,
    status         VARCHAR(32)  NOT NULL,
    output_dir     VARCHAR(512) NULL,
    read_count     INT NULL,
    like_count     INT NULL,
    share_count    INT NULL,
    created_at     DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_topic_norm_time (topic_norm, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------
-- 12. LLM Token 用量（每次模型响应一条，用于管理员成本看板）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_token_usage (
    id                        VARCHAR(64)  NOT NULL,
    task_id                   VARCHAR(64)  NOT NULL,
    article_id                VARCHAR(64)  NULL,
    user_id                   VARCHAR(64)  NOT NULL,
    username                  VARCHAR(64)  NOT NULL DEFAULT '',
    generation_mode           VARCHAR(24)  NOT NULL DEFAULT 'platform',
    stage                     VARCHAR(64)  NOT NULL DEFAULT 'unknown',
    provider                  VARCHAR(128) NOT NULL DEFAULT '',
    model                     VARCHAR(128) NOT NULL DEFAULT '',
    prompt_tokens             BIGINT NOT NULL DEFAULT 0,
    completion_tokens         BIGINT NOT NULL DEFAULT 0,
    total_tokens              BIGINT NOT NULL DEFAULT 0,
    cached_tokens             BIGINT NOT NULL DEFAULT 0,
    reasoning_tokens          BIGINT NOT NULL DEFAULT 0,
    estimated                 TINYINT NOT NULL DEFAULT 0,
    input_price_per_million   DECIMAL(12,4) NOT NULL DEFAULT 0,
    output_price_per_million  DECIMAL(12,4) NOT NULL DEFAULT 0,
    estimated_cost_micro_yuan BIGINT NOT NULL DEFAULT 0,
    created_at                DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_llm_token_usage_task (task_id),
    KEY idx_llm_token_usage_created (created_at),
    KEY idx_llm_token_usage_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
