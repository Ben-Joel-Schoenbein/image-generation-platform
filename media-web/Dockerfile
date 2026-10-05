FROM node:22-bookworm-slim AS build
ARG VITE_IMAGE_STUDIO_URL=https://images.example.com
ENV VITE_IMAGE_STUDIO_URL=${VITE_IMAGE_STUDIO_URL}
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
