# Voice Duplex Architecture

This document describes the full-duplex voice pipeline used by `src/live_call_console.py`.

## Scope

- Voice mode only (`--io-mode voice`).
- LLM conversation core remains unchanged.
- This document focuses on audio capture, STT, TTS, streaming interrupt behavior, and troubleshooting.

## Goals

- Support always-on microphone capture.
- Allow speech-driven barge-in while agent TTS playback is ongoing.
- Reduce first-word loss after interruption.
- Reduce multi-turn stutter caused by repeated audio stream churn.

## High-Level Architecture

### Components

- `VoiceIO` (`src/voice_io.py`)
  - Keeps a persistent input stream and mic frame buffer.
  - Exposes:
    - `read_mic_frame(timeout_s)`
    - `is_speech_frame(frame, min_rms=0.0)`
    - `play_pcm_queue(pcm_queue, stop_event)`
- `DuplexASRWorker` (`src/live_call_console.py`)
  - Capture thread:
    - Pulls mic frames continuously.
    - Performs local VAD segmentation.
    - Pushes PCM segments to STT queue.
    - Emits early barge signal on speech onset while agent is speaking.
  - STT thread:
    - Sends PCM segments to OpenAI transcription.
    - Routes recognized text into:
      - user queue (normal turn input)
      - barge queue (interruption input while agent speaking)
- Response/TTS path (`src/live_call_console.py`)
  - LLM SSE response stream drives token chunks.
  - Token chunks are grouped into TTS segments.
  - TTS worker synthesizes PCM segments.
  - Playback worker streams PCM via `play_pcm_queue`.
  - Barge monitor consumes duplex barge signal/text and raises interrupt.
- SSE client (`src/llm_module.py`)
  - Uses short-timeout `readline` loop for faster stop-event responsiveness.

## Turn Lifecycle (Voice Streaming Mode)

1. `DuplexASRWorker` continuously captures mic frames.
2. Main loop polls user-text queue and starts an LLM turn when text is available.
3. During agent streaming:
   - `agent_speaking_event` is set.
   - `DuplexASRWorker` routes recognized speech to barge queue.
   - Capture thread emits barge signal as soon as speech onset is detected.
   - Barge monitor sets interrupt event immediately.
4. Interrupt event propagates to:
   - LLM SSE streaming stop event.
   - TTS synthesis worker stop condition.
   - Playback stop event.
5. If barge text arrives, it is routed as next user turn.
6. If interruption happened but no recognized text arrived in the wait window, assistant asks user to repeat.

## Why This Is Full Duplex

- Mic capture never pauses for playback.
- STT segmentation/transcription runs in background while TTS playback is active.
- Interruption is speech-driven in real time, not by a separate press-to-record phase.
- Playback stop can occur before transcription text is fully returned (barge signal path).

## Key Files

- `src/live_call_console.py`
  - `DuplexASRWorker`
  - voice-mode main loop
  - streaming + TTS + interruption orchestration
- `src/voice_io.py`
  - persistent mic reader
  - output queue playback
- `src/voice_stt.py`
  - OpenAI transcription adapter (`/audio/transcriptions`)
- `src/voice_tts.py`
  - OpenAI TTS adapter (`/audio/speech`)
- `src/llm_module.py`
  - SSE streaming loop and stop-event handling

## Runtime Flags (Most Relevant)

- `--io-mode voice`
- `--stt-language en`
- `--voice-vad-mode`
- `--voice-energy-threshold`
- `--voice-preroll-ms`
- `--voice-min-speech-ms`
- `--voice-silence-ms`
- `--voice-max-seconds`
- `--tts-segment-chars-first`
- `--tts-segment-chars-next`
- `--enable-barge-in` / `--disable-barge-in`
- `--barge-max-seconds` (thread join and wait window budgeting)

## Tuning Guide

### Symptom: Barge-in feels late

- Lower:
  - `--voice-min-speech-ms`
  - `--voice-silence-ms`
- Increase responsiveness by keeping segment sizes moderate:
  - `--tts-segment-chars-first 18~28`
  - `--tts-segment-chars-next 40~70`

### Symptom: First words still get lost

- Increase:
  - `--voice-preroll-ms` (for better segment head coverage)
- Ensure headset use to reduce speaker echo contamination.

### Symptom: Too many false interruptions

- Increase:
  - `--voice-energy-threshold`
- Increase `--voice-min-speech-ms` slightly.

### Symptom: Multi-turn stutter

- Reduce TTS request fragmentation:
  - Increase `--tts-segment-chars-next`.
- Check network/API latency and timeout setting:
  - `--timeout-s` (default 30, Makefile uses 60 for voice targets).

## Troubleshooting

### `RuntimeError: pyaudio is required`

- Install and run in project venv:
  - `brew install portaudio`
  - `pip install pyaudio webrtcvad`

### `ModuleNotFoundError: pydantic`

- Install in active venv:
  - `pip install pydantic`

### `cannot read from timed out object`

- This was an SSE read compatibility issue.
- The streaming loop now handles timed-out reads and continues polling.

### `pkg_resources is deprecated` warning from `webrtcvad`

- Warning only; does not block runtime.
- It comes from upstream package internals.

## Validation Checklist

1. Start voice chat and confirm opening greeting playback.
2. Perform 10-20 turns continuous voice conversation.
3. Interrupt agent mid-sentence in at least 5 turns.
4. Verify:
   - playback stops quickly on speech onset
   - barge text is routed into next turn
   - no repeated streaming timeout failures
   - no progressive stutter increase across turns

## Current Limitations

- STT is still segment-based API transcription (not token-level ASR stream).
- Barge text availability depends on network STT latency after early stop signal.
- Acoustic echo cancellation is not implemented in code; headset is still recommended.
