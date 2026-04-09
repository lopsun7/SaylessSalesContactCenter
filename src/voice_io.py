#!/usr/bin/env python3
"""Audio I/O and VAD helpers for live voice interaction."""

from __future__ import annotations

import array
from collections import deque
import math
import threading
import time
from typing import Deque, List, Tuple


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

    def _recent_mic_frames(self, limit: int) -> List[bytes]:
        if limit <= 0:
            return []
        with self._mic_lock:
            if not self._mic_buffer:
                return []
            return list(self._mic_buffer)[-limit:]

    def _trim_mic_buffer(self, keep_recent_frames: int) -> None:
        keep = max(0, keep_recent_frames)
        with self._mic_lock:
            while len(self._mic_buffer) > keep:
                self._mic_buffer.popleft()

    def record_utterance(
        self,
        *,
        max_seconds: float = 8.0,
        start_timeout_s: float = 6.0,
        min_speech_ms: int = 180,
        silence_ms: int = 700,
        pre_roll_ms: int = 220,
        abort_event: threading.Event | None = None,
    ) -> bytes:
        frames: List[bytes] = []
        speaking = False
        speech_frames = 0
        silence_frames = 0

        min_speech_frames = max(1, int(min_speech_ms / self.frame_ms))
        silence_target_frames = max(1, int(silence_ms / self.frame_ms))
        pre_roll_frames = max(1, int(max(0, pre_roll_ms) / self.frame_ms))
        self._trim_mic_buffer(keep_recent_frames=pre_roll_frames)

        start_deadline = time.monotonic() + max(0.1, start_timeout_s)
        end_deadline = time.monotonic() + max(0.2, max_seconds)

        while time.monotonic() < end_deadline:
            if abort_event is not None and abort_event.is_set():
                break
            chunk = self._pop_mic_frame(timeout_s=self.frame_ms / 1000.0 * 2.0)
            if not chunk:
                continue

            speech = self._is_speech(chunk)
            if not speaking:
                if speech:
                    speaking = True
                    frames.extend(self._recent_mic_frames(pre_roll_frames))
                    frames.append(chunk)
                    speech_frames = 1
                elif time.monotonic() >= start_deadline:
                    break
                continue

            frames.append(chunk)
            if speech:
                speech_frames += 1
                silence_frames = 0
            else:
                silence_frames += 1

            if speech_frames >= min_speech_frames and silence_frames >= silence_target_frames:
                break

        return b"".join(frames)

    def play_pcm_with_barge_in(
        self,
        pcm_bytes: bytes,
        *,
        enable_barge_in: bool = True,
        barge_trigger_ms: int = 120,
        barge_max_seconds: float = 6.0,
        barge_silence_ms: int = 650,
        barge_preroll_ms: int = 260,
        barge_ignore_ms: int = 120,
        echo_gate_ratio: float = 1.35,
    ) -> Tuple[bool, bytes]:
        if not pcm_bytes:
            return False, b""

        pyaudio = self._pyaudio_mod
        out_stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.sample_rate,
            output=True,
            frames_per_buffer=self.frame_samples,
        )

        trigger_frames = max(1, int(barge_trigger_ms / self.frame_ms))
        ignore_frames = max(0, int(max(0, barge_ignore_ms) / self.frame_ms))
        barge_silence_target = max(1, int(barge_silence_ms / self.frame_ms))
        barge_max_frames = max(1, int((barge_max_seconds * 1000) / self.frame_ms))
        pre_roll_frames = max(trigger_frames, int(max(0, barge_preroll_ms) / self.frame_ms))
        pre_roll_buffer = deque(maxlen=max(1, pre_roll_frames))

        # Keep only near-realtime microphone frames before playback starts.
        self._trim_mic_buffer(keep_recent_frames=pre_roll_frames)
        consecutive_speech = 0
        played_frames = 0
        output_stopped = False

        try:
            for pos in range(0, len(pcm_bytes), self.frame_bytes):
                chunk = pcm_bytes[pos: pos + self.frame_bytes]
                if not chunk:
                    break
                if output_stopped:
                    break
                out_stream.write(chunk)
                played_frames += 1

                if not enable_barge_in:
                    continue

                heard = self._pop_mic_frame(timeout_s=self.frame_ms / 1000.0 * 2.0)
                if not heard:
                    continue
                pre_roll_buffer.append(heard)

                # Ignore the first short window to reduce immediate self-echo trigger.
                if played_frames <= ignore_frames:
                    continue

                chunk_rms = self._frame_rms(chunk)
                dynamic_gate = max(0.0, chunk_rms * max(1.0, echo_gate_ratio))
                if self._is_speech(heard, min_rms=dynamic_gate):
                    consecutive_speech += 1
                else:
                    consecutive_speech = 0

                if consecutive_speech < trigger_frames:
                    continue

                # Stop playback immediately once user speech is confirmed.
                if not output_stopped:
                    try:
                        out_stream.stop_stream()
                    except Exception:
                        pass
                    output_stopped = True

                # Include short pre-roll so first words are not truncated.
                utterance_frames: List[bytes] = list(pre_roll_buffer) or [heard]
                silence_frames = 0
                for _ in range(barge_max_frames):
                    c = self._pop_mic_frame(timeout_s=self.frame_ms / 1000.0 * 2.0)
                    if not c:
                        continue
                    utterance_frames.append(c)
                    if self._is_speech(c):
                        silence_frames = 0
                    else:
                        silence_frames += 1
                    if silence_frames >= barge_silence_target:
                        break

                return True, b"".join(utterance_frames)

            return False, b""
        finally:
            if not output_stopped:
                try:
                    out_stream.stop_stream()
                except Exception:
                    pass
            try:
                out_stream.close()
            except Exception:
                pass
