from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

from ai_visual_agent.config import get_settings
from ai_visual_agent.services.integration_health import record_integration_event
from ai_visual_agent.services.prompt_registry import get_prompt_version


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


@dataclass(frozen=True)
class StructuredLLMResult(Generic[StructuredModel]):
    output: StructuredModel
    backend: str
    model: str
    prompt_name: str
    prompt_version: str
    prompt_hash: str
    output_schema: str
    fallback_used: bool = False
    error: str | None = None
    attempts: int = 1
    retry_errors: list[str] | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "model": self.model,
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "output_schema": self.output_schema,
            "fallback_used": self.fallback_used,
            "error": self.error,
            "attempts": self.attempts,
            "retry_errors": self.retry_errors or [],
        }


class StructuredLLMProvider(Protocol):
    backend_name: str

    def invoke(
        self,
        *,
        schema: type[StructuredModel],
        prompt_name: str,
        context: dict[str, Any],
        fallback: StructuredModel,
        model_role: str = "strategy",
    ) -> StructuredLLMResult[StructuredModel]: ...


class MockStructuredLLMProvider:
    backend_name = "mock"
    model_name = "mock-structured-llm"

    def invoke(
        self,
        *,
        schema: type[StructuredModel],
        prompt_name: str,
        context: dict[str, Any],
        fallback: StructuredModel,
        model_role: str = "strategy",
    ) -> StructuredLLMResult[StructuredModel]:
        _ = (schema, context, model_role)
        prompt = get_prompt_version(prompt_name, include_content=False)
        return StructuredLLMResult(
            output=fallback,
            backend=self.backend_name,
            model=self.model_name,
            prompt_name=prompt_name,
            prompt_version=prompt.version,
            prompt_hash=prompt.content_hash,
            output_schema=schema.__name__,
            fallback_used=True,
        )


class DeepSeekStructuredLLMProvider:
    backend_name = "deepseek"

    def invoke(
        self,
        *,
        schema: type[StructuredModel],
        prompt_name: str,
        context: dict[str, Any],
        fallback: StructuredModel,
        model_role: str = "strategy",
    ) -> StructuredLLMResult[StructuredModel]:
        settings = get_settings()
        model = (
            settings.deepseek_model_fast
            if model_role == "fast"
            else settings.deepseek_model_strategy
        )
        if not settings.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for LLM_BACKEND=deepseek.")

        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError("LLM_BACKEND=deepseek requires langchain-openai.") from exc

        prompt = get_prompt_version(prompt_name, include_content=True)
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        context_json = json.dumps(context, ensure_ascii=False, default=str)
        attempts_per_model = _retry_attempts(settings)
        backoff_seconds = _retry_backoff_seconds(settings)
        retry_errors: list[str] = []
        last_error = ""
        attempted = 0

        for candidate_model in _candidate_models(settings, model_role):
            llm = ChatOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=candidate_model,
                temperature=settings.llm_temperature,
                timeout=_request_timeout(settings),
                max_retries=0,
            )
            for attempt in range(1, attempts_per_model + 1):
                attempted += 1
                try:
                    result = llm.invoke(
                        _structured_messages(
                            prompt_content=prompt.content,
                            schema_json=schema_json,
                            context_json=context_json,
                            attempt=attempt,
                            previous_error=last_error,
                        )
                    )
                    output = schema.model_validate(_parse_json_object(_message_content(result)))
                    return StructuredLLMResult(
                        output=output,
                        backend=self.backend_name,
                        model=candidate_model,
                        prompt_name=prompt_name,
                        prompt_version=prompt.version,
                        prompt_hash=prompt.content_hash,
                        output_schema=schema.__name__,
                        fallback_used=False,
                        attempts=attempted,
                        retry_errors=retry_errors,
                    )
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    retry_errors.append(
                        f"{candidate_model} attempt {attempt}/{attempts_per_model}: {last_error}"
                    )
                    if attempt < attempts_per_model and backoff_seconds > 0:
                        time.sleep(backoff_seconds * attempt)

        raise RuntimeError(
            "Structured LLM failed after "
            f"{attempted} attempts across {len(_candidate_models(settings, model_role))} model(s). "
            + " | ".join(retry_errors[-6:])
        )


def invoke_structured(
    *,
    schema: type[StructuredModel],
    prompt_name: str,
    context: dict[str, Any],
    fallback: StructuredModel,
    model_role: str = "strategy",
) -> StructuredLLMResult[StructuredModel]:
    provider = get_structured_llm_provider()
    try:
        result = provider.invoke(
            schema=schema,
            prompt_name=prompt_name,
            context=context,
            fallback=fallback,
            model_role=model_role,
        )
        record_integration_event(
            name="llm",
            backend=result.backend,
            model=result.model,
            ok=result.error is None and (result.backend == "mock" or not result.fallback_used),
            fallback_used=result.fallback_used,
            error=result.error,
        )
        return result
    except Exception as exc:
        try:
            prompt = get_prompt_version(prompt_name, include_content=False)
            prompt_version = prompt.version
            prompt_hash = prompt.content_hash
        except Exception:
            prompt_version = f"{prompt_name}@unknown"
            prompt_hash = ""
        result = StructuredLLMResult(
            output=fallback,
            backend=getattr(provider, "backend_name", "unknown"),
            model=_model_name_for_role(model_role),
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            output_schema=schema.__name__,
            fallback_used=True,
            error=f"{type(exc).__name__}: {exc}",
            attempts=_fallback_attempt_count(exc),
            retry_errors=_fallback_retry_errors(exc),
        )
        record_integration_event(
            name="llm",
            backend=result.backend,
            model=result.model,
            ok=False,
            fallback_used=True,
            error=result.error,
        )
        return result


@lru_cache
def get_structured_llm_provider() -> StructuredLLMProvider:
    backend = get_settings().llm_backend.lower()
    if backend == "deepseek":
        return DeepSeekStructuredLLMProvider()
    return MockStructuredLLMProvider()


def _model_name_for_role(model_role: str) -> str:
    settings = get_settings()
    return settings.deepseek_model_fast if model_role == "fast" else settings.deepseek_model_strategy


def _candidate_models(settings: Any, model_role: str) -> list[str]:
    primary = (
        settings.deepseek_model_fast
        if model_role == "fast"
        else settings.deepseek_model_strategy
    )
    backup = (
        settings.deepseek_model_strategy
        if model_role == "fast"
        else settings.deepseek_model_fast
    )
    models: list[str] = []
    for item in [primary, backup]:
        if item and item not in models:
            models.append(item)
    return models


def _retry_attempts(settings: Any) -> int:
    try:
        return max(1, int(getattr(settings, "llm_retry_attempts", 3)))
    except (TypeError, ValueError):
        return 3


def _retry_backoff_seconds(settings: Any) -> float:
    try:
        return max(0.0, float(getattr(settings, "llm_retry_backoff_seconds", 0.3)))
    except (TypeError, ValueError):
        return 0.3


def _request_timeout(settings: Any) -> float:
    try:
        return max(5.0, float(getattr(settings, "llm_request_timeout", 120.0)))
    except (TypeError, ValueError):
        return 120.0


def _structured_messages(
    *,
    prompt_content: str,
    schema_json: str,
    context_json: str,
    attempt: int,
    previous_error: str = "",
) -> list[tuple[str, str]]:
    retry_instruction = ""
    if attempt > 1:
        retry_instruction = (
            "\n\n这是自动重试。上一轮失败原因："
            + previous_error
            + "\n请降低创造性，严格按 JSON Schema 输出完整 JSON；不要省略必填字段。"
        )
    return [
        (
            "system",
            prompt_content
            + "\n\n你必须只返回一个合法 JSON 对象，不要 Markdown，不要解释，不要代码块。"
            + "返回对象必须能被下方 JSON Schema 校验通过。"
            + retry_instruction,
        ),
        (
            "human",
            "JSON Schema:\n"
            + schema_json
            + "\n\nContext JSON:\n"
            + context_json
            + "\n\n只输出 JSON 对象本身。",
        ),
    ]


def _fallback_attempt_count(exc: Exception) -> int:
    message = str(exc)
    marker = "failed after "
    if marker not in message:
        return 1
    tail = message.split(marker, 1)[1].split(" attempts", 1)[0]
    try:
        return max(1, int(tail.strip()))
    except ValueError:
        return 1


def _fallback_retry_errors(exc: Exception) -> list[str]:
    message = str(exc)
    if " | " not in message:
        return [f"{type(exc).__name__}: {message}"]
    return message.split(" | ")[-6:]


def _message_content(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None) or getattr(item, "content", None)
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    return str(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = json.loads(_first_balanced_json_object(text))
    if not isinstance(parsed, dict):
        raise ValueError("Structured LLM response must be a JSON object.")
    return parsed


def _first_balanced_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("Structured LLM response did not contain a JSON object.")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("Structured LLM response contained incomplete JSON.")
