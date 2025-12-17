import os
import time
import random
import json
import paho.mqtt.client as mqtt

broker = "mosquitto"
port = 1883
topic = "weather"

station_id = os.getenv("STATION_ID", "WS-XX")
interval = int(os.getenv("INTERVAL", "5"))
random
client = mqtt.Client()
client.connect(broker, port, 60)

while True:
    if random.random() < 0.01:
        temperature = -999
    else:
        temperature = round(random.uniform(15, 30), 1)
        
    if random.random() < 0.005:
        print("Simulierter Totalausfall")
        break

    data = {
        "stationId": station_id,
        "temperature": temperature,
        "humidity": round(random.uniform(30, 60), 1),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    client.publish(topic, json.dumps(data))
    print(f"[{station_id}] Published: {data}")
    time.sleep(interval)
