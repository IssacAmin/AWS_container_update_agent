from numpy import sign
import udsoncan.ResponseCode
from uds_client import UDSClient, publish_status
import logging
#Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



def send_update(MQTTClient, target, update: bytes, signature: bytes = None):
    if not isinstance(update, bytes):
        logger.error("Update data must be of type bytes")
        return
    
    # convert update bytes into a list of bytes objects for each xdelta instruction
    update_segments = list()
    i = 0
    while i < len(update):
        if update[i] == 0x11:
            ins_size = 9
        elif update[i] == 0x00:
            ins_size = 9 + int.from_bytes(update[i+5:i+9], 'big')
        elif update[i] == 0x01:
            ins_size = 9
        elif update[i] == 0x10:
            ins_size = 7
        else:
            raise Exception(f"Unknown instruction type: {update[i]}")
        if update[i] == 0x10 and signature is not None:
            update_segments.append(update[i:i+ins_size] + signature)
        else:
            update_segments.append(update[i:i+ins_size])
        
        i += ins_size
        

    client = UDSClient(MQTTClient, 'can0', 500000, 0x456, 0x123)
    
    try:
        logger.info("Starting security access")
        response = client.security_access(1)
        if not response.valid or not response.positive:
            raise Exception(f"failed to get security access")
        
        logger.info("Starting programming session")
        response = client.session_control(0x02, timeout=300)  # Start programming session
        if not response.valid:
            raise Exception(f"Failed to start programming session: {response.code_name}")
        if not response.positive:
            if response.code_name == udsoncan.ResponseCode.ResponseCode.RequestCorrectlyReceived_ResponsePending:
                response = client.ecu_reset(0x01)
                if not response.valid or not response.positive:
                    raise Exception(f"Failed to reset ECU: {response.code_name}")
                else:
                    response = client.session_control(0x01, timeout=300)
            else: 
                raise Exception(f"Failed to start programming session: {response.code_name}")
                        
        
        logger.info("Starting routine control for flashbank reset")
        response = client.routine_control(0xff00, 1000) #flashbank erase takes a long time
        if not response.valid or not response.positive:
            raise Exception(f"failed to run erase flashbank routine")
        
        # logger.info("Disabling communication")
        # response = client.communication_disable(0x02)
        # if not response['positive']:
        #     raise Exception(f"Failed to disable communication: {response['code_name']}")

        logger.info("Requesting download")
        response = client.request_download(0x00000000, len(update), 32, 32, 0, 0)
        #TODO: extract maxNumberOfBlockLength from response
        
        if not response.valid or not response.positive:
            raise Exception(f"Failed to request download: {response.code_name}")

        logger.info("Transferring data")
        block_sequence_counter = 0x01
        for ins in update_segments:
            logger.info(f"Transferring instruction: {ins.hex()}")
            response = client.transfer_data(block_sequence_counter, ins)
            block_sequence_counter = (block_sequence_counter + 1) % 0x100
            if not response.valid or not response.positive:
                raise Exception(f"Failed to transfer data: {response.code_name}")
        
        logger.info("Requesting transfer exit")
        response = client.request_transfer_exit()
        if not response.valid or not response.positive:
            raise Exception(f"Failed to request transfer exit: {response.code_name}")

        logger.info("validating app")
        response = client.routine_control(0xff01, 1000, data = bytes([1]))
        if not response.valid or not response.positive:
            raise Exception(f"failed to validate app")
        
        logger.info("switching app")
        response = client.routine_control(0xff02, 1000, data = bytes([1])) 
        if not response.valid or not response.positive:
            raise Exception(f"failed to switch to new app")
        
        logger.info("Resetting ECU")
        response = client.ecu_reset(0x01)
        if not response.valid or not response.positive:
            raise Exception(f"Failed to reset ECU: {response.code_name}")
        
    except Exception as e:
        logger.error(f"An error occurred during the update process: {e}")
        raise Exception(f"{e}")
    finally:
        logger.info("Shutting down client")
        client.shutdown()

if __name__ == '__main__':
    
    with open('deltafile.hex', 'rb') as f:
        byte_array = f.read()
        
    target = 25
    
    # send_update(target,byte_array)
    pass
