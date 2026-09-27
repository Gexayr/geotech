FROM python:3.10-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
# web app + API.  Batch run on a map:  docker compose run --rm vineyard python -m vine run /app/maps/farm.tif --out /app/results/farm --web
CMD ["python", "app/server.py", "--host", "0.0.0.0", "--port", "8000"]
