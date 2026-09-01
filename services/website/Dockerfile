FROM nginx:1.29-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html styles.css script.js favicon.svg /usr/share/nginx/html/
COPY entrypoint.sh /docker-entrypoint.d/40-app-url.sh
RUN chmod +x /docker-entrypoint.d/40-app-url.sh

EXPOSE 80
