FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# pkg_resources (part of setuptools) is needed by webrtcvad; not included in Python 3.12-slim by default
RUN pip install --no-cache-dir "setuptools<71"

# Install torch first (arm64 Linux has no CUDA, always CPU-only)
RUN pip install --no-cache-dir torch==2.2.2

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
