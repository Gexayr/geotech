# Single-container build: the backend serves both the API and the built
# frontend from one process on one port — see app/main.py's static mount.

# ---- Stage 1: build the frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: backend + the frontend's static build ----
# 3.12, not 3.11 — rasterio==1.5.1 requires Python >=3.12.
FROM python:3.12-slim
WORKDIR /app

# rasterio's wheel bundles GDAL but still dynamically links a couple of
# system libs GDAL depends on (expat for XML, libgomp for OpenMP) — not
# present in the slim base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=frontend-build /frontend/dist ./static

ARG APP_PORT=9080
ENV APP_PORT=${APP_PORT}
EXPOSE ${APP_PORT}

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT}"]
