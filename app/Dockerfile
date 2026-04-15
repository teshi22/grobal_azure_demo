# ============================================================
# 統合 Dockerfile — Frontend (Next.js static) + Backend (FastAPI)
# ============================================================

# --- Stage 1: フロントエンドビルド ---
FROM node:22-alpine AS frontend-builder

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ .

ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN npm run build

# --- Stage 2: バックエンド + 静的ファイル配信 ---
FROM python:3.13-slim

WORKDIR /app/backend

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# フロントエンドビルド成果物をコピー
COPY --from=frontend-builder /build/out /app/static

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
