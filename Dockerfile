# Process-safety-monitor scanner image — deps only; the repo is mounted at /app
# at run time so code/data changes don't require a rebuild.
#   docker build -t psm:latest .
#   docker run --rm --env-file .env -v "$PWD:/app" psm:latest python -u main.py
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
CMD ["python", "-u", "main.py"]
