# Shared mock Shadownet Sidecar (real MCP + JSON-RPC compat + webhook trigger).
# Built locally; runs on each harness's internal, zero-egress network.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'mcp>=1.2' 'uvicorn[standard]==0.32.*'
COPY main.py /app/main.py
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]