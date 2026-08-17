FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/
COPY schema.sql seed.py ./

EXPOSE 8080

# App Runner health-checks the port it is told about; /healthz is cheap and
# does not touch the database, so a cold DB never fails the deployment.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
