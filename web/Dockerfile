FROM python:3.12-slim

# curl required for Docker healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy shared module first (build context is repo root)
COPY shared/ ./shared/

# Install Python dependencies
COPY web/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy web application
COPY web/ ./

CMD ["python", "app.py"]
