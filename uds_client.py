from calendar import c
import udsoncan
import isotp
from udsoncan.connections import PythonIsoTpConnection
from udsoncan.client import Client
from udsoncan.configs import default_client_config
import can
import logging
from udsoncan import services
import paho.mqtt.client as mqtt
import json

STATUS_TOPIC = "status/jetson-nano-devkit"

def publish_status(client, status, message):
    payload = {
        "status": status,
        "message": message
    }
    client.publish(STATUS_TOPIC, json.dumps(payload))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UDS_SEED_SIZE = 4
UDS_KEY_SIZE = 4

def getKey(seed):
    return seed

class UDSClient:
    def __init__(self, MQTTClient, can_interface, can_bitrate, ecu_address, hmi_address, default_retries=5):
        self.can_interface = can_interface
        self.MQTTClient = MQTTClient
        self.can_bitrate = can_bitrate
        self.ecu_address = ecu_address
        self.hmi_address = hmi_address
        self.default_retries = default_retries
        self.client_config = default_client_config.copy()
        self.client_config['security_algo'] = self.custom_security_algo
        try:
            self.bus = can.Bus(interface="socketcan", channel=self.can_interface, bitrate=self.can_bitrate)
        except Exception as e:
            logger.info("Failed to acquire CAN bus")
            raise
        self.isotp_address = isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=self.ecu_address, txid=self.hmi_address)
        # Refer to isotp documentation for full details about parameters
        self.isotp_params = {
        'stmin': 32,                            # Will request the sender to wait 32ms between consecutive frame. 0-127ms or 100-900ns with values from 0xF1-0xF9
        'blocksize': 8,                         # Request the sender to send 8 consecutives frames before sending a new flow control message
        'wftmax': 0,                            # Number of wait frame allowed before triggering an error
        'tx_data_length': 8,                    # Link layer (CAN layer) works with 8 byte payload (CAN 2.0)
        # Minimum length of CAN messages. When different from None, messages are padded to meet this length. Works with CAN 2.0 and CAN FD.
        'tx_data_min_length': None,
        'tx_padding': 0x55,                     # Will pad all transmitted CAN messages with byte 0x5.
        'rx_flowcontrol_timeout': 1000,         # Triggers a timeout if a flow control is awaited for more than 1000 milliseconds
        'rx_consecutive_frame_timeout': 1000,   # Triggers a timeout if a consecutive frame is awaited for more than 1000 milliseconds
        'override_receiver_stmin': None,        # When sending, respect the stmin requirement of the receiver. Could be set to a float value in seconds.
        'max_frame_size': 4095,                 # Limit the size of receive frame.
        'can_fd': False,                        # Does not set the can_fd flag on the output CAN messages
        'bitrate_switch': False,                # Does not set the bitrate_switch flag on the output CAN messages
        'rate_limit_enable': False,             # Disable the rate limiter
        'rate_limit_max_bitrate': 1000000,      # Ignored when rate_limit_enable=False. Sets the max bitrate when rate_limit_enable=True
        'rate_limit_window_size': 0.2,          # Ignored when rate_limit_enable=False. Sets the averaging window size for bitrate calculation when rate_limit_enable=True
        'listen_mode': False,                   # Does not use the listen_mode which prevent transmission.
        }

        self.stack = isotp.CanStack(bus=self.bus, address=self.isotp_address, params=self.isotp_params)
        self.conn = PythonIsoTpConnection(self.stack)

    def custom_security_algo(self, level: int, seed: bytes, params):
        key = seed
        return key

    def session_control(self, session_type, timeout=300.0):
        try:
            self.client_config['p2_timeout'] = timeout
            with Client(self.conn, config=self.client_config) as client:
                response = client.change_session(session_type)
                if not response.service == services.DiagnosticSessionControl:
                    raise
                logger.info(f"return message to changing sessions: {response}")
                return response
        except Exception as e:
            #could be invalid response exception
            logger.error(f"Error in session_control: {e}")
            raise

    def tester_present(self, timeout=300.0):
        try:
            self.client_config['p2_timeout'] = timeout
            with Client(self.conn, config=self.client_config) as client:
                response = client.tester_present()
                logger.info(f"return message to tester present: {response}")
        except Exception as e:
            logger.error(f"Error in tester_present: {e}")
            raise

    def read_did(self, did, timeout=300.0):
        try:
            self.client_config['p2_timeout'] = timeout
            with Client(self.conn, config=self.client_config) as client:
                response = client.read_data_by_identifier(did) 
                logger.info(f"return message to read did: {response}")
        except Exception as e:
            logger.error(f"Error in read_did: {e}")
            raise

    def write_did(self, did, data, timeout=300.0):
        try:
            self.client_config['p2_timeout'] = timeout
            with Client(self.conn, config=self.client_config) as client:
                response = client.write_data_by_identifier(did, data)
                logger.info(f"return message to write did: {response}")
        except Exception as e:
            logger.error(f"Error in write_did: {e}")
            raise

    def routine_control(self, routine_id, control_type, data=b'', timeout=1.0):
        try:
            self.client_config['p2_timeout'] = timeout
            with Client(self.conn, config=self.client_config) as client:
                response = client.routine_control(routine_id, control_type, data)
                logger.info(f"return message to routine control: {response}")
        except Exception as e:
            logger.error(f"Error in routine_control: {e}")
            raise

    def security_access(self, level, timeout=300.0):
        try:
            self.client_config['p2_timeout'] = timeout
            with Client(self.conn, config=self.client_config) as client:
                response = client.unlock_security_access(level)
        except Exception as e:
            logger.error(f"Error in security_access: {e}")
            raise

    #TODO: parameters should be modified
    def communication_disable(self, communication_type, timeout=300.0):
        try:
            self.client_config['p2_timeout'] = timeout
            # return self.send_request(services.CommunicationControl, communication_type.to_bytes(1, 'big'), timeout=timeout)
            # with Client(self.conn, config=self.client_config) as client:
                # response = client.communication_control(communication_type)
            return
        except Exception as e:
            logger.error(f"Error in communication_disable: {e}")
            raise

    def request_download(self, memory_address, memory_size,address_format, memory_size_format, compression, encryption , timeout=1.0):
        try:
            dfi = udsoncan.DataFormatIdentifier(compression, encryption)
            self.client_config['p2_timeout'] = timeout
            memoryLocInstance = udsoncan.MemoryLocation(memory_address, memory_size, address_format, memory_size_format)
            with Client(self.conn, config=self.client_config) as client:
                response = client.request_download(memoryLocInstance, dfi)
                logger.info(f"return message to request download: {response}")
                return response
        except Exception as e:
            logger.error(f"Error in request_download: {e}")
            raise

    def transfer_data(self, block_sequence_counter, data : bytes, timeout=1.0):
        try:
            self.client_config['p2_timeout'] = timeout
            # request_data = block_sequence_counter.to_bytes(1, 'big') + data
            # return self.send_request(services.TransferData, request_data, timeout=timeout)
            with Client(self.conn, config=self.client_config) as client:
                response = client.transfer_data(block_sequence_counter, data)
                return response
        except Exception as e:
            logger.error(f"Error in transfer_data: {e}")
            raise

    def request_transfer_exit(self, timeout=300.0):
        try:
            self.client_config['p2_timeout'] = timeout
            # return self.send_request(services.RequestTransferExit, timeout=timeout)
            with Client(self.conn, config=self.client_config) as client:
                response = client.request_transfer_exit()                
                logger.info(f"return message to request transfer exit: {response}")
                return response
        except Exception as e:
            logger.error(f"Error in request_transfer_exit: {e}")
            raise

    def ecu_reset(self, reset_type, timeout=300.0):
        try:
            self.client_config['p2_timeout'] = timeout
            # return self.send_request(services.ECUReset, subfunction = reset_type, timeout=timeout)
            with Client(self.conn, config=self.client_config) as client:
                response = client.ecu_reset(reset_type)
                logger.info(f"return message to ecu reset: {response}")
                return response
        except Exception as e:
            logger.error(f"Error in ecu_reset: {e}")
            raise

    def shutdown(self):
        try:
            if self.bus is not None:
                self.bus.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down CAN bus: {e}")
            raise

if __name__ == "__main__":
    while True:
        continue

