from uds_client import UDSClient
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def send_update(target: int, update: bytes):
    if not isinstance(update, bytes):
        logger.error("Update data must be of type bytes")
        return
    
    # convert update bytes into a list of bytes objects for each xdelta instruction
    update_segments = list()
    i = 0
    while i < len(update):
        if update[i] == 0x11:
            ins_size = 9
            update_segments.append(update[i:i+ins_size])
            i += ins_size
        elif update[i] == 0x00:
            ins_size = 9 + int.from_bytes(update[i+5:i+9], 'big')
            update_segments.append(update[i:i+ins_size])
            i += ins_size
        

    client = UDSClient('can0', 500000, 0x123, 0x456)
    try:
        logger.info("Starting programming session")
        response = client.session_control(0x01)  # Start programming session
        if not response['positive']:
            raise Exception(f"Failed to start programming session: {response['code_name']}")
        
        #TODO: This should be a double request (session control)


        #TODO: ????????????? gbt el seed mnen anta
        logger.info("Starting security access")
        response = client.security_access(0x01)
        if not response['positive']:
            raise Exception(f"Failed to authenticate: {response['code_name']}")

        # logger.info("Disabling communication")
        # response = client.communication_disable(0x02)
        # if not response['positive']:
        #     raise Exception(f"Failed to disable communication: {response['code_name']}")

        logger.info("Requesting download")
        response = client.request_download(0x01, 0x00000000, len(update))
        if not response['positive']:
            raise Exception(f"Failed to request download: {response['code_name']}")

        logger.info("Transferring data")
        for ins in update_segments:
            response = client.transfer_data(0x01, ins)
            if not response['positive']:
                raise Exception(f"Failed to transfer data: {response['code_name']}")

        #TODO: send new crc
        logger.info("Requesting transfer exit")
        response = client.request_transfer_exit()
        if not response['positive']:
            raise Exception(f"Failed to request transfer exit: {response['code_name']}")

        logger.info("Resetting ECU")
        response = client.ecu_reset(0x01)
        if not response['positive']:
            raise Exception(f"Failed to reset ECU: {response['code_name']}")

        logger.info("Switching back to default session")
        response = client.session_control(0x01)  # Default session
        if not response['positive']:
            raise Exception(f"Failed to switch back to default session: {response['code_name']}")

        logger.info(f"Update sent to target {target}")
    except Exception as e:
        logger.error(f"An error occurred during the update process: {e}")
    finally:
        logger.info("Shutting down client")
        client.shutdown()

if __name__ == '__main__':
    
    with open('deltafile.hex', 'rb') as f:
        byte_array = f.read()
        
    target = 25
    
    send_update(target,byte_array)
    pass