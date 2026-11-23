FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ECOMOPS_SSO_MODE=dev

# poppler is required by the document-intake pipeline; curl powers the healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
COPY .streamlit ./.streamlit

RUN pip install --no-cache-dir -e ".[ui,api,chat,docs]"

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1

CMD ["python", "-m", "streamlit", "run", "src/ecomops/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
