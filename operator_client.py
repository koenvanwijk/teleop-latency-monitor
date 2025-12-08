#!/usr/bin/env python
import argparse
import asyncio
import json
import time
from datetime import datetime

import websockets

def format_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

async def latency_loop(host: str, port: int, interval: float):
    uri = f"ws://{host}:{port}"
    print(f"Connecting to {uri} ...")

    try:
        async with websockets.connect(uri) as websocket:
            print("Connected. Starting latency measurements...")
            while True:
                t0 = time.time()
                ping_msg = {"type": "ping", "t0": t0}
                await websocket.send(json.dumps(ping_msg))

                msg = await websocket.recv()
                t2 = time.time()

                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    print(f"Received non-JSON message: {msg!r}")
                    await asyncio.sleep(interval)
                    continue

                if data.get("type") != "pong":
                    print(f"Received unexpected message type: {data.get('type')}")
                    await asyncio.sleep(interval)
                    continue

                t0_resp = data.get("t0")
                t1 = data.get("t1")

                rtt = t2 - t0
                rtt_ms = rtt * 1000.0
                one_way_ms = rtt_ms / 2.0

                uplink_ms = None
                downlink_ms = None
                if isinstance(t0_resp, (int, float)) and isinstance(t1, (int, float)):
                    uplink_ms = (t1 - t0_resp) * 1000.0
                    downlink_ms = (t2 - t1) * 1000.0

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if uplink_ms is not None and downlink_ms is not None:
                    print(
                        f"[{now_str}] RTT: {rtt_ms:6.1f} ms | "
                        f"est. one-way: {one_way_ms:6.1f} ms | "
                        f"uplink: {uplink_ms:6.1f} ms | "
                        f"downlink: {downlink_ms:6.1f} ms"
                    )
                else:
                    print(
                        f"[{now_str}] RTT: {rtt_ms:6.1f} ms | "
                        f"est. one-way: {one_way_ms:6.1f} ms"
                    )

                await asyncio.sleep(interval)
    except websockets.ConnectionClosed as exc:
        print(f"Connection closed: {exc}")
    except OSError as exc:
        print(f"Failed to connect to {uri}: {exc}")
    except KeyboardInterrupt:
        print("Stopped by user")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Operator-side latency monitor client"
    )
    parser.add_argument(
        "--host",
        type=str,
        required=True,
        help="Robot server host/IP (e.g. 192.168.1.42)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Robot server port (default: 8765)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Ping interval in seconds (default: 1.0)",
    )

    args = parser.parse_args()

    asyncio.run(latency_loop(args.host, args.port, args.interval))
