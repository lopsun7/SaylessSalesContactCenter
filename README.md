# Self-Improving Call Center Sales Agent (Binox G1)

This project is a text-first simulation of a sales call center agent for wireless earbuds.

It demonstrates:
- sales conversation handling,
- outcome -> analysis -> targeted improvement loop,
- explicit improvement logic,
- live-call gated self-improvement with traceability.

## Scope
- Domain: wireless earbuds e-commerce recommendation/sales.
- Input mode: text-first baseline + optional live voice mode.
- Design goal: modular, diagnosable improvements (not monolithic prompt rewrites).

## Core Architecture
Pipeline:
1. `state_tracker`
2. `signal_extractor`
3. `strategy_selector`
4. `content_planner`
5. `response_generator`
6. `evaluator`

Main runtime: `src/baseline_v0.py`
Interactive runtime: `src/live_call_console.py`

## Two Improvement Channels
### 1) Policy Improvement (`what to do`)
Stable behavior rules (decision layer), for example:
- discovery gates,
- objection ordering,
- close readiness,
- ranking weights,
- response limits.

Config: `config/policy_version.yaml`
Candidate optimizer: `OpenAIChatClient.propose_candidate_updates` in `src/llm_module.py`

### 2) Script Improvement (`how to say it`)
Language realization assets (expression layer), for example:
- objection templates,
- trust snippets,
- soft-close phrasing,
- style constraints.

Config: `assets/script_pack_v0.json`
Candidate optimizer: `OpenAIChatClient.propose_candidate_updates` in `src/llm_module.py`

## Validation Strategy
- Primary live-call improvement loop runs in `src/live_call_console.py`.
- Candidate optimization is LLM-driven.
- Candidate acceptance is gated by a second LLM judgment (no suite replay in this mode).

## Live LLM Interaction (OpenAI API)
You can chat with the agent in real time and optionally apply self-improvement from the live call.

Set API key:
```bash
pip install pydantic
export OPENAI_API_KEY=\"<your_key>\"
```

Start interactive LLM chat:
```bash
make chat
```

Chat + apply self-improvement (writes back to policy/scripts):
```bash
make chat-improve
```

Notes:
- Live session artifacts are saved under `tests/live_calls/`.
- In `--mode llm`, ingestion and reply generation are fully LLM-driven (no rule pipeline for turn handling).
- Reply generation uses SSE streaming by default.
- During streaming, type `/barge <your next utterance>` to interrupt the current assistant turn.
- If API call fails and fallback is enabled in `--mode llm`, the app sends a minimal safe retry prompt.
- LLM JSON ingestion/judging uses Pydantic validation plus one retry on parse/schema failure.
- Model can be changed with `OPENAI_MODEL` (default currently `gpt-4.1-mini`).
- In self-improve mode, a live negative/failing call now runs a gated loop:
  - use LLM to propose policy/script candidate updates,
  - auto-apply generated candidate updates when generation succeeds,
  - append an auditable trace event to `tests/live_calls/improvement_trace.jsonl`.

Direct run examples:
```bash
python3 src/live_call_console.py --mode llm --fallback-on-llm-error
python3 src/live_call_console.py --mode llm --no-streaming --disable-barge-in
```

## Voice Mode (OpenAI STT + OpenAI TTS)
Voice mode keeps the same self-improvement loop and state workflow, but uses:
- STT: OpenAI `/audio/transcriptions` (`gpt-4o-mini-transcribe` by default),
- TTS: OpenAI `/audio/speech` (`gpt-4o-mini-tts`, configurable voice/model),
- Always-on mic stream: background capture stays open to reduce wake-up latency.
- Barge-in: microphone-side interruption detection during agent playback, with pre-roll and echo gate.

Install optional voice dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
brew install portaudio
CPPFLAGS='-I/opt/homebrew/include' LDFLAGS='-L/opt/homebrew/lib' pip install pydantic pyaudio webrtcvad
```

Set required env vars:
```bash
export OPENAI_API_KEY="<your_openai_key>"
# optional overrides
export OPENAI_STT_MODEL="gpt-4o-mini-transcribe"
export OPENAI_TTS_MODEL="gpt-4o-mini-tts"
export OPENAI_TTS_VOICE="alloy"
```

Run voice mode:
```bash
make voice-chat
```
`voice-chat` runs in English-only mode (`--stt-language en` + English-only reply prompt).
It now uses auto-listen by default (no Enter needed between turns).
It also starts with an automatic spoken opening greeting.
Auto-listen now runs in a background worker (mic capture + STT), so the main loop does not block on recording.

Voice mode + self-improvement write-back:
```bash
make voice-chat-improve
```

Useful flags:
```bash
python3 src/live_call_console.py \
  --io-mode voice \
  --tts-model gpt-4o-mini-tts \
  --tts-voice alloy \
  --voice-vad-mode 2 \
  --voice-energy-threshold 260 \
  --barge-trigger-ms 80 \
  --barge-preroll-ms 300 \
  --barge-ignore-ms 80 \
  --echo-gate-ratio 1.35 \
  --barge-silence-ms 650
```

In voice mode, if you type normal text (not `/end` or `/state`) at the prompt,
it is treated as direct user input for that turn (useful for debugging STT errors).
Use `--voice-manual-turn` if you want the old press-Enter-per-turn behavior.

## Key Output Artifacts
- Live session output: `tests/live_calls/<SESSION_ID>.json`
- Live self-improvement loop output: `tests/live_calls/<SESSION_ID>_improvement.json`
- Improvement trace stream: `tests/live_calls/improvement_trace.jsonl`

## Supporting Design Docs
- `requirements.md`
- `failure_taxonomy.md`
- `test_matrix.md`
- `workflow_spec.md`
- `improvement_policy.md`
- `evaluation_rubric.md`

## Notes
- This repository intentionally prioritizes explainability and reproducibility for take-home assessment evaluation.
- Text mode requires OpenAI API access.
- Voice mode requires OpenAI API (LLM + STT + TTS) and local audio I/O support.
