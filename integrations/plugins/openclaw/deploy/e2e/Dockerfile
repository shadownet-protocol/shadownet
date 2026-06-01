# Pytest driver for the OpenClaw Tier-1 integration harness. Built locally;
# runs on the harness's internal, zero-egress network alongside the gateway.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'pytest==8.*' 'httpx==0.27.*'
COPY test_openclaw_e2e.py /app/test_openclaw_e2e.py
CMD ["pytest", "-v", "/app/test_openclaw_e2e.py"]