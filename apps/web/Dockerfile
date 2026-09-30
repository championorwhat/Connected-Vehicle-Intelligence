# syntax=docker/dockerfile:1.7
# Prognos dashboard: Vite build served by nginx, proxying /v1 to the API.
#   docker build -f apps/web/Dockerfile -t prognos-web apps/web
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.inc /etc/nginx/security-headers.inc
USER 101
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
