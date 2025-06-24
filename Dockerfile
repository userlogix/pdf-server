FROM python:3.11-slim

RUN apt-get update && apt-get install -y ghostscript curl && \
    pip install fastapi uvicorn python-multipart requests PyPDF2

COPY . /app
WORKDIR /app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]