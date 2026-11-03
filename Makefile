# Convenience targets. On a host with ROS 2 Jazzy (native Ubuntu 24.04, or inside
# the dev container) build/test/test-sim/lint run directly; on any other host
# (e.g. a Mac) they run in the dev image, so `make test` means the same everywhere.
SHELL := /bin/bash
ROS_SETUP := source /opt/ros/$${ROS_DISTRO:-jazzy}/setup.bash
WS_SETUP := $(ROS_SETUP) && source install/setup.bash
IMAGE ?= ar3-dev
DOCKER_RUN := docker run --rm -v "$(CURDIR)":/ar3_ws -w /ar3_ws $(IMAGE)
COMPOSE := docker compose -f docker/compose.yaml
PLATFORMS ?= linux/amd64,linux/arm64
HAVE_ROS := $(wildcard /opt/ros/$(or $(ROS_DISTRO),jazzy)/setup.bash)
# Label of the built images; the cockpit's image check compares it (core/repo.py, same files).
DOCKERFILE_HASH := $(shell cat docker/Dockerfile docker/entrypoint.sh docker/vnc-start.sh | \
	{ sha256sum 2>/dev/null || shasum -a 256; } | cut -c1-16)
AR3CTL := tools/cockpit/backend/.venv/bin/ar3ctl
# The cockpit agent in every stack checks this token (written by ./cockpit.sh); without it the
# stack runs, but the cockpit cannot talk to the agent (docs/manual/cockpit.md).
COCKPIT_DATA_DIR ?= $(HOME)/.ar3cockpit
export COCKPIT_AGENT_TOKEN ?= $(shell cat $(COCKPIT_DATA_DIR)/agent.token 2>/dev/null)
# One ROS stack per host (docs/manual/cockpit.md): the stacks would share port 8765 and the ROS graph.
STACKS := mac vnc linux linux-gpu remote
define one_stack
	@running="$(running_stack)"; \
	if [ -n "$$running" ]; then \
	  echo "A ROS stack is already running ($$running). Stop it first (make down, or Ctrl-C" \
	       "in its terminal)."; exit 1; fi
endef
# Linux stacks: GPU=1 uses the NVIDIA service, HEADLESS=1 opens no windows.
LINUX_SERVICE = $(if $(filter 1,$(GPU)),linux-gpu,linux)
LINUX_ENV = AR3_HEADLESS=$(if $(filter 1,$(HEADLESS)),1,0)
# Every stack target: DETACH=1 starts in the background (the cockpit does that; stop with
# `make down`), LICHTBLICK=1 also starts Lichtblick web on :8080.
UP = up $(if $(filter 1,$(DETACH)),--detach) --force-recreate
define viewers
	$(if $(filter 1,$(LICHTBLICK)),$(COMPOSE) --profile viewers up --detach lichtblick)
endef
define running_stack
$$(for s in $(STACKS); do docker ps -q --filter label=com.docker.compose.project=ar3 \
    --filter label=com.docker.compose.service=$$s | sed "s/.*/$$s/"; done | sort -u | paste -sd' ' -)
endef

.DEFAULT_GOAL := help

.PHONY: help deps build test test-sim lint clean image image-vnc image-multiarch \
	docker-build docker-test docker-test-sim docker-lint shell up-mac sim-mac vnc zenohd \
	up-linux sim-linux remote down restart lichtblick pull-viewers bag \
	demo-video demo-render demo-gif demo-publish cockpit cockpit-test cockpit-docs cockpit-login-assets ws-build smoke-sim dockerfile-hash doctor status-json \
	telemetry-export telemetry-compress scenario-run scenario-pack scenario-unpack assistant-index

ifeq ($(HAVE_ROS),)
build test test-sim lint: image
	$(DOCKER_RUN) make $@
else

deps:            ## install ROS dependencies of all packages (needs sudo)
	$(ROS_SETUP) && rosdep install --from-paths src --ignore-src -y -r

build:           ## colcon build (symlink install, tests enabled)
	$(ROS_SETUP) && colcon build --symlink-install --cmake-args -DBUILD_TESTING=ON

test: build      ## unit + mock integration tests (what CI runs)
	$(WS_SETUP) && colcon test --packages-select ar3_description ar3_moveit_config ar3_bringup ar3_cockpit_agent \
	  --event-handlers console_cohesion+ ; colcon test-result --verbose

test-sim: build  ## headless Gazebo integration test (local only, several minutes)
	$(WS_SETUP) && launch_test src/ar3_gazebo/test/test_sim.py

lint:            ## ruff + format check
	ruff check . && ruff format --check .
endif

clean:
	rm -rf build install log

image:           ## build the dev image (native arch)
	docker build -f docker/Dockerfile --target dev --build-arg DOCKERFILE_HASH=$(DOCKERFILE_HASH) \
	  -t $(IMAGE) .

image-vnc: image ## build the noVNC image (dev + TigerVNC/openbox/noVNC)
	docker build -f docker/Dockerfile --target vnc --build-arg DOCKERFILE_HASH=$(DOCKERFILE_HASH) \
	  -t $(IMAGE)-vnc .

image-multiarch: ## build dev and vnc for $(PLATFORMS) with buildx (no push, as in CI)
	docker buildx build -f docker/Dockerfile --platform $(PLATFORMS) --target dev .
	docker buildx build -f docker/Dockerfile --platform $(PLATFORMS) --target vnc .

docker-build docker-test docker-test-sim docker-lint: docker-%:
	$(DOCKER_RUN) make $*

ws-build: docker-build  ## colcon build inside the dev container (any host)

smoke-sim:       ## make test-sim, result recorded in .cockpit/results/smoke-sim.json (cockpit check)
	@rc=0; $(MAKE) test-sim || rc=$$?; \
	  tools/cockpit/scripts/record_result.sh smoke-sim $$rc || { \
	    echo "smoke-sim: could not record the result in .cockpit/results" >&2; \
	    [ $$rc -ne 0 ] || rc=1; }; exit $$rc

dockerfile-hash: ## the image label value for the current Dockerfile (cockpit image check)
	@echo $(DOCKERFILE_HASH)

shell:           ## interactive shell in the dev container (see docker/compose.yaml for GUI/devices)
	docker compose -f docker/compose.yaml run --rm dev

up-mac: image    ## Mac: mock + MoveIt + foxglove_bridge on :8765 (Ctrl-C stops; DETACH=1)
	$(one_stack)
	$(viewers)
	AR3_MODE=mock $(COMPOSE) --profile mac $(UP) mac

sim-mac: image   ## Mac: headless Gazebo + MoveIt + foxglove_bridge on :8765
	$(one_stack)
	$(viewers)
	AR3_MODE=sim $(COMPOSE) --profile mac $(UP) mac

vnc: image-vnc   ## RViz MotionPlanning in the browser: http://localhost:6080/vnc.html
	$(one_stack)
	$(viewers)
	$(COMPOSE) --profile vnc $(UP) vnc

up-linux: image  ## Linux: mock + MoveIt + RViz window (+ bridge :8765). HEADLESS=1, GPU=1
	$(one_stack)
	$(viewers)
	AR3_MODE=mock $(LINUX_ENV) $(COMPOSE) $(UP) $(LINUX_SERVICE)

sim-linux: image ## Linux: Gazebo + MoveIt + Gazebo and RViz windows. HEADLESS=1, GPU=1
	$(one_stack)
	$(viewers)
	AR3_MODE=sim $(LINUX_ENV) $(COMPOSE) $(UP) $(LINUX_SERVICE)

remote: image    ## Mac, REMOTE: only the cockpit agent, Zenoh client of Workstation A (docker/.env)
	$(one_stack)
	$(viewers)
	$(COMPOSE) --profile mac $(UP) remote

down:            ## stop the running ROS stack (any of the targets above) and Lichtblick web
	$(COMPOSE) --profile '*' rm --stop --force $(STACKS) lichtblick

restart:         ## restart the running ROS stack's container (same mode and settings)
	@running="$(running_stack)"; \
	if [ -z "$$running" ]; then echo "No ROS stack is running."; exit 1; fi; \
	echo "$(COMPOSE) restart $$running"; $(COMPOSE) --profile '*' restart $$running

lichtblick:      ## Lichtblick web on http://localhost:8080 (connects to foxglove_bridge :8765)
	$(COMPOSE) --profile viewers up --detach lichtblick

pull-viewers:    ## pull the Lichtblick web image (pinned in docker/compose.yaml)
	$(COMPOSE) --profile viewers pull lichtblick

bag:             ## rosbag (MCAP) in the running stack: PRESET=default|cameras DURATION=60 -> data/bags/
	@running="$(running_stack)"; \
	if [ -z "$$running" ]; then echo "No ROS stack is running."; exit 1; fi; \
	echo "$(COMPOSE) exec -T $$running bash docker/record_bag.sh $(or $(PRESET),default) $(or $(DURATION),60)"; \
	$(COMPOSE) --profile '*' exec -T -w /ar3_ws $$running \
	  bash docker/record_bag.sh $(or $(PRESET),default) $(or $(DURATION),60)

zenohd: image    ## Workstation A: Zenoh router on tcp/7447 for remote (Mac) clients
	$(COMPOSE) --profile linux up zenohd

# --- AR3 Cockpit (docs/manual/cockpit.md). Runs natively on the host, no ROS needed. ---
cockpit:         ## start the AR3 Cockpit on http://127.0.0.1:8400 (= ./cockpit.sh)
	./cockpit.sh

doctor:          ## the Install & Setup checklist in the terminal (= ar3ctl doctor; ./cockpit.sh once)
	$(AR3CTL) doctor

status-json:     ## host, running stack and viewers as JSON (= ar3ctl status --json)
	@$(AR3CTL) status --json

telemetry-export: ## a cockpit session to data/exports/: SESSION=<id> [FORMAT=parquet|csv] [FROM=…] [TO=…]
	$(AR3CTL) telemetry-export $(SESSION) --format $(or $(FORMAT),parquet) \
	  $(if $(RUN),--run $(RUN)) $(if $(FROM),--from $(FROM)) $(if $(TO),--to $(TO))

telemetry-compress: ## run the compression policies now (ALL=1: also today's chunks)
	$(AR3CTL) telemetry-compress $(if $(filter 1,$(ALL)),--all)

scenario-run:    ## run a scenario in the running sim: ID=<id> [MODE=] [REPEAT=1] [SEED=] [KEEP_SCENE=1] [BAG=1]
	$(AR3CTL) scenario-run $(ID) $(if $(MODE),--mode $(MODE)) $(if $(REPEAT),--repeat $(REPEAT)) \
	  $(if $(SEED),--seed $(SEED)) $(if $(filter 1,$(KEEP_SCENE)),--keep-scene) $(if $(filter 1,$(BAG)),--bag)

scenario-pack:   ## pack-and-go: IDS="a b" [OUT=file.zip] -> one zip with what they reference
	$(AR3CTL) scenarios-pack $(IDS) $(if $(OUT),-o $(OUT))

scenario-unpack: ## import a scenario pack: FILE=… [REPLACE=1 | COPY=1] (without: a preview)
	$(AR3CTL) scenarios-unpack $(FILE) $(if $(filter 1,$(REPLACE)),--replace) $(if $(filter 1,$(COPY)),--copy)

assistant-index: ## re-index the assistant's knowledge (changed files; FULL=1: all, embeddings too)
	$(AR3CTL) assistant-index $(if $(filter 1,$(FULL)),--full)

cockpit-docs:    ## regenerate docs/manual/cockpit-components.md from config/cockpit/components.yaml
	$(AR3CTL) components --markdown > docs/manual/cockpit-components.md

cockpit-test:    ## cockpit backend tests (needs the cockpit DB: ./cockpit.sh once) + e2e (fake mode)
	cd tools/cockpit/backend && uv run pytest -q
	cd tools/cockpit/frontend && npm run build && npx playwright test

cockpit-login-assets: image  ## regenerate the login screen's URDF + MoveIt trajectory (mock)
	docker run --rm -e ROS_DOMAIN_ID=87 -v "$(CURDIR)":/ar3_ws -w /ar3_ws $(IMAGE) \
	  bash tools/cockpit/scripts/login_assets.sh

help:            ## list the targets with their descriptions (default: plain `make`)
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sort -t: -k1,1 -u | \
	  awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# Demo videos (tools/demo/README.md). DEMO_EXECUTE=0 / DEMO_TESTS=0 shorten chapter 1;
# DEMO_THEME=dark records the GUI in dark mode.
RENDER = uv run --no-project --with pillow python tools/demo/render.py
demo-video: DEMO_DIR := $(CURDIR)/build/demo/$(shell date +%Y%m%d-%H%M%S)
demo-video:      ## record the cockpit demo on this host and render the videos (~30-45 min) -> build/demo/<time>/out
	cd tools/cockpit/frontend && npm run build && \
	  DEMO_OUT=$(DEMO_DIR) npx playwright test -c playwright.demo.config.ts
	$(RENDER) $(DEMO_DIR) $(RENDER_ARGS)

# The newest complete recording, unless DIR=build/demo/<time> names another.
DEMO_LATEST = $(lastword $(sort $(patsubst %/meta.json,%,$(wildcard build/demo/*/meta.json))))

demo-render:     ## render the demo videos again from the newest recording (or DIR=build/demo/<time>) [RENDER_ARGS=...]
	$(eval DIR := $(or $(DIR),$(DEMO_LATEST)))
	@test -n "$(DIR)" || { echo "no recording in build/demo/ (make demo-video first)"; exit 1; }
	$(RENDER) $(DIR) $(RENDER_ARGS)

demo-gif:        ## render only the time-lapse (demo.gif, demo.mp4) of the newest recording [RENDER_ARGS=...]
	$(eval DIR := $(or $(DIR),$(DEMO_LATEST)))
	@test -n "$(DIR)" || { echo "no recording in build/demo/ (make demo-video first)"; exit 1; }
	$(RENDER) $(DIR) --only gif $(RENDER_ARGS)

demo-publish:    ## copy demo.gif and poster.jpg of the newest rendered recording (or DIR=...) to docs/media/
	$(eval DIR := $(or $(DIR),$(DEMO_LATEST)))
	@test -f "$(DIR)/out/demo.gif" || { echo "no rendered demo.gif in '$(DIR)' (make demo-render first)"; exit 1; }
	mkdir -p docs/media
	cp $(DIR)/out/demo.gif docs/media/ar3-cockpit-demo.gif
	cp $(DIR)/out/poster.jpg docs/media/ar3-cockpit-poster.jpg
	@ls -lh docs/media
