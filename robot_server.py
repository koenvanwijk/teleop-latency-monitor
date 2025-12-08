#!/usr/bin/env python
import argparse
import asyncio
import json
import time
from datetime import datetime

import websockets

def log(msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")

async def handle_connection(websocket: websockets.WebSocketServerProtocol):
    log(f"Client connected: {websocket.remote_address}")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                log(f"Received non-JSON message: {message!r}")
                continue

            msg_type = data.get("type")
            if msg_type == "ping":
                t0 = data.get("t0")
                t1 = time.time()
                response = {
                    "type": "pong",
                    "t0": t0,
                    "t1": t1,
                }
                await websocket.send(json.dumps(response))
            else:
                log(f"Received unknown message type: {msg_type}")
    except websockets.ConnectionClosed:
        log("Client disconnected")
    except Exception as exc:
        log(f"Error in connection handler: {exc}")

async def main(host: str, port: int):
    log(f"Starting latency server on {host}:{port}")
    async with websockets.serve(handle_connection, host, port):
        log("Server is running. Waiting for connections...")
        await asyncio.Future()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Robot-side latency WebSocket server"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host/IP to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to listen on (default: 8765)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        log("Server stopped by user")
