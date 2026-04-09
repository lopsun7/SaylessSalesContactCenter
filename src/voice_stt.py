#!/usr/bin/env python3
"""OpenAI speech-to-text adapter (PCM16LE -> transcription text)."""

from __future__ import annotations

import io
import json
import os
import uuid
import wave
import urllib.error
import urllib.request
from typing import Dict, Tuple


class OpenAITranscriptionClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: int = 30,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
        self.timeout_s = timeout_s
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")

        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for STT.")

    @staticmethod
    def _pcm_to_wav_bytes(pcm: bytes, sample_rate: int = 16000) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buffer.getvalue()

    @staticmethod
    def _build_multipart(
        fields: Dict[str, str],
        file_field: str,
        filename: str,
        file_bytes: bytes,
        mime_type: str,
    ) -> Tuple[bytes, str]:
        boundary = f"----codex-{uuid.uuid4().hex}"
        chunks: list[bytes] = []

        for key, value in fields.items():
            chunks.append(f"--{boundary}\r\n".encode("utf-8"))
            chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            chunks.append(str(value).encode("utf-8"))
            chunks.append(b"\r\n")

        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8")
        )
        chunks.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
        chunks.append(file_bytes)
        chunks.append(b"\r\n")

        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(chunks)
        return body, boundary

    def transcribe_pcm16le(
        self,
        pcm: bytes,
        sample_rate: int = 16000,
        language: str | None = None,
    ) -> str:
        if not pcm:
            return ""

        wav_bytes = self._pcm_to_wav_bytes(pcm, sample_rate=sample_rate)
        fields = {
            "model": self.model,
            "response_format": "json",
        }
        if language:
            fields["language"] = language

        body, boundary = self._build_multipart(
            fields=fields,
            file_field="file",
            filename="speech.wav",
            file_bytes=wav_bytes,
            mime_type="audio/wav",
        )

        req = urllib.request.Request(
            url=f"{self.base_url}/audio/transcriptions",
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            data=body,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"OpenAI STT error {exc.code}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI STT network error: {exc}") from exc

        text = str(payload.get("text", "")).strip()
        return text
