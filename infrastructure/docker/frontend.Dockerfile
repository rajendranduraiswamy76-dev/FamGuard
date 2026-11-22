FROM node:24-bookworm-slim AS builder
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_AUTH_MODE=test \
    NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM node:24-bookworm-slim AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_AUTH_MODE=test \
    NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
WORKDIR /app
COPY --from=builder --chown=node:node /app /app
USER node
CMD ["npm", "run", "start", "--", "--hostname", "127.0.0.1", "--port", "3000"]
