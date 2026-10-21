FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOST=0.0.0.0 FASTEMBED_CACHE_PATH=/models
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000 50051
# HOST=0.0.0.0 makes server.py refuse to start without API_KEY.
CMD ["python", "server.py"]
