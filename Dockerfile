FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY data/notify/.gitkeep ./data/notify/.gitkeep

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir pyyaml requests \
    && pip install --no-cache-dir --no-deps .

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "pm_football_bot.runtime"]
