#!/usr/bin/env python3
"""Audio I/O and VAD helpers for live voice interaction."""

from __future__ import annotations

import array
from collections import deque
import math
import queue
import threading
import time
from typing import Deque


class VoiceIOError(RuntimeError):
    pass


class VoiceIO:
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        vad_mode: int = 2,
        energy_threshold: int = 380,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_samples = int(sample_rate * frame_ms / 1000)
        self.frame_bytes = self.frame_samples * 2
        self.energy_threshold = energy_threshold

        try:
            import pyaudio  # type: ignore
        except ImportError as exc:
            raise VoiceIOError(
                "pyaudio is required for voice mode. Install with: pip install pyaudio"
            ) from exc

        self._pyaudio_mod = pyaudio
        self._pa = pyaudio.PyAudio()

        self._vad = None
        try:
            import webrtcvad  # type: ignore

            self._vad = webrtcvad.Vad(max(0, min(3, int(vad_mode))))
        except ImportError:
            self._vad = None

        # Keep microphone stream open across the whole session to avoid warm-up
        # latency and first-token loss when user barges in.
        try:
            self._in_stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.frame_samples,
            )
        except Exception as exc:
            self._pa.terminate()
            raise VoiceIOError(f"failed to open microphone input stream: {exc}") from exc

        self._mic_buffer: Deque[bytes] = deque(maxlen=max(200, int(10000 / self.frame_ms)))
        self._mic_lock = threading.Lock()
        self._mic_stop = threading.Event()
        self._mic_thread = threading.Thread(target=self._mic_reader_loop, daemon=True)
        self._mic_thread.start()

    def close(self) -> None:
        self._mic_stop.set()
        self._mic_thread.join(timeout=0.5)
        try:
            self._in_stream.stop_stream()
            self._in_stream.close()
        except Exception:
            pass
        try:
            self._pa.terminate()
        except Exception:
            pass

    @staticmethod
    def _frame_rms(frame: bytes) -> float:
        if not frame:
            return 0.0
        samples = array.array("h")
        samples.frombytes(frame[: len(frame) - (len(frame) % 2)])
        if not samples:
            return 0.0
        mean_square = sum(int(s) * int(s) for s in samples) / len(samples)
        return math.sqrt(mean_square)

    def _is_speech(self, frame: bytes, *, min_rms: float = 0.0) -> bool:
        if len(frame) < self.frame_bytes:
            return False

        rms = self._frame_rms(frame)
        threshold = max(float(self.energy_threshold), float(min_rms))

        if self._vad is not None:
            try:
                vad_ok = bool(self._vad.is_speech(frame, self.sample_rate))
                if not vad_ok:
                    return False
            except Exception:
                pass

        return rms >= threshold

    def _mic_reader_loop(self) -> None:
        while not self._mic_stop.is_set():
            try:
                chunk = self._in_stream.read(self.frame_samples, exception_on_overflow=False)
            except Exception:
                if self._mic_stop.is_set():
                    return
                time.sleep(0.01)
                continue

            with self._mic_lock:
                self._mic_buffer.append(chunk)

    def _pop_mic_frame(self, timeout_s: float = 0.25) -> bytes:
        deadline = time.monotonic() + max(0.01, timeout_s)
        sleep_s = max(0.002, self.frame_ms / 2000.0)
        while time.monotonic() < deadline and not self._mic_stop.is_set():
            with self._mic_lock:
                if self._mic_buffer:
                    return self._mic_buffer.popleft()
            time.sleep(sleep_s)
        return b""

    def read_mic_frame(self, timeout_s: float = 0.25) -> bytes:
        """Public mic frame reader for duplex pipelines."""
        return self._pop_mic_frame(timeout_s=timeout_s)

    def is_speech_frame(self, frame: bytes, *, min_rms: float = 0.0) -> bool:
        return self._is_speech(frame, min_rms=min_rms)

    def play_pcm_queue(
        self,
        pcm_queue: "queue.Queue[bytes | None]",
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Play PCM segments continuously until sentinel or stop event."""
        pyaudio = self._pyaudio_mod
        out_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            output=True,
            frames_per_buffer=self.frame_samples,
        )
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    pcm_bytes = pcm_queue.get(timeout=0.06)
                except queue.Empty:
                    continue
                if pcm_bytes is None:
                    break
                if not pcm_bytes:
                    continue
                for pos in range(0, len(pcm_bytes), self.frame_bytes):
                    if stop_event is not None and stop_event.is_set():
                        break
                    chunk = pcm_bytes[pos: pos + self.frame_bytes]
                    if not chunk:
                        break
                    out_stream.write(chunk)
        finally:
            try:
                out_stream.stop_stream()
            except Exception:
                pass
            try:
                out_stream.close()
            except Exception:
                pass
