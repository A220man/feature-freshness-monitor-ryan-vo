FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_KEYCLOAK_ISSUER=http://localhost:8080/realms/freshness
ARG VITE_KEYCLOAK_CLIENT_ID=freshness-ui
ARG VITE_API_BASE=/api
ENV VITE_KEYCLOAK_ISSUER=$VITE_KEYCLOAK_ISSUER VITE_KEYCLOAK_CLIENT_ID=$VITE_KEYCLOAK_CLIENT_ID VITE_API_BASE=$VITE_API_BASE
RUN npm run build
FROM nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
