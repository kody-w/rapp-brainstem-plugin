FROM python:3.12-slim AS rapp-work-sdk

ARG RAPP_WORK_SOURCE_COMMIT

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY RAPP_WORK_SDK_PIN.json ./
RUN python -c 'import json,re,sys; pin=json.load(open("RAPP_WORK_SDK_PIN.json", encoding="utf-8")); commit=sys.argv[1]; expected="https://github.com/kody-w/rapp-work"; assert re.fullmatch(r"[0-9a-f]{40}", commit or ""), "RAPP_WORK_SOURCE_COMMIT must be exactly 40 lowercase hexadecimal characters"; assert pin.get("sourceRepository")==expected, "unexpected RAPP Work sourceRepository"; assert pin.get("sourceCommit")==commit and pin.get("sourceCommitStatus")=="pinned", "build commit must equal the pinned SDK sourceCommit"' "$RAPP_WORK_SOURCE_COMMIT"
RUN python -m venv /opt/rapp-work \
    && /opt/rapp-work/bin/pip install --no-cache-dir \
        "rapp-work @ git+https://github.com/kody-w/rapp-work.git@${RAPP_WORK_SOURCE_COMMIT}" \
    && /opt/rapp-work/bin/python -c 'import importlib.metadata,json,sys; direct=json.loads(importlib.metadata.distribution("rapp-work").read_text("direct_url.json") or "{}"); vcs=direct.get("vcs_info", {}); expected="https://github.com/kody-w/rapp-work.git"; commit=sys.argv[1]; assert direct.get("url")==expected, "installed RAPP Work source repository mismatch"; assert vcs.get("vcs")=="git" and vcs.get("requested_revision")==commit and vcs.get("commit_id")==commit, "installed RAPP Work source commit mismatch"' "$RAPP_WORK_SOURCE_COMMIT" \
    && /opt/rapp-work/bin/python -m rapp_work --version \
        | python -c 'import json,sys; value=json.load(sys.stdin); assert value.get("status")=="ok" and value.get("profile")=="rapp-work-sdk/1" and value.get("result",{}).get("sdk_version")=="1.0.0", "installed RAPP Work SDK is incompatible"'


FROM python:3.12-slim AS gateway-builder

WORKDIR /build/gateway
COPY . ./
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels .


FROM python:3.12-slim

ARG RAPP_WORK_SOURCE_COMMIT

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COPILOT_SKIP_CLI_DOWNLOAD=0 \
    RAPP_ROOT=/app \
    RAPP_STATE_PATH=/data/copilot-home \
    RAPP_WORK_COMMAND="/opt/rapp-work/bin/python -m rapp_work" \
    RAPP_WORK_ROOTS=/workspaces \
    RAPP_WORK_SDK_SOURCE_COMMIT=$RAPP_WORK_SOURCE_COMMIT

WORKDIR /app

COPY --from=rapp-work-sdk /opt/rapp-work /opt/rapp-work
COPY --from=gateway-builder /build/wheels /build/wheels
COPY README.md LICENSE RAPP_WORK_SDK_PIN.json RAPP_WORK_PLUGIN_RELEASE.json ./
RUN pip install --no-cache-dir /build/wheels/rapp_brainstem_gateway-*.whl \
    && rm -rf /build/wheels \
    && python -m copilot download-runtime

COPY agents ./agents
COPY soul.md ./soul.md

RUN useradd --create-home --uid 10001 brainstem \
    && mkdir -p /data/copilot-home /workspaces \
    && chown -R brainstem:brainstem /app /data /workspaces

USER brainstem
EXPOSE 7071

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7071/health', timeout=3)"

CMD ["uvicorn", "rapp_brainstem_gateway.app:app", "--host", "0.0.0.0", "--port", "7071", "--proxy-headers"]
