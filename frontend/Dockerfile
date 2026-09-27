# Procurement Verifier — frontend (build + nginx serve)
# The browser calls the API through same-origin nginx, which proxies /api,
# /health and /ready to the backend service. No hard-coded localhost URLs.

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1