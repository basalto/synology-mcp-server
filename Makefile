SHELL      := /bin/bash
DEPLOY_DIR := $(shell pwd)
STACK      := synology-mcp
SERVICE    := synology-mcp

.PHONY: install-service uninstall-service start stop restart status logs upgrade help

## install-service  Create and enable systemd service (starts on boot)
install-service:
	printf '%s\n' \
		'[Unit]' \
		'Description=Synology DSM MCP Server' \
		'Requires=docker.service' \
		'After=docker.service network-online.target' \
		'Wants=network-online.target' \
		'' \
		'[Service]' \
		'Type=oneshot' \
		'RemainAfterExit=yes' \
		'WorkingDirectory=$(DEPLOY_DIR)' \
		'ExecStart=/bin/bash -c "set -a && source $(DEPLOY_DIR)/.env && set +a && /usr/bin/docker stack deploy -c $(DEPLOY_DIR)/docker-compose.yml --with-registry-auth $(STACK)"' \
		'ExecStop=/usr/bin/docker stack rm $(STACK)' \
		'TimeoutStartSec=120' \
		'' \
		'[Install]' \
		'WantedBy=multi-user.target' \
		| sudo tee /etc/systemd/system/$(SERVICE).service > /dev/null
	sudo systemctl daemon-reload
	sudo systemctl enable $(SERVICE)
	@echo "Service $(SERVICE) installed — run 'make start' to start it"

## uninstall-service  Stop, disable and remove systemd service
uninstall-service:
	sudo systemctl stop $(SERVICE) 2>/dev/null || true
	sudo systemctl disable $(SERVICE) 2>/dev/null || true
	sudo rm -f /etc/systemd/system/$(SERVICE).service
	sudo systemctl daemon-reload
	@echo "Service $(SERVICE) removed"

## start    Deploy the stack
start:
	set -a && source .env && set +a && \
	docker stack deploy -c docker-compose.yml --with-registry-auth $(STACK)

## stop     Remove the stack
stop:
	docker stack rm $(STACK)

## restart  Redeploy the stack
restart:
	set -a && source .env && set +a && \
	docker stack deploy -c docker-compose.yml --with-registry-auth $(STACK)

## status   Show stack service status
status:
	docker stack ps $(STACK)

## logs     Tail container logs
logs:
	docker service logs -f --tail=100 $(STACK)_synology-mcp

## upgrade  Pull latest image and redeploy
upgrade:
	docker pull ghcr.io/basalto/synology-mcp-server:latest
	set -a && source .env && set +a && \
	docker stack deploy -c docker-compose.yml --with-registry-auth $(STACK)

## help     Show available targets
help:
	@grep -E '^## ' Makefile | sed 's/^## //'
