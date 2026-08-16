FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

RUN apt-get update && apt-get install -y tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*

# Install dependencies with retries and timeout for network reliability
COPY requirements.txt .

RUN pip install --no-cache-dir --default-timeout=100 --retries=10 -r requirements.txt

# Install Chromium browser binary
RUN playwright install chromium

# Copy application files
COPY . .

# Create downloads directory
RUN mkdir -p downloads

# Expose FastAPI Port 5550
EXPOSE 5550

# Command to run server
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5550"]
