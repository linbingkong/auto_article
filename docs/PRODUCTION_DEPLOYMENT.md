# 观思辩明微信公众号 Agent：生产环境部署手册

> 适用版本：`wechat-auto-article-agent 0.2.x`  
> 推荐平台：Ubuntu Server 22.04/24.04 LTS  
> 推荐架构：单机、单 Python 进程、Nginx HTTPS、systemd  
> 默认路径：`/opt/wechat-agent`

本文覆盖服务器准备、密钥、HTTPS、systemd、公众号配置、人工注册审批、备份恢复、升级回滚、监控及故障排查。

## 1. 架构和边界

```text
用户 → HTTPS :443 → Nginx → HTTP 127.0.0.1:8000 → FastAPI/Uvicorn
                                                    ├─ JWT/RBAC/注册审批
                                                    ├─ SQLite 与文章文件
                                                    ├─ LLM/Tavily/图片服务
                                                    └─ 微信官方草稿 API
```

- Nginx 是唯一公网入口；8000 不得开放公网。
- 必须使用 HTTPS。
- 当前使用 SQLite、进程内任务状态及进程内 Token 吊销集合，适合单机单进程及约 10+ 用户。
- **不要配置多个 Uvicorn worker，也不要运行多个实例指向同一 `output/`。**
- 公开注册不调用微信消息接口，由管理员人工核对公众号私信手机号。
- 推荐固定 `publish_mode: draft_only`，正式发表由管理员完成。

## 2. 上线前准备

### 2.1 服务器

建议最低配置：2 vCPU、4 GB RAM、40 GB SSD、Ubuntu 22.04/24.04、固定公网出网 IP。服务器需访问 DeepSeek、Tavily、图片服务和 `api.weixin.qq.com`。

### 2.2 域名和网络

准备域名，例如 `wechat-agent.example.com`，A/AAAA 记录指向服务器。仅开放 TCP 22、80、443；SSH 建议限制管理员固定 IP。不要开放 TCP 8000。

### 2.3 第三方凭证

按需准备：微信公众号 AppID/AppSecret、DeepSeek Key、Tavily Key、DashScope/OpenAI 图片 Key、通知 Webhook 和“观思辩明”二维码。服务器固定出网 IP 必须加入公众号 IP 白名单，否则常见错误为 `40164`。

## 3. 安装依赖

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  git nginx curl ca-certificates openssl \
  certbot sqlite3 rsync fonts-noto-cjk

python3 --version
nginx -v
```

`fonts-noto-cjk` 用于 Pillow 封面和正文信息图的中文绘制。未安装中文字体时系统会停止生成本地文字图片并给出明确错误，避免输出缺字方框；也可以通过 `WECHAT_AGENT_CJK_FONT=/path/to/font.ttc` 指定自定义中文字体。

Python 必须为 3.12 或更高（代码使用了 PEP 701 的 f-string 语法；`pyproject.toml` 已声明 `requires-python = ">=3.12"`，在更低版本上 pip 安装会直接报错）。Ubuntu 22.04 默认仓库为 3.10，可用 Deadsnakes PPA 安装：

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev
python3.12 --version
```

## 4. 创建用户并部署代码

```bash
sudo useradd --system \
  --home-dir /opt/wechat-agent \
  --shell /usr/sbin/nologin \
  wechat-agent
sudo install -d -m 0750 -o wechat-agent -g wechat-agent /opt/wechat-agent
```

从 Git 部署：

```bash
sudo -u wechat-agent git clone <仓库URL> /opt/wechat-agent
```

或从构建机同步：

```bash
rsync -av --delete \
  --exclude '.git/' --exclude '.venv/' \
  --exclude 'config/.env' --exclude 'output/' \
  ./ root@SERVER_IP:/opt/wechat-agent/
sudo chown -R wechat-agent:wechat-agent /opt/wechat-agent
```

更新时不得删除 `output/` 和 `config/.env`。

## 5. Python 环境

```bash
sudo -u wechat-agent python3 -m venv /opt/wechat-agent/.venv
sudo -u wechat-agent /opt/wechat-agent/.venv/bin/pip install --upgrade pip setuptools wheel
sudo -u wechat-agent /opt/wechat-agent/.venv/bin/pip install /opt/wechat-agent
sudo -u wechat-agent /opt/wechat-agent/.venv/bin/wechat-agent --help
```

上线前建议运行测试：

```bash
sudo -u wechat-agent /opt/wechat-agent/.venv/bin/pip install pytest
cd /opt/wechat-agent
sudo -u wechat-agent .venv/bin/python -m pytest -q tests
```

当前版本预期为 `44 passed` 或更多。

## 6. 生产密钥

```bash
cd /opt/wechat-agent
sudo -u wechat-agent cp config/.env.example config/.env
sudo chmod 0600 config/.env
openssl rand -hex 32       # JWT_SECRET
openssl rand -hex 32       # PHONE_HASH_SECRET，必须不同
openssl rand -hex 32       # USER_CONFIG_SECRET，再使用一个独立值
openssl rand -base64 24    # INITIAL_ADMIN_PASSWORD
sudoedit config/.env
```

示例：

```dotenv
WECHAT_APP_ID=公众号AppID
WECHAT_APP_SECRET=公众号AppSecret
DEEPSEEK_API_KEY=DeepSeekKey
TAVILY_API_KEY=TavilyKey
IMAGE_API_KEY=图片服务对应的Key
# 兼容旧版本：若未配置 IMAGE_API_KEY，会读取 DASHSCOPE_API_KEY
NOTIFY_WEBHOOK_URL=

JWT_SECRET=至少64个十六进制字符
PHONE_HASH_SECRET=另一个至少64个十六进制字符
USER_CONFIG_SECRET=第三个独立的至少64位十六进制字符
# 启用微信支付时填写，必须严格为 32 字节
WECHAT_PAY_API_V3_KEY=
INITIAL_ADMIN_PASSWORD=首次登录强密码

# 仅旧自动化兼容时配置
WEB_ADMIN_TOKEN=
```

要求：

- JWT 与手机号 HMAC 密钥不得相同。
- `.env` 不得提交 Git、发送聊天群或写入工单正文。
- `PHONE_HASH_SECRET` 丢失后无法匹配已有注册手机号。
- `USER_CONFIG_SECRET` 丢失后无法解密所有用户的个人 LLM、Tavily 和文生图配置，必须纳入加密备份且不可随意轮换。
- `WECHAT_PAY_API_V3_KEY` 仅用于微信支付通知解密，必须严格为 32 字节并与其他密钥分离保管。
- 商户私钥 PEM 权限建议设为 `chmod 600`，微信支付公钥/平台证书轮换时需同步更新 ID 与文件。
- 支付通知 URL 必须是公网 HTTPS 地址 `/api/payments/wechat/notify`，且不得被额外登录认证或代理缓存阻断。
- 修改 `JWT_SECRET` 会使已有登录 Token 失效。
- `INITIAL_ADMIN_PASSWORD` 只在数据库中没有有效管理员时生效。

首次登录后修改管理员密码，删除 `INITIAL_ADMIN_PASSWORD` 并重启。若 `output/auth.db` 已存在管理员，修改该变量不会重置密码。

## 7. 业务配置

```bash
sudoedit /opt/wechat-agent/config/config.yaml
```

核心示例：

```yaml
wechat:
  app_id: ${WECHAT_APP_ID}
  app_secret: ${WECHAT_APP_SECRET}
  publish_mode: draft_only

llm:
  base_url: https://api.deepseek.com
  api_key: ${DEEPSEEK_API_KEY}
  model: deepseek-chat
  temperature: 0.8
  max_tokens: 4096
  timeout: 120

registration:
  enabled: true
  default_role: viewer
  account_name: 观思辩明
  follow_instructions: 请关注公众号后，私信发送注册时填写的完整手机号。
  private_message_instructions: 请在公众号私信中发送注册手机号，管理员核对后批准。
  qr_image: ''
  application_expire_days: 7

schedule:
  enabled: false
  daily_time: "08:30"
  timezone: Asia/Shanghai

output_dir: output
```

说明：

- 公开注册前必须配置 `PHONE_HASH_SECRET`。
- 二维码可在“配置中心 → 公开注册与人工审批”上传。
- 公开申请默认 viewer，审批时可改 editor，不能获得 admin。
- Web 内启用调度后，不要再运行独立 `wechat-agent schedule`，否则会重复执行。

```bash
sudo chown -R wechat-agent:wechat-agent /opt/wechat-agent/config
sudo chmod 0750 /opt/wechat-agent/config
sudo chmod 0600 /opt/wechat-agent/config/.env
sudo chmod 0640 /opt/wechat-agent/config/config.yaml
sudo install -d -m 0700 -o wechat-agent -g wechat-agent /opt/wechat-agent/output
```

## 8. 上线前检查

```bash
cd /opt/wechat-agent
sudo -u wechat-agent .venv/bin/wechat-agent check-config
sudo -u wechat-agent .venv/bin/wechat-agent run --dry-run --articles 1
```

`dry-run` 不推送公众号草稿，但仍可能调用热点、Tavily、LLM 和图片 API并产生费用。确认 `output/` 中生成 Markdown、HTML、封面和结果文件。

## 9. systemd

项目提供 `deploy/wechat-agent-web.service`：

```bash
cd /opt/wechat-agent
sudo cp deploy/wechat-agent-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/wechat-agent-web.service
sudo systemctl enable --now wechat-agent-web
sudo systemctl status wechat-agent-web --no-pager
sudo journalctl -u wechat-agent-web -n 100 --no-pager
curl -fsS http://127.0.0.1:8000/api/health
```

确认只监听本机：

```bash
sudo ss -lntp | grep 8000
```

必须显示 `127.0.0.1:8000`，不能是 `0.0.0.0:8000`。

模板启用了 `NoNewPrivileges`、`PrivateTmp`、`ProtectHome`、`ProtectSystem=full` 和 `UMask=0077`，仅允许写入 `output/`、`config/` 和 `web/assets/`。配置保存和二维码上传依赖后两个可写目录。

## 10. DNS、TLS 和 Nginx

### 10.1 防火墙

```bash
getent hosts wechat-agent.example.com
sudo ufw allow from <管理员固定IP> to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

云安全组需同步配置。

### 10.2 证书

确保域名已解析且 80 可访问：

```bash
sudo systemctl stop nginx
sudo certbot certonly --standalone \
  -d wechat-agent.example.com \
  -m admin@example.com \
  --agree-tos --no-eff-email
```

### 10.3 Nginx

```bash
cd /opt/wechat-agent
sudo cp deploy/nginx-wechat-agent.conf /etc/nginx/sites-available/wechat-agent
sudo sed -i \
  's/wechat-agent\.example\.com/你的实际域名/g' \
  /etc/nginx/sites-available/wechat-agent
sudo ln -sfn /etc/nginx/sites-available/wechat-agent /etc/nginx/sites-enabled/wechat-agent
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable --now nginx
```

验证：

```bash
curl -I https://你的实际域名/
curl -fsS https://你的实际域名/api/health
sudo certbot renew --dry-run
```

生产入口为 `https://你的实际域名`。

## 11. 首次登录

1. 使用 admin 和初始密码登录。
2. 立即修改密码并创建备用管理员。
3. 删除 `.env` 中的 `INITIAL_ADMIN_PASSWORD`，重启服务。
4. 测试 LLM、Tavily、图片、公众号和通知配置。
5. 上传“观思辩明”二维码并检查注册文案。
6. 生成测试文章，只推草稿箱，人工检查标题、摘要、封面、正文图和排版。

角色建议：admin 仅限运维/主编；editor 给内容编辑；viewer 为公开注册默认角色。

## 12. 注册和人工审批

用户：注册 → 关注“观思辩明” → 私信完整手机号 → 查询进度。

管理员：公众号后台复制手机号 → “注册审批”精确查找 → 核对用户名和手机号 → 勾选人工确认 → 批准为 viewer/editor 或填写原因拒绝。

隐私机制：

- 数据库保存手机号明文（`users.phone`），管理员页面可直接查看完整号码。
- 同时保留 `PHONE_HASH_SECRET` 生成的 HMAC，用于唯一性约束和精确匹配。
- 搜索时按完整号码精确匹配。
- 日志和审计仅记录末四位掩码。
- `PHONE_HASH_SECRET` 必须进入加密备份。

系统不能自动确认关注、私信或取消关注；管理员确认是审批依据并写入审计。

### 用户级外部服务配置

- LLM、Tavily 和文生图由 admin/editor 在“我的 API 配置”中分别保存。
- editor 没有对应个人配置时，搜索或生成请求会被拒绝，不能消耗系统公共 Key。
- admin 没有个人配置时可以回退“配置中心”的系统默认服务。
- 用户配置使用 Fernet 加密写入 `output/auth.db`；API 和管理界面只返回掩码和配置状态。
- 管理员可以查看每个用户三类服务的配置状态并清除，但无法查看、复制或导出密钥。
- 微信公众号、通知、调度、写作定位和注册审批仍为系统级配置。

## 13. 微信公众号设置

1. 获取 AppID/AppSecret。
2. 将服务器出网 IP 加入公众号白名单。
3. 确认账号具备素材和草稿 API 权限。
4. 保持 `publish_mode: draft_only`。
5. 在配置中心验证凭证。
6. 首次推送后检查草稿标题、摘要、封面、正文图、引用和 AI 声明。

不得使用模拟登录或浏览器自动发表作为生产通道。

## 14. 日志和监控

```bash
sudo journalctl -u wechat-agent-web -f
sudo journalctl -u wechat-agent-web --since "1 hour ago"
sudo journalctl -u wechat-agent-web -p warning --since today
sudo tail -f /var/log/nginx/wechat-agent.access.log
sudo tail -f /var/log/nginx/wechat-agent.error.log
curl -fsS https://你的实际域名/api/health
```

建议监控 HTTPS 状态与延迟、systemd、磁盘空间、证书剩余时间、任务失败量及外部 API 错误率。健康接口不返回密钥。

## 15. 备份和恢复

关键数据：

| 路径 | 内容 |
|---|---|
| `config/.env` | API Key、JWT、手机号 HMAC 密钥 |
| `config/config.yaml` | 业务和注册配置 |
| `output/auth.db` | 用户、申请、审计 |
| `output/history.db` | 运行和草稿历史 |
| `output/web_articles/` | 文章、版本和图片 |
| `web/assets/account-qr-custom.png` | 公众号二维码 |

用户信息数据库的完整建表语句见 [`database/schema.sql`](../database/schema.sql)。该脚本全部使用 `IF NOT EXISTS`，可在已有库上重复执行；应用启动时也会自动补建缺失的表和索引，二者兼容。

全部数据存储位置、代码更新防覆盖操作清单，以及迁移到生产 MySQL 的完整方案见 [`docs/DATA_STORAGE.md`](DATA_STORAGE.md)（含 `database/schema_mysql.sql` 建库脚本与 `database/migrate_sqlite_to_mysql.py` 迁移脚本）。

一致性备份：

```bash
BACKUP_DIR=/srv/backups/wechat-agent/$(date +%Y%m%d_%H%M%S)
sudo install -d -m 0700 "$BACKUP_DIR"
sudo systemctl stop wechat-agent-web
sudo rsync -a /opt/wechat-agent/config/ "$BACKUP_DIR/config/"
sudo rsync -a /opt/wechat-agent/output/ "$BACKUP_DIR/output/"
sudo install -D -m 0600 \
  /opt/wechat-agent/web/assets/account-qr-custom.png \
  "$BACKUP_DIR/web/assets/account-qr-custom.png" 2>/dev/null || true
sudo systemctl start wechat-agent-web
sudo tar -C "$(dirname "$BACKUP_DIR")" \
  -czf "${BACKUP_DIR}.tar.gz" "$(basename "$BACKUP_DIR")"
```

至少每日备份，保留 7 个日备和 4 个周备；加密并复制异地，每季度恢复演练。

恢复：

```bash
sudo systemctl stop wechat-agent-web
sudo rsync -a --delete /srv/restore/config/ /opt/wechat-agent/config/
sudo rsync -a --delete /srv/restore/output/ /opt/wechat-agent/output/
sudo chown -R wechat-agent:wechat-agent /opt/wechat-agent/config /opt/wechat-agent/output
sudo chmod 0600 /opt/wechat-agent/config/.env
sudo systemctl start wechat-agent-web
```

不要在服务运行时覆盖 SQLite 文件。

## 16. 升级和回滚

升级前完整备份并记录 commit/tag：

```bash
cd /opt/wechat-agent
sudo systemctl stop wechat-agent-web
sudo -u wechat-agent git fetch --tags
sudo -u wechat-agent git checkout <目标版本>
sudo -u wechat-agent .venv/bin/pip install --upgrade /opt/wechat-agent
sudo -u wechat-agent .venv/bin/python -m pytest -q tests
sudo systemctl start wechat-agent-web
curl -fsS http://127.0.0.1:8000/api/health
```

再验收登录、用户、审批、配置、文章和生成。

代码回滚：

```bash
sudo systemctl stop wechat-agent-web
cd /opt/wechat-agent
sudo -u wechat-agent git checkout <上一稳定版本>
sudo -u wechat-agent .venv/bin/pip install --upgrade /opt/wechat-agent
sudo systemctl start wechat-agent-web
```

若升级包含不兼容迁移，还需恢复升级前数据库和文章目录。

## 17. 安全检查清单

- [ ] Uvicorn 只监听 `127.0.0.1:8000`
- [ ] 公网仅开放 80/443，SSH 限制来源
- [ ] HTTP 强制跳转 HTTPS
- [ ] JWT、手机号 HMAC 和用户配置加密密钥彼此独立且足够长
- [ ] editor 未配置个人服务时无法使用系统 LLM/Tavily/文生图额度
- [ ] `.env` 权限 0600
- [ ] 初始密码已修改并从 `.env` 删除
- [ ] 至少有两个受控管理员
- [ ] 未登录管理 API 返回 401
- [ ] viewer/editor 不能访问用户、审批和审计
- [ ] 公开申请不能成为 admin
- [ ] 公众号保持 draft_only
- [ ] 不运行多个 Uvicorn worker
- [ ] 不重复启动调度器
- [ ] 备份已加密并完成恢复演练

```bash
curl -i https://你的实际域名/api/users
```

应返回 `401 Unauthorized`。

## 18. 常见故障

### 页面一直加载

```bash
curl -I https://你的实际域名/app.js
curl -I https://你的实际域名/core.js
sudo journalctl -u wechat-agent-web -n 100 --no-pager
sudo tail -n 100 /var/log/nginx/wechat-agent.error.log
```

浏览器打开 F12 控制台并强制刷新。

### Nginx 502

```bash
sudo systemctl status wechat-agent-web --no-pager
curl -v http://127.0.0.1:8000/api/health
sudo ss -lntp | grep 8000
```

检查工作目录、虚拟环境和权限。

### PHONE_HASH_SECRET 未配置

```bash
sudo grep '^PHONE_HASH_SECRET=' /opt/wechat-agent/config/.env
sudo systemctl restart wechat-agent-web
```

不要把密钥值粘贴到排障记录。

### 登录后立即失效

检查多个实例、JWT 密钥变更、用户禁用及服务器时间：

```bash
timedatectl status
```

### 微信错误 40164

服务器固定出网 IP 未加入公众号白名单。

### LLM 超时或 401

检查 Key、`base_url`、模型、余额和网络；Nginx 与应用超时均需足够长。

### SQLite database is locked

确认只有一个 Web 进程；不要把 `output/` 放在 NFS/SMB；不要运行中覆盖数据库。

### systemd 无法写配置或二维码

```bash
sudo journalctl -u wechat-agent-web -n 100 --no-pager
sudo systemctl cat wechat-agent-web
sudo ls -ld /opt/wechat-agent/{config,output,web/assets}
```

确认目录属于 `wechat-agent` 且位于 `ReadWritePaths`。

### 忘记管理员密码

由另一管理员重置。若无可用管理员，先备份 `output/auth.db`，再执行受控离线恢复；禁止直接删除生产数据库。

## 19. 最终验收

- [ ] DNS、HTTPS、证书续期正常
- [ ] 8000 不可从公网访问
- [ ] systemd 自动启动和故障重启正常
- [ ] admin/editor/viewer 权限符合预期
- [ ] 旧文章已统一迁移给 admin，新文章记录实际创建者
- [ ] editor 默认只能看到自己的文章，viewer 默认看不到未共享文章
- [ ] view/edit/push 按文章授权生效，共享用户不能删除文章
- [ ] 文章附件签名链接可访问且过期/撤销权限后失效
- [ ] 禁用账号立即失效
- [ ] 注册、手机号核对、批准、拒绝、重新申请正常
- [ ] 审计中没有完整手机号
- [ ] 热点、搜索、LLM、图片服务正常
- [ ] viewer 文章页只读
- [ ] 测试文章仅推草稿箱且排版正常
- [ ] 日志、监控、每日备份和恢复演练完成

完成以上验收后再开放生产用户。
