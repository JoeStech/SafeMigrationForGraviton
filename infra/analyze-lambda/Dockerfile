# Analyze Lambda — container image with Arm MCP server baked in.
#
# Copies the pre-built MCP server app, venv, migration tools, and skopeo
# directly from the official armlimited/arm-mcp image so the Lambda can
# spawn the MCP server as a local subprocess (stdio) with zero network
# dependencies.

FROM armlimited/arm-mcp:latest AS mcp

FROM public.ecr.aws/lambda/python:3.12

# ── System deps needed by the MCP server tools ──────────────────────
RUN dnf install -y git libmagic && dnf clean all

# ── Copy Arm MCP server + tools from the official image ─────────────
# Python venv with fastmcp, sentence-transformers, torch, usearch, etc.
COPY --from=mcp /app /opt/arm-mcp

# Arm migration tools (migrate-ease scanners)
COPY --from=mcp /opt/arm-migration-tools /opt/arm-migration-tools

# skopeo binary for container image inspection
COPY --from=mcp /usr/bin/skopeo /usr/bin/skopeo

# llvm-mca and related binaries
COPY --from=mcp /usr/local/bin/ /usr/local/bin/

# ── Lambda handler dependencies ─────────────────────────────────────
COPY requirements-lambda.txt /tmp/requirements-lambda.txt
RUN pip install --no-cache-dir -r /tmp/requirements-lambda.txt

# ── Application code ────────────────────────────────────────────────
COPY src/ ${LAMBDA_TASK_ROOT}/src/

# ── Environment ─────────────────────────────────────────────────────
ENV MCP_SERVER_DIR=/opt/arm-mcp \
    MCP_VENV_PYTHON=/opt/arm-mcp/.venv/bin/python \
    MCP_SERVER_SCRIPT=/opt/arm-mcp/server.py

CMD ["src.lambdas.analyze.handler.handler"]
