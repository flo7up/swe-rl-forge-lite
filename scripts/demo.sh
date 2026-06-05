#!/usr/bin/env sh
set -eu

python -m pip install -e ".[dev]"
forge --help
forge fetch examples/tasks.yaml
forge verify click-pr-001
forge package click-pr-001
forge reward taskpacks/click-pr-001
forge report click-pr-001
forge dashboard

printf '%s\n' "Dashboard generated at .forge/dashboard/index.html"