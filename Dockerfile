FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig.json ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run check && npm run build

FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN python -m pip install --no-cache-dir -r backend/requirements.txt
COPY alembic.ini ./
COPY backend ./backend
COPY --from=frontend-build /build/dist ./frontend/dist
USER 10001:10001
EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn backend.app:app --host 0.0.0.0 --port \"$PORT\""]
