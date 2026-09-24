# 数据表说明（wechat_agent · MySQL）

本文说明生产数据库 `wechat_agent` 中每张表存放的内容。字段级建表语句见 `schema_mysql.sql`（MySQL）与 `schema.sql`（SQLite），两边结构一致。

- 生产库：`MYSQL_HOST:3306/wechat_agent`，MySQL 8.0.46，utf8mb4 / InnoDB
- 共 12 张表，分为 6 个业务域
- 写入方：`UserStore`、`UserApiConfigStore`、`BillingStore`、`AuditStore`、`ArticleAccessStore`、`HistoryStore` 六个 Store（SQLite/MySQL 双后端）
- 行数统计时间：2026-09-07 迁移完成并实时核对，共 602 行

## 一、用户与注册域

### 1. users — 账号、公开注册申请、审批记录（当前 3 行）

正式账号与注册申请共用一张表，用 `status` 区分。`role` 取值 admin / editor / creator / viewer；`status` 取值 pending_approval / active / rejected / disabled。

| 字段 | 存放内容 |
|---|---|
| `id` | 用户唯一 ID（uuid hex，32 位） |
| `username` | 登录用户名（3–32 位字母数字，唯一） |
| `password_hash` | bcrypt 密码哈希（不存明文） |
| `role` | 角色 |
| `status` | 账号/申请状态 |
| `phone_hash` | 手机号 HMAC-SHA256 摘要（生成列 `phone_hash_uk` 唯一，同号只允许一个账号或申请） |
| `phone_last4` | 手机号末四位（脱敏展示） |
| `phone` | 注册阶段的核对手机号 |
| `registration_token_hash` | 注册进度查询凭证 SHA-256（生成列 `reg_token_uk` 唯一） |
| `applied_at` | 提交注册申请时间 |
| `reviewed_at` | 审批时间 |
| `reviewed_by` | 审批管理员 user id |
| `review_note` | 审批备注/拒绝原因（≤500 字） |
| `created_at` / `updated_at` | 创建/更新时间 |

产生新行的操作：管理员后台创建用户；公开注册提交申请。审批动作只更新本表 `status` 与审批字段。

### 2. user_api_configs — 个人 API 配置（当前 0 行）

| 字段 | 存放内容 |
|---|---|
| `user_id` | 用户 ID（主键） |
| `encrypted_payload` | Fernet 加密 JSON：个人 LLM / Tavily 搜索 / 文生图的 base_url、model、api_key 等 |
| `llm_configured` | 个人 LLM 是否已配置（0/1） |
| `search_configured` | 个人搜索是否已配置（0/1） |
| `image_configured` | 个人文生图是否已配置（0/1） |
| `updated_at` | 更新时间 |

产生新行的操作：用户在"我的 API 配置"保存任一服务；解密依赖环境变量 `USER_CONFIG_SECRET`，丢失后密文不可恢复。

## 二、额度与计费域

### 3. user_generation_accounts — 每用户额度账本（当前 93 行）

| 字段 | 存放内容 |
|---|---|
| `user_id` | 用户 ID（主键） |
| `trial_total` | 试用总额度 |
| `trial_used` | 试用已消费次数 |
| `trial_reserved` | 试用预占中次数 |
| `paid_credits` | 付费余额 |
| `paid_reserved` | 付费预占中次数 |
| `created_at` / `updated_at` | 创建/更新时间 |

五个计数列有 `chk_uga_nonneg` 非负 CHECK 约束。首次注册赠送试用、微信支付入账、生成预占时创建账户行。

### 4. trial_identities — 试用赠送身份（当前 2 行）

| 字段 | 存放内容 |
|---|---|
| `phone_hash` | 手机号 HMAC 摘要（主键：一个手机号只赠送一次试用） |
| `user_id` | 获赠用户 |
| `granted_count` | 实际赠送次数（同用户重复申请按顶格；跨用户不再加量） |
| `granted_at` | 赠送时间 |

### 5. generation_usage — 生成用量流水（当前 0 行）

| 字段 | 存放内容 |
|---|---|
| `id` | 流水 ID |
| `task_id` | 生成任务 ID（唯一：一次生成只允许一条预占） |
| `user_id` | 用户 ID |
| `article_id` | 关联文章 ID（可空） |
| `source` | trial / paid |
| `status` | reserved（预占）→ consumed（成功消费）/ released（失败释放）；结算幂等 |
| `unit_price_fen` | 单次单价（分） |
| `failure_reason` | 失败原因（≤500 字，可空） |
| `created_at` | 预占时间 |
| `settled_at` | 结算时间 |

产生新行的操作：每次点击"生成文章"前预占一条；任务结束后 `BillingStore.settle()` 结算。

### 6. credit_adjustments — 额度人工调整台账（当前 4 行）

| 字段 | 存放内容 |
|---|---|
| `id` | 流水 ID |
| `user_id` | 被调整用户 |
| `delta` | 增减量（正充值/负扣减） |
| `balance_after` | 调整后余额 |
| `reason` | 调整原因（必填，≤500 字） |
| `operator_user_id` | 操作管理员；微信支付入账时为 `wechat-pay-callback` |
| `created_at` | 调整时间 |

## 三、支付域

### 7. payment_orders — 微信 Native 扫码订单（当前 0 行）

| 字段 | 存放内容 |
|---|---|
| `id` | 订单 ID |
| `order_no` | 商户订单号（`GSBM`+时间+随机，唯一） |
| `user_id` | 购买用户 |
| `amount_fen` | 总金额（分，= credit_count × unit_price_fen） |
| `credit_count` | 购买生成次数 |
| `unit_price_fen` | 单价（分） |
| `status` | pending / paid / closed / failed / refunded |
| `provider` | 支付渠道（wechat_native） |
| `code_url` | 微信扫码支付二维码链接 |
| `provider_transaction_id` | 微信交易号（生成列 `transaction_uk` 跨订单唯一，防重复入账） |
| `created_at` | 创建时间 |
| `expires_at` | 过期时间（5–60 分钟，过期惰性关闭） |
| `paid_at` | 支付时间 |
| `updated_at` | 更新时间 |

### 8. payment_events — 支付回调事件（当前 0 行）

| 字段 | 存放内容 |
|---|---|
| `event_id` | 微信通知 ID（主键：同一回调重复推送直接幂等返回） |
| `order_no` | 关联订单号 |
| `transaction_id` | 微信交易号 |
| `payload_hash` | 回调报文摘要（同 event_id 内容不一致报 PAYMENT_EVENT_CONFLICT） |
| `created_at` | 回调时间 |

## 四、权限域

### 9. article_permissions — 文章共享权限（当前 0 行）

| 字段 | 存放内容 |
|---|---|
| `article_id` + `user_id` | 复合主键：哪篇文章授权给哪个用户 |
| `can_view` | 可查看（0/1，默认 1） |
| `can_edit` | 可编辑（0/1） |
| `can_push` | 可推送草稿（0/1） |
| `granted_by` | 授权人 user id |
| `created_at` / `updated_at` | 授权/更新时间 |

文章删除、用户删除时会清理对应权限行。

## 五、内容与审计域

### 10. articles — 选题与发布记录（当前 0 行）

| 字段 | 存放内容 |
|---|---|
| `id` | 自增主键（流水线返回的 history_id） |
| `topic` | 原始选题标题 |
| `topic_norm` | 归一化标题（7 天选题去重依据，算法已冻结，改算法会导致历史去重失配） |
| `source` | 选题来源平台 |
| `score` | 热度评分 |
| `article_title` | 生成文章标题 |
| `draft_media_id` | 微信草稿 media_id |
| `status` | generated / draft_created / published |
| `output_dir` | 正文与图片所在本地目录（`output/web_articles/{id}/`） |
| `read_count` / `like_count` / `share_count` | 阅读回填数据（接口预留，未自动对接） |
| `created_at` | 生成时间 |

每次流水线运行产生一条；正文文件本体不在此表。

### 11. audit_logs — 审计日志（当前 500 行）

| 字段 | 存放内容 |
|---|---|
| `id` | 日志 ID |
| `user_id` / `username` | 操作人 |
| `action` | 操作类型（登录、审批、改配置、调额度、支付回调等） |
| `resource` / `resource_id` | 操作对象类型与 ID |
| `detail` | JSON 详情（TEXT） |
| `ip` / `user_agent` | 来源 IP 与 UA |
| `created_at` | 操作时间 |

敏感操作由 `AuditStore.record()` 统一留痕，管理台可按用户/动作检索。

### 12. llm_token_usage — LLM Token 与成本明细

| 字段 | 存放内容 |
|---|---|
| `id` | 单次模型响应的用量记录 ID |
| `task_id` / `article_id` | 生成任务与文章关联 ID；失败任务可能无 article_id |
| `user_id` / `username` | 发起生成的用户 |
| `generation_mode` | platform / personal |
| `stage` | outline、section_N、refine、fact_revision、fact_audit、titles 等 |
| `provider` / `model` | 调用网关主机与模型，不保存 API Key |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | 输入、输出和总 Token |
| `cached_tokens` / `reasoning_tokens` | 服务商返回的缓存命中和推理 Token |
| `estimated` | 服务商未返回 usage 时是否由文本长度近似估算 |
| `input_price_per_million` / `output_price_per_million` | 调用发生时的单价快照（元/百万 Token） |
| `estimated_cost_micro_yuan` | 估算成本，微元整数避免小数精度损失 |
| `created_at` | 模型响应时间 |

管理员 Token 看板按任务、日期、模型和阶段聚合此表。成本是根据配置单价估算，不替代服务商账单。

## 附：不在数据库中的数据

| 数据 | 位置 |
|---|---|
| 文章正文（md/html）、封面、配图、版本快照、evidence | `output/web_articles/{id}/`（由 `articles.output_dir` 指向） |
| 系统配置（LLM/图片/公众号/注册/计费） | `config/config.yaml` |
| 密钥（JWT、HMAC、加密、支付 v3、MySQL 连接） | `config/.env` |
| 注册二维码 | `web/assets/account-qr-custom.png` |
| 热点缓存、生成任务队列、支付查询缓存 | 进程内存（重启丢失，设计如此） |
