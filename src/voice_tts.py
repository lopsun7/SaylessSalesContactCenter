#!/usr/bin/env python3
"""OpenAI text-to-speech adapter that returns PCM16 mono bytes."""

from __future__ import annotations

import array
import io
import json
import os
import urllib.error
import urllib.request
import wave


def _resample_pcm16_mono(raw_pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate or not raw_pcm:
        return raw_pcm

    samples = array.array("h")
    samples.frombytes(raw_pcm)
    if not samples:
        return b""

    out_len = max(1, int(len(samples) * dst_rate / src_rate))
    out = array.array("h", [0] * out_len)
    for idx in range(out_len):
        src_idx = int(idx * src_rate / dst_rate)
        if src_idx >= len(samples):
            src_idx = len(samples) - 1
        out[idx] = samples[src_idx]
    return out.tobytes()


class OpenAITTSClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        response_format: str | None = None,
        speed: float = 1.0,
        timeout_s: int = 30,
        base_url: str | None = None,
        target_sample_rate: int = 16000,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        self.voice = voice or os.getenv("OPENAI_TTS_VOICE", "alloy")
        self.response_format = (response_format or os.getenv("OPENAI_TTS_RESPONSE_FORMAT", "wav")).lower()
        self.speed = float(speed)
        self.timeout_s = timeout_s
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.target_sample_rate = int(target_sample_rate)

        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for TTS.")
        if self.response_format not in {"wav", "pcm"}:
            raise ValueError("OPENAI TTS response_format must be 'wav' or 'pcm'.")

    def _wav_to_pcm16_mono(self, wav_bytes: bytes) -> bytes:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

        if sample_width != 2:
            raise RuntimeError(f"Unsupported WAV sample width for TTS output: {sample_width}")

        if channels == 1:
            mono = frames
        else:
            samples = array.array("h")
            samples.frombytes(frames)
            if not samples:
                return b""
            mono_samples = array.array("h")
            for idx in range(0, len(samples), channels):
                chunk = samples[idx: idx + channels]
                if not chunk:
                    continue
                mono_samples.append(int(sum(chunk) / len(chunk)))
            mono = mono_samples.tobytes()

        return _resample_pcm16_mono(mono, sample_rate, self.target_sample_rate)

    def synthesize_to_pcm(self, text: str) -> bytes:
        cleaned = text.strip()
        if not cleaned:
            return b""

        payload = {
            "model": self.model,
            "voice": self.voice,
            "input": cleaned,
            "response_format": self.response_format,
        }
        if self.speed != 1.0:
            payload["speed"] = self.speed

        req = urllib.request.Request(
            url=f"{self.base_url}/audio/speech",
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload).encode("utf-8"),
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                audio_bytes = resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"OpenAI TTS error {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI TTS network error: {exc}") from exc

        if self.response_format == "wav":
            return self._wav_to_pcm16_mono(audio_bytes)
        return audio_bytes
