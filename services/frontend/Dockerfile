# One Quasar (Vue 3) codebase for both clients. `quasar build -m spa` produces
# dist/spa, served here; `quasar build -m capacitor` produces the app from the
# same source, outside compose.
FROM nginx:1.27-alpine

COPY dist/spa/ /usr/share/nginx/html/

EXPOSE 80
