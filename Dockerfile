# Use a lightweight Python 3.11 image for compatibility and performance
FROM python:3.11-slim

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set workspace
WORKDIR /app

# Install system dependencies (needed for some math libraries)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the dashboard port
EXPOSE 8000

# Command to run the bot and dashboard
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
