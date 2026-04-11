# Stage 1: Build the Vue 3 production bundle
FROM node:20-alpine AS build

WORKDIR /app

ARG VITE_DEBUG_MODE=false
ENV VITE_DEBUG_MODE=${VITE_DEBUG_MODE}

COPY frontend/package.json frontend/package-lock.json* /app/
RUN npm ci --silent

COPY frontend /app
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:stable-alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]