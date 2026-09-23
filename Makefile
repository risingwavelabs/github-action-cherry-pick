SHELL := /bin/bash
IMAGE = cherry-pick
TAG ?= $(shell git rev-parse --short HEAD)
DRY_RUN ?= false

.PHONY: build
build:
	docker build -t ${IMAGE}:${TAG} .

.PHONY: test
test: build
	docker run --rm --network none --entrypoint python3 \
		-e CHERRY_PICK_TEST_CONTAINER=1 -v "$(CURDIR):/work:ro" -w /work \
		${IMAGE}:${TAG} -B -m unittest discover -s tests -v

.PHONY: run
run:
	docker run -e GITHUB_TOKEN=${GITHUB_TOKEN} \
		-e GITHUB_ACTOR=ops \
		-e GITBOT_EMAIL=dummy@dmm.com \
		-e DRY_RUN=${DRY_RUN} \
		${IMAGE}:${TAG}
