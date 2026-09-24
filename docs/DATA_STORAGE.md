# 数据存储全景与防覆盖指南

本文回答两个问题：**系统当前把哪些数据存在哪里**，以及**代码更新时如何保证线上用户数据不被覆盖**。配套文件：

- `database/schema.sql` — SQLite 建表语句（与应用自动建表兼容）
- `database/schema_mysql.sql` — 生产 MySQL 建库脚本（12 张表）
- `database/migrate_sqlite_to_mysql.py` — SQLite → MySQL 一次性迁移脚本
- `database/TABLES.md` — 每张表存放内容的逐字段说明
- `docs/TOKEN_USAGE.md` — Token 看板口径、成本公式与上线步骤

## 一、存储清单

| 存储位置 | 类型 | 内容 | 写入方 | 代码更新影响 |
|---|---|---|---|---|
| `output/auth.db` | SQLite | 用户、注册审批、个人 API 密钥（加密）、额度账户、支付订单与回调、审计日志、文章权限、LLM Token 成本明细，共 11 张表 | `UserStore` / `UserApiConfigStore` / `BillingStore` / `ArticleAccessStore` / `AuditStore` / `TokenUsageStore` | 应用启动只 `CREATE IF NOT EXISTS` 和补列，**从不删除或清空数据** |
| `output/history.db` | SQLite | `articles` 表：每次生成的选题、标题、状态、输出目录、阅读数据；7 天选题去重依据 | `HistoryStore` | 同上 |
| `output/web_articles/{文章id}/` | 文件 | `article.md`、`article.html`、`evidence.txt`、`metadata.json`、`cover.png`、`inline_*.png`、`versions/v*.md|json` | `ArticleStore` | 应用不删除旧目录 |
| `config/config.yaml` | YAML | 系统配置：LLM/图片/搜索/公众号/注册/计费 | 管理台保存时整体重写；代码更新不写它 | 只有整目录覆盖式部署才会丢失 |
| `config/.env` | env 文件 | API Key、JWT 密钥、`PHONE_HASH_SECRET`、`USER_CONFIG_SECRET` | 管理台保存时更新 | 同上 |
| `web/assets/account-qr-custom.png` | 图片 | 上传的注册二维码 | 管理台上传 | **唯一位于代码目录内的数据文件**，覆盖 `web/` 部署有风险 |
| 进程内存 | — | 热点缓存、生成任务队列、支付查询缓存 | 运行时 | 重启丢失，属设计行为，无需持久化 |
| `output/_*_test/`、`output/history_smoke.db`、`output/service.log` | 测试/日志 | 自动化测试产物、运行日志 | 测试与服务进程 | 可随时清理，不是生产数据 |

说明：`config.yaml` 中的 `wechat.token_cache_file` 字段目前为预留，公众号 access_token 并未持久化到磁盘。

## 二、"代码更新会覆盖用户数据"的真实风险

**应用代码本身不会覆盖用户数据**：两个 SQLite 库都只在启动时补建缺失的表结构，数据行不会被重写；`output/web_articles/` 只追加不清理。

真正会丢数据的操作是**部署方式**：

1. 用 `rsync --delete` / 整目录复制覆盖部署目录（连带删除 `output/` 和 `config/`）；
2. `git clean -fdx` 清理未跟踪文件（`output/`、`config/.env` 通常未被 git 跟踪）；
3. 重装环境时删除 `output/`；
4. 部署包整体覆盖 `web/`，丢掉 `account-qr-custom.png`。

## 三、防覆盖措施（立即可用，无需改代码）

### 1. 部署范围最小化

代码更新只同步代码，不碰数据：

```bash
# 只更新代码目录，output 和 config 完全不动
git pull                     # 仓库内不含 output/、config/.env
# 或 rsync 时显式排除：
rsync -av --exclude output/ --exclude config/ src/ web/ scripts/ /opt/wechat-agent/
```

### 2. 升级前备份（幂等、可回滚）

```bash
BACKUP_DIR=/srv/backups/wechat-agent/$(date +%Y%m%d_%H%M%S)
install -d -m 0700 "$BACKUP_DIR"
cp output/auth.db output/history.db "$BACKUP_DIR/"
cp -r output/web_articles config/config.yaml config/.env "$BACKUP_DIR/"
cp web/assets/account-qr-custom.png "$BACKUP_DIR/" 2>/dev/null || true
```

### 3. 升级后验证

- `GET /api/health` 返回 `ok: true`；
- 用一个已有用户登录成功；
- 管理台用户列表数量与升级前一致。

## 四、迁移到 MySQL

### 1. 现状与目标

当前存储层直接使用 Python 标准库 `sqlite3`。`database/schema_mysql.sql` + `database/migrate_sqlite_to_mysql.py` 已完成数据库侧准备，可以把全部用户数据落到外部 MySQL，使数据与代码目录彻底解耦——之后即使部署目录被整体删除，用户数据仍安全在 MySQL 中。

### 2. 步骤

```bash
# ① 建库建表（可重复执行）
mysql -h <host> -u root -p < database/schema_mysql.sql

# ② 预演：核对行数
python database/migrate_sqlite_to_mysql.py --sqlite output/auth.db --host <host> --user root --password '***' --dry-run
python database/migrate_sqlite_to_mysql.py --sqlite output/history.db --host <host> --user root --password '***' --dry-run

# ③ 正式迁移（密码建议通过环境变量传入）
export MYSQL_PASSWORD='***'
python database/migrate_sqlite_to_mysql.py --sqlite output/auth.db --host <host> --user <migration-user> --database wechat_agent --database-exists
python database/migrate_sqlite_to_mysql.py --sqlite output/history.db --host <host> --user <migration-user> --database wechat_agent --database-exists
```

需要 `pip install pymysql`。迁移期间建议停止服务或保证无写入。

### 3. SQLite → MySQL 的结构差异

| SQLite 结构 | MySQL 等价实现 |
|---|---|
| 部分唯一索引 `WHERE col IS NOT NULL AND col <> ''` | 生成列（空串/NULL 归一为 NULL）+ UNIQUE KEY |
| `CHECK` 约束 | 保留（MySQL 8.0.16+ 强制生效） |
| `TEXT DEFAULT ''`（users.phone） | 改为 `VARCHAR(32) NULL` |
| ISO 8601 文本时间（`2026-09-04T11:03:32`） | `DATETIME`（MySQL 直接接受 T 分隔符） |
| `articles.id` 自增 | `BIGINT AUTO_INCREMENT`，迁移后校正起点 |

### 4. 运行时切换

七个数据库 Store 已支持 SQLite/MySQL 双后端。生产环境在 `config/.env` 配置：

```dotenv
DATABASE_BACKEND=mysql
MYSQL_HOST=<host>
MYSQL_PORT=3306
MYSQL_DATABASE=wechat_agent
MYSQL_USER=wechat_agent_app
MYSQL_PASSWORD=<仅保存在环境或受控 .env 中>
MYSQL_CONNECT_TIMEOUT=10
# 可选：公网 MySQL TLS CA
MYSQL_SSL_CA=/path/to/ca.pem
```

启用后，下列 Store 全部实时读写 MySQL：

- `UserStore`
- `HistoryStore`
- `BillingStore`
- `ArticleAccessStore`
- `AuditStore`
- `UserApiConfigStore`
- `TokenUsageStore`

应用启动只验证预建表是否存在，**不会自动执行生产 DDL，也不会清表**。上线 Token 看板前，先以 MySQL 管理员执行 `database/add_token_usage_mysql.sql`；应用账号本身不需要 DDL 权限。测试套件在 `tests/conftest.py` 强制使用临时 SQLite，避免测试污染生产 MySQL。切换并验证成功后，`output/auth.db` 和 `output/history.db` 只作为迁移前备份保留。

### 5. 账号来源 IP（部署到服务器时必读）

MySQL 账号按 `'用户名'@'来源IP'` 逐条匹配。`wechat_agent_app` 默认只授权了开发机 IP `49.65.153.147`；把应用部署到 MySQL 同一台服务器上时，连接来源会变成 `39.96.80.70`（服务器自身公网 IP），若不加账号会报 `1045 Access denied for 'wechat_agent_app'@'39.96.80.70'`。用 root 在服务器上执行：

```sql
-- 线上应用与本机 MySQL 同机部署（推荐，配合 MYSQL_HOST=127.0.0.1）
CREATE USER IF NOT EXISTS 'wechat_agent_app'@'localhost' IDENTIFIED BY '<同 .env 中的密码>';
GRANT SELECT, INSERT, UPDATE, DELETE ON wechat_agent.* TO 'wechat_agent_app'@'localhost';
-- 如确需经公网 IP 回连（不推荐）：
-- CREATE USER IF NOT EXISTS 'wechat_agent_app'@'39.96.80.70' IDENTIFIED BY '<同 .env 中的密码>';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON wechat_agent.* TO 'wechat_agent_app'@'39.96.80.70';
FLUSH PRIVILEGES;
```

同机部署时线上 `.env` 中 `MYSQL_HOST` 建议改为 `127.0.0.1`：不经过安全组与公网，延迟更低。

> root 账号默认只存在 `'root'@'localhost'`（仅限本机登录）。在服务器上管理数据库时不要加 `-h <公网IP>`（那样来源会变成服务器公网 IP，被拒 1045），直接 `mysql -uroot -p` 或 `mysql -h 127.0.0.1 -uroot -p`；Ubuntu/Debian 系统包安装的 MySQL 可用 `sudo mysql` 免密进入（auth_socket）。

## 五、关键密钥与数据的关系

| 密钥 | 保护的数据 | 丢失后果 |
|---|---|---|
| `USER_CONFIG_SECRET` | `user_api_configs.encrypted_payload` | 所有用户的个人 API 配置**无法解密**，需重新填写 |
| `PHONE_HASH_SECRET` | `users.phone_hash`、`trial_identities.phone_hash` | 手机号摘要对不上，试用赠送与同号判定失效 |
| bcrypt 密码哈希 | `users.password_hash` | 单向哈希，无法恢复明文，只能重置 |
