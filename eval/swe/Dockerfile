# Base image for the SWE-bench handoff experiment: Python, git and the Codex CLI.
# Per-instance images (built by swe.py) add the task repo's dependencies on top.
FROM python:3.9-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @openai/codex@0.146.1 \
    && rm -rf /var/lib/apt/lists/*
RUN git config --system user.email agent@example.invalid && git config --system user.name agent \
    && git config --system --add safe.directory '*'
