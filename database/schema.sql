-- =====================================================================
-- 观思辩明 · 用户信息数据库初始化脚本
-- 数据库：SQLite（默认文件 output/auth.db）
-- 说明：
--   1. 全部使用 CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS，
--      可以在任何已有库上重复执行，不会影响现有数据。
--   2. 应用启动时也会自动补建缺失的表和索引，本脚本用于外部预建、
--      备份校验或迁移评审。
--   3. 所有时间字段统一为 ISO 8601 文本（datetime.now().isoformat()）。
--   4. 密码使用 bcrypt 哈希（password_hash 列），手机号只保存 HMAC-SHA256
--      摘要（phone_hash）与末四位（phone_last4），明文 phone 仅为注册审批
--      阶段的管理员核对字段。
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. 用户表（含公开注册申请与人工审批字段）
--    role:    admin / editor / creator / viewer
--    status:  pending_approval / active / rejected / disabled
--    公开注册的申请、审批记录与正式账号共用本表，以 phone_hash 区分：
--    phone_hash 非空即代表该行来自公开注册流程。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id                      TEXT PRIMARY KEY,              -- uuid4().hex
    username                TEXT UNIQUE NOT NULL,          -- 3-32 位字母数字
    password_hash           TEXT NOT NULL,                 -- bcrypt
    role                    TEXT NOT NULL DEFAULT 'viewer',
    status                  TEXT NOT NULL DEFAULT 'active',
    phone_hash              TEXT,                          -- HMAC-SHA256(phone, PHONE_HASH_SECRET)
    phone_last4             TEXT,                          -- 手机号末四位，用于脱敏展示
    phone                   TEXT DEFAULT '',               -- 注册阶段的明文手机号，供管理员人工核对
    registration_token_hash TEXT,                          -- 注册进度查询凭证 SHA-256
    applied_at              TEXT,                          -- 提交注册申请时间
    reviewed_at             TEXT,                          -- 审批时间
    reviewed_by             TEXT,                          -- 审批管理员 user id
    review_note             TEXT,                          -- 审批备注/拒绝原因（≤500 字）
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_username       ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_status_applied ON users(status, applied_at);

-- 同一手机号只允许一个账号或一份申请
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_hash
    ON users(phone_hash)
    WHERE phone_hash IS NOT NULL AND phone_hash <> '';

-- 注册进度查询凭证一次性有效
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_registration_token
    ON users(registration_token_hash)
    WHERE registration_token_hash IS NOT NULL AND registration_token_hash <> '';

-- ---------------------------------------------------------------------
-- 2. 个人 API 配置（LLM / 搜索 / 文生图密钥，Fernet 加密 payload）
--    encrypted_payload 为 JSON 加密串，密钥来自 USER_CONFIG_SECRET。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_api_configs (
    user_id           TEXT PRIMARY KEY,
    encrypted_payload TEXT NOT NULL,
    llm_configured    INTEGER NOT NULL DEFAULT 0,
    search_configured INTEGER NOT NULL DEFAULT 0,
    image_configured  INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- 3. 生成额度账户（试用 + 付费）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_generation_accounts (
    user_id        TEXT PRIMARY KEY,
    trial_total    INTEGER NOT NULL DEFAULT 0 CHECK (trial_total    >= 0),
    trial_used     INTEGER NOT NULL DEFAULT 0 CHECK (trial_used     >= 0),
    trial_reserved INTEGER NOT NULL DEFAULT 0 CHECK (trial_reserved >= 0),
    paid_credits   INTEGER NOT NULL DEFAULT 0 CHECK (paid_credits   >= 0),
    paid_reserved  INTEGER NOT NULL DEFAULT 0 CHECK (paid_reserved  >= 0),
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- 4. 试用额度身份表（同一手机号只赠送一次试用）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trial_identities (
    phone_hash    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    granted_count INTEGER NOT NULL,
    granted_at    TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- 5. 生成用量流水（预留 → 结算/释放）
--    source: trial / paid
--    status: reserved / consumed / released
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS generation_usage (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL UNIQUE,
    user_id        TEXT NOT NULL,
    article_id     TEXT,
    source         TEXT NOT NULL CHECK (source IN ('trial', 'paid')),
    status         TEXT NOT NULL CHECK (status IN ('reserved', 'consumed', 'released')),
    unit_price_fen INTEGER NOT NULL DEFAULT 0,
    failure_reason TEXT,
    created_at     TEXT NOT NULL,
    settled_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_generation_usage_user_created
    ON generation_usage(user_id, created_at DESC);

-- ---------------------------------------------------------------------
-- 6. 人工调整流水（管理员充值/扣减额度）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS credit_adjustments (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    delta            INTEGER NOT NULL,
    balance_after    INTEGER NOT NULL,
    reason           TEXT NOT NULL,
    operator_user_id TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- 7. 支付订单（微信 Native 扫码）
--    status: pending / paid / closed / failed / refunded
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payment_orders (
    id                      TEXT PRIMARY KEY,
    order_no                TEXT NOT NULL UNIQUE,
    user_id                 TEXT NOT NULL,
    amount_fen              INTEGER NOT NULL,
    credit_count            INTEGER NOT NULL,
    unit_price_fen          INTEGER NOT NULL,
    status                  TEXT NOT NULL
                            CHECK (status IN ('pending', 'paid', 'closed', 'failed', 'refunded')),
    provider                TEXT NOT NULL,
    code_url                TEXT,
    provider_transaction_id TEXT,
    created_at              TEXT NOT NULL,
    expires_at              TEXT NOT NULL,
    paid_at                 TEXT,
    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_orders_user_created
    ON payment_orders(user_id, created_at DESC);

-- 同一渠道交易号只能关联一个订单（回调幂等）
CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_orders_transaction
    ON payment_orders(provider_transaction_id)
    WHERE provider_transaction_id IS NOT NULL AND provider_transaction_id <> '';

-- ---------------------------------------------------------------------
-- 8. 支付回调事件（按事件 ID 幂等去重）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payment_events (
    event_id       TEXT PRIMARY KEY,
    order_no       TEXT NOT NULL,
    transaction_id TEXT,
    payload_hash   TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- 9. 文章访问权限（共享设置）
--    can_view / can_edit / can_push：0=无权限 1=有权限
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS article_permissions (
    article_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    can_view   INTEGER NOT NULL DEFAULT 1,
    can_edit   INTEGER NOT NULL DEFAULT 0,
    can_push   INTEGER NOT NULL DEFAULT 0,
    granted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (article_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_article_permissions_user
    ON article_permissions(user_id);

-- ---------------------------------------------------------------------
-- 10. 审计日志（敏感操作留痕）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    username    TEXT,
    action      TEXT NOT NULL,
    resource    TEXT,
    resource_id TEXT,
    detail      TEXT,
    ip          TEXT,
    user_agent  TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action    ON audit_logs(action);

-- ---------------------------------------------------------------------
-- 11. LLM Token 用量（每次模型响应一条，用于管理员成本看板）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_token_usage (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, article_id TEXT,
    user_id TEXT NOT NULL, username TEXT NOT NULL DEFAULT '',
    generation_mode TEXT NOT NULL DEFAULT 'platform', stage TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0, cached_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0, estimated INTEGER NOT NULL DEFAULT 0,
    input_price_per_million REAL NOT NULL DEFAULT 0,
    output_price_per_million REAL NOT NULL DEFAULT 0,
    estimated_cost_micro_yuan INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_token_usage_task ON llm_token_usage(task_id);
CREATE INDEX IF NOT EXISTS idx_llm_token_usage_created ON llm_token_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_token_usage_user_created ON llm_token_usage(user_id, created_at);

-- =====================================================================
-- 可选：预置初始管理员
-- 应用首次启动时，若库中无有效管理员，会根据环境变量 INITIAL_ADMIN_PASSWORD
-- 自动创建用户名 admin 的账号（未配置则生成一次性随机密码）。
-- 如需用 SQL 直接预置，请先生成 bcrypt 哈希：
--   python -c "import bcrypt;print(bcrypt.hashpw(b'你的密码', bcrypt.gensalt()).decode())"
-- 再取消下面注释并替换哈希：
--
-- INSERT INTO users (id, username, password_hash, role, status, created_at, updated_at)
-- VALUES (lower(hex(randomblob(16))), 'admin',
--         '$2b$12$替换为你的bcrypt哈希', 'admin', 'active',
--         datetime('now', 'localtime'), datetime('now', 'localtime'));
-- =====================================================================
