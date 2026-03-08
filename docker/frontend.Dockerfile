FROM node:20-alpine

WORKDIR /app

# Install deps first (better layer caching)
COPY frontend/package.json /app/package.json
COPY frontend/vite.config.js /app/vite.config.js
RUN npm install --silent

# Copy source
COPY frontend /app

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
