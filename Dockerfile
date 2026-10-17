FROM python:3.13-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
COPY requirements.lock /tmp/requirements.lock
RUN /opt/venv/bin/pip install --require-hashes --only-binary=:all: -r /tmp/requirements.lock

FROM python:3.13-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 bot && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin bot
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY src/voice_notify /app/voice_notify
USER 10001:10001
CMD ["python", "-m", "voice_notify"]
