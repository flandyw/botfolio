# Web dashboard: built React app plus the Node server that reads Alpaca Paper and proxies the worker API.
FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
USER node
EXPOSE 8601
CMD ["npm", "run", "server"]
