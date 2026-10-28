FROM python:3.12-slim

WORKDIR /app

# Copy shared module first (build context is repo root)
COPY shared/ ./shared/

# Install Python dependencies
COPY worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy worker application (preserve package structure so `from worker.X` imports work)
COPY worker/ ./worker/

CMD ["python", "worker/worker.py"]
