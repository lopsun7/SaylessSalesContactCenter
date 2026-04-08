PYTHON := python3

.PHONY: help chat chat-improve clean-runs

help:
	@echo "Targets:"
	@echo "  make chat       # Start interactive LLM chat (requires OPENAI_API_KEY)"
	@echo "  make chat-improve # Chat + gated self-improvement write-back"
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

clean-runs:
	find tests/runs -type f -delete 2>/dev/null || true
	find tests/runs -type d -empty -delete 2>/dev/null || true
	find tests/live_calls -type f -delete 2>/dev/null || true
	find tests/live_calls -type d -empty -delete 2>/dev/null || true
	@echo "Generated run artifacts cleaned."
