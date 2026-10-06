FROM python:3.12-slim

WORKDIR /app

# Dependencies first, code second. Layers are cached up to the first one whose
# inputs changed, so putting the code first would reinstall every dependency on
# every edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1

# 0.0.0.0, not 127.0.0.1. Bind to localhost inside a container and nothing
# outside it can connect.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
