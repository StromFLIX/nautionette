FROM nginx:1.31.8-alpine
RUN apk upgrade --no-cache

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html styles.css brand.css brand.mjs script.js releases.mjs definitions.mjs activity.mjs activity-simulation.mjs urls.mjs product-preview.mjs workspace-demo.mjs workspace-demo.css favicon.svg /usr/share/nginx/html/
COPY assets/ /usr/share/nginx/html/assets/
COPY entrypoint.sh /docker-entrypoint.d/40-app-url.sh
RUN chmod +x /docker-entrypoint.d/40-app-url.sh

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
	CMD ["wget", "-q", "-T", "4", "-O", "/dev/null", "http://127.0.0.1/"]
