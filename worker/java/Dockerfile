# Use an image with OpenJ9 JVM installed
FROM adoptopenjdk:11-jdk-openj9

# Set the working directory in the container
WORKDIR /app/worker/java

# Copy all files from the current directory to the workdir in the container
COPY . .

# Install the 'time' package
RUN apt-get update && apt-get install -y time

# Set environment variables for JVM tuning
ENV JAVA_OPTS="-Xms512m -Xmx2g -XX:+UseG1GC"
