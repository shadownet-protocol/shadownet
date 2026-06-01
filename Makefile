# Containerized dev + integration harness entry points.
#
# Every target runs inside Docker so host machines never install the agent
# hosts' heavy/unsafe dependency trees. Untrusted host images run with dropped
# capabilities, no new privileges, named-volume-only mounts, and zero egress —
# see CLAUDE.md "Container & harness security". See
# integrations/plugins/openclaw/deploy/compose.openclaw.yml.

OPENCLAW_DIR := integrations/plugins/openclaw/deploy
OPENCLAW_COMPOSE := $(OPENCLAW_DIR)/compose.openclaw.yml
HERMES_DIR := integrations/plugins/hermes-agent/deploy
HERMES_COMPOSE := $(HERMES_DIR)/compose.hermes.yml
MOCK_DIR := integrations/harness/shadownet-mock

.PHONY: openclaw-unit
openclaw-unit: ## Lint + build + test the OpenClaw plugin in a Node container (no host installs).
	docker compose -f $(OPENCLAW_COMPOSE) --profile unit run --rm --build unit

.PHONY: openclaw-e2e
openclaw-e2e: ## Boot the real gateway + mock + stub LLM (isolated) and run the e2e driver.
	bash $(OPENCLAW_DIR)/run-e2e.sh

.PHONY: openclaw-demo
openclaw-demo: ## Bring up a pokable gateway with the plugin wired to the mock (control UI on :18789).
	docker compose -f $(OPENCLAW_COMPOSE) -f $(OPENCLAW_DIR)/compose.openclaw.demo.yml --profile demo up --build

.PHONY: openclaw-clean
openclaw-clean: ## Tear down the OpenClaw harness and remove its images + named volumes.
	docker compose -f $(OPENCLAW_COMPOSE) --profile e2e down -v --remove-orphans --rmi local

.PHONY: hermes-e2e
hermes-e2e: ## Boot the real Hermes gateway + shared mock + stub LLM (isolated) and run the e2e driver.
	bash $(HERMES_DIR)/run-e2e.sh

.PHONY: hermes-demo
hermes-demo: ## Bring up a Hermes gateway with the plugin wired to the shared mock Sidecar.
	docker compose -f $(HERMES_COMPOSE) --profile demo up --build

.PHONY: hermes-clean
hermes-clean: ## Tear down the Hermes harness and remove its images + named volumes.
	docker compose -f $(HERMES_COMPOSE) --profile e2e down -v --remove-orphans --rmi local

.PHONY: mock-selftest
mock-selftest: ## Validate the shared mock Sidecar (MCP + JSON-RPC + trace) in isolation.
	docker compose -f $(MOCK_DIR)/compose.selftest.yml up --build --abort-on-container-exit --exit-code-from selftest

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'