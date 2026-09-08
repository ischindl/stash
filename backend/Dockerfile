FROM python:3.12-slim

# System deps for the Celery worker:
#   - curl: healthcheck in compose
#   - libreoffice + poppler-utils: PPTX → PDF → PNG for the Drive PPTX importer
#   - libnss3 / libnspr4 / etc.: required by Playwright Chromium
# Chromium binary itself is downloaded by `playwright install` below.
# libreoffice adds ~400 MB to the image but is the only sane PPTX
# renderer that runs in a headless container; alternatives like
# unoconv are libreoffice wrappers anyway.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libreoffice-impress libreoffice-core \
    poppler-utils \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
    libfontconfig1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 stash && \
    useradd --uid 1000 --gid stash --create-home stash

WORKDIR /app

COPY backend/requirements.txt .
# Dogfood-box constraint: local embedding inference runs on CPU, and the CUDA
# torch wheel costs 5GB on a disk that already runs Redis bgsave. Pin the CPU
# build so `pip install -r` cannot pull the nvidia metapackage.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.13.0+cpu
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browser binary. Installs into /root/.cache by default; we
# redirect to a stash-readable path so the worker (running as stash)
# can launch it.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install chromium && chown -R stash:stash /opt/playwright

COPY backend/ ./backend/
COPY docs/skills/ ./docs/skills/
# The VFS model + shell, shared verbatim with the `stash vfs` CLI command.
COPY stashvfs/ ./stashvfs/
COPY backend/entrypoint.sh ./entrypoint.sh
COPY alembic.ini ./alembic.ini
# The CLI release number clients are told about (backend/cli_release.py).
COPY pyproject.toml ./pyproject.toml

# The `stash` CLI itself, installed from the root pyproject (the same package
# publish.yml ships to PyPI, so its deps resolve from one source of truth).
# A sprites box gets the CLI from the sprite seed; in local exec mode the
# harness runs as a subprocess of THIS image and the curator and agent turns
# shell out to `stash`, so the image has to carry it.
COPY cli/ ./cli/
COPY stashai/ ./stashai/
COPY README.md ./README.md
RUN pip install --no-cache-dir .

# Bake the local embedding model into the image. With EMBEDDING_PROVIDER=local
# the semantic-search endpoints answer from a sentence-transformers model on
# disk; downloading it at first request made the founder's first query after
# every deploy hang, and a fresh container has no writable cache to put it in
# anyway. The model id matches EMBEDDING_MODEL in the deployment env.
ENV HF_HOME=/opt/hf
RUN mkdir -p /opt/hf && HF_HOME=/opt/hf python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" && chmod -R a+rX /opt/hf

# Node runtime + the pi coding agent, for local exec mode (AGENT_EXEC_MODE
# defaults to "local"): harness turns run as subprocesses of THIS image, and
# the pi harness shells out to the `pi` binary. Cloud sprite boxes get pi from
# the sprite seed instead; local exec has no seed, so the image carries it.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates xz-utils && \
    curl -fsSL https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz -o /tmp/node.tar.xz && \
    tar -xJf /tmp/node.tar.xz -C /opt && \
    ln -sf /opt/node-v22.23.2-linux-x64/bin/node /usr/local/bin/node && \
    ln -sf /opt/node-v22.23.2-linux-x64/bin/npm /usr/local/bin/npm && \
    ln -sf /opt/node-v22.23.2-linux-x64/bin/npx /usr/local/bin/npx && \
    rm /tmp/node.tar.xz && \
    npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.84.2 && \
    ln -sf /opt/node-v22.23.2-linux-x64/bin/pi /usr/local/bin/pi

RUN mkdir -p /app/.secrets && \
    chmod 700 /app/.secrets && \
    chmod +x /app/entrypoint.sh && \
    chown -R stash:stash /app
USER stash

EXPOSE 3456

ENTRYPOINT ["/app/entrypoint.sh"]
# Two workers so one request wedging its event loop (CPU-bound work, a stuck
# call) can't take the whole instance's /health down with it — a single-worker
# instance froze and was killed by Render's health check on 2026-08-19.
# Celery services override this CMD, so this only affects the web service.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "3456", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
