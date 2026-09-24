# 微信公众号自动发布文章技术路径调研报告

> 调研时间：2025 年（基于 2024–2025 年公开资料）
> 调研范围：官方 API、第三方开源工具（CLI / Skill / MCP Server / SDK）、非官方模拟登录路径、企业微信等生态中间件、定时发布方案
> 声明：所有链接均来自实际检索结果；接口细节以微信官方文档为准，具体数字以官方接口频控返回为准。

---

## 0. 结论速览

- **最合规、最推荐的路径**：微信官方「草稿箱（draft/*）+ 发布（freepublish/*）」接口。订阅号、服务号均适用；发布（发表）不占用群发次数、一天可多次，是自动化的官方正路。
- **官方接口不支持「定时发布」参数**：`freepublish/submit` 没有定时字段；后台 UI 的定时能力主要针对「群发」（定时群发）。定时发布需由外部调度器（cron / APScheduler / GitHub Actions schedule / 云函数定时器）在目标时刻触发。
- **模拟登录公众号后台（扫码 + 无头浏览器 / 协议库）属于违规路径**：存在封号、验证码、token 失效风险，且微信 2025 年起明确打击「AI 自动化创作 / 代笔」，仅建议低频、低风险场景兜底。
- **企业微信 webhook 不能发布公众号文章**：企业微信群机器人只能向企业微信群发消息，与公众号体系完全隔离，只能用于「发布成功后的通知」。
- **第三方工具（wxgzh-cli、wechat-publisher、publish-mcp-server、woa 等）本质都是官方 API 的封装**，不改变能力边界，价值在易用性与 Agent 集成。

---

## 1. 技术路径一：微信公众号官方 API（合规首选）

官方「服务端 API」是唯一合规的自动化发布通道。核心链路为：**取 token → 上传素材 → 建草稿 → 发布 → 查状态**。

### 1.1 获取 access_token

**经典方式（appid + secret）**

```
GET https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=APPID&secret=APPSECRET
```

- 返回 `access_token`（有效期 2 小时，即 `expires_in=7200`）。
- 官方要求缓存并复用，避免频繁调用；经典接口每日调用有上限（旧文档为 2000 次/天），超限返回 `45009` 等错误码。

**稳定版接口（stable_token，官方推荐）**

```
POST https://api.weixin.qq.com/cgi-bin/stable_token
body: { "grant_type": "client_credential", "appid": "...", "secret": "...", "force_refresh": false }
```

- 稳定版接口默认复用缓存 token，只有 `force_refresh=true` 时才强制刷新，可显著减少因高频刷新导致的限流，是 2023 年后官方推荐做法。
- 参考：[微信官方文档·获取稳定版接口调用凭据](https://developers.weixin.qq.com/minigame/dev/api-backend/access-token/api_getstableaccesstoken.html)、[CSDN：getStableAccessToken 详解](https://blog.csdn.net/longfeng995/article/details/130185005)、[博客园：获取稳定版接口调用凭据](https://www.cnblogs.com/AtlasLapetos/p/18640885)

### 1.2 草稿箱接口（draft/add、draft/update）

- `POST /cgi-bin/draft/add`：新增草稿，参数为 `articles` 数组（字段含 `title`、`author`、`digest`、`content`、`content_source_url`、`thumb_media_id`、`need_open_comment`、`only_fans_can_comment` 等），返回草稿 `media_id`。
- `POST /cgi-bin/draft/update`：按 `media_id` + `index` 更新草稿（修改标题/正文/封面等）。
- 配套接口：`draft/get`（获取）、`draft/delete`（删除）、`draft/count`（计数）、`draft/batchget`（批量获取）。
- **注意点**：正文 `content` 里的图片须先通过「上传图片」类接口换成本地可访问的 URL；封面 `thumb_media_id` 来自永久素材中的图片 `media_id`。
- 参考：[CSDN：基于微信官方 API 构建本地化公众号草稿管理工具与自动化发布流水线](https://blog.csdn.net/weixin_28686771/article/details/160698304)、[ClawHub：WeChat MP Publish Toolkit](https://clawhub.ai/yyi162751-collab/skills/wechat-mp-publish-toolkit)
### 1.3 发布接口（freepublish/submit、freepublish/get）

- `POST /cgi-bin/freepublish/submit`：参数为草稿 `media_id`，把草稿「发表」（即后台的「发表」按钮）。**发布不推送给粉丝、不占用群发次数、一天可多次**。
- `POST /cgi-bin/freepublish/get`：发布是异步的，用 `publish_id` 轮询查询发布状态（`article_status`、`article_detail` 等），成功后才算真正上线。
- 配套接口：`freepublish/delete`（删除已发布文章）、`freepublish/batchget`（分页获取已发布列表）。
- **「发布」vs「群发」**：群发（mass send）会推送给所有粉丝、受次数限制（服务号每月约 4 次、订阅号每天 1 次）；发布（freepublish）只把文章发表到公众号主页，不打扰粉丝，适合高频自动化。
- 参考：[微信官方文档·发布能力](https://developers.weixin.qq.com/doc/subscription/guide/product/publish.html)、[微信官方文档·发布草稿接口（freepublish/submit）](https://developers.weixin.qq.com/doc/subscription/api/public/api_freepublish_submit)、[博客园：公众号「发表」和「群发」有什么区别](https://www.cnblogs.com/omnigoai/p/22511623)、[站长之家：公众号内测「发布」功能，支持一天多次](https://www.chinaz.com/2021/0928/1309341.shtml)

### 1.4 永久素材管理（material/add_news、material/add_material）

- `POST /cgi-bin/material/add_material`：上传永久素材（`type=image/voice/video/thumb`），返回 `media_id`，可用于封面图、正文图片等。
- `POST /cgi-bin/material/add_news`：新增永久图文素材（老方案，用于群发/客服消息；新版自动化通常改用 `draft/add`）。
- `POST /cgi-bin/material/uploadimg`：上传图片并获得仅用于图文正文的 URL（不占素材库数量）。
- 配套接口：`material/get_material`、`material/del_material`、`material/get_materialcount`、`material/batchget_material`。
- **限制**：永久素材总量有上限（图片类约 5000 张等，以官方素材管理文档为准）；上传图文常见错误如 `40007 invalid media_id`（media_id 不存在/类型不符）。
- 参考：[微信官方文档·上传永久素材](https://developers.weixin.qq.com/doc/service/api/material/permanent/api_addmaterial)、[微信官方文档·素材管理](https://developers.weixin.qq.com/doc/subscription/guide/product/asset.html)、[md2wechat：addMaterial 注意事项与常见问题](https://www.md2wechat.com/zh/blog/wechat-add-material-api-notes)、[CSDN：上传图文 errcode 40007 解决方案](https://blog.csdn.net/amberom/article/details/145949413)

### 1.5 能力边界：定时？频率限制？权限/认证？

| 维度 | 结论 |
|---|---|
| **能否定时发布** | **不能**。`freepublish/submit` 与 `draft/*` 均无定时参数；官方无「定时发布接口」。后台 UI 的定时能力仅覆盖「群发」（定时群发，见第 5 节）。 |
| **发布次数限制** | 「发布」本身不限制每日次数（区别于群发）；但各接口有分钟级/日级频控（官方未公开精确阈值，超限返回 `45008`/`45009` 等错误码）。参考[接口调用频次限制说明](https://m.w3cschool.cn/weixinkaifawendang/2yqt1q8e.html)与[官方英文文档·频次限制](https://developers.weixin.qq.com/doc/service/en/guide/dev/api/limit.html)。 |
| **账号类型** | 草稿/发布（发表）能力对**订阅号、服务号均开放**（官方「发布能力」文档同时挂在 subscription 与 service 两个域下）；个人订阅号后台也有「发表」按钮，API 侧多数情况可用。 |
| **是否需要认证** | 「发布能力」属于基础能力，官方文档未要求认证；但**永久素材接口（material/*）在接口权限列表中通常要求「认证」**，未认证账号需先测试确认。群发类接口（mass/*）一般要求「服务号 + 认证」。参考[公众平台接口权限列表说明](https://www.aoyacms.com/18/58/326.html)、[微信公众号接口权限说明（w3cschool）](https://m.w3cschool.cn/weixinkaifawendang/qbtf1q8d.html)、[CSDN：三类微信公众号之间的区别及接口权限](https://blog.csdn.net/weixin_40485506/article/details/89477481) |

> 提示：不同时期接口权限有调整，落地前请以官方文档 + 微信开放社区（fuwu.weixin.qq.com）问答为准，并用测试号先行验证。

### 1.6 官方 API 标准自动化流水线（伪代码级）

```
1. 取 token（stable_token，缓存 2 小时内复用）
2. material/add_material(type=image) 上传封面 → 得到 thumb_media_id
3. material/uploadimg 上传正文图片 → 得到正文 URL
4. draft/add（title/content/thumb_media_id...） → 得到草稿 media_id
5. freepublish/submit(media_id) → 得到 publish_id
6. 轮询 freepublish/get 直到 article_status=成功
```

参考：[CSDN：基于微信官方 API 构建本地化公众号草稿管理工具与自动化发布流水线](https://blog.csdn.net/weixin_28686771/article/details/160698304)、[CSDN：从零构建微信公众号自动化发布引擎：模块化设计与实战](https://blog.csdn.net/weixin_29051811/article/details/159604369)、[CSDN：n8n 对接微信公众号（正式发布）](https://blog.csdn.net/MKxcq/article/details/156449666)

---

## 2. 技术路径二：第三方开源发布工具

这类工具本质都是官方 API 的封装，价值在「Markdown → 草稿 → 发布」一键化与 Agent 集成。

### 2.1 wxgzh-cli（GitHub: fanbuz/wxgzh-cli）

- **原理**：微信公众号开放平台命令行工具（「agent friendly」），配置 appid/secret 后走官方 API（token/素材/草稿/发布），支持把 Markdown 转草稿、传封面、正式发布；提供 `docs/security.md` 说明密钥安全（环境变量、避免落盘等）。
- **优点**：官方 API、合规、易嵌入 CI/CD 与 Agent 工作流；命令行形态便于脚本化。
- **缺点**：依赖官方接口权限（素材接口需认证号）；需要自行处理 token 缓存与频控。
- **风险**：低（合规路径）。
- 参考：[GitHub: fanbuz/wxgzh-cli](https://github.com/fanbuz/wxgzh-cli)、[wxgzh-cli/docs/security.md](https://github.com/fanbuz/wxgzh-cli/blob/main/docs/security.md)

### 2.2 wechat-publisher（GitHub: jiji262/wechat-publisher）

- **原理**：Claude/Agent「Skill」形态的公众号发布技能，`SKILL.md` 定义完整流程：读取 Markdown → 上传封面/正文图片 → 新建草稿 → 发布 → 查询结果；走官方 API。
- **优点**：对 Claude Code / OpenClaw 等 Agent 框架友好，开箱即用；流程文档化、可审计。
- **缺点**：本质仍是官方接口封装，受同样的权限/频控约束；依赖 Agent 运行时。
- **风险**：低（合规路径）。
- 参考：[GitHub: jiji262/wechat-publisher](https://github.com/jiji262/wechat-publisher)、[wechat-publisher/SKILL.md](https://github.com/jiji262/wechat-publisher/blob/main/SKILL.md)

### 2.3 @jesonliu/publish-mcp-server

- **原理**：npm 发布的 MCP（Model Context Protocol）Server，把公众号发布能力包装成 MCP 工具（上传素材、建草稿、发布等），供 Claude Desktop、Cursor 等 MCP 客户端调用；走官方 API。
- **优点**：AI 编程/对话工具可直接「对话式」发布；MCP 生态标准接入。
- **缺点**：较新、生态尚小；安全边界（把 appid/secret 交给 MCP 客户端）需自行把控。
- **风险**：低（合规路径，但注意密钥管理）。
- 参考：[npm: @jesonliu/publish-mcp-server](https://www.npmjs.com/package/@jesonliu/publish-mcp-server)、[CSDN：从 Markdown 到公众号，自动发布新体验——文颜 MCP Server](https://adg.csdn.net/69533a605b9f5f31781bef0e.html)

### 2.4 @ziikoo/woa

- **原理**：npm 上的微信公众号（WeChat Official Account，WOA）SDK/中间件，封装官方接口（token、素材、草稿、发布等），供 Node 项目调用。
- **优点**：库形态便于集成进自研流水线；npm 生态。
- **缺点**：公开文档较少（npm 页面信息有限），需以仓库/源码为准评估维护活跃度。
- **风险**：低（合规路径，但第三方库代码需自行审计）。
- 参考：[npm: @ziikoo/woa](https://www.npmjs.com/package/@ziikoo/woa)
### 2.5 其他值得关注的项目

- **lyhue1991/wxgzh**：Agent Skill，「markdown → 微信公众号草稿箱」，官方 API。[GitHub](https://github.com/lyhue1991/wxgzh)
- **16Miku/wechat-auto-publishing**：完整自动发文工作流 Skill（环境准备→资讯整理→写稿→图片准备→草稿发布→正式发布→结果归档→定时调度）。[GitHub](https://github.com/16Miku/wechat-auto-publishing)、[CSDN 介绍](https://blog.csdn.net/m0_73479109/article/details/159867021)
- **wechat-oa-skill / wechat-auto-publisher**：同类公众号自动发布 Skill。[CSDN：wechat-oa-skill 核心原理与实战](https://blog.csdn.net/weixin_42523907/article/details/161030597)
- **wenyan-cli（文颜）**：Markdown → 公众号排版/发布工具链，社区调研称「最佳现代方案：wenyan-cli + GitHub Actions 自动发布草稿」。[B 站调研：创作自动化操作开源仓库深度调研（2024—2026）](https://www.bilibili.com/opus/1197492416110657560)
- **yaojiwei520/Wechat-messages**：公众号定时推送消息示例。[GitHub](https://github.com/yaojiwei520/Wechat-messages)

---

## 3. 技术路径三：非官方路径（模拟登录公众号后台）

### 3.1 原理与代表性工具

- **原理**：模拟真人登录 `mp.weixin.qq.com` 后台（扫码获取 cookie/后台 token），再调用后台内嵌接口（`/cgi-bin/*`）或直接操作 DOM 完成「新建草稿 → 发表」；或走微信客户端协议（个人微信）间接操作。
- **代表性工具/类库**：
  - **wechatpy**（Python）：主流是**官方 API 封装**（token/素材/消息/菜单），本身并不提供公众号后台的模拟登录能力；如需后台登录，通常配合 requests 保存登录态实现，注意这与 itchat 类「个人微信协议」不是一回事。
  - **itchat / Wechaty（puppet-wechat4u 等）**：个人微信协议机器人，与公众号后台无关，且个人微信协议封号风险极高，社区大量讨论其不可靠性。[CowAgent issue #2457：itchat 被封风险](https://github.com/zhayujie/CowAgent/issues/2457)、[LINUX DO：Wechaty 免费协议讨论](https://linux.do/t/topic/201686)、[GitHub: puppet-wechat4u](https://github.com/Nimbly8836/puppet-wechat4u)
  - **Playwright / Puppeteer / DrissionPage**：无头/有头浏览器自动化操作 mp.weixin.qq.com 后台（扫码登录 → 进草稿箱 → 点「发表」）。典型实践见 [博客园：公众号封面自动上传——扫码登录能进草稿，自动正式发布仍看 API](https://www.cnblogs.com/omnigoai/p/22504495)（该文指出：模拟登录能完成草稿环节，但正式发布仍建议走 API，说明纯模拟路径的局限）。
- **优点**：不依赖开发者接口权限；理论上任何账号（含未认证个人号）都能操作；可模拟「真人」行为绕过部分接口限制。
- **缺点/风险**：
  - **封号风险高**：微信对非真人自动化操作持续收紧，2025 年明确打击「AI 自动化创作 / 代笔」，违规账号会被封禁处理。[虎嗅：微信明确禁止 AI 自动化创作，违规账号将被封禁](https://www.huxiu.com/article/4850513.html)、[搜狐：用 AI 写公众号年赚 200 万的夫妻被微信封号](https://www.sohu.com/a/1007647328_114760)、[运营派：微信打击「AI 代笔」三类行为或将封号](https://www.yunyingpai.com/news/1059687.html)
  - **验证码 / 扫码频繁失效**：登录态（cookie、后台 token）短时失效，需人工扫码，自动化连续性差。
  - **接口易变**：后台内嵌接口签名、参数随版本变化，爬虫维护成本高。
  - **违反平台规则**：属于《微信公众平台运营规范》禁止的自动化操作，存在封禁账号、清空内容风险。
- **适用场景**：仅建议用于——未认证个人号确需自动化且无 API 权限的兜底；低频（如每周 1–2 次）、可人工盯防验证码的场景；以及学习研究。不建议用于生产级高频发布。

---

## 4. 技术路径四：企业微信 / 微信生态中间件能否间接发布？

- **结论：不能。** 企业微信与微信公众号是两个独立体系，企业微信侧没有任何「发布公众号文章」的接口。
  - 企业微信群机器人 webhook 只能向**企业微信群**推送文本/Markdown/图片消息，无法触达公众号粉丝页。[企业微信官方文档·消息推送配置说明](https://developer.work.weixin.qq.com/document/path/99110)、[腾讯云开发者：企业微信官方机器人无法对接外部群/个人微信](https://cloud.tencent.com.cn/developer/article/2684021)
  - 企业微信应用消息、客户联系等能力同样只能触达企业微信用户。
- **合理用法**：把企业微信 webhook 当作**发布结果通知渠道**（例如公众号发布成功后，向团队企业微信群推送「已发布」消息），而不是发布通道本身。
- **关于「woa」等中间件**：如 2.4 所述，`@ziikoo/woa` 这类包是官方 API 的 Node SDK/中间件，属于「封装官方接口」而非新的发布通道，不改变能力边界。
- 参考：[企业微信·消息推送配置说明](https://developer.work.weixin.qq.com/document/path/99110)

---

## 5. 技术路径五：定时发布方案

### 5.1 官方支持情况

- **发布接口（freepublish/submit）无定时参数**，草稿接口同样没有；官方 API 不支持「到点自动发表」。
- **后台 UI 的定时能力仅针对「群发」**：公众号后台「群发」支持**定时群发**（可预约未来某时刻群发，到期前可取消，2017 年上线）。[看云：公众平台定时群发的方法、规则介绍](https://www.kancloud.cn/w469001293/wx_zh/1112052)、[PConline：「定时群发」上线](https://news.pconline.com.cn/947/9471107.html)、[TechWeb：公众号上线定时推送功能](https://m.techweb.com.cn/article/2017-06-29/2549084.shtml)
  - 注意：定时群发占用群发次数，且窗口有限，不适合高频自动化。
- 因此「定时发布（发表）」必须走**外部调度 + 官方发布接口**。

### 5.2 常用替代方案（外部调度器触发 freepublish/submit）

| 方案 | 说明 | 参考 |
|---|---|---|
| **GitHub Actions schedule（cron）** | 仓库内 workflow 用 `schedule: - cron: '0 8 * * *'` 定时跑脚本：取 token → 建草稿 → 发布；免费、无需自建服务器，日志可审计。 | [OSCHINA：GitHub Actions 实现公众号自动化部署方案](https://my.oschina.net/emacs_7997622/blog/19477285)、[B 站调研：wenyan-cli + GitHub Actions 自动发布草稿](https://www.bilibili.com/opus/1197492416110657560) |
| **APScheduler / cron（本机或 VPS）** | Python `APScheduler`（CronTrigger）或系统 cron 定时执行发布脚本，适合有常驻服务器/开发机的场景。 | [linux.do：利用 API 总结新闻后自动在公众号发布](https://linux.do/t/topic/1277671/5) |
| **云函数定时器（SCF / FC / Lambda）** | 腾讯云 SCF、阿里云 FC、AWS Lambda + 定时触发器，无服务器、免运维。 | 通用实践，无特定链接 |
| **Agent 工作流内建调度** | 如 16Miku/wechat-auto-publishing 将「定时调度」作为工作流环节。 | [GitHub: 16Miku/wechat-auto-publishing](https://github.com/16Miku/wechat-auto-publishing) |
| **n8n / 低代码定时器** | n8n 的 Schedule Trigger 节点 + 公众号发布节点。 | [CSDN：n8n 对接微信公众号（正式发布）](https://blog.csdn.net/MKxcq/article/details/156449666) |

> 工程要点：定时任务触发的是「发布」而不是「群发」；发布为异步，需轮询 `freepublish/get` 确认上线；token 必须在 2 小时窗口内缓存复用。
---

## 6. 技术路径对比表

| 技术路径 | 原理 | 账号要求 | 是否支持定时 | 封号/合规风险 | 成本与维护 | 适用场景 |
|---|---|---|---|---|---|---|
| **官方 API：draft + freepublish** | 官方服务端接口，token→素材→草稿→发布 | 订阅号/服务号均可；素材接口多需认证 | ❌ 无定时参数（需外部调度） | 极低（合规） | 低（免费，需开发） | **首选**：一切自动化发布 |
| **官方 API：群发 mass/sendall** | 官方群发接口，推送粉丝 | 一般需服务号+认证 | ⚠️ 仅后台 UI「定时群发」 | 低（合规） | 低 | 必须推送给粉丝的运营场景（注意次数限制） |
| **第三方 CLI/Skill/MCP（wxgzh-cli、wechat-publisher、publish-mcp-server、woa 等）** | 官方 API 的封装，一键化 Markdown→发布 | 同官方 API | ❌ 无定时（需外部调度） | 低（合规，注意密钥管理） | 极低（开箱即用） | 个人/团队快速落地，Agent 集成 |
| **模拟登录后台（Playwright/DrissionPage/itchat 类）** | 扫码登录 mp.weixin.qq.com，操作后台或内嵌接口 | 任意账号（含未认证个人号） | ✅ 可自行实现 | **高**（封号、验证码、token 失效、违反平台规范） | 高（易随接口变动失效） | 仅兜底：无 API 权限的低频场景，慎用 |
| **企业微信 webhook / 中间件** | 企业微信群机器人推送 | 企业微信 | ✅（群消息定时） | 低 | 低 | **不能发布公众号**；只作发布结果通知 |
| **外部调度 + 官方发布 API（GitHub Actions / APScheduler / cron / 云函数）** | 定时器在目标时刻调 freepublish/submit | 同官方 API | ✅ 完美支持 | 低 | 中 | **定时发布的推荐实现方式** |

---

## 7. 推荐组合（落地建议）

1. **发布通道**：官方 `draft/add` + `freepublish/submit`（+ `freepublish/get` 轮询），账号优先用**认证服务号/订阅号**以解锁素材接口。
2. **token 管理**：用 `stable_token` 稳定版接口 + 本地缓存（2 小时内复用），规避频控。
3. **内容管线**：Markdown →（wxgzh-cli / wechat-publisher / 自研脚本）→ 草稿 → 发布。
4. **定时**：GitHub Actions `schedule` 或 APScheduler/cron 触发，发布成功后用企业微信 webhook 通知团队。
5. **红线**：不要用模拟登录/协议库做生产级自动化；关注官方对 AI 自动化内容的打击政策。

---

## 8. 主要参考资料

**微信官方文档**
- 发布能力：https://developers.weixin.qq.com/doc/subscription/guide/product/publish.html
- 发布草稿（freepublish/submit）：https://developers.weixin.qq.com/doc/subscription/api/public/api_freepublish_submit
- 上传永久素材（material/add_material）：https://developers.weixin.qq.com/doc/service/api/material/permanent/api_addmaterial
- 素材管理：https://developers.weixin.qq.com/doc/subscription/guide/product/asset.html
- 稳定版 access_token：https://developers.weixin.qq.com/minigame/dev/api-backend/access-token/api_getstableaccesstoken.html
- 服务端 API 调用说明：https://developers.weixin.qq.com/doc/subscription/guide/dev/api/
- 接口频率限制（英文）：https://developers.weixin.qq.com/doc/service/en/guide/dev/api/limit.html
- 企业微信·消息推送配置说明：https://developer.work.weixin.qq.com/document/path/99110

**GitHub / npm 项目**
- fanbuz/wxgzh-cli：https://github.com/fanbuz/wxgzh-cli （security 文档：https://github.com/fanbuz/wxgzh-cli/blob/main/docs/security.md）
- jiji262/wechat-publisher：https://github.com/jiji262/wechat-publisher （SKILL.md：https://github.com/jiji262/wechat-publisher/blob/main/SKILL.md）
- @jesonliu/publish-mcp-server：https://www.npmjs.com/package/@jesonliu/publish-mcp-server
- @ziikoo/woa：https://www.npmjs.com/package/@ziikoo/woa
- lyhue1991/wxgzh：https://github.com/lyhue1991/wxgzh
- 16Miku/wechat-auto-publishing：https://github.com/16Miku/wechat-auto-publishing
- yaojiwei520/Wechat-messages：https://github.com/yaojiwei520/Wechat-messages
- Nimbly8836/puppet-wechat4u：https://github.com/Nimbly8836/puppet-wechat4u

**CSDN / 博客 / 社区文章**
- 本地化公众号草稿管理工具与自动化发布流水线：https://blog.csdn.net/weixin_28686771/article/details/160698304
- 从零构建微信公众号自动化发布引擎：https://blog.csdn.net/weixin_29051811/article/details/159604369
- wechat-oa-skill 核心原理与实战：https://blog.csdn.net/weixin_42523907/article/details/161030597
- 微信公众号自动发文 Skill 全链路：https://blog.csdn.net/m0_73479109/article/details/159867021
- n8n 对接微信公众号（正式发布）：https://blog.csdn.net/MKxcq/article/details/156449666
- 上传图文 errcode 40007 解决方案：https://blog.csdn.net/amberom/article/details/145949413
- 三类微信公众号之间的区别及接口权限：https://blog.csdn.net/weixin_40485506/article/details/89477481
- getStableAccessToken 详解：https://blog.csdn.net/longfeng995/article/details/130185005
- 获取稳定版接口调用凭据（博客园）：https://www.cnblogs.com/AtlasLapetos/p/18640885
- 公众号「发表」和「群发」的区别：https://www.cnblogs.com/omnigoai/p/22511623
- 扫码登录能进草稿，自动正式发布仍看 API：https://www.cnblogs.com/omnigoai/p/22504495
- 公众号内测「发布」功能，支持一天多次：https://www.chinaz.com/2021/0928/1309341.shtml
- addMaterial 注意事项与常见问题（md2wechat）：https://www.md2wechat.com/zh/blog/wechat-add-material-api-notes
- GitHub Actions 实现公众号自动化部署方案（OSCHINA）：https://my.oschina.net/emacs_7997622/blog/19477285
- 利用 API 总结新闻后自动在公众号发布（linux.do）：https://linux.do/t/topic/1277671
- 创作自动化开源仓库深度调研 2024—2026（B 站）：https://www.bilibili.com/opus/1197492416110657560
- 公众平台定时群发的方法、规则介绍（看云）：https://www.kancloud.cn/w469001293/wx_zh/1112052
- 「定时群发」上线（PConline）：https://news.pconline.com.cn/947/9471107.html
- 公众号上线定时推送功能（TechWeb）：https://m.techweb.com.cn/article/2017-06-29/2549084.shtml
- 接口调用频次限制说明（w3cschool）：https://m.w3cschool.cn/weixinkaifawendang/2yqt1q8e.html
- 微信公众号接口权限说明（w3cschool）：https://m.w3cschool.cn/weixinkaifawendang/qbtf1q8d.html
- 公众平台接口权限列表说明（aoyacms）：https://www.aoyacms.com/18/58/326.html

**风险与合规相关**
- 微信明确禁止 AI 自动化创作，违规账号将被封禁（虎嗅）：https://www.huxiu.com/article/4850513.html
- 用 AI 写公众号年赚 200 万的夫妻被微信封号（搜狐）：https://www.sohu.com/a/1007647328_114760
- 微信打击「AI 代笔」三类行为或将封号（运营派）：https://www.yunyingpai.com/news/1059687.html
- itchat 被封风险讨论（CowAgent issue #2457）：https://github.com/zhayujie/CowAgent/issues/2457
- Wechaty 免费协议讨论（LINUX DO）：https://linux.do/t/topic/201686
- 企业微信官方机器人无法对接外部群/个人微信（腾讯云）：https://cloud.tencent.com.cn/developer/article/2684021
