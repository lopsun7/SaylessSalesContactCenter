PYTHON := $(if $(wildcard .venv/bin/python3),.venv/bin/python3,python3)

.PHONY: help chat chat-improve chat-nostream voice-chat voice-chat-improve clean-runs

help:
	@echo "Targets:"
	@echo "  make chat       # Start interactive LLM chat with SSE streaming (OPENAI_API_KEY)"
	@echo "                  # During streaming, type '/barge <text>' to interrupt"
	@echo "  make chat-improve # Chat + gated self-improvement write-back"
	@echo "  make chat-nostream # Chat with non-streaming response mode"
	@echo "  make voice-chat # Voice mode chat (OpenAI STT + OpenAI TTS)"
	@echo "  make voice-chat-improve # Voice mode + self-improvement write-back"
	@echo "  make clean-runs # Remove generated run artifacts"

chat:
	$(PYTHON) src/live_call_console.py \
		--mode llm \
		--ingestion-mode llm \
		--policy config/policy_version.yaml \
		--scripts assets/script_pack_v0.json \
		--catalog data/product_catalog.json \
		--out-dir tests/live_calls \
		--fallback-on-llm-error

chat-improve:
	$(PYTHON) src/live_call_console.py \
		--mode llm \
		--ingestion-mode llm \
		--policy config/policy_version.yaml \
		--scripts assets/script_pack_v0.json \
		--catalog data/product_catalog.json \
		--out-dir tests/live_calls \
		--self-improve \
		--write-back-policy \
		--write-back-scripts \
		--fallback-on-llm-error

chat-nostream:
	$(PYTHON) src/live_call_console.py \
		--mode llm \
		--ingestion-mode llm \
		--no-streaming \
		--disable-barge-in \
		--policy config/policy_version.yaml \
		--scripts assets/script_pack_v0.json \
		--catalog data/product_catalog.json \
		--out-dir tests/live_calls \
		--fallback-on-llm-error

voice-chat:
	$(PYTHON) src/live_call_console.py \
		--mode llm \
		--ingestion-mode llm \
		--io-mode voice \
		--stt-language en \
		--voice-energy-threshold 260 \
		--barge-trigger-ms 80 \
		--barge-ignore-ms 80 \
		--timeout-s 60 \
		--policy config/policy_version.yaml \
		--scripts assets/script_pack_v0.json \
		--catalog data/product_catalog.json \
		--out-dir tests/live_calls \
		--fallback-on-llm-error

voice-chat-improve:
	$(PYTHON) src/live_call_console.py \
		--mode llm \
		--ingestion-mode llm \
		--io-mode voice \
		--stt-language en \
		--voice-energy-threshold 260 \
		--barge-trigger-ms 80 \
		--barge-ignore-ms 80 \
		--timeout-s 60 \
		--policy config/policy_version.yaml \
		--scripts assets/script_pack_v0.json \
		--catalog data/product_catalog.json \
		--out-dir tests/live_calls \
		--self-improve \
		--write-back-policy \
		--write-back-scripts \
		--fallback-on-llm-error

clean-runs:
	find tests/runs -type f -delete 2>/dev/null || true
	find tests/runs -type d -empty -delete 2>/dev/null || true
	find tests/live_calls -type f -delete 2>/dev/null || true
	find tests/live_calls -type d -empty -delete 2>/dev/null || true
	@echo "Generated run artifacts cleaned."
