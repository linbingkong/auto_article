# 通用图片服务配置

界面只提供两个服务源：**Pillow 本地图片服务**和**自定义源**。系统不判断、不拦截 URL 格式，填写的地址会被原样使用，服务商返回什么错误就透传什么错误。

## 两种服务源

| source | 界面名称 | 配置要求 |
|---|---|---|
| `pillow` | Pillow 本地图片服务 | 无需 URL、模型和 Key，不发起外部请求 |
| `custom` | 自定义源 | 填写图片 API Base URL、图片模型和对应 Key |

## 自定义源调用规则

URL 原样使用，只有两种调用方式：

| 你填写的 URL | 系统行为 |
|---|---|
| 路径包含 `/services/aigc/`（DashScope 原生图片端点） | 按原生异步协议调用，任务轮询使用同一主机 |
| 其他任何 HTTP(S) 地址 | 按 OpenAI Images 兼容协议调用 |

OpenAI Images 方式下：

```text
POST {Base URL}/images/generations
```

如果 URL 已经以 `/images/generations` 结尾，系统不会重复追加；如果省略了 `https://`，系统会自动补全。

```yaml
image:
  source: custom
  provider: auto
  api_key: ${IMAGE_API_KEY}
  model: image-model-name
  base_url: https://images.example.com/v1
  api_size: 2560x1440
  inline_images: 2
```

## 生成尺寸

`api_size`（配置中心"生成尺寸"）原样传给服务商，格式为 `宽x高`（也接受 `宽*高`）：

- 留空时使用默认值：DashScope 原生端点封面 `1664*928`、正文图 `1280*720`；OpenAI Images 兼容调用 `1536x1024`；
- **部分模型有最低总像素要求**：火山引擎 Seedream 要求总像素 ≥ 3,686,400（如 `2560x1440` 或 `1920x1920`），默认 `1536x1024` 会被服务商拒绝（HTTP 400 "size must be at least ..."），此时请在配置中心把生成尺寸改为该模型支持的值；
- 宽高至少 256 像素；系统只做格式与下限校验，尺寸是否被模型支持以服务商响应为准。

## 原生 DashScope 图片端点

当 URL 路径包含 `/services/aigc/` 时：

- URL 中已是 `multimodal-generation` 或 `text2image` 端点的，按该端点原样提交；
- 只填了 DashScope 根地址时，`qwen-image` 模型使用 multimodal-generation，其他图片模型使用 text2image；
- 异步任务查询地址与提交地址同主机。

Token Plan 与百炼按量付费在 Token Plan 网关和 DashScope 主机上的 Key 互不通用，请使用与 URL 匹配的 Key；填错时服务商会直接返回 401。

## 系统不做 URL 格式判断

- 不拦截 `compatible-mode/v1`：它会被当作 OpenAI Images 基础地址，自动追加 `/images/generations`，是否真正支持图片以连接测试或服务商响应为准；
- 不拦截 Coding Plan 等编程套餐地址：调用结果由服务商返回；
- 不校验 Key 前缀（`sk-`、`sk-sp-` 等）：Key 类型是否匹配由服务商判定；
- 唯一的模型检查：`t2v`、`video` 等视频生成模型不能用于公众号静态配图，保存时会明确提示。

## 配置迁移

旧 source 值会在读取时自动映射为 `custom`：

```text
bailian_payg
bailian_token_plan
openai
openai_compatible
```

旧配置的 URL、模型和加密 Key 会保留，无需数据库迁移或重新输入密钥。新保存的配置只写 `pillow` 或 `custom`。

旧版 `${DASHSCOPE_API_KEY}` 仍可读取；新配置统一使用 `${IMAGE_API_KEY}`。

## 连接测试

- Pillow：仅检查本地图片能力，不收费；
- 其他自定义源（含 OpenAI 官方）：实际调用一次最小图片生成请求，可能产生费用；
- 原生 DashScope 端点：提交最小异步图片任务并验证任务路由，可能产生费用。

## 常见错误

服务商返回的错误会原样展示，常见情况：

| 错误 | 通常原因 | 处理 |
|---|---|---|
| HTTP 400（size 相关） | 生成尺寸不被该模型支持 | 部分模型有最低总像素要求（如 Seedream ≥2560x1440），在配置中心修改"生成尺寸" |
| HTTP 401 | Key 与 URL 所属网关不匹配 | 核对服务商和 Key 类型 |
| HTTP 403 | 没有图片模型权限 | 在服务商控制台开通模型 |
| HTTP 404 | 模型或图片路径不存在 | 核对模型 ID，并确认该服务支持 `/images/generations`；百炼兼容模式没有图片接口，请改用原生端点 `https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation` |
| HTTP 429 | 余额或频率超限 | 检查额度后重试 |
| 视频模型提示 | 使用了 t2v/video 模型 | 改用 qwen-image、wan-image 等静态图片模型 |
