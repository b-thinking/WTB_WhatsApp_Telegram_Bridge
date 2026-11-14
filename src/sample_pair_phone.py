#!/usr/bin/env python3

import os
import sys
import threading

from dotenv import load_dotenv
from neonize.client import NewClient
from neonize.events import ConnectedEv, PairStatusEv
from neonize.utils.enum import ClientName

# Configuration
SESSION_DB = "session.db"

qr_ready = threading.Event()
connected = threading.Event()
paired = threading.Event()

client = NewClient(SESSION_DB)


# QR callback: we don't need to display the QR.
# Its arrival tells us that the WhatsApp connection
# is ready for phone-number pairing.
def on_qr(client: NewClient, qr_png: bytes) -> None:
    print("WhatsApp connection initialized (QR received)")
    qr_ready.set()


client.qr(on_qr)


@client.event(PairStatusEv)
def on_paired(client: NewClient, event: PairStatusEv) -> None:
    print("WhatsApp device successfully paired")
    paired.set()


@client.event(ConnectedEv)
def on_connected(client: NewClient, event: ConnectedEv) -> None:
    print("Connected to WhatsApp")
    connected.set()


def main():

    load_dotenv()

    phone_number = os.getenv("PHONE_NUMBER", "")

    if not phone_number or len(phone_number) < 8:
        print("Please set PHONE_NUMBER in .env")
        sys.exit(1)

    # Start WhatsApp connection in a separate thread.
    print("Starting WhatsApp connection...")

    connection_thread = threading.Thread(
        target=client.connect,
        daemon=True,
    )

    connection_thread.start()

    try:
        # Wait until the initial QR handshake is ready.
        if not qr_ready.wait(timeout=30):
            raise TimeoutError("WhatsApp QR initialization timed out")

        # Request phone-number pairing code.
        print("Requesting pairing code...")

        code = client.PairPhone(
            phone_number,
            show_push_notification=True,
            client_name=ClientName.LINUX,
        )

        print(f"\nPairing code: {code}")
        print("\nEnter this code in WhatsApp:")
        print("Settings > Linked Devices > Link a Device")
        print("> Link with phone number instead")

        # Wait for successful connection.
        connected.wait()

    except KeyboardInterrupt:
        print("\nStopping WhatsApp client...")

    finally:
        client.stop()
        connection_thread.join(timeout=5)


if __name__ == "__main__":
    main()
