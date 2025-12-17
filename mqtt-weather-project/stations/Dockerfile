FROM python:3.10-slim

WORKDIR /app
COPY station1.py .

RUN pip install paho-mqtt

CMD ["python", "station1.py"]
