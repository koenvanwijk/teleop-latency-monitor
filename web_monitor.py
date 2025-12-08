#!/usr/bin/env python
import argparse
import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List
import logging

import websockets
from aiohttp import web, WSMsgType
import aiohttp_cors

# Store recent latency measurements
latency_history: List[Dict] = []
MAX_HISTORY = 100

async def latency_collector(robot_host: str, robot_port: int):
    """Collect latency data from robot server"""
    uri = f"ws://{robot_host}:{robot_port}"
    print(f"Connecting to robot server at {uri}")
    
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                print("Connected to robot server. Starting measurements...")
                while True:
                    t0 = time.time()
                    ping_msg = {"type": "ping", "t0": t0}
                    await websocket.send(json.dumps(ping_msg))

                    msg = await websocket.recv()
                    t2 = time.time()

                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        continue

                    if data.get("type") != "pong":
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

                    # Store measurement
                    measurement = {
                        "timestamp": time.time(),
                        "datetime": datetime.now().isoformat(),
                        "rtt_ms": round(rtt_ms, 1),
                        "one_way_ms": round(one_way_ms, 1),
                        "uplink_ms": round(uplink_ms, 1) if uplink_ms is not None else None,
                        "downlink_ms": round(downlink_ms, 1) if downlink_ms is not None else None,
                    }
                    
                    latency_history.append(measurement)
                    if len(latency_history) > MAX_HISTORY:
                        latency_history.pop(0)
                    
                    # Send to all connected websocket clients
                    if websocket_clients:
                        await broadcast_to_clients(measurement)
                    
                    await asyncio.sleep(1.0)
                    
        except Exception as e:
            print(f"Connection error: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)

# WebSocket clients for real-time updates
websocket_clients = set()

async def broadcast_to_clients(data):
    """Send data to all connected websocket clients"""
    global websocket_clients
    if websocket_clients:
        disconnected = set()
        for ws in websocket_clients:
            try:
                await ws.send_str(json.dumps(data))
            except:
                disconnected.add(ws)
        
        # Remove disconnected clients
        websocket_clients -= disconnected

async def websocket_handler(request):
    """WebSocket handler for real-time updates"""
    global websocket_clients
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    websocket_clients.add(ws)
    
    # Send recent history
    for measurement in latency_history[-20:]:  # Last 20 measurements
        await ws.send_str(json.dumps(measurement))
    
    async for msg in ws:
        if msg.type == WSMsgType.ERROR:
            print(f'WebSocket error: {ws.exception()}')
    
    websocket_clients.discard(ws)
    return ws

async def api_history(request):
    """API endpoint to get latency history"""
    return web.json_response(latency_history[-50:])  # Last 50 measurements

async def api_latest(request):
    """API endpoint to get latest measurement"""
    if latency_history:
        return web.json_response(latency_history[-1])
    return web.json_response({})

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
            <div id="status" class="status disconnected">Connecting...</div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Round Trip Time</div>
                <div id="rtt-value" class="stat-value rtt">--</div>
                <div class="stat-label">ms</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Est. One-Way</div>
                <div id="oneway-value" class="stat-value">--</div>
                <div class="stat-label">ms</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Uplink</div>
                <div id="uplink-value" class="stat-value uplink">--</div>
                <div class="stat-label">ms</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Downlink</div>
                <div id="downlink-value" class="stat-value downlink">--</div>
                <div class="stat-label">ms</div>
            </div>
        </div>
        
        <div class="chart-container">
            <canvas id="latencyChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h3>Recent Measurements</h3>
            <div id="measurements-table">
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="background-color: #f8f9fa;">
                            <th style="padding: 10px; border: 1px solid #dee2e6;">Time</th>
                            <th style="padding: 10px; border: 1px solid #dee2e6;">RTT (ms)</th>
                            <th style="padding: 10px; border: 1px solid #dee2e6;">Uplink (ms)</th>
                            <th style="padding: 10px; border: 1px solid #dee2e6;">Downlink (ms)</th>
                        </tr>
                    </thead>
                    <tbody id="measurements-tbody">
                    </tbody>
                </table>
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
                        label: 'RTT (ms)',
                        data: [],
                        borderColor: '#007bff',
                        backgroundColor: 'rgba(0, 123, 255, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Uplink (ms)',
                        data: [],
                        borderColor: '#28a745',
                        backgroundColor: 'rgba(40, 167, 69, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Downlink (ms)',
                        data: [],
                        borderColor: '#dc3545',
                        backgroundColor: 'rgba(220, 53, 69, 0.1)',
                        tension: 0.1
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    title: {
                        display: true,
                        text: 'Latency Over Time'
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

        // Direct WebSocket connection to robot server for latency measurements
        let robotWs;
        const measurements = [];
        let measurementInterval;
        
        function connectToRobot() {
            // Connect directly to robot server (through port forwarding)
            const robotUrl = 'ws://localhost:8765';
            robotWs = new WebSocket(robotUrl);
            
            robotWs.onopen = function() {
                document.getElementById('status').textContent = 'Connected to Robot';
                document.getElementById('status').className = 'status connected';
                startMeasurements();
            };
            
            robotWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'pong') {
                        handlePongResponse(data);
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
            measurementInterval = setInterval(sendPing, 1000); // Every second
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
                const t0 = performance.now() / 1000.0; // Convert to seconds
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
            const t2 = performance.now() / 1000.0; // Convert to seconds
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
        
        function updateDisplay(data) {
            // Update current values
            document.getElementById('rtt-value').textContent = data.rtt_ms || '--';
            document.getElementById('oneway-value').textContent = data.one_way_ms || '--';
            document.getElementById('uplink-value').textContent = data.uplink_ms || '--';
            document.getElementById('downlink-value').textContent = data.downlink_ms || '--';
            
            // Add to measurements array
            measurements.push(data);
            if (measurements.length > 50) {
                measurements.shift();
            }
            
            // Update chart
            const times = measurements.map(m => new Date(m.timestamp * 1000).toLocaleTimeString());
            chart.data.labels = times;
            chart.data.datasets[0].data = measurements.map(m => m.rtt_ms);
            chart.data.datasets[1].data = measurements.map(m => m.uplink_ms);
            chart.data.datasets[2].data = measurements.map(m => m.downlink_ms);
            chart.update('none');
            
            // Update table
            updateTable();
        }
        
        function updateTable() {
            const tbody = document.getElementById('measurements-tbody');
            tbody.innerHTML = '';
            
            const recent = measurements.slice(-10).reverse(); // Last 10, newest first
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

async def create_app(web_port: int):
    """Create and configure the web application"""
    app = web.Application()
    
    # Setup CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })
    
    # Routes
    app.router.add_get('/', index_handler)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/api/history', api_history)
    app.router.add_get('/api/latest', api_latest)
    
    # Add CORS to all routes
    for route in list(app.router.routes()):
        cors.add(route)
    
    return app

async def main(robot_host: str, robot_port: int, web_port: int):
    """Main function to start both latency collection and web server"""
    print(f"Starting web monitor on port {web_port}")
    print(f"Will collect latency data from {robot_host}:{robot_port}")
    
    # Create web app
    app = await create_app(web_port)
    
    # Start web server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', web_port)
    await site.start()
    
    print(f"Web interface available at: http://localhost:{web_port}")
    
    # Start latency collection
    await latency_collector(robot_host, robot_port)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Web-based latency monitor"
    )
    parser.add_argument(
        "--robot-host",
        type=str,
        required=True,
        help="Robot server host/IP (e.g. localhost if port forwarded)",
    )
    parser.add_argument(
        "--robot-port",
        type=int,
        default=8765,
        help="Robot server port (default: 8765)",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8080,
        help="Web server port (default: 8080)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(main(args.robot_host, args.robot_port, args.web_port))
    except KeyboardInterrupt:
        print("Web monitor stopped by user")