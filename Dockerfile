# Use official Playwright image which includes system dependencies for browsers
FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=10000

WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium only (to keep image size smaller)
RUN playwright install chromium

# Copy the rest of the application code
COPY . .

# Create output directory
RUN mkdir -p output

# Expose the port Render expects
EXPOSE ${PORT}

# Run the server
# Render provides the PORT environment variable automatically
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT}
