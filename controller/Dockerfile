FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY configs ./configs

ENV PYTHONUNBUFFERED=1 \
    BACKEND_URL=http://backend:8080 \
    PROMETHEUS_URL=http://prometheus:9090 \
    MODEL_PATH=/models/predictor.joblib \
    CONTROLLER_CONFIG=/app/configs/controller.yaml

CMD ["python", "-m", "src.main"]
