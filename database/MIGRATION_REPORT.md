# MySQL 数据迁移报告

- 迁移时间：2026-09-07 08:38（本机时间）
- 目标：`39.96.80.70:3306/wechat_agent`
- MySQL：`8.0.46-0ubuntu0.22.04.4`
- 字符集/引擎：`utf8mb4` / `InnoDB`
- 目标表：11 张
- 迁移前本地一致性备份：`output/backups/mysql-migration-20260907_083829/`
- 各表字段级说明：`database/TABLES.md`

## 行数核对

| 表 | SQLite | MySQL | 一致 |
|---|---:|---:|---|
| users | 3 | 3 | 是 |
| user_api_configs | 0 | 0 | 是 |
| user_generation_accounts | 93 | 93 | 是 |
| trial_identities | 2 | 2 | 是 |
| generation_usage | 0 | 0 | 是 |
| credit_adjustments | 4 | 4 | 是 |
| payment_orders | 0 | 0 | 是 |
| payment_events | 0 | 0 | 是 |
| article_permissions | 0 | 0 | 是 |
| audit_logs | 500 | 500 | 是 |
| articles | 0 | 0 | 是 |

验证结果：`ALL_MATCH=True`，共迁移 602 行，所有源表与目标表行数一致。

## 安全收尾

`wechat_migrator` 已删除。生产应用使用最小权限账号 `wechat_agent_app`（仅 SELECT/INSERT/UPDATE/DELETE）。由于应用需要持续连接远程 MySQL，阿里云安全组应只允许当前应用出口 IP `49.65.153.147/32` 访问 TCP 3306，严禁开放 `0.0.0.0/0`。数据库管理员密码曾通过会话传递，建议一并轮换。

## 运行时状态

六个数据库 Store 已完成 SQLite/MySQL 双后端改造，生产 `config/.env` 已切换到 `wechat_agent` MySQL 库；新用户、额度、订单、审计、权限、个人 API 配置和历史记录均实时写入 MySQL。迁移前 SQLite 文件保留为只读回退备份。
