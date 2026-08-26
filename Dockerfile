FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
COPY config ./config
COPY app.py ./
COPY pages ./pages
COPY .streamlit ./.streamlit
COPY data/notify/.gitkeep ./data/notify/.gitkeep
COPY data/predict/.gitkeep ./data/predict/.gitkeep

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1
ENV PORT=8501
EXPOSE 8501

CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true"]
