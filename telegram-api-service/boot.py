# telegram-api-service/boot.py
import asyncio
import sys
import os

# Add the current directory to path so app.py can be imported
sys.path.insert(0, os.path.dirname(__file__))

from app import client, startup_telethon_client, connection_future

# This function is called by Hypercorn just before it starts serving requests
async def before_serve():
    # This will run once per Hypercorn worker process (there's usually one on Render Free/Hobby)
    print("Hypercorn before_serve hook activated. Initiating Telethon client connection...")
    try:
        # Call the async startup function. It will run on Hypercorn's event loop.
        await startup_telethon_client()
        print("Telethon client successfully connected via before_serve hook.")
    except Exception as e:
        print(f"CRITICAL ERROR: Telethon client failed to connect in before_serve: {e}")
        # If the client cannot connect, the application is likely non-functional.
        # You might want to explicitly shut down the server if this is critical.
        # For now, let's just log and allow it to proceed, as subsequent API calls will check connection_future.
        pass # Keep server alive but API calls will fail fast