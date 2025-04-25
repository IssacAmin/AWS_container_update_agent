import udsoncan
import udsoncan.Request
import isotp
from udsoncan.connections import PythonIsoTpConnection
from udsoncan.client import Client
from udsoncan.configs import default_client_config
import can
import logging
from udsoncan import services

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UDS_SEED_SIZE = 4
UDS_KEY_SIZE = 4

def getKey(seed):
    return seed

class UDSClient:
    def __init__(self, can_interface, can_bitrate, ecu_address, hmi_address, default_retries=5):
        self.can_interface = can_interface
        self.can_bitrate = can_bitrate
        self.ecu_address = ecu_address
        self.hmi_address = hmi_address
        self.default_retries = default_retries
        self.client_config = default_client_config.copy()
        self.client_config['security_algo'] = self.custom_security_algo
        try:
            self.bus = can.interface.Bus(interface='socketcan', channel=self.can_interface, bitrate=self.can_bitrate)
        except Exception as e:
            logger.error(f"Failed to initialize CAN bus: {e}")
            raise

    def custom_security_algo(self, level, seed):
        key = seed
        return key

    def send_request(self, service, data, timeout=1.0):
        isotp_address = isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=self.ecu_address, txid=self.hmi_address)
        isotp_layer = isotp.TransportLayer(rxfn=self.bus.recv, txfn=self.bus.send, address=isotp_address)
        conn = PythonIsoTpConnection(isotp_layer)
        try:
            with Client(conn, config=self.client_config) as client:
                request = udsoncan.Request(service, data=data)
                client.conn.send(request)
                logger.info(f"Sent UDS request: {request}")

                received_message = self.bus.recv(timeout=timeout)
                if received_message is None:
                    raise Exception(f'No CAN frame received from ECU 0x{self.ecu_address:x} within {timeout} seconds')
                response = client.can_to_uds(received_message.data, received_message.is_extended_id)
                logger.info(f"Received CAN message: {received_message}")
                return self.handle_response(response)
        except Exception as e:
            logger.error(f"Error sending request:  {e}")
            raise

    def handle_response(self, response):
        try:
            if response.code == udsoncan.Response.Code.PositiveResponse:
                return {
                    'positive': True,
                    'data': response.data,
                    'service_id': response.service_id
                }
            else:
                return {
                    'positive': False,
                    'code': response.code,
                    'code_name': response.code_name
                }
        except Exception as e:
            logger.error(f"Error handling response: {e}")
            raise

    def session_control(self, session_type, timeout=1.0):
        try:
            return self.send_request(services.DiagnosticSessionControl, session_type.to_bytes(1, 'big'), timeout=timeout)
        except Exception as e:
            logger.error(f"Error in session_control: {e}")
            raise

    def tester_present(self, timeout=1.0):
        try:
            return self.send_request(services.TesterPresent, b'', timeout=timeout)
        except Exception as e:
            logger.error(f"Error in tester_present: {e}")
            raise

    def read_did(self, did, timeout=1.0):
        try:
            return self.send_request(services.ReadDataByIdentifier, did.to_bytes(2, 'big'), timeout=timeout)
        except Exception as e:
            logger.error(f"Error in read_did: {e}")
            raise

    def write_did(self, did, data, timeout=1.0):
        try:
            request_data = did.to_bytes(2, 'big') + data
            return self.send_request(services.WriteDataByIdentifier, request_data, timeout=timeout)
        except Exception as e:
            logger.error(f"Error in write_did: {e}")
            raise

    def routine_control(self, routine_id, control_type, data=b'', timeout=1.0):
        try:
            request_data = routine_id.to_bytes(2, 'big') + control_type.to_bytes(1, 'big') + data
            return self.send_request(services.RoutineControl, request_data, timeout=timeout)
        except Exception as e:
            logger.error(f"Error in routine_control: {e}")
            raise

    #TODO: what seed????
    def security_access(self, level, timeout=1.0):
        try:
            #request seed
            request_data = (1+2*(level-1)).to_bytes(1, 'big')
            response = self.send_request(services.SecurityAccess, request_data, timeout=timeout)
            if not response['positive']:
                return response
            seed = response['data'][1:]
            key = getKey(seed)
            
            #send key
            #TODO: verify seed size
            #TODO: check the key.to_bytes
            request_data = (2*level).to_bytes(1,'big') + key.to_bytes()
            return self.send_request(services.SecurityAccess, request_data, timeout=timeout)
        except Exception as e:
            logger.error(f"Error in security_access: {e}")
            raise

    def communication_disable(self, communication_type, timeout=1.0):
        try:
            return self.send_request(services.CommunicationControl, communication_type.to_bytes(1, 'big'), timeout=timeout)
        except Exception as e:
            logger.error(f"Error in communication_disable: {e}")
            raise

    def request_download(self, data_format_identifier, memory_address, memory_size, timeout=1.0):
        try:
            request_data = data_format_identifier.to_bytes(1, 'big') + memory_address.to_bytes(4, 'big') + memory_size.to_bytes(4, 'big')
            return self.send_request(services.RequestDownload, request_data, timeout=timeout)
        except Exception as e:
            logger.error(f"Error in request_download: {e}")
            raise

    def transfer_data(self, block_sequence_counter, data, timeout=1.0):
        try:
            request_data = block_sequence_counter.to_bytes(1, 'big') + data
            return self.send_request(services.TransferData, request_data, timeout=timeout)
        except Exception as e:
            logger.error(f"Error in transfer_data: {e}")
            raise

    def request_transfer_exit(self, timeout=1.0):
        try:
            return self.send_request(services.RequestTransferExit, b'', timeout=timeout)
        except Exception as e:
            logger.error(f"Error in request_transfer_exit: {e}")
            raise

    def ecu_reset(self, reset_type, timeout=1.0):
        try:
            return self.send_request(services.ECUReset, reset_type.to_bytes(1, 'big'), timeout=timeout)
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
    CAN_INTERFACE = 'can0'
    CAN_BITRATE = 500000 # TODO
    ECU_ADDRESS = 0x123
    HMI_ADDRESS = 0x456

    client = UDSClient(CAN_INTERFACE, CAN_BITRATE, ECU_ADDRESS, HMI_ADDRESS)

    try:
        did = 0xF190
        response = client.read_did(did)
        if response['positive']:
            print(f'Read DID 0x{did:x}: {response["data"]}')
        else:
            print(f'Error reading DID 0x{did:x}: {response["code_name"]}')

    except Exception as e:
        logger.error(f'Error: {e}')

    finally:
        client.shutdown()
