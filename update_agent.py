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
import tempfile
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
from ecdsa import SigningKey, NIST256p
import hashlib


# === GLOBAL VARIABLES === #
ecu_update_id = ""
ecu_curr_update_id = ""
curr_segment_no = -1
prev_segment_no = -1
first_segment = True
total_segments = -1
full_payload = ""
server_get_req_signal = False
delta_file_ready = False
ecu_name = ""
ecu_version = ""
# === CONFIGURATION === #

DEVICE_ID = "jetson-nano-devkit"
AWS_IOT_ENDPOINT = "a2cv4n8w6s0bt0-ats.iot.eu-north-1.amazonaws.com"
MQTT_PORT = 8883

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CERT_FILE = os.path.join(SCRIPT_DIR, "certs", "device.crt")
KEY_FILE = os.path.join(SCRIPT_DIR, "certs", "device.key")
CA_CERT = os.path.join(SCRIPT_DIR, "certs", "AmazonRootCA1.pem")
PRIVATE_KEY_FILE = os.path.join(SCRIPT_DIR, "certs", "private_key.pem")
# === MQTT TOPICS === #
UPDATE_TOPIC = f"update/{DEVICE_ID}"
MARKETPLACE_PUBLISH_TOPIC = f"marketplace_requests/{DEVICE_ID}"
MARKETPLACE_SUBSCRIBE_TOPIC = f"marketplace/{DEVICE_ID}"
REQUESTS_TOPIC = f"requests/{DEVICE_ID}"
STATUS_TOPIC = f"status/{DEVICE_ID}"
ONBOOT_TOPIC = f"on-boot/{DEVICE_ID}"
UPDATE_DB_TOPIC = f"update-db/{DEVICE_ID}"


# ===CONSTANTS=== #
SERVER_INTERNAL_PORT = 8080

class SimpleHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode()
        print(f"Received POST data: {body}")
        try:
            req = json.loads(body)
            print(f"gui feature request: {req}")
            req["car_id"] = DEVICE_ID
        except:
            print("Wrong Http Request format, ignoring request...")
            self.send_response(400)
            return 
        print(f"Sent feature request: {req}")
        client.publish(REQUESTS_TOPIC, json.dumps(req))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Signal received")

    def do_GET(self):
        global update_thread
        if self.path == "/marketplace":
            json_dir = os.path.join(SCRIPT_DIR, "json")
            installed_features_json_path = os.path.join(json_dir, "installed_features.json")

            print("Received GET request: Requesting marketplace items")
            msg = {
            "car_id": DEVICE_ID,
            "message": "Requesting Marketplace items"
            }
            client.publish(MARKETPLACE_PUBLISH_TOPIC, json.dumps(msg))
            global server_get_req_signal
            while(not server_get_req_signal):
                pass
            server_get_req_signal = False
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            marketplace_dir = os.path.join(SCRIPT_DIR, "json")
            marketplace_path = os.path.join(marketplace_dir, "marketplace.json")
            os.makedirs(marketplace_dir, exist_ok=True)
            with open(marketplace_path, "r") as f:
                try:
                    data = json.load(f)
                    annotated_data = annotate_marketplace_with_installed(data,installed_features_json_path)
                    self.wfile.write(json.dumps(annotated_data).encode('utf-8'))
                except json.JSONDecodeError:
                    print("Warning: Failed to decode JSON")
        elif self.path == "/update":
            print("recieved a start update request from GUI")
            update_thread.start()
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"ok")


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

def annotate_marketplace_with_installed(marketplace_data, installed_file_path):
    try:
        with open(installed_file_path, "r") as f:
            installed_data = json.load(f)
    except Exception as e:
        print(f"Failed to read installed features: {e}")
        installed_data = {}
    
    # Build a lookup set of installed feature IDs
    installed_ids = set()
    for feature in installed_data.get("features", []):
        if feature.get("installed") == True:
            installed_ids.add(feature.get("id"))
    print(installed_ids)
    # Annotate marketplace items
    for item in marketplace_data.get("marketplace", []):
        item_id = item.get("container_id")
        item["installed"] = item_id in installed_ids
    print(f"merged data: {marketplace_data}")
    return marketplace_data


def notify_cloud_onboot():
    json_dir = os.path.join(SCRIPT_DIR, "json")
    installed_features_json_path = os.path.join(json_dir, "installed_features.json")
    ecu_applications_json_path = os.path.join(json_dir, "ecu_applications.json")

    #checking current features and adding to them if it does not exist
    applications_data = { "applications": [] }
    if os.path.exists(ecu_applications_json_path):
        with open(ecu_applications_json_path, "r") as f:
            try:
                applications_data = json.load(f)
            except json.JSONDecodeError:
                print("Warning: Failed to decode JSON, starting fresh.")

    features_data = { "features": [] }
    if os.path.exists(installed_features_json_path):
        with open(installed_features_json_path, "r") as f:
            try:
                features_data = json.load(f)
            except json.JSONDecodeError:
                print("Warning: Failed to decode JSON, starting fresh.")
    
    payload = {
        "car_id": DEVICE_ID,
        "applications": applications_data["applications"],
        "features": features_data["features"]
    }
    
    print(f"startup notification: {payload}")
    client.publish(ONBOOT_TOPIC, json.dumps(payload))    

    msg = {
        "message": "Requesting Marketplace items"
    }
    client.publish(MARKETPLACE_PUBLISH_TOPIC, json.dumps(msg))
    return

def commit_app_update_version(target, version):
    json_dir = os.path.join(SCRIPT_DIR, "json")
    ecu_applications_json_path = os.path.join(json_dir, "ecu_applications.json")
    #checking current features and adding to them if it does not exist
    applications_data = { "applications": [] }
    if os.path.exists(ecu_applications_json_path):
        with open(ecu_applications_json_path, "r") as f:
            try:
                applications_data = json.load(f)
            except json.JSONDecodeError:
                print("Warning: Failed to decode JSON, starting fresh.")

        # Add new feature if not already installed
        app_exists = any(app["id"] == target for app in applications_data["applications"])
        if not app_exists:
            print("App name not found in JSON")
            return 
        else:
            for app in applications_data["applications"]:
                if app["id"] == target:
                    app["version"] = version
                    break
            atomic_json_write_safe(applications_data,ecu_applications_json_path)
            print(f"updated '{target}' version in ecu_applications.json.")
    
    payload = {
    "car_id": DEVICE_ID,
    "update_target": "ECU",
    "id": target,
    "version": version
    }
    client.publish(UPDATE_DB_TOPIC,json.dumps(payload))
    return



def atomic_json_write_safe(data, filename):
    lockfile_path = filename + ".lock"
    dirname = os.path.dirname(filename) or "."

    with open(lockfile_path, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)  # Only blocks other writers

        try:
            with tempfile.NamedTemporaryFile("w", dir=dirname, delete=False) as tmpfile:
                json.dump(data, tmpfile)
                tmpfile.flush()
                os.fsync(tmpfile.fileno())
                temp_name = tmpfile.name

            os.replace(temp_name, filename)  # Atomic swap

        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


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
    global delta_file_ready
    global ecu_name
    global ecu_version

    update_target = payload.get("update_target")
    if not update_target:
        print("error update target wrong")
        publish_status("Update failed", "Missing 'update_target'")
        return

    if update_target == "HMI":
        hmi_data = payload.get("HMI_meta_data", {})

        manifest = hmi_data.get("manifest")
        container_id = manifest.get("container_id")
        feature_name = hmi_data.get("feature_name")
        update_version = hmi_data.get("version")
        if not manifest:
            print("error manifest wrong")
            publish_status("Update failed", "Missing 'manifest' in HMI_meta_data")
            return
        if not container_id:
            publish_status("Update failed", "Manifest missing 'container_id'")
            return

        manifest_dir = os.path.join(SCRIPT_DIR, "manifests")
        manifest_path = os.path.join(manifest_dir, f"{container_id}.json")

        json_dir = os.path.join(SCRIPT_DIR, "json")
        installed_features_json_path = os.path.join(json_dir, "installed_features.json")
        
        #Adding the new container manifest
        os.makedirs(manifest_dir, exist_ok=True)
        try:
            atomic_json_write_safe(manifest,manifest_path)
        except Exception as e:
            publish_status("error", f"Failed to write manifest: {e}")
            return
        
        #checking current features and adding to them if it does not exist
        features_data = { "features": [] }
        if os.path.exists(installed_features_json_path):
            with open(installed_features_json_path, "r") as f:
                try:
                    features_data = json.load(f)
                except json.JSONDecodeError:
                    print("Warning: Failed to decode JSON, starting fresh.")

        # Add new feature if not already installed
        feature_exists = any(feature["id"] == container_id for feature in features_data["features"])
        if not feature_exists:
            features_data["features"].append({
                "name": feature_name,
                "version": update_version,
                "id": container_id,
                "installed": True
            })
            atomic_json_write_safe(features_data,installed_features_json_path)
            print(f"Added feature '{container_id}' to installed_features.json.")
            payload = {
                "update_target": "HMI",
                "car_id": DEVICE_ID,
                "id": container_id,
                "name": feature_name,
                "version": update_version
            }
            client.publish(UPDATE_DB_TOPIC,json.dumps(payload))
        else:
            for feature in features_data["features"]:
                if feature["id"] == container_id:
                    feature["version"] = update_version
                    break
            atomic_json_write_safe(features_data,installed_features_json_path)
            print(f"updated '{container_id}' version in installed_features.json.")
            payload = {
                "update_target": "HMI",
                "car_id": DEVICE_ID,
                "id": container_id,
                "name": feature_name,
                "version": update_version
            }
            client.publish(UPDATE_DB_TOPIC,json.dumps(payload))
        return


    elif update_target == "ECU":
        json_dir = os.path.join(SCRIPT_DIR, "json")
        ecu_data = payload.get("ECU_meta_data", {})
        segmented = bool(ecu_data.get("segmented"))
        target_ecu = ecu_data.get("target_ecu")
        update_version = ecu_data.get("version")
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
            if curr_segment_no == total_segments - 1:
                publish_status("Update done", "ECU update relayed to UDS")
                delta_file_ready = True
                ecu_name = target_ecu
                ecu_version = update_version
                publish_status("Update done", "ECU update recieved")
        elif segmented is False:
            prepare_payload(ecu_compressed_payload)          
            publish_status("Update done", "ECU update relayed to UDS")
            delta_file_ready = True
            ecu_name = target_ecu
            ecu_version = update_version
            publish_status("Update done", "ECU update recieved")
        return

def sign_delta_file(data_bytes):
    with open(PRIVATE_KEY_FILE, "rb") as f:
        sk = SigningKey.from_pem(f.read())  # Load EC private key

    # Sign the data (hashing internally with SHA-256)
    signature = sk.sign(data_bytes, hashfunc=hashlib.sha256)

    return signature


def update_ecu(MQTTClient):
    global delta_file_ready
    global ecu_name
    global ecu_version

    while(not(delta_file_ready and  ecu_name != "" and ecu_version != "" )):
        #print(f"file ready: {delta_file_ready}, user accepted: {user_accepted_update}, ecu name: {ecu_name}, ecu version: {ecu_version}")
        #print("waiting for signal")
        time.sleep(1)
    delta_file_ready = False

    with open("deltafile.hex","rb") as f:
        delta_bytes = f.read()
    
    signature = sign_delta_file(delta_bytes)
    complete_payload = delta_bytes + signature
    try:
        #send_update(MQTTClient, ecu_name, complete_payload)
        print("***********FLASH SEQUENCE DONE***********")
    except Exception as e:
        publish_status("Update failed", "ECU update failed")
    else:
        commit_app_update_version(ecu_name, ecu_version)
        publish_status("Update done", "ECU update segment recieved")

    ecu_name = ""
    ecu_version = ""
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
            with open("deltafile.hex","wb") as f:
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
    with open("deltafile.hex","wb") as f:
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
            
            #send the new data to the gui over the 8080 port
            #signal the server to respond
            global server_get_req_signal
            server_get_req_signal = True
        except Exception as e:
            publish_status("error", f"Failed to write marketplace items")
            return
        publish_status("done", "Marketplace successfully fetched")


# === MQTT CALLBACKS ===
subscribed = False

def on_connect(client, userdata, flags, rc):
    global subscribed 
    if rc == 0 and not subscribed:
        print("Connected to AWS IoT Core.")
        client.subscribe([(UPDATE_TOPIC, 1), (MARKETPLACE_SUBSCRIBE_TOPIC, 1)])
        subscribed = True
        print(f"Subscribed to topics: {UPDATE_TOPIC}, {MARKETPLACE_SUBSCRIBE_TOPIC}")
        notify_cloud_onboot()
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

def start_http_server():
    server = HTTPServer(('0.0.0.0', SERVER_INTERNAL_PORT), SimpleHandler)
    print(f"[HTTP] Listening on port {SERVER_INTERNAL_PORT}...")
    server.serve_forever()


# === MAIN ===

print("version 1.4.5")
client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

client.connect(AWS_IOT_ENDPOINT, MQTT_PORT)
client.loop_start()  # Use non-blocking loop

# Start HTTP server in its own thread
http_thread = threading.Thread(target=start_http_server, daemon=True)
http_thread.start()

update_thread = threading.Thread(target=update_ecu, args=(client,), daemon=True)


# Keep main thread alive
try:
    while True:
        pass
except KeyboardInterrupt:
    print("Exiting...")
    client.disconnect()
