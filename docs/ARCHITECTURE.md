# 架构说明

## 1. Web 管理层与流水线

```text
浏览器零构建 SPA（HTML / CSS / ES Modules）
        ↕ same-origin JSON API + JWT Bearer（旧自动化可选 X-Admin-Token）
FastAPI WebApp
        ├─ ConfigService（YAML + .env，密钥脱敏）
        ├─ TaskManager（最多 2 个后台生成/推送任务）
        ├─ ArticleStore（Markdown / HTML / 版本 / 图片）
        └─ HotspotService（抓取、评分、缓存）
        ↓
APScheduler / Linux cron
        ↓
HotTopicFetcher（微博/知乎/头条/财联社/B站/百度）
        ↓
HistoryStore（SQLite，7天选题去重）
        ↓
TopicSelector（黑白名单、排名、热度、跨平台去重、评分）
        ↓
ArticleWriter（大纲→分节→润色→标题）
        ↓
ImageSourceResolver（服务源兼容校验、端点与轮询路由）
        ↓
ImageGenerator（Pillow / DashScope 多模态 / OpenAI Images 协议）
        ↓
MarkdownFormatter（Markdown→微信内联样式 HTML）
        ↓
WechatClient（stable_token→上传素材→draft/add）
        ↓
Notifier（飞书/企业微信提醒人工审核发表）
        ↓
HistoryStore（草稿ID、状态、输出目录、后续阅读数据回填）
```

## 2. Agent 边界

- **选题 Agent**：聚合热点并用确定性规则打分。后续可加入 LLM 二次评审。
- **写作 Agent**：多阶段调用 OpenAI 兼容 LLM；本地 vLLM/Ollama 与外部 DeepSeek 只需切换 `base_url/model/api_key`。
- **视觉 Agent**：默认 Pillow 模板保证稳定；需要高审美时切换文生图 API。
- **发布 Agent**：只走官方公众号 API，默认仅投草稿箱。
- **人类审批节点**：管理员在公众号后台完成终审与发表，这是合规边界。

## 3. 状态与存储

- 每次运行输出到 `output/YYYYMMDD_HHMMSS/`：
  - `selected_topics.json`
  - `article_1/article.md`
  - `article_1/article.html`
  - `article_1/cover.png`
  - `result.json`
- `output/history.db`：SQLite 历史、7天去重、草稿状态、可回填阅读/点赞/转发。
- `output/web_articles/<article_id>/`：管理台文章的 Markdown、微信 HTML、封面、正文图、归属元数据和版本快照。
- `output/auth.db.article_permissions`：按文章保存用户的 view/edit/push 共享权限；admin 全局可见，editor 默认只管理本人文章，viewer 仅查看获授权文章。
- 文章附件通过短时 HMAC 签名 URL 加载，并再次校验用户状态与文章权限。
- 后台任务保存在进程内，重启后任务列表会清空，但已生成文章不会丢失。

## 4. 安全

- `AppSecret`、模型 Key、Webhook 只放 `config/.env` 或系统环境变量。
- `config/.env` 已在 `.gitignore` 中。
- 云服务器出网 IP 必须加入公众号后台 IP 白名单。
- 默认 `publish_mode=draft_only`；不要在生产中使用浏览器模拟登录。
- Web API 默认强制 JWT 登录；`WEB_ADMIN_TOKEN` 只用于明确携带旧 Token 的自动化兼容，空值不会匿名放行；生产必须配合 HTTPS/Nginx。
- 浏览器只接收密钥是否已配置和掩码，不接收密钥明文。
- Markdown 在服务端先转义再生成受控 HTML，素材路径限制在文章目录内。
- 生成/推草稿接口有每客户端每分钟 5 次的防重复限流。
