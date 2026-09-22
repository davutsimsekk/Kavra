# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.14

FROM node:22-bookworm-slim AS web-build
WORKDIR /build/webui
COPY webui/package.json webui/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY webui/ ./
RUN npm run build


FROM python:${PYTHON_VERSION}-slim-bookworm AS python-build

# 1 = yerel ağır TTS motorları (CUDA Torch, XTTS, Anka, Chatterbox) imaja kurulur.
# 0 = GPU'suz sunucu için hafif imaj: yalnız çekirdek + Edge/Piper; ağır sesler
# uzak GPU'da çalışır (bkz. REMOTE_TTS.md). compose: KAVRA_LOCAL_TTS=0
ARG LOCAL_TTS=1

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git libsndfile1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt requirements-tts.txt requirements-chatterbox.txt ./

RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/python -m pip install --prefer-binary -r requirements.txt \
    && if [ "$LOCAL_TTS" = "1" ]; then /opt/venv/bin/python -m pip install --prefer-binary -r requirements-tts.txt; fi

# Chatterbox transformers 5.x ister; Coqui ise 4.x kullanır. İkinci bir CUDA
# Torch indirmeden yalnızca çakışan paketleri ayrı venv'de tutuyoruz.
RUN if [ "$LOCAL_TTS" = "1" ]; then \
      python -m venv /opt/chatterbox-venv \
      && main_site=$(/opt/venv/bin/python -c 'import site; print(site.getsitepackages()[0])') \
      && chatter_site=$(/opt/chatterbox-venv/bin/python -c 'import site; print(site.getsitepackages()[0])') \
      && printf '%s\n' "$main_site" > "$chatter_site/kavra_parent_venv.pth" \
      && /opt/chatterbox-venv/bin/python -m pip install --upgrade pip setuptools wheel \
      && /opt/chatterbox-venv/bin/python -m pip install --prefer-binary -r requirements-chatterbox.txt \
      && /opt/chatterbox-venv/bin/python -m pip install --no-deps chatterbox-tts==0.1.7 \
      && /opt/chatterbox-venv/bin/python -c "import torch, transformers; assert transformers.__version__ == '5.2.0'; print('Shared torch:', torch.__version__)"; \
    else mkdir -p /opt/chatterbox-venv; fi


FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    KAVRA_DATA_DIR=/data/runtime \
    KAVRA_CACHE_DIR=/data/_cache \
    KAVRA_MODELS_DIR=/data/models \
    KAVRA_PROJECTS_DIR=/data/projects \
    KAVRA_STUDY_DATA_DIR=/data/study_data \
    CHATTERBOX_PYTHON=/opt/chatterbox-venv/bin/python \
    TEMP=/data/_cache/tmp \
    TMP=/data/_cache/tmp \
    TMPDIR=/data/_cache/tmp \
    HF_HOME=/data/_cache/hf \
    HUGGINGFACE_HUB_CACHE=/data/_cache/hf \
    HF_XET_CACHE=/data/_cache/hf_xet \
    HF_HUB_DISABLE_XET=1 \
    TORCH_HOME=/data/_cache/torch \
    TTS_HOME=/data/_cache/tts_home \
    XDG_CACHE_HOME=/data/_cache/xdg \
    COQUI_TOS_AGREED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core libgomp1 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-build /opt/venv /opt/venv
COPY --from=python-build /opt/chatterbox-venv /opt/chatterbox-venv

WORKDIR /app
COPY app/ ./app/
COPY studio_web/ ./studio_web/
COPY prompts/ ./prompts/
COPY --from=web-build /build/webui/dist ./webui/dist

RUN mkdir -p /data/runtime /data/_cache/tmp /data/models /data/projects /data/study_data

EXPOSE 8768

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8768/api/bootstrap', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "studio_web.api:app", "--host", "0.0.0.0", "--port", "8768"]
