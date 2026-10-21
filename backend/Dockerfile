FROM node:22-bookworm-slim AS xiaoju-runtime
WORKDIR /runtime
COPY agent_runtime/package.json agent_runtime/package-lock.json ./
RUN npm ci --ignore-scripts --omit=dev --no-audit --no-fund

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata libstdc++6 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=xiaoju-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=xiaoju-runtime /runtime/node_modules ./agent_runtime/node_modules
EXPOSE 8000
# 迁移前置：alembic upgrade head 成功后再启动（PRD 12.2）
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*'"]
