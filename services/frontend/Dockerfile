# One Quasar (Vue 3) codebase for both clients. This builds the web bundle;
# Capacitor wraps the same dist/ as the app, outside compose.
FROM node:24-bookworm-slim AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist/ /usr/share/nginx/html/
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
	CMD ["wget", "-q", "-T", "4", "-O", "/dev/null", "http://127.0.0.1/"]
