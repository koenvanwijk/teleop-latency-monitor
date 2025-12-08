#!/usr/bin/env python
import argparse
import asyncio
import json
import time
from aiohttp import web, WSMsgType
import websockets

# Store active browser connections
browser_connections = set()

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
            <p>Measuring latency from your browser to the robot server</p>
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
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Robot Server</div>
                <div id="robot-rtt" class="stat-value rtt">--</div>
                <div class="stat-label">ms RTT (WebSocket)</div>
                <div class="stat-label" style="font-size: 0.7em; color: #888;" id="robot-ip">localhost:8765</div>
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
            }, 4000); // Every 4 seconds with staggered requests
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
            updateChart();
        }

        function updateDisplay(data) {
            // Update robot server values
            document.getElementById('robot-rtt').textContent = data.rtt_ms || '--';
            const updownText = data.uplink_ms && data.downlink_ms ? 
                `${data.uplink_ms}/${data.downlink_ms}` : '--';
            document.getElementById('robot-updown').textContent = updownText;
            
            // Add to robot measurements array
            measurements.robot.push(data);
            if (measurements.robot.length > 50) {
                measurements.robot.shift();
            }
            
            updateChart();
            updateTable();
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

async def main(web_port: int):
    """Simple web server to serve the latency monitor interface"""
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
    print("The web page will measure latency directly from your browser to the robot server")
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour
    except KeyboardInterrupt:
        print("Web server stopped")

if __name__ == "__main__":
    import asyncio
    
    parser = argparse.ArgumentParser(
        description="Simple web interface for latency monitoring"
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8080,
        help="Web server port (default: 8080)",
    )

    args = parser.parse_args()

    try:
        asyncio.run(main(args.web_port))
    except KeyboardInterrupt:
        print("Web server stopped by user")