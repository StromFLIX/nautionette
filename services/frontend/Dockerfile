# One Quasar (Vue 3) codebase for both clients. This builds the web bundle;
# Capacitor wraps the same dist/ as the app, outside compose.
FROM node:24-bookworm-slim AS build
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
RUN npm install -g --ignore-scripts npm@11.21.2 && npm cache clean --force
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG APP_VERSION=dev
ARG APP_RELEASE_TAG=
RUN APP_VERSION="$APP_VERSION" APP_RELEASE_TAG="$APP_RELEASE_TAG" npm run build

FROM nginx:1.31.8-alpine
RUN apk upgrade --no-cache
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist/ /usr/share/nginx/html/
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
	CMD ["wget", "-q", "-T", "4", "-O", "/dev/null", "http://127.0.0.1/"]
