# Analyze Lambda — built on top of the Arm MCP server image.
#
# Instead of copying Ubuntu-built artifacts into an AL2023 Lambda base
# (which causes glibc/shared-lib mismatches), we keep the Ubuntu
# environment from arm-mcp and install the AWS Lambda Runtime Interface
# Client so it can run as a container-image Lambda.

FROM armlimited/arm-mcp:latest

ENV DEBIAN_FRONTEND=noninteractive

# ── Build LD_PRELOAD shim ────────────────────────────────────────────
# Lambda's Firecracker VM mounts a real sysfs, but /sys/devices/system/cpu/possible
# and /sys/devices/system/cpu/present are absent in Lambda's restricted sandbox.
# pytorch/cpuinfo calls openat() (not just fopen/open) to read these files.
# We intercept fopen, fopen64, open, open64, openat, openat64 at the C level
# and return a pipe containing "0\n" (= single CPU) when the file is missing.
COPY infra/analyze-lambda/cpu_shim.c /tmp/cpu_shim.c
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && \
    gcc -shared -fPIC -o /app/cpu_shim.so /tmp/cpu_shim.c -ldl && \
    rm /tmp/cpu_shim.c && \
    apt-get remove -y gcc libc6-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY infra/analyze-lambda/mcp_server_wrapper.py /app/mcp_server_wrapper.py

# ── Pre-download the sentence-transformers model into the image ──────
# Lambda has no outbound HuggingFace access and a read-only filesystem.
# Download the model at build time into /app/.hf_cache, then set
# HF_HOME to that path and TRANSFORMERS_OFFLINE=1 so it never tries
# to reach the network or write to a non-existent home directory.
RUN HF_HOME=/app/.hf_cache /app/.venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# ── Install the Lambda Runtime Interface Client ─────────────────────
RUN /app/.venv/bin/pip install --no-cache-dir awslambdaric

# ── Install the Lambda handler's own dependencies ───────────────────
COPY requirements-lambda.txt /tmp/requirements-lambda.txt
RUN /app/.venv/bin/pip install --no-cache-dir -r /tmp/requirements-lambda.txt

# ── Application code ────────────────────────────────────────────────
COPY src/ /var/task/src/

# ── Bundle the GitHub App private key if present ────────────────────
ARG PEM_FILE=""
RUN if [ -n "$PEM_FILE" ]; then cp "$PEM_FILE" /var/task/; fi

WORKDIR /var/task

ENV MCP_SERVER_DIR=/app \
    MCP_VENV_PYTHON=/app/.venv/bin/python \
    MCP_SERVER_SCRIPT=/app/mcp_server_wrapper.py \
    TOKENIZERS_PARALLELISM=false \
    DISABLE_MLFLOW_INTEGRATION=TRUE \
    HF_HOME=/app/.hf_cache \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

ENTRYPOINT ["/app/.venv/bin/python", "-m", "awslambdaric"]
CMD ["src.lambdas.analyze.handler.handler"]
