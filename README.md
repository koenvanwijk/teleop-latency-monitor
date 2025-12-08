# Teleop Latency Monitor

Simple Python tool to monitor network latency between a teleoperated robot and a remote operator.

It uses a WebSocket ping/pong protocol:

- The **operator** (client) sends a `ping` message with timestamp `t0`.
- The **robot** (server) replies immediately with a `pong` message containing:
  - `t0` (original timestamp from the operator)
  - `t1` (timestamp on the robot when the ping was received)
- The operator notes `t2` (its own time when the `pong` arrives).

From these we can compute:
- RTT = `t2 - t0`
- Estimated one-way latency ≈ RTT / 2

If both machines have reasonably synced clocks (e.g. via NTP), we can also estimate:
- Uplink latency (operator → robot) ≈ `t1 - t0`
- Downlink latency (robot → operator) ≈ `t2 - t1`

> ⚠️ This project measures **network/command latency**, not video encoding/decoding latency.  
> It’s still very useful while you’re watching the video and driving to see how “stale” your commands might be.

## Installation

1. Create a virtual environment (optional but recommended):

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Robot side

```bash
python robot_server.py --host 0.0.0.0 --port 8765
```

### Operator side

```bash
python operator_client.py --host ROBOT_IP --port 8765 --interval 1.0
```
