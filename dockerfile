# Use a minimal Python base image
FROM python:3.10-slim

# Install system tools
RUN apt-get update && apt-get install -y \
    curl wget git iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy your script and everything else into the container
COPY . .

# Install Python dependencies directly
RUN pip install python-can requests paho-mqtt udsoncan \
    can can-isotp ecdsa
	
# Make the update script executable
RUN chmod +x update_agent.py
RUN chmod +x flashing_script.py
RUN chmod +x uds_client.py

# Run the script
CMD ["python", "update_agent.py"]


