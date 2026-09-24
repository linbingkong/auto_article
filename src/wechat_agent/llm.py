"""LLM 客户端模块。

OpenAI 兼容接口（/chat/completions），因此同一套代码既可对接本地部署的
文本大模型（vLLM / Ollama / LM Studio 等，base_url 指向本地），也可对接
外部 API（DeepSeek / 通义千问 / 智谱 / OpenAI 等）。只需改配置。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import requests

from .config import LLMConfig

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """LLM 调用错误。"""


class LLMClient:
    """OpenAI 兼容 Chat Completions 客户端。"""

    def __init__(self, config: LLMConfig, usage_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.config = config
        self.usage_callback = usage_callback
        self._base = config.base_url.rstrip("/")
        if not self._base.endswith("/chat/completions"):
            self._endpoint = self._base + "/chat/completions"
        else:
            self._endpoint = self._base
        self._session = requests.Session()
        # LLM 服务端点（DeepSeek/智谱等国内端点）直连；本机代理曾造成 CLOSE_WAIT
        # 堆积与长时间无响应（每次重试最长可拖 3×读超时）。Tavily 等海外端点
        # 仍由 search/evidence 各自会话按需走系统代理。
        self._session.trust_env = False

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        thinking: Optional[bool] = None,
        usage_label: str = "unknown",
    ) -> str:
        """单次对话补全，返回文本。

        推理模型（如 DeepSeek R 系列 / V 系列）会先输出 reasoning_content，
        且推理 token 计入 max_tokens 预算。若因 finish_reason=length 截断
        （无论有没有部分正文），自动加倍预算重试，最多 2 次。
        """
        budget = max_tokens or self.config.max_tokens
        attempt = 0
        thinking_retry = False
        thinking_mode = thinking
        selected_model = model or self.config.model
        model_lower = selected_model.lower()
        base_lower = self._base.lower()
        supports_thinking_switch = "deepseek" in base_lower or "deepseek" in model_lower
        # qwen3 系混合推理模型（qwen3.8-max / qwen3.6-plus 等）默认开启思考，
        # 推理 token 计入输出并按更高费率计费；未显式要求思考时一律关闭。
        qwen_hybrid = "qwen" in model_lower or "qwen" in base_lower
        network_attempt = 0
        while True:
            payload: Dict[str, Any] = {
                "model": selected_model,
                "messages": messages,
                "temperature": temperature if temperature is not None else self.config.temperature,
                "max_tokens": budget,
                "stream": False,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            if thinking_mode is not None and supports_thinking_switch:
                payload["thinking"] = {"type": "enabled" if thinking_mode else "disabled"}
            elif qwen_hybrid:
                payload["enable_thinking"] = bool(thinking_mode) if thinking_mode is not None else False

            headers = {"Content-Type": "application/json"}
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"

            try:
                read_timeout = max(self.config.timeout, 300.0 if budget >= 8192 else 120.0)
                r = self._session.post(
                    self._endpoint,
                    headers=headers,
                    json=payload,
                    timeout=(15.0, read_timeout),
                )
                if r.status_code == 404:
                    raise LLMError(self._describe_404(payload["model"])) from None
                if r.status_code == 400 and "enable_thinking" in r.text.lower() and payload.pop("enable_thinking", None) is not None:
                    logger.warning("服务商不支持 enable_thinking 参数，已去除后重试")
                    continue
                if r.status_code == 400 and "max_tokens" in r.text.lower():
                    raise LLMError(
                        f"服务商拒绝了 max_tokens={budget}：{r.text.strip()[:300]}。"
                        "请把 config.yaml 中 llm.max_tokens 调整为该模型允许的输出上限后重试。"
                    ) from None
                r.raise_for_status()
                data = r.json()
                network_attempt = 0
            except (requests.Timeout, requests.ConnectionError) as e:
                if network_attempt < 4:
                    network_attempt += 1
                    delay = (2, 4, 8, 15)[network_attempt - 1]
                    logger.warning(
                        "LLM 网络超时/连接失败（%s），%d 秒后自动重试（第 %d/4 次）",
                        e, delay, network_attempt,
                    )
                    time.sleep(delay)
                    continue
                raise LLMError(
                    f"LLM 网络请求连续 {network_attempt + 1} 次失败：{e}。已自动使用最长读取超时并指数退避重试，"
                    "请检查模型服务状态或稍后再试。"
                ) from e
            except requests.RequestException as e:
                raise LLMError(f"LLM request failed: {e}") from e
            except (KeyError, IndexError, ValueError) as e:
                raise LLMError(f"LLM response parse failed: {e}") from e

            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            reasoning = str(message.get("reasoning_content") or "")
            finish_reason = str(choice.get("finish_reason") or "unknown")
            self._emit_usage(data, selected_model, usage_label, messages, str(content or ""), reasoning)
            if content and finish_reason != "length":
                return str(content).strip()

            if not content and reasoning and finish_reason == "stop" and not thinking_retry:
                thinking_retry = True
                thinking_mode = False
                logger.warning(
                    "LLM 正常结束但只返回 %d 字符 reasoning_content；自动关闭 Thinking Mode 重试以获取最终 content",
                    len(reasoning),
                )
                continue
            if finish_reason == "length" and attempt < 2 and budget < 32768:
                attempt += 1
                new_budget = min(budget * 2, 32768)
                logger.warning(
                    "LLM 输出在 max_tokens=%s 处被截断（finish_reason=length，含 %d 字符推理），"
                    "自动改用 max_tokens=%s 重试（第 %d 次）",
                    budget, len(reasoning), new_budget, attempt,
                )
                budget = new_budget
                continue

            if content and finish_reason == "length":
                raise LLMError(
                    f"LLM 输出连续在 max_tokens={budget} 处被截断。"
                    "请调大配置中的 llm.max_tokens，或让模型缩短输出后重试。"
                )
            detail = (
                f"，检测到 {len(reasoning)} 字符内部推理，finish_reason={finish_reason}"
                if reasoning
                else f"，finish_reason={finish_reason}"
            )
            raise LLMError(
                "LLM 没有返回最终答案" + detail +
                "。系统已拒绝把内部推理当作正文；请调大配置中的 llm.max_tokens 后重试。"
            )

    # ------------------------------------------------------------------
    def _emit_usage(self, data: Dict[str, Any], model: str, stage: str,
                    messages: List[Dict[str, str]], content: str, reasoning: str) -> None:
        """上报一次已收到响应的 Token 用量；服务商缺 usage 时给出明确标记的近似值。"""
        if not self.usage_callback:
            return
        usage = data.get("usage") or {}
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        estimated = prompt is None or completion is None
        if prompt is None:
            prompt = max(1, len(json.dumps(messages, ensure_ascii=False)) // 2)
        if completion is None:
            completion = max(1, (len(content) + len(reasoning)) // 2)
        prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
        record = {
            "stage": stage or "unknown",
            "provider": urlparse(self._base).netloc,
            "model": model,
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "total_tokens": int(usage.get("total_tokens") or int(prompt or 0) + int(completion or 0)),
            "cached_tokens": int(prompt_details.get("cached_tokens") or usage.get("cached_tokens") or 0),
            "reasoning_tokens": int(completion_details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0),
            "estimated": estimated,
        }
        try:
            self.usage_callback(record)
        except Exception:
            logger.exception("token usage callback failed; generation continues")

    # ------------------------------------------------------------------
    def _describe_404(self, model: str) -> str:
        """vLLM 对不存在的模型返回 404 且不带错误详情；尝试拉取可用模型列表给出提示。"""
        base = self._base
        if base.endswith("/chat/completions"):
            base = base.rsplit("/chat/completions", 1)[0]
        candidates = [base, base.rsplit("/v1", 1)[0] + "/v1"]
        available: List[str] = []
        for url in dict.fromkeys(candidates):
            try:
                r = self._session.get(
                    url.rstrip("/") + "/models", timeout=8,
                    headers={"Authorization": f"Bearer {self.config.api_key}"}
                    if self.config.api_key else {},
                )
                if r.status_code == 200:
                    data = r.json()
                    available = [
                        item.get("id", "")
                        for item in data.get("data", [])
                        if item.get("id")
                    ]
                    break
            except (requests.RequestException, ValueError):
                continue
        hint = ""
        if available:
            listed = ", ".join(available[:8])
            hint = f"；服务端可用模型：{listed}" + (
                f"。当前配置的模型名「{model}」不在其中" if model not in available else ""
            )
        return (
            f"模型接口返回 404：服务端不存在模型「{model}」{hint}。"
            "请检查 config.yaml 中 llm.model 是否与服务端模型 ID 完全一致"
        )

    # ------------------------------------------------------------------
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """请求 JSON 输出并解析。部分本地模型不支持 response_format，
        失败时退化为从文本中提取 JSON。"""
        text = self.chat(messages, model=model, temperature=temperature, json_mode=True)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 退化为提取第一个 {...}
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            raise LLMError(f"LLM did not return valid JSON: {text[:200]}")

    # ------------------------------------------------------------------
    def complete(self, prompt: str, **kw: Any) -> str:
        """便捷方法：单条 system+user 消息。"""
        messages = [
            {"role": "system", "content": "你是一个专业的微信公众号内容创作助手。"},
            {"role": "user", "content": prompt},
        ]
        return self.chat(messages, **kw)
