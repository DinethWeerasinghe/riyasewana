# Use a lightweight official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables for Python and Playwright
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PORT=5000

# Install system dependencies required for Playwright and Chromium
# These are essential for Chromium to launch successfully on Linux
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser binaries and their system dependencies
# This command installs the browser and all standard shared libraries (.so files)
# required for headless Chromium to run on Linux.
RUN playwright install chromium --with-deps

# Copy the rest of the application files
COPY . .

# Expose port (Render/Linux router will bind to this port)
EXPOSE 5000

# Command to run the application
CMD ["python", "app.py"]
