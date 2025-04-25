import isotp
import time

# ISO-TP Addressing: using normal 11-bit IDs
addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x123, rxid=0x321)

# Create ISO-TP stack for socketCAN interface (vcan0 or can0)
stack = isotp.CanStack(bus=None, address=addr, interface='socketcan', channel='vcan0')

# Message longer than 8 bytes to trigger ISO-TP segmentation
message = bytes([0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80,
                 0x90, 0x10, 0x20, 0x30, 0x40, 0x50])

# Run forever
while True:
    stack.send(message)
    print(f"Sent: {message.hex()}")

    # Process ISO-TP transmission (segmentation, timing, etc.)
    for _ in range(50):  # roughly 100ms of processing
        stack.process()
        time.sleep(0.002)

    time.sleep(1)  # Wait 1 second between sends
