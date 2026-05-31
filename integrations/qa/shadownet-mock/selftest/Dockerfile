# Self-test client for the shared mock (used by compose.selftest.yml).
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'mcp>=1.2' 'anyio' 'httpx'
COPY test_mock_mcp.py /app/test_mock_mcp.py
CMD ["python", "test_mock_mcp.py"]