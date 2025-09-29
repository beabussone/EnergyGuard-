# --- Build stage ---
FROM node:20-alpine AS builder

WORKDIR /app

# Copia dipendenze e installa
COPY package*.json ./
RUN npm install

# Copia il resto del codice
COPY . .

# Build per la produzione (static files)
RUN npm run build

# --- Runtime stage ---
FROM nginx:1.27-alpine

# Copia i file statici nella directory di Nginx
COPY --from=builder /app/dist /usr/share/nginx/html

# Espone la porta HTTP standard
EXPOSE 80

# Nginx parte in automatico come entrypoint