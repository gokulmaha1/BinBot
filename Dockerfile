# ── Build Stage ──────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies for TA-Lib
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install TA-Lib C library
RUN wget -q http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz \
    && tar -xzf ta-lib-0.4.0-src.tar.gz \
    && cd ta-lib/ \
    && ./configure --prefix=/usr \
    && make \
    && make install \
    && cd .. \
    && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime Stage ────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy TA-Lib shared libraries
COPY --from=builder /usr/lib/libta_lib* /usr/lib/
COPY --from=builder /usr/include/ta-lib /usr/include/ta-lib
RUN ldconfig

# Copy installed Python packages
COPY --from=builder /install /usr/local

# Copy application
COPY . .

# Set Python path to backend directory for proper 'app' module imports
ENV PYTHONPATH=/app/backend

# Create data directories
RUN mkdir -p /app/data /app/ml_models

# Expose port
EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "backend.app.main:sio_app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
