FROM python:3.12-slim

# Set build instructions: Must be run from the project root context, e.g.:
# docker build -f backend/Dockerfile .

WORKDIR /app

# Install system dependencies for Tesseract OCR, PDF parsing, and PostgreSQL
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install global Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install backend-specific dependencies
COPY backend/requirements.txt ./backend_requirements.txt
RUN pip install --no-cache-dir -r backend_requirements.txt

# Copy the entire workspace to run Phase 6/7/8 scripts as subprocesses
COPY . .

# Run as a non-root user. The uploads/analytics directories are created here
# (and excluded from the build context via .dockerignore) so that Docker
# initializes their named-volume mounts with appuser ownership on first run,
# instead of the root:root ownership Docker would otherwise assign.
RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /app/backend/uploads /app/data/analytics_v2 \
    && chown -R appuser:appuser /app
USER appuser

# Move to backend directory to run FastAPI
WORKDIR /app/backend

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
