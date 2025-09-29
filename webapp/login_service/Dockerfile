FROM python:3.11-slim
WORKDIR /app

# dipendenze minime
RUN pip install --no-cache-dir fastapi==0.115.0 "uvicorn[standard]==0.30.5"

# copia il servizio
COPY main.py .

EXPOSE 7000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7000"]