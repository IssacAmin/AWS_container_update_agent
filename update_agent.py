import ssl
import json
import subprocess
import requests
import os
import paho.mqtt.client as mqtt
import threading
import time


# === CONFIGURATION ===

DEVICE_ID = "jetson-nano-devkit"
AWS_IOT_ENDPOINT = "a2cv4n8w6s0bt0-ats.iot.eu-north-1.amazonaws.com"
MQTT_PORT = 8883

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CERT_FILE = os.path.join(SCRIPT_DIR, "certs", "device.crt")
KEY_FILE = os.path.join(SCRIPT_DIR, "certs", "device.key")
CA_CERT = os.path.join(SCRIPT_DIR, "certs", "AmazonRootCA1.pem")

UPDATE_TOPIC = f"update/{DEVICE_ID}"
MARKETPLACE_PUBLISH_TOPIC = f"marketplace_request/{DEVICE_ID}"
MARKETPLACE_SUBSCRIBE_TOPIC = f"marketplace/{DEVICE_ID}"
REQUESTS_TOPIC = f"requests/{DEVICE_ID}"
STATUS_TOPIC = f"status/{DEVICE_ID}"

FILE_TO_WATCH = os.path.join(SCRIPT_DIR, "json", "requests.json")
POLL_INTERVAL = 1  # seconds


# === MQTT SETUP ===

client = mqtt.Client(client_id=DEVICE_ID)

client.tls_set(
    ca_certs=CA_CERT,
    certfile=CERT_FILE,
    keyfile=KEY_FILE,
    tls_version=ssl.PROTOCOL_TLSv1_2
)

client.tls_insecure_set(False)


# === FUNCTIONALITY ===

def run_command(cmd):
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
    else:
        print(f"Output: {result.stdout}")
    return result.returncode == 0


def publish_status(status, message):
    payload = {
        "status": status,
        "message": message
    }
    client.publish(STATUS_TOPIC, json.dumps(payload))


def handle_update(payload):
    update_target = payload.get("update_target")

    if not update_target:
        print("error update target wrong")
        publish_status("error", "Missing 'update_target'")
        return

    if update_target == "HMI":
        hmi_data = payload.get("HMI_meta_data", {})
        action = hmi_data.get("action")
        manifest = hmi_data.get("manifest")

        if not action or not manifest:
            print("error action or manifest wrong")
            publish_status("error", "Missing 'action' or 'manifest' in HMI_meta_data")
            return

        container_id = manifest.get("container_id")
        if not container_id:
            publish_status("error", "Manifest missing 'container_id'")
            return

        manifest_dir = os.path.join(SCRIPT_DIR, "manifests")
        manifest_path = os.path.join(manifest_dir, f"{container_id}.json")

        json_dir = os.path.join(SCRIPT_DIR, "json")
        json_path = os.path.join(json_dir, "installed_features.json")
        os.makedirs(manifest_dir, exist_ok=True)
        features_data = { "features": [] }

        if os.path.exists(json_path):
            with open(json_path, "r") as f:
                try:
                    features_data = json.load(f)
                except json.JSONDecodeError:
                    print("Warning: Failed to decode JSON, starting fresh.")

        # Add new feature if not already installed
        feature_exists = any(feature["name"] == container_id for feature in features_data["features"])
        if not feature_exists:
            features_data["features"].append({
                "name": container_id,
                "installed": True
            })
            with open(json_path, "w") as f:
                json.dump(features_data, f, indent=2)

            print(f"Added feature '{container_id}' to installed_features.json.")

            try:
                with open(manifest_path, "w") as f:
                    json.dump(manifest, f, indent=2)
            except Exception as e:
                publish_status("error", f"Failed to write manifest: {e}")
                return
            
            publish_status("done", "Manifest updated successfully Version 1.0")


def handle_marketplace_payload(payload):
    marketplace = payload.get("marketplace")
    if len(marketplace) > 0:
        marketplace_dir = os.path.join(SCRIPT_DIR, "json")
        marketplace_path = os.path.join(marketplace_dir, "marketplace.json")
        os.makedirs(marketplace_dir, exist_ok=True)

        try:
            with open(marketplace_path, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            publish_status("error", f"Failed to write marketplace items")
            return
        publish_status("done", "Marketplace successfully fetched")


def watch_file_and_publish():
    print(f"Watching file: {FILE_TO_WATCH}")
    last_mtime = None

    while True:
        try:
            if os.path.exists(FILE_TO_WATCH):
                mtime = os.path.getmtime(FILE_TO_WATCH)
                if last_mtime is None or mtime != last_mtime:
                    last_mtime = mtime

                    with open(FILE_TO_WATCH, "r") as f:
                        features = json.load(f)

                    if isinstance(features, list) and features:
                        print(f"Publishing {len(features)} feature(s): {features}")
                        client.publish(REQUESTS_TOPIC, json.dumps(features))
                        publish_status("info", f"Published {len(features)} feature(s).")

                        # Clear list after publishing
                        with open(FILE_TO_WATCH, "w") as f:
                            json.dump([], f, indent=2)

        except Exception as e:
            print(f"Error watching file: {e}")
            publish_status("error", f"File watch error: {e}")
        time.sleep(POLL_INTERVAL)


# === MQTT CALLBACKS ===

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to AWS IoT Core.")
        client.subscribe([UPDATE_TOPIC, MARKETPLACE_SUBSCRIBE_TOPIC])
        print(f"Subscribed to topics: {UPDATE_TOPIC}, {MARKETPLACE_SUBSCRIBE_TOPIC}")
    else:
        print(f"Connection failed with code {rc}")


def on_message(client, userdata, msg):
    print(f"Received message on {msg.topic}")
    try:
        payload = json.loads(msg.payload.decode())
        print(f"Payload: {payload}")
        if msg.topic == UPDATE_TOPIC:
            handle_update(payload)
        elif msg.topic == MARKETPLACE_SUBSCRIBE_TOPIC:
            handle_marketplace_payload(payload)
    except Exception as e:
        print(f"Failed to handle message: {e}")
        publish_status("error", str(e))


def on_disconnect(client, userdata, rc):
    print(f"Disconnected with return code {rc}")
    if rc != 0:
        print("Unexpected disconnection. Trying to reconnect...")


# === MAIN ===

client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

client.connect(AWS_IOT_ENDPOINT, MQTT_PORT)
client.loop_start()  # Use non-blocking loop

# Start file watch in background thread
threading.Thread(target=watch_file_and_publish, daemon=True).start()

# Keep main thread alive
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Exiting...")
    client.disconnect()
