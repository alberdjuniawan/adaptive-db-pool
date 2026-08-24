-include .env
export

POSTGRES_HOST ?= localhost
POSTGRES_PORT ?= 5432
POSTGRES_USER ?= adaptive
POSTGRES_PASSWORD ?= adaptive
POSTGRES_DB ?= adaptive

DATABASE_URL ?= postgres://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@$(POSTGRES_HOST):$(POSTGRES_PORT)/$(POSTGRES_DB)?sslmode=disable

MIGRATIONS_DIR := postgres/migrations

.PHONY: dev up down logs migrate-up migrate-down migrate-status seed seed-benchmark sqlc test lint build benchmark experiment clean

dev:
	go -C backend run ./cmd/api

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f backend

migrate-up:
	DATABASE_URL='$(DATABASE_URL)' goose -dir $(MIGRATIONS_DIR) postgres '$(DATABASE_URL)' up

migrate-down:
	DATABASE_URL='$(DATABASE_URL)' goose -dir $(MIGRATIONS_DIR) postgres '$(DATABASE_URL)' down

migrate-status:
	goose -dir $(MIGRATIONS_DIR) postgres '$(DATABASE_URL)' status

seed:
	PGPASSWORD=$(POSTGRES_PASSWORD) psql -h $(POSTGRES_HOST) -p $(POSTGRES_PORT) -U $(POSTGRES_USER) -d $(POSTGRES_DB) -f postgres/seeds/development.sql

seed-benchmark:
	PGPASSWORD=$(POSTGRES_PASSWORD) psql -h $(POSTGRES_HOST) -p $(POSTGRES_PORT) -U $(POSTGRES_USER) -d $(POSTGRES_DB) -f postgres/seeds/benchmark.sql

sqlc:
	sqlc generate -f backend/sqlc.yaml

test:
	go -C backend test ./...

lint:
	go -C backend vet ./...
	test -z "$$(gofmt -l backend)"

build:
	mkdir -p backend/bin
	go -C backend build -o bin/api ./cmd/api

benchmark:
	@echo "Usage: make benchmark SCENARIO=mixed [BASE_URL=http://localhost:8080]"
	k6 run experiments/scenarios/$(SCENARIO).js

experiment:
	./experiments/scripts/run.sh experiments/configs/$(CONFIG).yaml

clean:
	rm -rf backend/bin
