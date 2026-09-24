# 端到端微信公众号自动发文智能体项目调研报告

> 调研目标：热点选题 → 写作 → 配图 → 发布 全链路自动化的公众号智能体项目
> 调研方式：web_search 检索 + 通过 GitHub API / raw.githubusercontent 直读各仓库 README、SKILL.md、源码与元数据（Star 数、最近 push 时间）
> 调研时间：2026-08（数据以抓取时刻为准）
> 声明：所有链接均来自实际检索/抓取结果；`fanbuz/wxgzh-cli` 仓库已 404（下文注明）；个别页面（华为云论坛）为 JS 渲染，仅能确认标题与链接。

---

## 0. 结论速览

- 这些项目的**发布路径几乎全部收敛到同一合规通道**：微信公众号官方 API（`access_token → 上传素材 → draft/add 草稿箱 →（可选 freepublish/submit 正式发表）`），并有 3 个共同前提：AppID/AppSecret、服务器 IP 白名单、**最后一步"正式发表"普遍留给人工在手机/后台点击**。
- 形态上分为三类：① **MCP/Agent 框架编排**（ccblog = Claude Code + MCP Server；fengyun-publish = Claude Code skill + Python 工具链）；② **Skill 包**（wechat-flow、16Miku/wechat-auto-publishing、jiji262/wechat-publisher、baoyu-post-to-wechat、LucianaiB 系列，跑在 Claude Code / OpenClaw / Codex 上）；③ **CLI/脚本工具**（wxgzh-cli 已失效、@wenyan-md/cli 等）。
- 没有一款做到"无人值守正式发表"的成熟方案：要么只进草稿箱+人工发表（最稳），要么用浏览器自动化模拟登录（违规高风险，16Miku 仅作为可选通道），要么用 freepublish 但被实测出可见性问题（16Miku 明确不推荐默认使用）。
- 2025 年起微信明确打击"非真人自动化创作"，全自动无人值守 AI 发文有删文/限流/封号风险，**所有方案的"最后人工确认"既是工程决策，也是合规需要**。

---

## 1. 全链路拆解与发布路径（背景）

一个完整的"公众号自动发文智能体"由以下环节组成：

```
热点选题采集 → 选题筛选/评分 → AI 写作（大纲→正文→润色）
  → AI 配图/封面 → 排版（Markdown→微信富文本）→ 发布（草稿箱/正式发表）
  → 定时调度（每日触发）→ 效果数据回填/复盘
```

发布路径（关键约束）：
- **官方 API 草稿箱（draft/add）**：唯一合规且被所有项目采用的主路径；`freepublish/submit` 可正式发表（不推送粉丝、不占群发次数），但部分项目实测其"可见性/列表行为"异常（16Miku）。
- **模拟登录/浏览器自动化（Playwright/Chrome CDP）**：违规高风险（封号、验证码、接口易变），仅 16Miku 通道 B 与 baoyu-post-to-wechat 的 Browser 方式保留为可选。
- 官方接口**无定时发布参数**，定时需外部调度（cron / GitHub Actions / Agent 内建调度）。

---

## 2. 重点项目深度分析

### 2.1 ccblog（GitHub: Mor-Li/ccblog）—— Claude Code 多 Agent 文章生产管线

- **定位与形态**：README 自述 "Production-Ready Multi-Agent System for WeChat Article Generation" /「通过 Claude Code 自动发布博客到微信公众号」。它不是传统单体应用，而是 **Claude Code（MCP 客户端）+ 10 个本地 Agent 定义（`.claude/agents/*.md`）+ 3 个 MCP Server** 的编排式管线，核心输入是"论文/网页/聊天记录/GitHub 仓库链接"，输出是发布到公众号草稿箱的图文文章。
- **Agent 划分（.claude/agents/ 下 10 个）**：
  - 内容抓取：`pdf-parser-mineru`（MinerU Cloud API 解析论文 PDF）、`blog-text-scraper`（网页正文）、`blog-image-scraper`（网页图片本地化）、`repo-explorer`（克隆并分析 GitHub 仓库）；
  - 写作与评审：`wechat-blog-writer`（主笔）、`blog-content-refiner`（润色/补充解释）、`gemini-blog-critic`（对照原文批判性审查）、`gemini-guided-rewriter`（仿 Gemini 文风重构叙事）、`blog-diagram-generator`（生成框架图）、`paper-critic`（论文锐评三则）。
  - 编排靠仓库根 `PROMPT.md`（超级 agent 提示词）：判断内容类型（PDF/网页/聊天记录/openreview/GitHub）→ 选抓取策略 → 写作 → 多轮优化（refiner → gemini critic → refiner → guided rewriter → diagram generator）→ 用 `wenyan-mcp` 发布。
- **模型**：全部走 **API**（非本地 LLM）：Claude（Sonnet 4.5 等）+ Gemini 3 Pro Thinking；图片生成用 `gemini-image-mcp`（OpenAI 兼容接口/千循 API）。README 明说"API 成本高于传统媒体，但质量优先"。
- **发布方式**：`wenyan-mcp`（文颜 MCP Server，`@wenyan-md/mcp`，作者 caol64）→ **微信公众号官方 API 草稿箱**（需 AppID/AppSecret + IP 白名单；8 套 Typora 主题排版；自动上传本地/网络图片）。仓库 `CLAUDE.md` 沉淀了大量发布踩坑：API 传图限 1MB、不支持 WebP、45166 锚点链接报错等。
- **部署要求**：Node.js（npm + tsc 编译 wenyan-mcp）、Claude Code CLI、Python（scripts/ 爬虫工具）、MinerU Cloud API Token、`MAX_MCP_OUTPUT_TOKENS=100000`；可选 Docker 运行 MCP。
- **优点**：多轮交叉验证的质量管线（5 轮 refinement）在同类中最"重"；图文并茂、可复现；Apache-2.0。
- **缺点**：不是"热点选题→自动运营"路线，而是"论文/技术内容深度解读"路线（选题靠作者兴趣）；依赖多个付费 API，单篇成本高；README 安装命令硬编码了作者本机路径；发布只到草稿箱，正式发表仍需人工。
- **维护状态**：**134 stars**，语言 Python，最近 push **2026-07-20**，活跃。

### 2.2 wechat-flow（GitHub: bingyue/wechat-flow）—— 公众号内容生产与自动发布工作流 Skill

- **定位与形态**：README 自述「面向公众号内容生产与自动发布的 Agent 工作流项目」，**兼容 Claude Code 与 OpenClaw 的 skill 格式**（`SKILL.md` 为编排核心，`dist/openclaw/` 为 CI 自动构建的 OpenClaw 版）。本质是"内容生产工作流规范 + 本地工具链 + 外部平台 API 适配层"三层结构：编排层（SKILL.md，Step 1-8）/ 执行层（toolkit/、scripts/）/ 知识层（references/、personas/、themes/）。
- **流程步骤（8 Step）**：环境检查+风格 Onboard → 热点抓取（微博+头条+百度热搜，`scripts/fetch_hotspots.py`）+ 历史去重 + SEO 评分（百度+360，`seo_keywords.py`）→ 7 套写作框架选择 + WebSearch 真实素材采集 + 内容增强 → 写作（真实信息锚定+5 套写作人格注入+编辑锚点）→ SEO 优化+质量自检（`humanness_score.py`）→ 视觉 AI（封面 3 创意+内文 3-6 配图，`toolkit/image_gen.py`，9 个生图供应商自动 fallback）→ 预检+排版（16+ 主题，微信兼容自动修复：外链转脚注、CJK 空格、暗黑模式）+发布 → 写入历史+效果复盘（`fetch_stats.py` 回填阅读数据）+修改学习飞轮（`learn_edits.py`）。
- **技术栈**：**Python**（toolkit/cli.py、converter.py、publisher.py、wechat_api.py、image_gen.py；requirements.txt），可选 Playwright 兜底；模型由宿主 Agent（Claude Code/OpenClaw）提供，生图需第三方图片 API Key。
- **发布方式**：**官方 API 草稿箱**。源码确认：`wechat_api.py` 走 `cgi-bin/token`（带缓存）、`media/uploadimg`（正文图）、`material/add_material`（封面）→ `publisher.py` 调 `cgi-bin/draft/add` 建草稿（支持"小绿书"图片帖 article_type=newspic）；**不做 freepublish 正式发表**。
- **优点**：全链路设计最完整（热点→选题→写作→SEO→生图→排版→草稿→复盘→风格飞轮）；"编辑锚点"策略（留 2-3 处给作者加自己的话）兼顾 AI 效率与人工参与；MIT。
- **缺点**：Star 极少（**4 stars**）、社区小；发布止于草稿箱；README 安装命令写的 clone 地址是 `oaker-io/wechat-flow`，而实际可访问仓库为 `bingyue/wechat-flow`（地址不一致，按用户题设以 bingyue 为准）。
- **维护状态**：语言 Python，创建 2026-04-13，最近 push **2026-04-22**，较新但更新放缓。

### 2.3 wechat-auto-publishing（GitHub: 16Miku/wechat-auto-publishing）—— 公众号自动发文完整 Skill

- **定位与形态**：README 自述为「微信公众号自动发文工作流的完整 Skill」，把「环境准备 → 资讯整理 → 写稿 → 图片准备 → 草稿发布 → 正式发布 → 结果归档 → 定时调度」沉淀为可复现、可交付、可扩展的**本地/服务器可交接**工作流。形态是 **OpenClaw / Claude Code 的 Skill 包**（SKILL.md 编排 + references/*.md 文档层 + templates/ 脚本层，含 `publish.mjs`、`feishu-draft-ready.example.sh/.ps1`），同时发布在 **ClawHub**（`clawhub.ai/16miku/skills/wechat-auto-publishing`，可 `openclaw skills install @16miku/wechat-auto-publishing`）。
- **双通道发布架构**：A) **微信开放平台 API**（草稿/定时/服务器场景）；B) **Chrome DevTools 操控本机浏览器**（Windows 上自动点到扫码，模拟真人"发表"）。
- **4 种发布模式**：
  - `draft_notify_feishu`（**生产默认**）：API 只进草稿箱（media_id）→ 飞书推送「待发布·草稿已进箱」→ **管理员在 mp 后台手点发表**；
  - `draft_only`：仅草稿不通知（调试）；
  - `browser_full`：本机浏览器发表 + 可选飞书推验证码；
  - `api_freepublish`：draft + freepublish 正式发表，**标注为"实验，接受可见性异常"**。
- **核心工程结论（源码/README 明示）**：`freepublish` 常出现「搜得到、页/列表行为异常」，**OpenClaw/服务器日更默认不要无人值守 freepublish**；Linux 无头（Xvfb）登录微信正发成本高且不建议；生产 = 草稿自动化 + 飞书触达 + **人正发**；安全铁律：Skill 包内永不写真实 AppID/Secret/Cookie。
- **依赖工具**：微信官方 API（WECHAT_*）、飞书机器人（FEISHU_NOTIFY_OPEN_ID）、Chrome（可选通道 B）、定时调度与告警（scheduling-and-alerting.md）。
- **优点**：工程化程度高，把"能不能全自动正式发表"的坑（freepublish 可见性、Linux 无头登录、多账号自检、双 ProseMirror 编辑器）沉淀成了文档与默认策略；密钥外置的安全边界清晰。
- **缺点**：默认仍是"草稿+人工发表"而非全自动正式发表；依赖飞书做通知（可换成其他 IM）；仓库较新。
- **维护状态**：**49 stars**，语言 JavaScript，最近 push **2026-07-28**，活跃。

### 2.4 fengyun-publish（GitHub: duliangkuan/fengyun-publish）—— 单人 AI 公众号端到端写作发布流水线

- **定位与形态**：README 自述 "End-to-end AI ship pipeline for WeChat Official Accounts"——作者（风云）一个人运营 AI 赛道公众号「研究 Agent 的云」的生产系统。形态是 **Python 3.x + Claude Code skill 编排**：48 个 `tools/` 脚本（约 15,016 行）+ PowerShell preflight + `WRITE_AGENT.md`（系统级宪法，定义 19 步全流程）+ `CLAUDE.md`（项目上下文）。
- **19 步流水线（核心步骤）**：北极星填空 → Voice DNA 读取 → **ITI 选题**（I-1 广搜聚合：aihot API / TrendRadar / RSSHub / we-mp-rss 16 个公众号 feed / arxiv → topic_recommender 排序 + 7 天去重）→ I-2 深搜调研 → `fengyun-writer` skill 写稿 → `fengyun_lint` 机械检查 → `humanizer-zh` 去 AI 味 → 王小波语感预审 → **三轨 critic 投票**（A 数字分 SOP≥60 / B 灵魂判断 / C 挂名意愿，gate_tree 决策）→ **gate.py 物理门控**（PreToolUse hook：11 组 pass_flag、8 项 fake-pass 防伪、Round 25 配图非空硬规则、R18 自曝 AI 身份立即终止）→ 配图决策（花叔 Mode 2）+ **Seedream 内文图生成** → 封面模板生成 → huashu 排版 + **推草稿** → 报告 + audit log。
- **发布方式**：**微信公众号官方 API 推草稿箱**（WECHAT_APPID/SECRET + 自建 Cloudflare Worker 反代 `mp-proxy-worker` 解决官方代理不稳），**最后一步由作者在手机审阅+发布**（NORTH_STAR 红线：草稿箱最后一击永不自动化）。
- **依赖模型/服务**：Anthropic Claude（所有 skill 底层）、火山引擎方舟 Seedream（配图）、DeepSeek（标题钩子公式提炼，V4-flash）、critic 用 LightGBM 机器学习模型（2730 篇 KOL 语料 + 80k 评论训练，SOP v2.1）。
- **优点**：极端工程化——物理 gate 拦截、三轨独立评委防"自嗨"、数据驱动选题（2730 篇语料 + parquet 特征矩阵）、数据飞轮（baseline 193→198）；Phase 1-18 的真实运行报告都在仓库里，是"一个人 + AI 运营公众号"的最完整公开样本。
- **缺点**：**重度个人化**（硬编码作者风格画像、卡兹克/花叔对标，语料 corpus/ 与 437MB db.sqlite 不随仓库分发）；前提 Windows + PowerShell + Docker（we-mp-rss/RSSHub）；`PROJECT_GAPS_AUDIT.md` 自审显示数据飞轮、发布后监控、humanizer 自动接入等约 6 成环节仍缺失；上云调研 Phase 10-16 暂停。
- **维护状态**：**25 stars**，语言 Python，最近 push **2026-06-22**，个人项目、活跃但受众小；MIT。

### 2.5 OpenClaw（原 Claude Computer Use 社区版）与 ClawHub Skill 生态

- **OpenClaw 本体**（GitHub: openclaw/openclaw，**约 38.7 万 stars**，TypeScript，MIT）：开源自主 AI 助理，由奥地利工程师 Peter Steinberger 开发，2025 年末以 **Clawdbot** 发布，后更名 **Moltbot**，最终定名 **OpenClaw**（维基百科记录），因红龙虾图标被昵称"龙虾"。本地优先、可自托管：Gateway 为本地控制面（会话/工具/事件/渠道），Control UI/CLI/TUI 接入，Channels 连接微信/飞书/TG/Discord 等 20+ 平台，通过 **tools / skills / plugins** 扩展能力，ClawHub 为官方技能注册表。安装：`npm install -g openclaw` 或官方安装脚本（macOS/Linux/WSL2/Windows）。
  - 关于"原 Claude Computer Use 社区版"：中文社区有将其与 Claude Computer Use 关联的说法，但官方与维基记载的前身是 Clawdbot（本地化 AI 电脑操作助手定位），报告按可查证口径描述。
- **Skill 机制**：Skill = 以 `SKILL.md` 为说明/编排的模块化"能力包"（可含 scripts/、assets/、package.json），模型把 Skill 当作工具调用（而非自己去读 markdown）——这是 OpenClaw 与 Claude Code 在 Skill 实现上的差异（知乎技术文：《OpenClaw 的 Skills 的实现和 Claude Code 不一样》）。安装方式：`openclaw skills install <作者>/<技能>` 或 `npx skills add <owner>/<repo>`。
- **ClawHub 上的 wechat-auto-publisher skill（gdp6539/wechat-auto-publisher）**：公众号自动化技能包，"从选题到写作到发布全流程自动化，热点监控+AI 写作+草稿管理"。
  - 流程：**热点监控**（GitHub Trending / Hacker News / 知乎热榜 / 微博热搜 / 36氪 / 虎嗅）→ **关键词筛选**（AI/科技赛道）→ **AI 文章生成** → **Markdown 草稿保存（人工审核）**；
  - 依赖：node-fetch、cheerio、dotenv、node-cron（定时）；**百炼 API（通义千问 DASHSCOPE_API_KEY）** 负责文章生成；**WECHAT_APP_ID/SECRET** 负责发布；
  - 命令：`/wx-publish`（全流程）、`/wx-monitor`、`/wx-generate`、`/wx-github`、`/wx-hn`；安装 `openclaw skills install @gdp6539/wechat-auto-publisher`。
  - 注意：该技能本质是"生成草稿+人工审核"（SKILL.md 标注"草稿管理-生成 Markdown 草稿，方便人工审核"），发布能力需自行配置公众号凭证。
- **微信公众号文章发布助手案例（华为云社区）**：[OpenClaw案例参考-微信公众号文章发布助手](https://bbs.huaweicloud.com/forum/thread-0212720971562020323-1-1.html)（论坛帖子为 JS 渲染，正文未能抓取，仅确认标题与链接存在；同系列还有"多智能体内容工厂"案例帖）。华为云另有 OpenClaw 部署方案 PDF 与"基于华为开发者空间云开发环境零构建零部署 OpenClaw(Moltbot)"教程，可作为云上部署参考。

### 2.6 其他类似项目

#### wxgzh-cli（GitHub: fanbuz/wxgzh-cli）
- 定位：**微信公众号开放平台 CLI，「agent friendly」**——配置 appid/secret 后走官方 API（token/素材/草稿/发布），支持 Markdown 转草稿、传封面、正式发布，并附 `docs/security.md` 密钥安全说明（环境变量、避免落盘）。此前调研确认其形态为合规的官方 API 封装。
- **维护状态：⚠️ 仓库当前 404（GitHub API 返回 404，作者 fanbuz 名下已无公开仓库），推测已删除或转私有，无法访问验证。** 同类替代：`lyhue1991/wxgzh`（19 stars，Agent Skill，"markdown → 微信公众号草稿箱"，官方 API）。

#### wechat-publisher（GitHub: jiji262/wechat-publisher）
- 定位与形态：**Skill + 独立 CLI 双形态**（README："可作为 Claude Code / Codex / Cursor 的 Skill，也可独立命令行调用"），**231 stars**，最近 push 2026-08-01，MIT，活跃。仓库语言标记为 HTML（含大量主题预览资源）。
- 流程（SKILL.md 定义 **7 阶段**）：① 理解需求与收集素材（brief.md）→ ② 全网信息搜索（权威层 WebSearch + **真人层** Reddit/V2EX/即刻/小红书语料）→ ③ 撰写骨架稿（8 种结构 × 7 种开头钩子）→ 3.5 **人味化改写**（反 AI 检测）→ ④ AI 配图（`scripts/generate_image.py`，OpenAI/Gemini 后端）→ ⑤ 排版（Markdown→微信 HTML，**15 套主题**，全内联样式）→ 5.5 **AI 味 gate**（`ai_score.py` 5 维打分，默认阈值 45，不过拦稿）→ ⑥ 发布草稿箱（可选 ⑦ 多平台同步）。
- 发布方式：**官方 API 草稿箱**（AppID/AppSecret + IP 白名单；access_token 2h 缓存；正文图 uploadimg、封面 add_material；错误码 FAQ 齐全）。多账号（main/tech）+ 不同 voice 人格，强调"风格差异化即反 AI 信号"。
- 依赖：Python（requests/pyyaml）、Node（`baoyu_image_gen.ts` 生图）、Gemini/OpenAI API。

#### baoyu-skills（GitHub: JimLiu/baoyu-skills）—— 宝玉技能集
- 定位与形态：宝玉（JimLiu）开源的 **AI Agent 技能集**，**25,341 stars**，TypeScript，920 个文件，20+ skills，同时发布到 ClawHub（按 skill 独立安装）。安装：`npx skills add jimliu/baoyu-skills` 或 `/plugin marketplace add JimLiu/baoyu-skills`。
- 公众号三件套：**baoyu-cover-image**（封面生成，5 维体系：Type×Palette×Rendering×Text×Mood，77 种组合）、**baoyu-article-illustrator**（正文配图）、**baoyu-post-to-wechat**（发布：支持**官方 API（推荐）**/ **Browser（Chrome 扫码登录）** / **Remote API（SSH SOCKS5 隧道借白名单 IP）** 三种方式，多账号管理）。另有 baoyu-markdown-to-html / baoyu-format-markdown / baoyu-diagram / baoyu-infographic 等配套。凭证放 `~/.baoyu-skills/.env` 或项目级 `.env`。
- 定位为"内容创作神器"，覆盖公众号全链路（写作→封面→配图→排版→发布），是社区最流行、维护最活跃的 Skill 生态之一。

#### LucianaiB（龙虾）
- 定位：OpenClaw 生态创作者/博主，腾讯云开发者社区作者（2026-03-17《干货 | 手把手教你用 OpenClaw + Skill 实现微信公众号全自动创作发布》），提供一套**公众号 AI 分身**技能组合：**china-hot-ranks**（中国热榜聚合器：微博/抖音/B站/百度热榜）、**wechat-topic-selector**（公众号选题助手，运营专家视角选 3 个爆款话题）、**wechat-publisher**（发布到草稿箱）。
- 流程：实时热榜监控 → AI 深度选题与拆解（模型示例为 qwen-3.5-plus）→ 一键同步草稿箱 → 手机端检查后点击发布；可加心跳机制每日推送（飞书）。部署常与腾讯云轻量服务器（OpenClaw 跑在云上）搭配。
- **维护状态：⚠️ 文章内提供的 github.com/lucianaib0318/* 三个仓库当前均 404**（GitHub 检索仅剩 `LucianaiB2004/LucianaiB2004` 空壳账号），技能包已不可直接安装；但其"热榜→选题→草稿箱→人工发布"方案思路与腾讯云部署教程仍可参考。

#### 腾讯云上 OpenClaw 公众号自动发文教程（同类方案参考）
- 程序员小饭（2026-03-30）：OpenClaw + **wechat-article-writer** Skill → 写文→自动排版→上传素材→**写入草稿箱**；人工打开后台草稿箱检查后点发布。强调 access_token 是公众号自动化核心（`cgi-bin/token`，expires_in 7200s）。
- 大盘鸡拌面（2026-06-21）：对比三类方案（通用 AI 写作+手动复制 / 第三方托管平台 / **OpenClaw 本地链路**）后推荐本地链路：Windows10 + 免费 OpenClaw + 已认证公众号 + appid/secret + Python3.9，**不托管账号权限、本地留存日志**；3 个必装 Skill（公众号图文格式化 / 全网热点低敏抓取 / 新媒体风控自检）+ 全局固定 Prompt（原创度≥85%、排版规范、禁广告极限词），支持定时任务与每周人工批量审核选题。核心观点：**OpenClaw 本地链路是目前腾讯生态官方推荐的合规新媒体自动化方案**（不模拟登录、只走 API）。
- 意疏（2026-04-29）：《小龙虾一篇讲透，从零到跑起来》——openclaw 安装 → 公众号自动发布 → 每日任务执行教程。

#### 其他参考：wenyan（文颜）工具链与个人实录
- 掘金实录（AI山脚学长，2026-02-26，《我用 AI 搭了一个公众号自动发文系统，全程踩坑实录》）：**Claude Code（写作引擎）+ AGENTS.md（写作规范）+ KIE API（生图：GPT-Image-1/Midjourney/Flux）+ ImageMagick（封面裁剪 900×383）+ @wenyan-md/cli（排版发布）** → 草稿箱；核心教训：正确包名是 `@wenyan-md/cli`（`wenyan-cli` 404）、公众号凭证用环境变量、**IP 白名单是最大坑（40164）**。
- 文颜（wenyan）工具链与 ccblog 同源：wenyan-mcp（MCP Server）/ wenyan-cli（CLI）/ wenyan-pc（桌面）/ wenyan-core（嵌入库），一键 Markdown→公众号/知乎/头条草稿箱。

---

## 3. 方案对比总表

| 方案 | 形态 | 技术栈 | 发布方式 | 模型/API 依赖 | 维护状态 | 是否适合个人快速部署 |
|---|---|---|---|---|---|---|
| **ccblog**（Mor-Li/ccblog） | MCP 客户端（Claude Code）+ 10 Agent + 3 MCP Server | Python 脚本 + Node（wenyan-mcp）+ Claude Code | 官方 API → **草稿箱**（wenyan-mcp） | Claude + Gemini（API）、MinerU、文颜 MCP | 134★，push 2026-07，活跃 | ⚠️ 中等：需 Claude Code + 多个 API Key，面向"论文/技术解读"而非热点运营；质量最高但成本高 |
| **wechat-flow**（bingyue/wechat-flow） | Skill（Claude Code / OpenClaw 双格式） | Python（toolkit/scripts）+ 可选 Playwright | 官方 API → **草稿箱**（draft/add，含小绿书图片帖） | 宿主 LLM + 9 家生图 API（可 fallback） | 4★，push 2026-04 | ✅ 适合：全链路（热点→写作→SEO→生图→排版→草稿→复盘）开箱即用，MIT，但 Star 少、社区小 |
| **wechat-auto-publishing**（16Miku） | OpenClaw / Claude Code Skill（ClawHub 可安装） | JavaScript（publish.mjs）+ 飞书脚本 | 官方 API 草稿 + 飞书通知 + **人工发表**（默认）；Chrome CDP 浏览器通道可选；freepublish 实验 | 微信 API、飞书、Chrome（可选） | 49★，push 2026-07，活跃 | ✅ 适合服务器/OpenClaw 日更；工程坑沉淀最全，但默认需要飞书 |
| **fengyun-publish**（duliangkuan） | Claude Code skill + Python 工具链（工业流水线） | Python 3.x（48 脚本）+ PowerShell + Docker + Claude Code | 官方 API → **草稿箱**（人工手机终审） | Claude、Seedream（配图）、DeepSeek、LightGBM critic、aihot/RSSHub/we-mp-rss | 25★，push 2026-06，个人项目 | ❌ 不适合直接复用：重度个人化、语料/数据库不随仓库分发、Windows+Docker 前提、6 成环节仍为 TODO |
| **OpenClaw + ClawHub wechat-auto-publisher**（gdp6539） | OpenClaw Skill（SKILL.md + Node 脚本） | Node（node-fetch/cheerio/node-cron） | 热点监控→AI 写作→**Markdown 草稿（人工审核）**；发布需自配公众号凭证 | 百炼 API（通义千问）+ 微信 API | ClawHub 在架（页面可访问），仓库本身可安装 | ✅ 适合：OpenClaw 用户一键安装；但"发布"部分较弱（主打草稿+人工） |
| **OpenClaw 本体 + 社区教程**（腾讯云/华为云案例） | 本地 AI 助理 + Skill 生态 | TypeScript（npm 包）+ 20+ 渠道 | 官方 API → 草稿箱（案例主流）；不模拟登录 | 任意主流/本地模型 | 38.7万★，push 2026-08，极活跃 | ✅ 适合：作为承载 Skill 的底座，官方推荐路线 |
| **wxgzh-cli**（fanbuz/wxgzh-cli） | CLI 工具 | 未确认（仓库已不可访问） | 官方 API 封装（token/素材/草稿/发布） | 微信 API | ⚠️ **仓库 404**，已失效 | ❌ 不可用；替代：lyhue1991/wxgzh（19★） |
| **wechat-publisher**（jiji262） | Skill + 独立 CLI | Python + Node + Gemini/OpenAI API | 官方 API → **草稿箱**（反 AI 味 gate 后） | 微信 API、Gemini/OpenAI 生图 | 231★，push 2026-08，活跃 | ✅ 适合：素材→写作→配图→排版→发布一条命令；反 AI 检测是特色 |
| **baoyu-skills**（JimLiu） | Skill 生态（20+ 技能） | TypeScript + npx | 官方 API（推荐）/ Chrome 浏览器 / SSH 隧道三种发布 | 宿主 LLM + 生图后端（Gemini 等） | 25,341★，push 2026-07，极活跃 | ✅ 适合：内容创作神器，公众号三件套（封面/配图/发布）即装即用 |
| **LucianaiB 龙虾** | OpenClaw Skill 组合（热榜/选题/发布） | Node + 微信 API + qwen | 官方 API → **草稿箱**（人工发布） | 通义千问 qwen-3.5-plus | ⚠️ 技能仓库 404，仅文章可参考 | ❌ 技能已不可装；思路（热榜→选题→草稿→人工发）可借鉴 |
| **wenyan 工具链**（@wenyan-md/*，caol64） | CLI / MCP Server / 桌面应用 | Node + npm | 官方 API → 草稿箱（多平台） | 微信 API | 活跃（wenyan-mcp 有 Docker/npm 分发） | ✅ 适合：仅"排版+发布"环节的轻量选择 |

---

## 4. 选型建议与红线提示

1. **发布通道选型（最重要）**：全部走**官方 API 草稿箱**，正式发表要么人工在后台点（最稳），要么接受 `freepublish` 的可见性风险（16Miku 实测不推荐）。**不要**在生产环境用模拟登录/浏览器自动化做正式发表（微信 2025 年起打击非真人自动化创作，封号风险高）。
2. **个人快速部署推荐组合**：
   - 想"一句话日更"：OpenClaw + baoyu 三件套（封面/配图/发布）或 16Miku/wechat-auto-publishing（草稿+飞书+人工发表）；
   - 想要完整热点→写作→排版流水线：wechat-flow 或 jiji262/wechat-publisher（后者带反 AI 味 gate）；
   - 深度技术内容/论文解读：ccblog（质量上限最高，成本也最高）；
   - 只缺"排版+发布"：wenyan（@wenyan-md/cli 或 wenyan-mcp）。
3. **合规红线**：AppSecret 勿入库/勿进 Skill 包；IP 白名单必配（40164）；API 传图限 1MB、不支持 WebP（45166/40113 常见坑）；关注官方对 AI 生成内容的标识要求（2025-08 起 AI 内容需声明）；高频无人值守 AI 发文有删文/限流风险，保留人工终审环节。
4. **定时**：官方无定时发布接口，用外部调度（cron / GitHub Actions schedule / OpenClaw 定时任务）在目标时刻触发"建草稿/发表"。

---

## 5. 主要参考链接

**GitHub 仓库（均已直读 README/元数据）**
- ccblog：https://github.com/Mor-Li/ccblog
- wechat-flow：https://github.com/bingyue/wechat-flow
- wechat-auto-publishing：https://github.com/16Miku/wechat-auto-publishing
- fengyun-publish：https://github.com/duliangkuan/fengyun-publish
- OpenClaw：https://github.com/openclaw/openclaw
- wechat-publisher（jiji262）：https://github.com/jiji262/wechat-publisher （SKILL.md：https://github.com/jiji262/wechat-publisher/blob/main/SKILL.md）
- baoyu-skills：https://github.com/JimLiu/baoyu-skills
- lyhue1991/wxgzh（wxgzh-cli 替代）：https://github.com/lyhue1991/wxgzh
- wenyan-mcp：https://github.com/caol64/wenyan-mcp

**ClawHub / 技能市场**
- 16Miku skill：https://clawhub.ai/16miku/skills/wechat-auto-publishing ；https://openclawai.io/skills/skill/wechat-auto-publishing
- wechat-auto-publisher（gdp6539）：https://docs.clawhub.ai/gdp6539/skills/wechat-auto-publisher ；https://clawhub-skills.com/skills/wechat-auto-publisher
- 华为云案例-微信公众号文章发布助手：https://bbs.huaweicloud.com/forum/thread-0212720971562020323-1-1.html

**文章/教程**
- 许雪里《使用OpenClaw+Skill自动发布微信公众号文章》：https://www.cnblogs.com/xuxueli/p/19721838
- LucianaiB《手把手教你用 OpenClaw + Skill 实现微信公众号全自动创作发布》（腾讯云）：https://cloud.tencent.com.cn/developer/article/2640353
- 程序员小饭《告别断更焦虑：我把 OpenClaw 变成了公众号"自动驾驶"神器》（腾讯云）：https://cloud.tencent.cn/developer/article/2647721
- 大盘鸡拌面《公众号全自动发布：OpenClaw 从撰稿到推送全链路实操》（腾讯云）：https://cloud.tencent.com.cn/developer/article/2694548
- AI山脚学长《我用 AI 搭了一个公众号自动发文系统，全程踩坑实录》：https://juejin.cn/post/7610616824568954920
- 微信公众号自动发文 Skill 全链路（CSDN）：https://blog.csdn.net/m0_73479109/article/details/159867021
- 创作自动化开源仓库深度调研 2024—2026（B 站）：https://www.bilibili.com/opus/1197492416110657560
- OpenClaw 历史（维基百科）：https://zh.wikipedia.org/zh-cn/OpenClaw
- OpenClaw 更名报道（CHINAZ）：https://m.chinaz.com/ainews/25122.shtml

**微信官方（发布路径背景）**
- 发布能力：https://developers.weixin.qq.com/doc/subscription/guide/product/publish.html
- 发布草稿 freepublish/submit：https://developers.weixin.qq.com/doc/subscription/api/public/api_freepublish_submit
- 稳定版 access_token：https://developers.weixin.qq.com/minigame/dev/api-backend/access-token/api_getstableaccesstoken.html