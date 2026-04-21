#!/bin/sh
set -eu

SERVER_NAME="${YTA_SERVER_NAME:-_}"
HTTPS_CERT_DIR="/etc/letsencrypt/live/${SERVER_NAME}"
HTTPS_TEMPLATE="/opt/yta-nginx/nginx.https.conf.template"
HTTP_TEMPLATE="/opt/yta-nginx/nginx.http.conf.template"
TARGET_CONF="/etc/nginx/conf.d/default.conf"

export YTA_SERVER_NAME="$SERVER_NAME"

if [ -f "${HTTPS_CERT_DIR}/fullchain.pem" ] && [ -f "${HTTPS_CERT_DIR}/privkey.pem" ]; then
  envsubst '${YTA_SERVER_NAME}' < "${HTTPS_TEMPLATE}" > "${TARGET_CONF}"
  echo "gateway: rendered HTTPS config for ${SERVER_NAME}"
else
  envsubst '${YTA_SERVER_NAME}' < "${HTTP_TEMPLATE}" > "${TARGET_CONF}"
  echo "gateway: rendered HTTP-only config for ${SERVER_NAME}"
fi
