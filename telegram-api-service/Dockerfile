# Use a slim Python 3.9 image as the base
FROM python:3.9-slim-buster

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies from requirements.txt
# --no-cache-dir: Prevents pip from storing cache, reducing image size
# -r: Install from the specified requirements file
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container
# This includes app.py and, importantly, your anon_session.session file
COPY . .

# Expose the port that your Flask application will listen on
# Render will map its internal $PORT variable to this exposed port automatically
EXPOSE 8080

# Command to run the application using Hypercorn (an ASGI server for async Flask)
# -b 0.0.0.0:8080: Binds the server to all network interfaces on port 8080
# app:app: Specifies that the Flask application instance 'app' is within the 'app.py' module
CMD ["hypercorn", "-b", "0.0.0.0:8080", "app:app"]
