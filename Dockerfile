FROM node:22-bookworm-slim

ARG LARK_CLI_VERSION=1.0.97

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    LARKSUITE_CLI_CONFIG_DIR=/state/lark-config \
    LARKSUITE_CLI_DATA_DIR=/state/lark-data \
    LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 \
    LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && npm install --global "@larksuite/cli@${LARK_CLI_VERSION}" \
    && groupadd --system --gid 10001 bridge \
    && useradd --system --uid 10001 --gid bridge --home-dir /home/bridge --create-home bridge \
    && mkdir -p /app /state/lark-config /state/lark-data /workspace \
    && chown -R bridge:bridge /state /workspace /home/bridge

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY --chown=bridge:bridge bridge ./bridge
COPY --chown=bridge:bridge scripts/run.py scripts/mock_lark_cli.py ./scripts/

USER bridge

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3).read()"

CMD ["python", "-m", "scripts.run"]
