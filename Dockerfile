# Devcontainer base image with Python and common tools
FROM mcr.microsoft.com/devcontainers/python:3.12

# Optional: extra tools for convenience
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl \
    && rm -rf /var/lib/apt/lists/*

# VS Code will mount the repo at /workspaces/kingmidis by default
WORKDIR /workspaces/kingmidis
