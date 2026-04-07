PYTHON := python3

.PHONY: help demo baseline iterations report chat chat-improve chat-deterministic clean-runs

help:
	@echo "Targets:"
	@echo "  make demo       # Run baseline + iterations + report"
	@echo "  make baseline   # Run fixed benchmark once"
	@echo "  make iterations # Run v0->v1->v2 iterative improvements"
	@echo "  make report     # Generate markdown report from comparison"
	@echo "  make chat       # Start interactive LLM chat (requires OPENAI_API_KEY)"
	@echo "  make chat-improve # Chat + apply self-improvement write-back"
	@echo "  make chat-deterministic # Interactive chat without API"
	@echo "  make clean-runs # Remove generated run artifacts"

baseline:
	$(PYTHON) src/baseline_v0.py \
		--policy config/policy_version.yaml \
		--scripts assets/script_pack_v0.json \
		--out tests/latest_run_v0.json

iterations:
	$(PYTHON) src/run_iterations.py \
		--cycles 2 \
		--max-policy-changes 1 \
		--max-script-changes 1 \
		--outdir tests/runs

report:
	$(PYTHON) src/generate_iteration_report.py \
		--comparison tests/runs/comparison.json \
		--out docs/iteration_report.md

demo: baseline iterations report
	@echo "Demo artifacts updated: tests/runs/comparison.json and docs/iteration_report.md"

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

chat-deterministic:
	$(PYTHON) src/live_call_console.py \
		--mode deterministic \
		--ingestion-mode rule \
		--policy config/policy_version.yaml \
		--scripts assets/script_pack_v0.json \
		--catalog data/product_catalog.json \
		--out-dir tests/live_calls

clean-runs:
	find tests/runs -type f -delete 2>/dev/null || true
	find tests/runs -type d -empty -delete 2>/dev/null || true
	find tests/live_calls -type f -delete 2>/dev/null || true
	find tests/live_calls -type d -empty -delete 2>/dev/null || true
	rm -f tests/latest_run_v0.json
	@echo "Generated run artifacts cleaned."
