FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
