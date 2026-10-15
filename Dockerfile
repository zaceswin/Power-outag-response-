# Use a patched official lightweight Python image
FROM python:3.12.7-slim-bookworm

# SECURITY FIX: Upgrade Debian system packages to patch high vulnerabilities
# The rm -rf command clears the apt cache to keep the image size small
RUN apt-get update && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside the container
WORKDIR /app

# Copy dependency list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose the port the app runs on
EXPOSE 8000

# Command to run the FastAPI application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]