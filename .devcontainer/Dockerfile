FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

# The base image ships a Yarn apt source whose signing key no longer verifies
# (NO_PUBKEY 62D54FD4003F6525). Its failing `apt-get update` breaks every later
# apt step: the docker-in-docker feature and `playwright install --with-deps`.
# We don't use Yarn, so drop the source.
RUN grep -rl "dl.yarnpkg.com" /etc/apt/sources.list.d/ 2>/dev/null | xargs -r rm -f \
    && apt-get update \
    && rm -rf /var/lib/apt/lists/*
