import ssl
import json
import subprocess
import os
import paho.mqtt.client as mqtt # type: ignore
import threading
import time
import base64
import zlib
from flashing_script import  send_update
import hashlib

# === GLOBAL VARIABLES === #
ecu_update_id = ""
ecu_curr_update_id = ""
curr_segment_no = -1
prev_segment_no = -1
first_segment = True
total_segments = -1
full_payload = ""
# === CONFIGURATION === #

DEVICE_ID = "jetson-nano-devkit"
AWS_IOT_ENDPOINT = "a2cv4n8w6s0bt0-ats.iot.eu-north-1.amazonaws.com"
MQTT_PORT = 8883

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CERT_FILE = os.path.join(SCRIPT_DIR, "certs", "device.crt")
KEY_FILE = os.path.join(SCRIPT_DIR, "certs", "device.key")
CA_CERT = os.path.join(SCRIPT_DIR, "certs", "AmazonRootCA1.pem")

# === MQTT TOPICS === #
UPDATE_TOPIC = f"update/{DEVICE_ID}"
MARKETPLACE_PUBLISH_TOPIC = f"marketplace_requests/{DEVICE_ID}"
MARKETPLACE_SUBSCRIBE_TOPIC = f"marketplace/{DEVICE_ID}"
REQUESTS_TOPIC = f"requests/{DEVICE_ID}"
STATUS_TOPIC = f"status/{DEVICE_ID}"

FILE_TO_WATCH = os.path.join(SCRIPT_DIR, "json", "requests.json")
MARKETPLACE_FILE_TO_WATCH = os.path.join(SCRIPT_DIR, "json", "marketplace_requests.json")
POLL_INTERVAL = 1  # seconds


# === MQTT SETUP === #

client = mqtt.Client(client_id=DEVICE_ID)

client.tls_set(
    ca_certs=CA_CERT,
    certfile=CERT_FILE,
    keyfile=KEY_FILE,
    tls_version=ssl.PROTOCOL_TLSv1_2
)

client.tls_insecure_set(False)


# === FUNCTIONALITY === #

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
    global ecu_update_id
    global ecu_curr_update_id 
    global curr_segment_no 
    global prev_segment_no 
    global first_segment 
    global total_segments 
    global segments 
    update_target = payload.get("update_target")

    if not update_target:
        print("error update target wrong")
        publish_status("error", "Missing 'update_target'")
        return

    if update_target == "HMI":
        hmi_data = payload.get("HMI_meta_data", {})
        action = hmi_data.get("action")
        manifest = hmi_data.get("manifest")
        feature_name = hmi_data.get("feature_name")
        print("1")
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
        feature_exists = any(feature["name"] == feature_name for feature in features_data["features"])
        if not feature_exists:
            features_data["features"].append({
                "name": feature_name,
                "installed": True
            })
            with open(json_path, "w") as f:
                json.dump(features_data, f, indent=2)

            print(f"Added feature '{feature_name}' to installed_features.json.")

            try:
                with open(manifest_path, "w") as f:
                    json.dump(manifest, f, indent=2)
            except Exception as e:
                publish_status("error", f"Failed to write manifest: {e}")
                return
            
            publish_status("done", "Manifest updated successfully Version 1.0")
    elif update_target == "ECU":
        ecu_data = payload.get("ECU_meta_data", {})
        segmented = bool(ecu_data.get("segmented"))
        if ecu_update_id == "":
            ecu_update_id = ecu_data.get("id")
            ecu_curr_update_id = ecu_data.get("id")
        else:
            ecu_curr_update_id = ecu_data.get("id")
        
        ecu_compressed_payload = payload.get("data")
        if segmented is True:
            total_segments = int(ecu_data.get("number_of_segments"))
            if curr_segment_no == -1:
                curr_segment_no = int(ecu_data.get("segment_no"))
            else:
                prev_segment_no = curr_segment_no
                curr_segment_no = int(ecu_data.get("segment_no"))
            assemble_payload(ecu_compressed_payload)
            update_ecu()
            publish_status("done", "ECU update segment recieved")
        elif segmented is False:
            prepare_payload(ecu_compressed_payload)
            update_ecu()
            publish_status("done", "ECU update relayed to UDS")
        return

def update_ecu():
    #Todo: call flashscript entry point
    with open("output.hex","rb") as f:
        delta_bytes = f.read()
    send_update(0,delta_bytes)
    return

def assemble_payload(compressed_payload):
    global ecu_update_id
    global ecu_curr_update_id 
    global curr_segment_no 
    global prev_segment_no 
    global first_segment 
    global total_segments 
    global full_payload

    if ecu_update_id == ecu_curr_update_id:
        if not first_segment:
            if curr_segment_no != prev_segment_no + 1:
                publish_status("error", "segment number is not matching previous one")
                return
            
        full_payload += compressed_payload
        prev_segment_no = curr_segment_no
        first_segment = False

        if(curr_segment_no == total_segments - 1):
            print("extracted payload:  " + full_payload)
            compressed_bytes = base64.b64decode(full_payload)
            print("compressed bytes:  " + str(compressed_bytes))
            decompressed_bytes = zlib.decompress(compressed_bytes)
            print("decompressed bytes:  " + str(decompressed_bytes))
            with open("output.hex","wb") as f:
                f.write(decompressed_bytes)
            first_segment = True
            ecu_update_id = ""
            ecu_curr_update_id = ""
            curr_segment_no  = -1
            prev_segment_no  = -1
            total_segments  = 0
            full_payload = ""
            
        publish_status("done", "assembled segmented payload successfully")
    else:
        publish_status("error", "segment id is not matching previous one")
        return

def prepare_payload(payload):
    print("extracted payload:  " + payload)
    compressed_bytes = base64.b64decode(payload)
    print("compressed bytes:  " + str(compressed_bytes))
    decompressed_bytes = zlib.decompress(compressed_bytes)
    print("decompressed bytes:  " + str(decompressed_bytes))
    with open("output.hex","wb") as f:
        f.write(decompressed_bytes)
    publish_status("done", "recieved payload successfully")
    return


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




def hash_file_content(filepath):
    with open(filepath, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def watch_marketplace_file_and_publish():
    print(f"Watching file: {MARKETPLACE_FILE_TO_WATCH}")
    last_hash = hash_file_content(MARKETPLACE_FILE_TO_WATCH)

    while True:
        try:
            if os.path.exists(MARKETPLACE_FILE_TO_WATCH):
                current_hash = hash_file_content(MARKETPLACE_FILE_TO_WATCH)
                if current_hash != last_hash:
                    last_hash = current_hash
                    msg = {
                        "message": "Requesting Marketplace items"
                    }
                    client.publish(MARKETPLACE_PUBLISH_TOPIC, json.dumps(msg))


                    # Reset hash to avoid re-publish due to our own write
                    last_hash = hash_file_content(MARKETPLACE_FILE_TO_WATCH)

        except Exception as e:
            print(f"Error watching file: {e}")
            publish_status("error", f"File watch error: {e}")
        time.sleep(POLL_INTERVAL)

def watch_request_file_and_publish():
    print(f"Watching file: {FILE_TO_WATCH}")
    last_hash = hash_file_content(FILE_TO_WATCH)

    while True:
        try:
            if os.path.exists(FILE_TO_WATCH):
                current_hash = hash_file_content(FILE_TO_WATCH)
                if current_hash != last_hash:
                    last_hash = current_hash
                    print("ana f el request hena ahoo")
                    with open(FILE_TO_WATCH, "r") as f:
                        features = json.load(f)

                    if isinstance(features, list) and features:
                        print(f"Publishing {len(features)} feature(s): {features}")
                        client.publish(REQUESTS_TOPIC, json.dumps(features))
                        publish_status("info", f"Published {len(features)} feature(s).")

                        # Clear list after publishing
                        with open(FILE_TO_WATCH, "w") as f:
                            json.dump([], f, indent=2)
                        
                        #Reset hash to avoid re-publish due to our own write
                        last_hash = hash_file_content(FILE_TO_WATCH)

        except Exception as e:
            print(f"Error watching file: {e}")
            publish_status("error", f"File watch error: {e}")
        time.sleep(POLL_INTERVAL)


# === MQTT CALLBACKS ===

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to AWS IoT Core.")
        client.subscribe([(UPDATE_TOPIC, 1), (MARKETPLACE_SUBSCRIBE_TOPIC, 1)])
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
threading.Thread(target=watch_request_file_and_publish, daemon=True).start()
threading.Thread(target=watch_marketplace_file_and_publish, daemon=True).start()

# Keep main thread alive
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Exiting...")
    client.disconnect()
