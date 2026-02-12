FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the modular bot package
COPY bot/ bot/

# Create a persistent data dir for sqlite (will be backed by a volume)
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
