"""LLM 客户端 — OpenAI 兼容 API，支持 Hermes / DeepSeek / Ollama 等。

使用 httpx 直接调用 /v1/chat/completions，不引入 openai SDK 减少依赖。
"""

import json
import logging
import re
import time

import httpx

from backend.config import settings

logger = logging.getLogger("llm_client")


class LLMClient:
    """OpenAI 兼容的 LLM 客户端。"""

    def __init__(self) -> None:
        self.api_base = settings.llm_api_base.rstrip("/")
        self.api_key = settings.llm_api_key or "not-needed"
        self.model = settings.llm_model
        self.max_tokens = settings.llm_max_tokens
        self.temperature = settings.llm_temperature

    @property
    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "not-needed":
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def chat(self, system_prompt: str, user_prompt: str) -> dict:
        """调用 LLM 生成回复。

        Returns:
            dict: {
                "content": str,       # 回复文本
                "model": str,         # 实际使用的模型名
                "tokens_used": int,   # 总 token 数
                "duration_ms": int,   # 耗时（毫秒）
            }
        """
        url = f"{self.api_base}/chat/completions"
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
        }
        if self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                resp = await client.post(url, json=payload, headers=self._headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error("LLM API 返回错误 %s: %s", e.response.status_code, e.response.text[:500])
                raise RuntimeError(f"LLM API 错误: {e.response.status_code}") from e
            except httpx.RequestError as e:
                logger.error("LLM API 请求失败: %s", e)
                raise RuntimeError(f"LLM 请求失败: {e}") from e

        elapsed_ms = int((time.monotonic() - start) * 1000)
        body = resp.json()

        content = ""
        choices = body.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")

        usage = body.get("usage", {})
        tokens_used = usage.get("total_tokens", 0)

        return {
            "content": content,
            "model": body.get("model", self.model),
            "tokens_used": tokens_used,
            "duration_ms": elapsed_ms,
        }

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        """调用 LLM 并从回复中提取 JSON。

        在 prompt 中追加 JSON 输出要求，然后从回复中用正则提取 JSON 块。
        兼容不支持 structured output 的模型。
        """
        enhanced_user = (
            f"{user_prompt}\n\n"
            "请严格以 JSON 格式输出，不要包含其他文字。"
            "JSON 内容用 ```json 和 ``` 包裹。"
        )

        result = await self.chat(system_prompt, enhanced_user)
        content = result["content"]

        # 尝试从 ```json ... ``` 中提取
        json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析整个内容
            json_str = content.strip()

        try:
            parsed = json.loads(json_str)
            result["parsed"] = parsed
        except json.JSONDecodeError:
            logger.warning("LLM 输出 JSON 解析失败，返回原始文本")
            result["parsed"] = {"raw_text": content}

        return result

    async def test_connection(self) -> dict:
        """测试 LLM 连接是否正常。

        Returns:
            dict: {"ok": bool, "model": str, "error": str|None}
        """
        try:
            result = await self.chat(
                system_prompt="You are a helpful assistant.",
                user_prompt="回复'连接成功'四个字即可。",
            )
            content = result.get("content", "")
            if content:
                return {
                    "ok": True,
                    "model": result.get("model", self.model),
                    "response_preview": content[:100],
                    "tokens_used": result.get("tokens_used", 0),
                    "duration_ms": result.get("duration_ms", 0),
                    "error": None,
                }
            return {"ok": False, "model": self.model, "error": "LLM 返回空内容"}
        except Exception as e:
            return {"ok": False, "model": self.model, "error": str(e)}
