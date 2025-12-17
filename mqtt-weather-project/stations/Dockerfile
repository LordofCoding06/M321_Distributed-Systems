FROM python:3.12-slim

WORKDIR /app

COPY station1.py .

RUN python -m pip install --no-cache-dir paho-mqtt

CMD ["python", "station1.py"]
