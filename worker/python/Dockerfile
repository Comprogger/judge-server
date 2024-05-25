# Use an image with GCC installed
FROM python:3.8

# Set the working directory in the container
WORKDIR /app/worker/python

# Copy all files from the current directory to the workdir in the container
COPY . .

# Install the 'time' package
RUN apt-get update && apt-get install -y time