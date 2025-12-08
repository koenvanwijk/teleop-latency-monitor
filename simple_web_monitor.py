#!/usr/bin/env python
import argparse
import asyncio
import json
import time
import socket
import struct
from aiohttp import web, WSMsgType
import websockets

# Store active browser connections
browser_connections = set()

def log(msg: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")

async def handle_robot_connection(websocket):
    """Handle WebSocket connection from browser for robot communication"""
    log(f"Robot client connected: {websocket.remote_address}")
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
                ping_id = data.get("id")  # Get ping ID if present
                t1 = time.time()
                response = {
                    "type": "pong",
                    "t0": t0,
                    "t1": t1,
                }
                # Include ping ID in response if it was provided
                if ping_id:
                    response["id"] = ping_id
                await websocket.send(json.dumps(response))
            else:
                log(f"Received unknown message type: {msg_type}")
    except websockets.ConnectionClosed:
        log("Robot client disconnected")
    except Exception as exc:
        log(f"Error in robot connection handler: {exc}")

async def ping_server(hostname):
    """Ping a server from the backend and return latency + IP"""
    import subprocess
    import socket
    try:
        # Resolve hostname to IP
        ip_address = socket.gethostbyname(hostname)
        
        # Use ping command (works on Linux)
        result = subprocess.run(['ping', '-c', '1', '-W', '3', hostname], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Parse ping output to get latency
            output = result.stdout
            # Look for time=XX.X ms
            import re
            match = re.search(r'time=(\d+\.?\d*)\s*ms', output)
            if match:
                return float(match.group(1)), ip_address
    except Exception as e:
        print(f"Ping error for {hostname}: {e}")
    return None, None

async def stun_test_server(stun_host, stun_port=3478):
    """Perform STUN binding request from server and measure latency"""
    try:
        start_time = time.time()
        
        # Create UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5.0)
        
        # STUN Binding Request message
        # Message Type: Binding Request (0x0001)
        # Message Length: 0 (no attributes)
        # Magic Cookie: 0x2112A442
        # Transaction ID: 12 random bytes
        import os
        transaction_id = os.urandom(12)
        
        # Pack STUN header
        message_type = 0x0001  # Binding Request
        message_length = 0     # No attributes
        magic_cookie = 0x2112A442
        
        stun_header = struct.pack('!HHI', message_type, message_length, magic_cookie) + transaction_id
        
        # Send STUN request
        sock.sendto(stun_header, (stun_host, stun_port))
        
        # Receive response
        data, addr = sock.recvfrom(1024)
        end_time = time.time()
        
        sock.close()
        
        # Verify this is a STUN Binding Response
        if len(data) >= 20:
            response_type, response_length = struct.unpack('!HH', data[:4])
            if response_type == 0x0101:  # Binding Response
                latency_ms = (end_time - start_time) * 1000
                return latency_ms
        
        return None
        
    except Exception as e:
        print(f"STUN error for {stun_host}:{stun_port}: {e}")
        return None

async def websocket_proxy_handler(request):
    """WebSocket proxy between browser and robot server + server-side ping handler"""
    ws_browser = web.WebSocketResponse()
    await ws_browser.prepare(request)
    
    browser_connections.add(ws_browser)
    
    try:
        # Connect to robot server
        async with websockets.connect('ws://localhost:8765') as ws_robot:
            # Handle messages in both directions
            async def browser_to_robot():
                async for msg in ws_browser:
                    if msg.type == WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            # Handle server-side ping requests
                            if data.get('type') == 'server_ping':
                                target = data.get('target')
                                ping_id = data.get('id')
                                
                                # Perform server-side ping
                                if target in ['amsterdam', 'sofia', 'eindhoven']:
                                    hostnames = {
                                        'amsterdam': 'google.nl',  # Google Netherlands (Amsterdam)
                                        'sofia': 'google.bg',      # Google Bulgaria (Sofia)
                                        'eindhoven': 'xs4all.nl'   # XS4ALL (Dutch ISP)
                                    }
                                    latency, ip_address = await ping_server(hostnames[target])
                                    
                                    response = {
                                        'type': 'server_ping_result',
                                        'target': target,
                                        'id': ping_id,
                                        'latency': latency,
                                        'ip': ip_address,
                                        'hostname': hostnames[target]
                                    }
                                    await ws_browser.send_str(json.dumps(response))
                            
                            # Handle server-side STUN requests
                            elif data.get('type') == 'server_stun':
                                stun_target = data.get('target')
                                stun_id = data.get('id')
                                
                                # Perform server-side STUN test
                                if stun_target in ['google', 'cloudflare']:
                                    stun_servers = {
                                        'google': 'stun.l.google.com',
                                        'cloudflare': 'stun.cloudflare.com'
                                    }
                                    stun_host = stun_servers[stun_target]
                                    stun_port = 19302 if stun_target == 'google' else 3478
                                    
                                    latency = await stun_test_server(stun_host, stun_port)
                                    
                                    response = {
                                        'type': 'server_stun_result',
                                        'target': stun_target,
                                        'id': stun_id,
                                        'latency': latency,
                                        'stun_server': f"{stun_host}:{stun_port}"
                                    }
                                    await ws_browser.send_str(json.dumps(response))
                            else:
                                # Forward to robot server
                                await ws_robot.send(msg.data)
                        except json.JSONDecodeError:
                            await ws_robot.send(msg.data)
                    elif msg.type == WSMsgType.ERROR:
                        print(f'Browser WebSocket error: {ws_browser.exception()}')
                        break
            
            async def robot_to_browser():
                async for msg in ws_robot:
                    try:
                        await ws_browser.send_str(msg)
                    except:
                        break
            
            # Run both directions concurrently
            await asyncio.gather(browser_to_robot(), robot_to_browser())
            
    except Exception as e:
        print(f"Robot connection error: {e}")
    finally:
        browser_connections.discard(ws_browser)
    
    return ws_browser

async def index_handler(request):
    """Serve the main webpage"""
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Teleop Latency Monitor</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            text-align: center;
        }
        .stat-value {
            font-size: 2em;
            font-weight: bold;
            margin: 10px 0;
        }
        .stat-label {
            color: #666;
            font-size: 0.9em;
        }
        .chart-container {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .status {
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 20px;
            text-align: center;
        }
        .status.connected {
            background-color: #d4edda;
            color: #155724;
        }
        .status.disconnected {
            background-color: #f8d7da;
            color: #721c24;
        }
        .rtt { color: #007bff; }
        .uplink { color: #28a745; }
        .downlink { color: #dc3545; }
        .clock-skew { color: #ffc107; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Teleop Latency Monitor</h1>
            <p>Integrated web interface with built-in robot server</p>
            <p style="font-size: 0.9em; color: #666; margin-bottom: 10px;">
                <strong>Note:</strong> Browser measurements use HTTP requests (DNS+TCP+HTTP overhead), 
                while Server measurements use direct ICMP ping packets. This explains the difference in latency.
            </p>
            <div style="margin-bottom: 10px;">
                <div style="display: inline-block; margin-right: 20px;">
                    <strong>Your IP:</strong> <span id="client-ip">--</span>
                </div>
                <div style="display: inline-block; margin-right: 20px;">
                    <strong>Robot Server:</strong> <span id="robot-server-ip">--</span>
                </div>
            </div>
            <div id="status" class="status disconnected">Connecting...</div>
        </div>
        
        <!-- Network Topology Diagram -->
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 15px; margin-bottom: 30px; position: relative; min-height: 500px;">
            <h2 style="color: white; text-align: center; margin-bottom: 30px;">🌐 Network Topology & Latency</h2>
            
            <!-- Geographic Servers & STUN (Top) -->
            <div style="display: flex; justify-content: space-around; margin-bottom: 50px;">
                <!-- Eindhoven -->
                <div style="text-align: center; position: relative;">
                    <div style="background: #6f42c1; color: white; padding: 15px 20px; border-radius: 50%; display: inline-block; font-weight: bold; min-width: 80px;">
                        🇳🇱<br>Eindhoven
                    </div>
                    <div style="margin-top: 10px; color: white;">
                        <div>Browser: <span id="topo-eindhoven-browser" style="font-weight: bold;">--</span>ms</div>
                        <div>Server: <span id="topo-eindhoven-server" style="font-weight: bold;">--</span>ms</div>
                        <div style="font-size: 0.8em; opacity: 0.8;">tue.nl</div>
                    </div>
                </div>
                
                <!-- Amsterdam -->
                <div style="text-align: center; position: relative;">
                    <div style="background: #28a745; color: white; padding: 15px 20px; border-radius: 50%; display: inline-block; font-weight: bold; min-width: 80px;">
                        🇳🇱<br>Amsterdam
                    </div>
                    <div style="margin-top: 10px; color: white;">
                        <div>Browser: <span id="topo-amsterdam-browser" style="font-weight: bold;">--</span>ms</div>
                        <div>Server: <span id="topo-amsterdam-server" style="font-weight: bold;">--</span>ms</div>
                        <div style="font-size: 0.8em; opacity: 0.8;">nluug.nl</div>
                    </div>
                </div>
                
                <!-- Sofia -->
                <div style="text-align: center; position: relative;">
                    <div style="background: #dc3545; color: white; padding: 15px 20px; border-radius: 50%; display: inline-block; font-weight: bold; min-width: 80px;">
                        🇧🇬<br>Sofia
                    </div>
                    <div style="margin-top: 10px; color: white;">
                        <div>Browser: <span id="topo-sofia-browser" style="font-weight: bold;">--</span>ms</div>
                        <div>Server: <span id="topo-sofia-server" style="font-weight: bold;">--</span>ms</div>
                        <div style="font-size: 0.8em; opacity: 0.8;">uni-sofia.bg</div>
                    </div>
                </div>
                
                <!-- Google STUN -->
                <div style="text-align: center; position: relative;">
                    <div style="background: #17a2b8; color: white; padding: 15px 20px; border-radius: 50%; display: inline-block; font-weight: bold; min-width: 80px;">
                        🌐<br>Google STUN
                    </div>
                    <div style="margin-top: 10px; color: white;">
                        <div>Browser: <span id="topo-stun-google-browser" style="font-weight: bold;">--</span>ms</div>
                        <div>Server: <span id="topo-stun-google-server" style="font-weight: bold;">--</span>ms</div>
                        <div style="font-size: 0.8em; opacity: 0.8;">stun.l.google.com</div>
                    </div>
                </div>
                
                <!-- Cloudflare STUN -->
                <div style="text-align: center; position: relative;">
                    <div style="background: #fd7e14; color: white; padding: 15px 20px; border-radius: 50%; display: inline-block; font-weight: bold; min-width: 80px;">
                        ☁️<br>Cloudflare STUN
                    </div>
                    <div style="margin-top: 10px; color: white;">
                        <div>Browser: <span id="topo-stun-cloudflare-browser" style="font-weight: bold;">--</span>ms</div>
                        <div>Server: <span id="topo-stun-cloudflare-server" style="font-weight: bold;">--</span>ms</div>
                        <div style="font-size: 0.8em; opacity: 0.8;">stun.cloudflare.com</div>
                    </div>
                </div>
            </div>
            
            <!-- Main Connection (Bottom) -->
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 50px;">
                <!-- Your Computer -->
                <div style="text-align: center; position: relative;">
                    <div style="background: #007bff; color: white; padding: 20px 30px; border-radius: 15px; font-weight: bold; box-shadow: 0 4px 15px rgba(0,0,0,0.2);">
                        💻<br>Your Computer<br>
                        <small style="opacity: 0.8;"><span id="topo-client-ip">--</span></small>
                    </div>
                </div>
                
                <!-- Connection Line -->
                <div style="flex: 1; text-align: center; position: relative; margin: 0 30px;">
                    <div style="height: 4px; background: linear-gradient(90deg, #007bff, #e83e8c); border-radius: 2px; position: relative;">
                        <div style="position: absolute; top: -30px; left: 50%; transform: translateX(-50%); background: rgba(255,255,255,0.9); padding: 8px 15px; border-radius: 20px; font-weight: bold; color: #333;">
                            <span id="topo-robot-rtt" style="color: #007bff;">--</span>ms
                        </div>
                        <div style="position: absolute; bottom: -40px; left: 50%; transform: translateX(-50%); color: white; font-size: 0.9em; text-align: center;">
                            WebSocket<br>Connection
                        </div>
                    </div>
                </div>
                
                <!-- Robot Server -->
                <div style="text-align: center; position: relative;">
                    <div style="background: #e83e8c; color: white; padding: 20px 30px; border-radius: 15px; font-weight: bold; box-shadow: 0 4px 15px rgba(0,0,0,0.2);">
                        🤖<br>Robot Server<br>
                        <small style="opacity: 0.8;"><span id="topo-robot-ip">localhost:8765</span></small>
                    </div>
                </div>
            </div>
            
            <!-- WebRTC Performance Tests (Bottom) -->
            <div style="margin-top: 40px; text-align: center;">
                <h3 style="color: white; margin-bottom: 20px;">🎮 WebRTC Teleoperation Performance</h3>
                <div style="display: flex; justify-content: center; gap: 30px;">
                    <!-- Small Packets -->
                    <div style="background: rgba(255,255,255,0.1); padding: 15px 20px; border-radius: 10px; border: 2px solid rgba(255,255,255,0.2);">
                        <div style="color: white; font-weight: bold; margin-bottom: 5px;">Small Packets</div>
                        <div style="font-size: 1.5em; font-weight: bold; color: #00d4aa;"><span id="topo-webrtc-small">--</span>ms</div>
                        <div style="font-size: 0.8em; color: rgba(255,255,255,0.8);">ping/pong</div>
                    </div>
                    
                    <!-- 8KB Stream -->
                    <div style="background: rgba(255,255,255,0.1); padding: 15px 20px; border-radius: 10px; border: 2px solid rgba(255,255,255,0.2);">
                        <div style="color: white; font-weight: bold; margin-bottom: 5px;">8KB Stream</div>
                        <div style="font-size: 1.5em; font-weight: bold; color: #e83e8c;"><span id="topo-webrtc-8kb">--</span>ms</div>
                        <div style="font-size: 0.8em; color: rgba(255,255,255,0.8);">low quality</div>
                    </div>
                    
                    <!-- 32KB Stream -->
                    <div style="background: rgba(255,255,255,0.1); padding: 15px 20px; border-radius: 10px; border: 2px solid rgba(255,255,255,0.2);">
                        <div style="color: white; font-weight: bold; margin-bottom: 5px;">32KB Stream</div>
                        <div style="font-size: 1.5em; font-weight: bold; color: #dc3545;"><span id="topo-webrtc-32kb">--</span>ms</div>
                        <div style="font-size: 0.8em; color: rgba(255,255,255,0.8);">medium quality</div>
                    </div>
                    
                    <!-- 64KB Stream -->
                    <div style="background: rgba(255,255,255,0.1); padding: 15px 20px; border-radius: 10px; border: 2px solid rgba(255,255,255,0.2);">
                        <div style="color: white; font-weight: bold; margin-bottom: 5px;">64KB Stream</div>
                        <div style="font-size: 1.5em; font-weight: bold; color: #6f42c1;"><span id="topo-webrtc-64kb">--</span>ms</div>
                        <div style="font-size: 0.8em; color: rgba(255,255,255,0.8);">high quality</div>
                    </div>
                </div>
                <div style="margin-top: 15px; color: rgba(255,255,255,0.8); font-size: 0.9em;">
                    30fps video stream latency • Frame age measurements
                </div>
            </div>
            
            <!-- Connection Status -->
            <div style="position: absolute; top: 20px; right: 20px;">
                <div id="topo-status" class="status disconnected" style="background: rgba(255,255,255,0.9); color: #721c24; padding: 8px 15px; border-radius: 20px; font-size: 0.9em;">
                    Connecting...
                </div>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Robot Server</div>
                <div id="robot-rtt" class="stat-value rtt">--</div>
                <div class="stat-label">ms RTT (WebSocket)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;" id="robot-ip">integrated:8765</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Eindhoven (Browser)</div>
                <div id="eindhoven-browser" class="stat-value" style="color: #6f42c1;">--</div>
                <div class="stat-label">ms RTT (HTTP)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;" id="eindhoven-browser-ip">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Eindhoven (Server)</div>
                <div id="eindhoven-server" class="stat-value" style="color: #6f42c1;">--</div>
                <div class="stat-label">ms RTT (PING)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;" id="eindhoven-server-ip">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Amsterdam (Browser)</div>
                <div id="amsterdam-browser" class="stat-value uplink">--</div>
                <div class="stat-label">ms RTT (HTTP)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;" id="amsterdam-browser-ip">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Amsterdam (Server)</div>
                <div id="amsterdam-server" class="stat-value uplink">--</div>
                <div class="stat-label">ms RTT (PING)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;" id="amsterdam-server-ip">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Sofia (Browser)</div>
                <div id="sofia-browser" class="stat-value downlink">--</div>
                <div class="stat-label">ms RTT (HTTP)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;" id="sofia-browser-ip">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Sofia (Server)</div>
                <div id="sofia-server" class="stat-value downlink">--</div>
                <div class="stat-label">ms RTT (PING)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;" id="sofia-server-ip">--</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Robot Up/Down</div>
                <div id="robot-updown" class="stat-value">--</div>
                <div class="stat-label">ms (WebSocket)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;">uplink/downlink</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">STUN Google (Browser)</div>
                <div id="stun-google-browser" class="stat-value" style="color: #28a745;">--</div>
                <div class="stat-label">ms RTT (WebRTC)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;">stun.l.google.com:19302</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">STUN Google (Server)</div>
                <div id="stun-google-server" class="stat-value" style="color: #28a745;">--</div>
                <div class="stat-label">ms RTT (STUN)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;">stun.l.google.com:19302</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">STUN Cloudflare (Browser)</div>
                <div id="stun-cloudflare-browser" class="stat-value" style="color: #6f42c1;">--</div>
                <div class="stat-label">ms RTT (WebRTC)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;">stun.cloudflare.com:3478</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">STUN Cloudflare (Server)</div>
                <div id="stun-cloudflare-server" class="stat-value" style="color: #6f42c1;">--</div>
                <div class="stat-label">ms RTT (STUN)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;">stun.cloudflare.com:3478</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">WebRTC Robot Echo</div>
                <div id="webrtc-robot-echo" class="stat-value" style="color: #fd7e14;">--</div>
                <div class="stat-label">ms RTT (WebRTC)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;">Small packets</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">30fps 8KB Stream</div>
                <div id="webrtc-8kb" class="stat-value" style="color: #e83e8c;">--</div>
                <div class="stat-label">ms Avg Age</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;">Low quality video</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">30fps 32KB Stream</div>
                <div id="webrtc-32kb" class="stat-value" style="color: #dc3545;">--</div>
                <div class="stat-label">ms Avg Age</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;">Medium quality video</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">30fps 64KB Stream</div>
                <div id="webrtc-64kb" class="stat-value" style="color: #6f42c1;">--</div>
                <div class="stat-label">ms Avg Age</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;">High quality video</div>
            </div>
        </div>
        
        <div class="chart-container">
            <canvas id="latencyChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h3>Recent Robot Server Measurements</h3>
            <div id="measurements-table">
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="background-color: #f8f9fa;">
                            <th style="padding: 10px; border: 1px solid #dee2e6;">Time</th>
                            <th style="padding: 10px; border: 1px solid #dee2e6;">Robot RTT (ms)</th>
                            <th style="padding: 10px; border: 1px solid #dee2e6;">Uplink (ms)</th>
                            <th style="padding: 10px; border: 1px solid #dee2e6;">Downlink (ms)</th>
                        </tr>
                    </thead>
                    <tbody id="measurements-tbody">
                    </tbody>
                </table>
            </div>
        </div>

        <!-- WebRTC Setup Information -->
        <div class="chart-container">
            <h3>WebRTC Infrastructure Requirements</h3>
            <div style="background-color: #f8f9fa; padding: 20px; border-radius: 8px; font-family: monospace; font-size: 14px;">
                <div style="margin-bottom: 15px;">
                    <strong style="color: #28a745;">✓ Currently Testing:</strong>
                    <div style="margin-left: 20px; margin-top: 5px;">
                        • <strong>STUN Servers:</strong> Google (stun.l.google.com:19302), Cloudflare (stun.cloudflare.com:3478)<br>
                        • <strong>Local WebRTC:</strong> P2P simulation for latency testing<br>
                        • <strong>ICE Gathering:</strong> Measuring NAT traversal performance
                    </div>
                </div>
                
                <div style="margin-bottom: 15px;">
                    <strong style="color: #ffc107;">⚠ For Production WebRTC (Robot ↔ Browser):</strong>
                    <div style="margin-left: 20px; margin-top: 5px;">
                        • <strong>Signaling Server:</strong> WebSocket/Socket.IO for SDP offer/answer exchange<br>
                        • <strong>STUN Server:</strong> For NAT discovery and public IP detection<br>
                        • <strong>TURN Server:</strong> Relay server for symmetric NAT/firewall traversal<br>
                        • <strong>ICE Framework:</strong> Connectivity establishment (STUN + TURN candidates)
                    </div>
                </div>
                
                <div style="margin-bottom: 15px;">
                    <strong style="color: #dc3545;">✗ Challenges for Robot Teleoperation:</strong>
                    <div style="margin-left: 20px; margin-top: 5px;">
                        • <strong>Symmetric NAT:</strong> Many corporate/mobile networks block direct P2P<br>
                        • <strong>TURN Costs:</strong> Relay servers require bandwidth allocation<br>
                        • <strong>Firewall Policy:</strong> Robot networks often restrict outbound connections<br>
                        • <strong>Latency Variability:</strong> ICE negotiation + TURN relay adds overhead
                    </div>
                </div>
                
                <div>
                    <strong style="color: #6f42c1;">💡 Current Test Results Indicate:</strong>
                    <div style="margin-left: 20px; margin-top: 5px;">
                        • <strong>STUN Latency:</strong> <span id="webrtc-info-stun">--</span> ms (NAT traversal time)<br>
                        • <strong>WebRTC Small Packets:</strong> <span id="webrtc-info-local">--</span> ms (text ping/pong)<br>
                        • <strong>WebRTC Teleoperation:</strong> <span id="webrtc-info-video">--</span> ms (realistic robot video)<br>
                        • <strong>WebSocket (current):</strong> <span id="webrtc-info-websocket">--</span> ms (existing robot connection)
                    </div>
                </div>

                <div style="margin-top: 15px;">
                    <strong style="color: #17a2b8;">🤔 Why WebSocket might be slower than WebRTC:</strong>
                    <div style="margin-left: 20px; margin-top: 5px;">
                        • <strong>Network Path:</strong> WebSocket → Web Server → Robot Server (2 hops)<br>
                        • <strong>Protocol Overhead:</strong> HTTP/WebSocket headers + JSON parsing<br>
                        • <strong>Server Processing:</strong> Python asyncio + message routing delays<br>
                        • <strong>TCP vs UDP:</strong> WebSocket uses TCP (reliability overhead)<br>
                        • <strong>Proxy Layer:</strong> Browser → aiohttp → websockets library → Robot
                    </div>
                </div>

                <div style="margin-top: 10px;">
                    <strong style="color: #28a745;">✓ WebRTC Small Packets are fastest because:</strong>
                    <div style="margin-left: 20px; margin-top: 5px;">
                        • <strong>Direct P2P:</strong> No intermediate servers (local loopback)<br>
                        • <strong>Optimized Stack:</strong> Browser's native WebRTC implementation<br>
                        • <strong>UDP DataChannel:</strong> Lower protocol overhead<br>
                        • <strong>Minimal Payload:</strong> Just "ping"/"pong" strings
                    </div>
                </div>

                <div style="margin-top: 10px;">
                    <strong style="color: #e83e8c;">🎥 30fps Video Stream Testing (realistic teleoperation):</strong>
                    <div style="margin-left: 20px; margin-top: 5px;">
                        • <strong>30fps Streams:</strong> 60 frames over 2 seconds per test<br>
                        • <strong>Frame Age Analysis:</strong> Average, min, max, and jitter measurements<br>
                        • <strong>Multiple Qualities:</strong> 8KB (low), 32KB (medium), 64KB (high)<br>
                        • <strong>Sustained Load:</strong> Tests network under continuous video traffic<br>
                        • <strong>Jitter Measurement:</strong> Frame age variation critical for smooth control<br>
                        • <strong>Real Teleoperation:</strong> Simulates actual robot video streaming
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Chart setup
        const ctx = document.getElementById('latencyChart').getContext('2d');
        const chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        label: 'Robot Server',
                        data: [],
                        borderColor: '#007bff',
                        backgroundColor: 'rgba(0, 123, 255, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Eindhoven (Browser)',
                        data: [],
                        borderColor: '#6f42c1',
                        backgroundColor: 'rgba(111, 66, 193, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Eindhoven (Server)',
                        data: [],
                        borderColor: '#e83e8c',
                        backgroundColor: 'rgba(232, 62, 140, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Amsterdam (Browser)',
                        data: [],
                        borderColor: '#28a745',
                        backgroundColor: 'rgba(40, 167, 69, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Amsterdam (Server)',
                        data: [],
                        borderColor: '#20c997',
                        backgroundColor: 'rgba(32, 201, 151, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Sofia (Browser)',
                        data: [],
                        borderColor: '#dc3545',
                        backgroundColor: 'rgba(220, 53, 69, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Sofia (Server)',
                        data: [],
                        borderColor: '#fd7e14',
                        backgroundColor: 'rgba(253, 126, 20, 0.1)',
                        tension: 0.1
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    title: {
                        display: true,
                        text: 'Latency Comparison: Robot vs Public Servers'
                    }
                },
                scales: {
                    x: {
                        display: true,
                        title: {
                            display: true,
                            text: 'Time'
                        }
                    },
                    y: {
                        display: true,
                        title: {
                            display: true,
                            text: 'Latency (ms)'
                        }
                    }
                }
            }
        });

        // Multiple ping targets
        let robotWs;
        const measurements = {
            robot: [],
            amsterdamBrowser: [],
            amsterdamServer: [],
            sofiaBrowser: [],
            sofiaServer: [],
            eindhovenBrowser: [],
            eindhovenServer: []
        };
        let measurementInterval;
        
        // Public ping servers - using geographic targets
        const pingTargets = {
            amsterdam: 'google.nl',        // Google Netherlands (likely Amsterdam)
            sofia: 'google.bg',            // Google Bulgaria (likely Sofia)  
            eindhoven: 'xs4all.nl'         // XS4ALL (major Dutch ISP, based in Netherlands)
        };
        
        // Store IP addresses
        const ipAddresses = {
            robot: 'localhost:8765',
            amsterdamBrowser: '--',
            amsterdamServer: '--',
            sofiaBrowser: '--',
            sofiaServer: '--'
        };
        
        // Resolve hostname to IP using DNS-over-HTTPS
        async function resolveHostname(hostname) {
            try {
                const response = await fetch(`https://dns.google/resolve?name=${hostname}&type=A`);
                const data = await response.json();
                if (data.Answer && data.Answer.length > 0) {
                    return data.Answer[0].data;
                }
            } catch (error) {
                console.error(`DNS resolution error for ${hostname}:`, error);
            }
            return null;
        }

        function connectToRobot() {
            // Connect through our web server proxy (accessible from local browser)
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const robotUrl = `${protocol}//${window.location.host}/ws-robot`;
            robotWs = new WebSocket(robotUrl);
            
            // Resolve IPs for browser targets
            Object.keys(pingTargets).forEach(async (target) => {
                const ip = await resolveHostname(pingTargets[target]);
                if (ip) {
                    const displayText = `${ip} (${pingTargets[target]})`;
                    ipAddresses[`${target}Browser`] = displayText;
                    document.getElementById(`${target}-browser-ip`).textContent = displayText;
                }
            });
            
            robotWs.onopen = function() {
                document.getElementById('status').textContent = 'Connected to Robot';
                document.getElementById('status').className = 'status connected';
                updateTopologyDisplay();
                
                // Get server information
                fetch('/api/server-info')
                    .then(response => response.json())
                    .then(data => {
                        const clientIp = data.client_ip || 'Unknown';
                        const serverInfo = data.server_external_ip ? 
                            `${data.server_external_ip} (${data.server_local_ip})` : 
                            data.server_local_ip;
                        
                        document.getElementById('client-ip').textContent = clientIp;
                        document.getElementById('robot-server-ip').textContent = serverInfo;
                    })
                    .catch(error => console.error('Error getting server info:', error));
                
                startMeasurements();
            };
            
            robotWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'pong') {
                        handlePongResponse(data);
                    } else if (data.type === 'server_ping_result') {
                        handleServerPingResponse(data);
                    } else if (data.type === 'server_stun_result') {
                        handleServerStunResponse(data);
                    }
                } catch (e) {
                    console.error('Error parsing robot response:', e);
                }
            };
            
            robotWs.onclose = function() {
                document.getElementById('status').textContent = 'Disconnected from Robot - Reconnecting...';
                document.getElementById('status').className = 'status disconnected';
                stopMeasurements();
                setTimeout(connectToRobot, 3000);
            };
            
            robotWs.onerror = function(error) {
                console.error('Robot WebSocket error:', error);
            };
        }
        
        function startMeasurements() {
            if (measurementInterval) clearInterval(measurementInterval);
            measurementInterval = setInterval(() => {
                sendPing(); // Robot server ping
                
                // Stagger the public server pings to reduce load
                setTimeout(() => {
                    pingPublicServerFromBrowser('eindhoven');
                    pingPublicServerFromServer('eindhoven');
                }, 500);
                
                setTimeout(() => {
                    pingPublicServerFromBrowser('amsterdam');
                    pingPublicServerFromServer('amsterdam');
                }, 1000);
                
                setTimeout(() => {
                    pingPublicServerFromBrowser('sofia');
                    pingPublicServerFromServer('sofia');
                }, 1500);
                
                // STUN tests (staggered to avoid overload)
                setTimeout(async () => {
                    // Browser STUN to Google
                    const googleBrowserLatency = await testSTUNFromBrowser('stun:stun.l.google.com:19302');
                    if (googleBrowserLatency > 0) {
                        document.getElementById('stun-google-browser').textContent = Math.round(googleBrowserLatency);
                    } else {
                        document.getElementById('stun-google-browser').textContent = 'ERR';
                    }
                    updateTopologyDisplay();
                    updateWebRTCInfo();
                    
                    // Server STUN to Google
                    stunTestFromServer('google');
                }, 2000);
                
                setTimeout(async () => {
                    // Browser STUN to Cloudflare
                    const cloudflareBrowserLatency = await testSTUNFromBrowser('stun:stun.cloudflare.com:3478');
                    if (cloudflareBrowserLatency > 0) {
                        document.getElementById('stun-cloudflare-browser').textContent = Math.round(cloudflareBrowserLatency);
                    } else {
                        document.getElementById('stun-cloudflare-browser').textContent = 'ERR';
                    }
                    updateTopologyDisplay();
                    updateWebRTCInfo();
                    
                    // Server STUN to Cloudflare
                    stunTestFromServer('cloudflare');
                }, 2500);
                
                // WebRTC Robot Echo test
                setTimeout(async () => {
                    const robotEchoLatency = await testWebRTCRobotEcho();
                    if (robotEchoLatency > 0) {
                        document.getElementById('webrtc-robot-echo').textContent = Math.round(robotEchoLatency);
                    } else {
                        document.getElementById('webrtc-robot-echo').textContent = 'ERR';
                    }
                    updateTopologyDisplay();
                    updateWebRTCInfo();
                }, 3000);
                
                // WebRTC 30fps Stream Tests - spaced for 3 second streams each
                setTimeout(async () => {
                    document.getElementById('webrtc-8kb').textContent = 'Testing...';
                    const eightKbLatency = await testWebRTCVideoSimulation(); // 8KB
                    if (eightKbLatency > 0) {
                        document.getElementById('webrtc-8kb').textContent = Math.round(eightKbLatency);
                    } else {
                        document.getElementById('webrtc-8kb').textContent = 'ERR';
                    }
                    updateTopologyDisplay();
                }, 3500);
                
                setTimeout(async () => {
                    document.getElementById('webrtc-32kb').textContent = 'Testing...';
                    const thirtyTwoKbLatency = await testWebRTCMediumVideo(); // 32KB
                    if (thirtyTwoKbLatency > 0) {
                        document.getElementById('webrtc-32kb').textContent = Math.round(thirtyTwoKbLatency);
                    } else {
                        document.getElementById('webrtc-32kb').textContent = 'ERR';
                    }
                    updateTopologyDisplay();
                }, 7000); // 3.5 seconds after first test
                
                setTimeout(async () => {
                    document.getElementById('webrtc-64kb').textContent = 'Testing...';
                    const sixtyFourKbLatency = await testWebRTCHighVideo(); // 64KB
                    if (sixtyFourKbLatency > 0) {
                        document.getElementById('webrtc-64kb').textContent = Math.round(sixtyFourKbLatency);
                    } else {
                        document.getElementById('webrtc-64kb').textContent = 'ERR';
                    }
                    updateTopologyDisplay();
                }, 10500); // 3.5 seconds after second test
            }, 15000); // Every 15 seconds (30fps streams take ~3 seconds each)
        }
        
        function stopMeasurements() {
            if (measurementInterval) {
                clearInterval(measurementInterval);
                measurementInterval = null;
            }
        }
        
        let pendingPings = {};
        
        function sendPing() {
            if (robotWs && robotWs.readyState === WebSocket.OPEN) {
                const t0 = Date.now() / 1000.0; // Use Date.now() for Unix timestamp compatibility
                const pingId = Math.random().toString(36).substr(2, 9);
                pendingPings[pingId] = t0;
                
                const ping = {
                    type: 'ping',
                    t0: t0,
                    id: pingId
                };
                
                robotWs.send(JSON.stringify(ping));
            }
        }
        
        function handlePongResponse(data) {
            const t2 = Date.now() / 1000.0; // Use Date.now() for Unix timestamp compatibility
            const pingId = data.id;
            
            if (!pendingPings[pingId]) return; // Ignore unknown pings
            
            const t0 = pendingPings[pingId];
            delete pendingPings[pingId];
            
            const t1 = data.t1;
            
            const rtt = t2 - t0;
            const rtt_ms = rtt * 1000.0;
            const one_way_ms = rtt_ms / 2.0;
            
            let uplink_ms = null;
            let downlink_ms = null;
            
            if (typeof t1 === 'number') {
                uplink_ms = (t1 - t0) * 1000.0;
                downlink_ms = (t2 - t1) * 1000.0;
                
                // Validate reasonable values (detect clock sync issues)
                if (Math.abs(uplink_ms) > 10000 || Math.abs(downlink_ms) > 10000) {
                    console.warn('Clock sync issue detected, using RTT/2 estimation');
                    uplink_ms = one_way_ms;
                    downlink_ms = one_way_ms;
                }
            }
            
            const measurement = {
                timestamp: Date.now() / 1000.0,
                datetime: new Date().toISOString(),
                rtt_ms: parseFloat(rtt_ms.toFixed(1)),
                one_way_ms: parseFloat(one_way_ms.toFixed(1)),
                uplink_ms: uplink_ms ? parseFloat(uplink_ms.toFixed(1)) : null,
                downlink_ms: downlink_ms ? parseFloat(downlink_ms.toFixed(1)) : null
            };
            
            updateDisplay(measurement);
        }
        
        // Browser-side ping function using HTTP requests
        async function pingPublicServerFromBrowser(target) {
            const startTime = Date.now();
            try {
                // Use a simple image request to measure latency (bypasses CORS)
                const img = new Image();
                const promise = new Promise((resolve) => {
                    img.onload = img.onerror = () => {
                        const endTime = Date.now();
                        const latency = endTime - startTime;
                        resolve(latency);
                    };
                });
                
                img.src = `https://${pingTargets[target]}/favicon.ico?t=${Date.now()}`;
                const latency = await promise;
                
                updateBrowserLatency(target, latency);
            } catch (error) {
                console.error(`Browser ping error for ${target}:`, error);
            }
        }
        
        // Server-side ping request
        function pingPublicServerFromServer(target) {
            const pingId = Math.random().toString(36).substr(2, 9);
            
            const serverPingRequest = {
                type: 'server_ping',
                target: target,
                id: pingId
            };
            
            if (robotWs && robotWs.readyState === WebSocket.OPEN) {
                robotWs.send(JSON.stringify(serverPingRequest));
            }
        }
        
        function handleServerPingResponse(data) {
            const { target, latency, ip, hostname } = data;
            if (latency !== null) {
                updateServerLatency(target, latency);
                // Update IP address display
                if (ip) {
                    const ipText = hostname ? `${ip} (${hostname})` : ip;
                    ipAddresses[`${target}Server`] = ipText;
                    document.getElementById(`${target}-server-ip`).textContent = ipText;
                }
            }
        }

        // WebRTC STUN connectivity test from browser
        async function testSTUNFromBrowser(stunServer) {
            try {
                const start = performance.now();
                const pc = new RTCPeerConnection({
                    iceServers: [{urls: stunServer}]
                });
                
                return new Promise((resolve) => {
                    let resolved = false;
                    const timeout = setTimeout(() => {
                        if (!resolved) {
                            resolved = true;
                            pc.close();
                            resolve(-1);
                        }
                    }, 5000);
                    
                    pc.onicecandidate = (event) => {
                        if (event.candidate && event.candidate.candidate.includes('srflx') && !resolved) {
                            resolved = true;
                            clearTimeout(timeout);
                            const end = performance.now();
                            pc.close();
                            resolve(end - start);
                        }
                    };
                    
                    pc.createDataChannel('test');
                    pc.createOffer().then(offer => pc.setLocalDescription(offer));
                });
            } catch (error) {
                console.error('Browser STUN test error:', error);
                return -1;
            }
        }

        // Server-side STUN request
        function stunTestFromServer(target) {
            const stunId = Math.random().toString(36).substr(2, 9);
            
            const serverStunRequest = {
                type: 'server_stun',
                target: target,
                id: stunId
            };
            
            if (robotWs && robotWs.readyState === WebSocket.OPEN) {
                robotWs.send(JSON.stringify(serverStunRequest));
            }
        }

        function handleServerStunResponse(data) {
            const { target, latency, stun_server } = data;
            if (latency !== null && latency > 0) {
                document.getElementById(`stun-${target}-server`).textContent = Math.round(latency);
            } else {
                document.getElementById(`stun-${target}-server`).textContent = 'ERR';
            }
            updateWebRTCInfo();
        }

        // WebRTC Robot Echo - simulates P2P connection to robot
        async function testWebRTCRobotEcho() {
            try {
                const start = performance.now();
                
                // Create local peer connections to simulate robot connection
                const localPc = new RTCPeerConnection();
                const remotePc = new RTCPeerConnection();
                
                // Create data channel
                const dataChannel = localPc.createDataChannel('robot-echo', {
                    ordered: true
                });
                
                return new Promise((resolve) => {
                    let resolved = false;
                    const timeout = setTimeout(() => {
                        if (!resolved) {
                            resolved = true;
                            localPc.close();
                            remotePc.close();
                            resolve(-1);
                        }
                    }, 3000);
                    
                    // Set up remote data channel handler
                    remotePc.ondatachannel = (event) => {
                        const remoteChannel = event.channel;
                        remoteChannel.onopen = () => {
                            // Send echo response
                            remoteChannel.send('robot-pong');
                        };
                        
                        remoteChannel.onmessage = (e) => {
                            if (e.data === 'robot-ping') {
                                remoteChannel.send('robot-pong');
                            }
                        };
                    };
                    
                    // Set up local data channel
                    dataChannel.onopen = () => {
                        dataChannel.send('robot-ping');
                    };
                    
                    dataChannel.onmessage = (e) => {
                        if (e.data === 'robot-pong' && !resolved) {
                            resolved = true;
                            clearTimeout(timeout);
                            const end = performance.now();
                            localPc.close();
                            remotePc.close();
                            resolve(end - start);
                        }
                    };
                    
                    // Set up ICE candidates exchange
                    localPc.onicecandidate = (e) => {
                        if (e.candidate) {
                            remotePc.addIceCandidate(e.candidate);
                        }
                    };
                    
                    remotePc.onicecandidate = (e) => {
                        if (e.candidate) {
                            localPc.addIceCandidate(e.candidate);
                        }
                    };
                    
                    // Create offer and exchange
                    localPc.createOffer().then(offer => {
                        localPc.setLocalDescription(offer);
                        remotePc.setRemoteDescription(offer);
                        return remotePc.createAnswer();
                    }).then(answer => {
                        remotePc.setLocalDescription(answer);
                        localPc.setRemoteDescription(answer);
                    }).catch(() => {
                        if (!resolved) {
                            resolved = true;
                            clearTimeout(timeout);
                            localPc.close();
                            remotePc.close();
                            resolve(-1);
                        }
                    });
                });
                
            } catch (error) {
                console.error('WebRTC Robot Echo error:', error);
                return -1;
            }
        }

        // WebRTC Frame Size Testing - 30fps video stream simulation
        async function testWebRTCFrameSize(frameSize, label) {
            try {
                const localPc = new RTCPeerConnection();
                const remotePc = new RTCPeerConnection();
                
                const dataChannel = localPc.createDataChannel(`frame-test-${frameSize}`, {
                    ordered: true,
                    maxRetransmits: 0
                });
                
                return new Promise((resolve) => {
                    let resolved = false;
                    let framesSent = 0;
                    let framesReceived = 0;
                    let frameAges = [];
                    let sendInterval = null;
                    const targetFrames = 60; // 2 seconds at 30fps
                    const frameInterval = 1000 / 30; // 33.33ms between frames
                    
                    const timeout = setTimeout(() => {
                        if (!resolved) {
                            resolved = true;
                            console.log(`${label} stream timeout`);
                            if (sendInterval) clearInterval(sendInterval);
                            localPc.close();
                            remotePc.close();
                            resolve(-1);
                        }
                    }, 10000);
                    
                    // Remote peer echoes frames back (simulates robot processing)
                    remotePc.ondatachannel = (event) => {
                        const remoteChannel = event.channel;
                        remoteChannel.onopen = () => {
                            console.log(`${label} remote channel ready for 30fps stream`);
                        };
                        
                        remoteChannel.onmessage = (e) => {
                            if (e.data instanceof ArrayBuffer) {
                                try {
                                    // Extract frame number and timestamp
                                    const frameView = new Uint8Array(e.data);
                                    const headerView = new Float64Array(frameView.slice(0, 16).buffer);
                                    const originalTimestamp = headerView[0];
                                    const frameNumber = headerView[1];
                                    
                                    const robotReceiveTime = performance.now();
                                    const frameAgeAtRobot = robotReceiveTime - originalTimestamp;
                                    
                                    // Log every 10th frame to avoid spam
                                    if (frameNumber % 10 === 0) {
                                        console.log(`${label} robot frame #${frameNumber} (age: ${frameAgeAtRobot.toFixed(1)}ms)`);
                                    }
                                    
                                    // Echo frame back immediately (simulate robot response)
                                    remoteChannel.send(e.data);
                                } catch (error) {
                                    console.warn(`${label} remote send error:`, error);
                                }
                            }
                        };
                    };
                    
                    // Local peer sends 30fps stream with timestamps
                    dataChannel.onopen = () => {
                        console.log(`Starting ${label} 30fps stream (${frameSize} bytes per frame)`);
                        
                        // Start sending frames at 30fps
                        sendInterval = setInterval(() => {
                            if (framesSent >= targetFrames) {
                                clearInterval(sendInterval);
                                return;
                            }
                            
                            const timestamp = performance.now();
                            
                            // Create frame with timestamp + frame number header (16 bytes total)
                            const frame = new ArrayBuffer(frameSize);
                            const frameView = new Uint8Array(frame);
                            const headerArray = new Float64Array([timestamp, framesSent]);
                            const headerView = new Uint8Array(headerArray.buffer);
                            
                            // Copy header to beginning of frame
                            frameView.set(headerView, 0);
                            
                            // Fill rest with video-like pattern
                            for (let i = 16; i < frameSize; i++) {
                                frameView[i] = (framesSent + i) % 256;
                            }
                            
                            try {
                                dataChannel.send(frame);
                                framesSent++;
                                
                                if (framesSent % 15 === 0) { // Log every half second
                                    console.log(`${label} sent frame #${framesSent}/${targetFrames}`);
                                }
                            } catch (error) {
                                console.warn(`${label} send error:`, error);
                                clearInterval(sendInterval);
                            }
                        }, frameInterval);
                    };
                    
                    dataChannel.onmessage = (e) => {
                        if (e.data instanceof ArrayBuffer) {
                            const receiveTime = performance.now();
                            
                            // Extract timestamp and frame number from received frame
                            const frameView = new Uint8Array(e.data);
                            const headerArray = new Float64Array(frameView.slice(0, 16).buffer);
                            const originalTimestamp = headerArray[0];
                            const frameNumber = headerArray[1];
                            
                            // Calculate frame age (one-way latency)
                            const frameAge = receiveTime - originalTimestamp;
                            frameAges.push(frameAge);
                            framesReceived++;
                            
                            // Log every 10th frame to avoid spam
                            if (framesReceived % 10 === 0) {
                                console.log(`${label} received frame #${frameNumber} (age: ${frameAge.toFixed(1)}ms)`);
                            }
                            
                            // Check if we've received all frames
                            if (framesReceived >= targetFrames && !resolved) {
                                resolved = true;
                                clearTimeout(timeout);
                                if (sendInterval) clearInterval(sendInterval);
                                
                                // Calculate statistics
                                const avgAge = frameAges.reduce((a, b) => a + b, 0) / frameAges.length;
                                const minAge = Math.min(...frameAges);
                                const maxAge = Math.max(...frameAges);
                                const jitter = maxAge - minAge;
                                
                                console.log(`${label} 30fps stream complete:
  - Frames: ${framesReceived}/${targetFrames}
  - Avg age: ${avgAge.toFixed(1)}ms
  - Min age: ${minAge.toFixed(1)}ms  
  - Max age: ${maxAge.toFixed(1)}ms
  - Jitter: ${jitter.toFixed(1)}ms
  - Frame size: ${e.data.byteLength} bytes`);
                                
                                localPc.close();
                                remotePc.close();
                                
                                // Return average frame age
                                resolve(avgAge);
                            }
                        }
                    };
                    
                    dataChannel.onerror = (e) => {
                        console.error('DataChannel error:', e);
                        if (!resolved) {
                            resolved = true;
                            clearTimeout(timeout);
                            localPc.close();
                            remotePc.close();
                            resolve(-1);
                        }
                    };
                    
                    // Set up ICE candidates exchange
                    localPc.onicecandidate = (e) => {
                        if (e.candidate) {
                            remotePc.addIceCandidate(e.candidate).catch(console.warn);
                        }
                    };
                    
                    remotePc.onicecandidate = (e) => {
                        if (e.candidate) {
                            localPc.addIceCandidate(e.candidate).catch(console.warn);
                        }
                    };
                    
                    // Create offer and exchange
                    localPc.createOffer().then(offer => {
                        return localPc.setLocalDescription(offer);
                    }).then(() => {
                        return remotePc.setRemoteDescription(localPc.localDescription);
                    }).then(() => {
                        return remotePc.createAnswer();
                    }).then(answer => {
                        return remotePc.setLocalDescription(answer);
                    }).then(() => {
                        return localPc.setRemoteDescription(remotePc.localDescription);
                    }).catch((error) => {
                        console.error('WebRTC negotiation error:', error);
                        if (!resolved) {
                            resolved = true;
                            clearTimeout(timeout);
                            localPc.close();
                            remotePc.close();
                            resolve(-1);
                        }
                    });
                });
                
            } catch (error) {
                console.error('WebRTC Frame Size Test error:', error);
                return -1;
            }
        }

        // Individual frame size test functions
        async function testWebRTCVideoSimulation() {
            return await testWebRTCFrameSize(8192, 'Low Quality (8KB)');
        }
        
        async function testWebRTCMediumVideo() {
            return await testWebRTCFrameSize(32768, 'Medium Quality (32KB)');
        }
        
        async function testWebRTCHighVideo() {
            return await testWebRTCFrameSize(65536, 'High Quality (64KB)');
        }
        
        function updateBrowserLatency(target, latency) {
            const key = `${target}Browser`;
            measurements[key].push({
                timestamp: Date.now() / 1000.0,
                rtt_ms: latency
            });
            
            if (measurements[key].length > 50) {
                measurements[key].shift();
            }
            
            // Update display
            document.getElementById(`${target}-browser`).textContent = latency.toFixed(1);
            
            // Update topology display
            updateTopologyDisplay();
            
            // Update IP address for browser ping (show hostname since we can't resolve IP in browser)
            const hostname = pingTargets[target];
            ipAddresses[key] = hostname;
            document.getElementById(`${target}-browser-ip`).textContent = hostname;
            
            updateChart();
        }
        
        function updateServerLatency(target, latency) {
            const key = `${target}Server`;
            measurements[key].push({
                timestamp: Date.now() / 1000.0,
                rtt_ms: latency
            });
            
            if (measurements[key].length > 50) {
                measurements[key].shift();
            }
            
            // Update display
            document.getElementById(`${target}-server`).textContent = latency.toFixed(1);
            updateTopologyDisplay();
            updateChart();
        }

        function updateDisplay(data) {
            // Update robot server values
            document.getElementById('robot-rtt').textContent = data.rtt_ms || '--';
            const updownText = data.uplink_ms && data.downlink_ms ? 
                `${data.uplink_ms}/${data.downlink_ms}` : '--';
            document.getElementById('robot-updown').textContent = updownText;
            
            // Update topology display
            updateTopologyDisplay();
            
            // Add to robot measurements array
            measurements.robot.push(data);
            if (measurements.robot.length > 50) {
                measurements.robot.shift();
            }
            
            updateChart();
            updateTable();
            updateWebRTCInfo();
        }
        
        function updateTopologyDisplay() {
            // Update topology diagram with current values
            document.getElementById('topo-robot-rtt').textContent = document.getElementById('robot-rtt').textContent;
            document.getElementById('topo-client-ip').textContent = document.getElementById('client-ip').textContent;
            
            // Geographic servers
            document.getElementById('topo-eindhoven-browser').textContent = document.getElementById('eindhoven-browser').textContent;
            document.getElementById('topo-eindhoven-server').textContent = document.getElementById('eindhoven-server').textContent;
            document.getElementById('topo-amsterdam-browser').textContent = document.getElementById('amsterdam-browser').textContent;
            document.getElementById('topo-amsterdam-server').textContent = document.getElementById('amsterdam-server').textContent;
            document.getElementById('topo-sofia-browser').textContent = document.getElementById('sofia-browser').textContent;
            document.getElementById('topo-sofia-server').textContent = document.getElementById('sofia-server').textContent;
            
            // WebRTC tests
            document.getElementById('topo-webrtc-small').textContent = document.getElementById('webrtc-robot-echo').textContent;
            document.getElementById('topo-webrtc-8kb').textContent = document.getElementById('webrtc-8kb').textContent;
            document.getElementById('topo-webrtc-32kb').textContent = document.getElementById('webrtc-32kb').textContent;
            document.getElementById('topo-webrtc-64kb').textContent = document.getElementById('webrtc-64kb').textContent;
            
            // STUN tests
            document.getElementById('topo-stun-google-browser').textContent = document.getElementById('stun-google-browser').textContent;
            document.getElementById('topo-stun-google-server').textContent = document.getElementById('stun-google-server').textContent;
            document.getElementById('topo-stun-cloudflare-browser').textContent = document.getElementById('stun-cloudflare-browser').textContent;
            document.getElementById('topo-stun-cloudflare-server').textContent = document.getElementById('stun-cloudflare-server').textContent;
            
            // Status
            const statusEl = document.getElementById('status');
            const topoStatusEl = document.getElementById('topo-status');
            if (statusEl && topoStatusEl) {
                topoStatusEl.textContent = statusEl.textContent;
                topoStatusEl.className = statusEl.className;
                if (statusEl.classList.contains('connected')) {
                    topoStatusEl.style.background = 'rgba(212, 237, 218, 0.9)';
                    topoStatusEl.style.color = '#155724';
                } else {
                    topoStatusEl.style.background = 'rgba(248, 215, 218, 0.9)';
                    topoStatusEl.style.color = '#721c24';
                }
            }
        }

        function updateWebRTCInfo() {
            // Update WebRTC information panel with current measurements
            const stunGoogle = document.getElementById('stun-google-browser').textContent;
            const stunCloudflare = document.getElementById('stun-cloudflare-browser').textContent;
            const webrtcEcho = document.getElementById('webrtc-robot-echo').textContent;
            const webrtc8kb = document.getElementById('webrtc-8kb').textContent;
            const robotRtt = document.getElementById('robot-rtt').textContent;
            
            // Show best STUN latency
            let bestStun = '--';
            if (stunGoogle !== '--' && stunGoogle !== 'ERR' && stunCloudflare !== '--' && stunCloudflare !== 'ERR') {
                bestStun = Math.min(parseInt(stunGoogle), parseInt(stunCloudflare)).toString();
            } else if (stunGoogle !== '--' && stunGoogle !== 'ERR') {
                bestStun = stunGoogle;
            } else if (stunCloudflare !== '--' && stunCloudflare !== 'ERR') {
                bestStun = stunCloudflare;
            }
            
            // Update WebRTC info elements (check if they exist first)
            const stunInfoEl = document.getElementById('webrtc-info-stun');
            const localInfoEl = document.getElementById('webrtc-info-local');
            const videoInfoEl = document.getElementById('webrtc-info-video');
            const websocketInfoEl = document.getElementById('webrtc-info-websocket');
            
            if (stunInfoEl) stunInfoEl.textContent = bestStun;
            if (localInfoEl) localInfoEl.textContent = webrtcEcho;
            if (videoInfoEl) videoInfoEl.textContent = webrtc8kb;
            if (websocketInfoEl) websocketInfoEl.textContent = robotRtt;
        }
        
        function updateChart() {
            // Get the longest measurements array to set common time labels
            const maxLength = Math.max(
                measurements.robot.length,
                measurements.eindhovenBrowser.length,
                measurements.eindhovenServer.length,
                measurements.amsterdamBrowser.length,
                measurements.amsterdamServer.length,
                measurements.sofiaBrowser.length,
                measurements.sofiaServer.length
            );
            
            if (maxLength === 0) return;
            
            // Use robot timestamps if available, otherwise create generic time labels
            const times = measurements.robot.length > 0 ?
                measurements.robot.map(m => new Date(m.timestamp * 1000).toLocaleTimeString()) :
                Array.from({length: maxLength}, (_, i) => new Date(Date.now() - (maxLength - i - 1) * 3000).toLocaleTimeString());
            
            chart.data.labels = times;
            chart.data.datasets[0].data = measurements.robot.map(m => m.rtt_ms);
            chart.data.datasets[1].data = measurements.eindhovenBrowser.map(m => m.rtt_ms);
            chart.data.datasets[2].data = measurements.eindhovenServer.map(m => m.rtt_ms);
            chart.data.datasets[3].data = measurements.amsterdamBrowser.map(m => m.rtt_ms);
            chart.data.datasets[4].data = measurements.amsterdamServer.map(m => m.rtt_ms);
            chart.data.datasets[5].data = measurements.sofiaBrowser.map(m => m.rtt_ms);
            chart.data.datasets[6].data = measurements.sofiaServer.map(m => m.rtt_ms);
            chart.update('none');
        }
        
        function updateTable() {
            const tbody = document.getElementById('measurements-tbody');
            tbody.innerHTML = '';
            
            const recent = measurements.robot.slice(-10).reverse(); // Last 10 robot measurements, newest first
            recent.forEach(data => {
                const row = tbody.insertRow();
                row.insertCell(0).textContent = new Date(data.timestamp * 1000).toLocaleTimeString();
                row.insertCell(1).textContent = data.rtt_ms;
                row.insertCell(2).textContent = data.uplink_ms || '--';
                row.insertCell(3).textContent = data.downlink_ms || '--';
                
                // Style cells based on values
                if (data.uplink_ms < 0) {
                    row.cells[2].style.color = '#ffc107';
                    row.cells[2].title = 'Clock skew detected';
                }
            });
        }
        
        // Start connection to robot
        connectToRobot();
    </script>
</body>
</html>
    """
    return web.Response(text=html, content_type='text/html')

async def server_info_handler(request):
    """Get server information"""
    import socket
    
    try:
        # Get server's IP address
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        
        # Get external IP (if possible)
        try:
            # Try to get external IP
            import urllib.request
            external_ip = urllib.request.urlopen('https://api.ipify.org').read().decode('utf8')
        except:
            external_ip = None
            
        info = {
            'server_hostname': hostname,
            'server_local_ip': local_ip,
            'server_external_ip': external_ip,
            'robot_server': 'localhost:8765',
            'client_ip': request.remote  # Client's IP as seen by server
        }
        
        return web.json_response(info)
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)

async def main(web_port: int, robot_port: int):
    """Simple web server and integrated robot WebSocket server"""
    app = web.Application()
    app.router.add_get('/', index_handler)
    app.router.add_get('/ws-robot', websocket_proxy_handler)
    app.router.add_get('/api/server-info', server_info_handler)
    
    # Start web server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', web_port)
    await site.start()
    
    print(f"Web interface available at: http://localhost:{web_port}")
    print(f"Robot WebSocket server starting on: ws://localhost:{robot_port}")
    print("The web page will measure latency directly from the integrated robot server")
    
    # Start robot WebSocket server
    log(f"Starting robot server on 0.0.0.0:{robot_port}")
    robot_server = await websockets.serve(handle_robot_connection, '0.0.0.0', robot_port)
    log("Robot server is running. Waiting for connections...")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour
    except KeyboardInterrupt:
        print("Servers stopped")
        robot_server.close()
        await robot_server.wait_closed()

if __name__ == "__main__":
    import asyncio
    
    parser = argparse.ArgumentParser(
        description="Integrated web interface and robot server for latency monitoring"
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8081,
        help="Web server port (default: 8081)",
    )
    parser.add_argument(
        "--robot-port",
        type=int,
        default=8765,
        help="Robot WebSocket server port (default: 8765)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(main(args.web_port, args.robot_port))
    except KeyboardInterrupt:
        print("Servers stopped by user")