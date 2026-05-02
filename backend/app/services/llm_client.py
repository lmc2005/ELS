import httpx
import json
import re
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from app.config import settings as app_settings
from app.services.budget_service import budget_service

logger = logging.getLogger("llm")


class BudgetExceededError(Exception):
    pass


class LLMClientError(Exception):
    pass


class LLMClient:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    def _get_config(self, model_override: str | None = None):
        return {
            "base_url": app_settings.llm_base_url.rstrip("/"),
            "api_key": app_settings.llm_api_key,
            "model": model_override or app_settings.llm_model,
            "timeout": app_settings.llm_timeout_seconds,
        }

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 2)

    @staticmethod
    def _parse_json_content(content: str) -> dict:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
        if fenced:
            try:
                return json.loads(fenced.group(1).strip())
            except json.JSONDecodeError:
                pass

        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {"raw": content}

    async def _record_usage(self, feature: str, model: str, input_tokens: int, output_tokens: int, cost: float):
        from app.database import Session, engine
        from app.models.usage import UsageEvent

        with Session(engine) as session:
            event = UsageEvent(
                provider="openai_compatible",
                feature=feature,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_rmb=cost,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(event)
            session.commit()

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        temperature: float = 0.4,
        model_override: str | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        config = self._get_config(model_override)
        if not config["base_url"] or not config["api_key"] or not config["model"]:
            raise LLMClientError("LLM not configured. Please set base_url, api_key, and model in Settings.")

        if not budget_service.check_budget():
            raise BudgetExceededError("Monthly budget exceeded. LLM calls are blocked.")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        request_body = {
            "model": config["model"],
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if max_tokens:
            request_body["max_tokens"] = max_tokens

        data = await self._post_chat_completion(config, request_body)

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        input_tokens = usage.get("prompt_tokens", self._estimate_tokens(system_prompt + user_prompt))
        output_tokens = usage.get("completion_tokens", self._estimate_tokens(content))
        cost = budget_service.compute_cost(input_tokens, output_tokens)

        await self._record_usage("chat_json", config["model"], input_tokens, output_tokens, cost)
        budget_service.add_cost(cost)

        return self._parse_json_content(content)

    async def chat_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.5,
        model_override: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        config = self._get_config(model_override)
        if not config["base_url"] or not config["api_key"] or not config["model"]:
            raise LLMClientError("LLM not configured. Please set base_url, api_key, and model in Settings.")

        if not budget_service.check_budget():
            raise BudgetExceededError("Monthly budget exceeded. LLM calls are blocked.")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        request_body = {
            "model": config["model"],
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            request_body["max_tokens"] = max_tokens

        data = await self._post_chat_completion(config, request_body)

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        input_tokens = usage.get("prompt_tokens", self._estimate_tokens(system_prompt + user_prompt))
        output_tokens = usage.get("completion_tokens", self._estimate_tokens(content))
        cost = budget_service.compute_cost(input_tokens, output_tokens)

        await self._record_usage("chat_text", config["model"], input_tokens, output_tokens, cost)
        budget_service.add_cost(cost)

        return content

    async def chat_text_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.5,
        model_override: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        config = self._get_config(model_override)
        if not config["base_url"] or not config["api_key"] or not config["model"]:
            raise LLMClientError("LLM not configured. Please set base_url, api_key, and model in Settings.")

        if not budget_service.check_budget():
            raise BudgetExceededError("Monthly budget exceeded. LLM calls are blocked.")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        request_body = {
            "model": config["model"],
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            request_body["max_tokens"] = max_tokens

        accumulated = ""
        usage: dict = {}
        try:
            async for payload in self._stream_chat_completion(config, request_body):
                usage = payload.get("usage") or usage
                delta, is_cumulative = self._extract_stream_delta(payload)
                if not delta:
                    continue
                if is_cumulative:
                    delta = self._trim_cumulative_stream_text(accumulated, delta)
                    if not delta:
                        continue
                accumulated += delta
                yield delta
        except LLMClientError as exc:
            if "400" not in str(exc) and "422" not in str(exc):
                raise

            logger.info("Streaming unavailable for %s, falling back to one-shot text completion", config["model"])
            content = await self.chat_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                model_override=model_override,
                max_tokens=max_tokens,
            )
            if content:
                yield content
            return

        input_tokens = usage.get("prompt_tokens", self._estimate_tokens(system_prompt + user_prompt))
        output_tokens = usage.get("completion_tokens", self._estimate_tokens(accumulated))
        cost = budget_service.compute_cost(input_tokens, output_tokens)

        await self._record_usage("chat_text_stream", config["model"], input_tokens, output_tokens, cost)
        budget_service.add_cost(cost)

    async def _post_chat_completion(self, config: dict, request_body: dict) -> dict:
        url = f"{config['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        }

        try:
            client = self._get_http_client()
            resp = await client.post(url, headers=headers, json=request_body, timeout=config["timeout"])
            if resp.status_code in {400, 422} and "response_format" in request_body:
                fallback_body = dict(request_body)
                fallback_body.pop("response_format", None)
                logger.info("Retrying without response_format (status %d)", resp.status_code)
                resp = await client.post(url, headers=headers, json=fallback_body, timeout=config["timeout"])
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:500] if e.response is not None else str(e)
            raise LLMClientError(f"LLM API request failed: {detail}")
        except httpx.HTTPError as e:
            raise LLMClientError(f"LLM API request failed: {e}")

    async def _stream_chat_completion(self, config: dict, request_body: dict) -> AsyncIterator[dict]:
        url = f"{config['base_url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        }

        try:
            client = self._get_http_client()
            async with client.stream("POST", url, headers=headers, json=request_body, timeout=config["timeout"]) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue

                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue

                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        logger.debug("Skipping non-JSON stream payload: %s", payload[:160])
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:500] if e.response is not None else str(e)
            raise LLMClientError(f"LLM API request failed: {detail}")
        except httpx.HTTPError as e:
            raise LLMClientError(f"LLM API request failed: {e}")

    @staticmethod
    def _extract_stream_delta(payload: dict) -> tuple[str, bool]:
        choices = payload.get("choices") or []
        if not choices:
            return "", False

        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content, False
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    piece = part.get("text") or part.get("content") or ""
                    if piece:
                        text_parts.append(str(piece))
            return "".join(text_parts), False

        message = choice.get("message") or {}
        final_content = message.get("content")
        return (final_content, True) if isinstance(final_content, str) else ("", False)

    @staticmethod
    def _trim_cumulative_stream_text(accumulated: str, chunk: str) -> str:
        if not accumulated:
            return chunk
        if chunk.startswith(accumulated):
            return chunk[len(accumulated) :]

        max_overlap = min(len(accumulated), len(chunk))
        for overlap in range(max_overlap, 0, -1):
            if accumulated.endswith(chunk[:overlap]):
                return chunk[overlap:]
        return chunk


llm_client = LLMClient()
