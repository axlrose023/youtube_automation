FROM node:22-alpine AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine

COPY deploy/gateway/nginx.http.conf.template /opt/yta-nginx/nginx.http.conf.template
COPY deploy/gateway/nginx.https.conf.template /opt/yta-nginx/nginx.https.conf.template
COPY deploy/gateway/render-nginx-conf.sh /docker-entrypoint.d/30-render-nginx-conf.sh
RUN chmod +x /docker-entrypoint.d/30-render-nginx-conf.sh
COPY --from=frontend-builder /app/frontend/dist /usr/share/nginx/html

HEALTHCHECK CMD wget --no-verbose --tries=1 -O /dev/null http://127.0.0.1/ || exit 1
