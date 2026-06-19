"""OpenAI 互換 API クライアント (llama.cpp / LM Studio / その他)。

要件定義の方針:
- LLM には「書かせる」ことに寄せ、リトライ・JSON 抽出・出力切れ検知は Python 側で制御。
- finish_reason == 'length' を出力切れとして検知する。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    text: str
    finish_reason: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_sec: float = 0.0

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "not-needed",
        temperature: float = 0.3,
        timeout_sec: int = 300,
        max_retries: int = 3,
        call_log=None,
    ):
        if not base_url:
            raise LLMError(
                "LLM の base_url が未設定です。--provider か --base-url を指定してください。"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.call_log = call_log  # CallLogger | None

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
        temperature: float | None = None,
        tag: str = "",
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            start = time.time()
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.timeout_sec
                )
                resp.raise_for_status()
                data = resp.json()
                elapsed = time.time() - start
                result = self._parse(data, elapsed)
                self._log(tag, messages, payload, result, attempt, ok=True)
                return result
            except Exception as e:  # noqa: BLE001 - リトライ対象を広く取る
                last_err = e
                elapsed = time.time() - start
                logger.warning(
                    "LLM 呼び出し失敗 (tag=%s, attempt=%d/%d): %s",
                    tag, attempt, self.max_retries, e,
                )
                self._log(tag, messages, payload, None, attempt, ok=False, error=str(e))
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 10))
        raise LLMError(f"LLM 呼び出しに失敗しました (tag={tag}): {last_err}")

    def _parse(self, data: dict[str, Any], elapsed: float) -> LLMResponse:
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
            finish = choice.get("finish_reason", "stop") or "stop"
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"想定外のレスポンス形式: {e}: {str(data)[:300]}")
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text.strip(),
            finish_reason=finish,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            elapsed_sec=round(elapsed, 2),
        )

    def _log(self, tag, messages, payload, result, attempt, ok, error=None):
        if not self.call_log:
            return
        self.call_log.write(
            {
                "tag": tag,
                "attempt": attempt,
                "ok": ok,
                "model": self.model,
                "max_tokens": payload.get("max_tokens"),
                "messages": messages,
                "finish_reason": result.finish_reason if result else None,
                "prompt_tokens": result.prompt_tokens if result else None,
                "completion_tokens": result.completion_tokens if result else None,
                "elapsed_sec": result.elapsed_sec if result else None,
                "response_text": result.text if result else None,
                "error": error,
            }
        )

    # ---- 便利メソッド ----

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float | None = None,
        tag: str = "",
    ) -> LLMResponse:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self.chat(messages, max_tokens=max_tokens, temperature=temperature, tag=tag)

    def complete_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        tag: str = "",
    ) -> Any:
        """JSON を期待する呼び出し。temperature を下げてから抽出する。"""
        resp = self.complete(
            system, user, max_tokens=max_tokens, temperature=0.1, tag=tag
        )
        return extract_json(resp.text)

    def ping(self) -> bool:
        """接続確認。/models を叩いてみる。"""
        try:
            resp = requests.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            return resp.status_code < 500
        except Exception:  # noqa: BLE001
            return False


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Any:
    """LLM 出力から JSON を頑健に取り出す。"""
    # reasoning 型モデルが本文に <think>...</think> を混ぜる場合に備えて除去
    text = _THINK.sub("", text or "").strip()
    # 1) コードフェンス内
    m = _JSON_FENCE.search(text)
    if m:
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    # 2) そのまま
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 3) 最初の { または [ から対応する括弧までを切り出す
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            chunk = text[start : end + 1]
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
    raise LLMError(f"JSON を抽出できませんでした: {text[:300]}")
