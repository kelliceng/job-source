# The image tag MUST match the playwright package version in requirements.txt,
# or the browsers baked into the image won't match the driver and every Tier 2
# navigation fails at runtime.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app

# Dependencies first so code changes don't invalidate the layer cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    JOBSOURCE_CACHE=/tmp/jobsource-cache

EXPOSE 8000
# One worker: each Tier 2 request drives a Chromium instance, and memory is
# the binding constraint on small instances, not CPU.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 120"]
