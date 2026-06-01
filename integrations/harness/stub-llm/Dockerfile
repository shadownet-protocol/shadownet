# Minimal OpenAI-compatible stub model server for the integration harness.
# Built locally from a standard slim base; runs on the harness's internal,
# zero-egress network. Digest pin is applied by the build step in
# deploy/compose.openclaw.yml's notes once resolved.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir 'fastapi==0.115.*' 'uvicorn[standard]==0.32.*'
COPY main.py /app/main.py
EXPOSE 9000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]