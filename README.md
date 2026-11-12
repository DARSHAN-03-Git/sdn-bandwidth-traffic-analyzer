# SDN Bandwidth Meter and Traffic Analyzer (Project 26, UDP)

A UDP traffic measurement application running on a Mininet network that is controlled by an OS-Ken (OpenFlow 1.3) SDN controller. The project compares **application-level** measurements (packets sent and received, throughput, loss, measured by the UDP sender and receiver) with **network-level** measurements (OpenFlow flow statistics collected by the controller), under different traffic loads and during a link failure.

Team: `<names and roll numbers>`  |  Repository: `<GitHub URL>`

## 1. What the project does

| Project 26 expectation | Where it is implemented |
|---|---|
| Generate UDP measurement traffic | `udp_sender.py` (numbered datagrams at a set packet rate) |
| Count transmitted and received packets | `udp_sender.py` and `udp_receiver.py` write JSON results |
| Calculate throughput | Both programs; `analyze_results.py` compares them |
| Collect SDN flow statistics | `sdn_controller.py` polls every switch every 2 s into `results/sdn_flow_stats.csv` |
| Measure packet loss | `udp_receiver.py` records which sequence numbers are missing; `analyze_results.py` reports loss bursts |
| Compare application and network measurements | `analyze_results.py` (per-switch flow-counter deltas against sender and receiver counts) |
| Analyze different traffic loads | six load levels, 100 to 3000 packets per second (section 7) |

On top of that, the controller provides dynamic SDN behaviour: shortest-path routing with a primary and a backup path, and automatic rerouting when a link fails or recovers. A static baseline mode (no rerouting) is included for comparison.

## 2. Architecture

```
            +--------------------------------------------+
            |   OS-Ken controller  (sdn_controller.py)   |
            |  routing (BFS) | ARP | flow rules | stats    |
            +----+---------+---------+---------+----------+
                 |OpenFlow 1.3 (TCP 6653)      |
   h1 --- s1 ----+---- s2 ---- s4 --- h2   primary path
   UDP    |                     |           UDP
 sender   +-------- s3 ---------+         receiver
                  backup path
```

Normal path: `h1 - s1 - s2 - s4 - h2`. Backup path: `h1 - s1 - s3 - s4 - h2`. All links are configured as 20 Mbps with 5 ms delay (Mininet `TCLink`). Hosts are `h1 = 10.0.0.1` (sender) and `h2 = 10.0.0.2` (receiver).

### Port map

| Switch | Port | Connected to |
|---|---|---|
| s1 | 1 / 2 / 3 | h1 / s2 / s3 |
| s2 | 1 / 2 | s1 / s4 |
| s3 | 1 / 2 | s1 / s4 |
| s4 | 1 / 2 / 3 | h2 / s2 / s3 |

## 3. How the controller works

- **Reactive flow installation.** Each switch has a table-miss rule that sends unmatched packets to the controller. For the first IPv4 packet at a switch, the controller computes the next hop with a breadth-first search over the links that are currently up, installs a priority-10 rule (match: in-port, IPv4 source and destination; idle timeout 15 s) and forwards the packet with a PacketOut.
- **ARP is routed, not flooded.** ARP requests and replies are forwarded along the same shortest path as IP traffic. (The original controller flooded ARP using only local link state; with only the s1-s2 link down, the ARP reply from h2 was sent to s2 and dropped, so h1 could not resolve h2. Routing ARP fixed this.)
- **Link failure and recovery.** The controller tracks port state from OpenFlow port-status events and from a port-description poll every 2 s (so a duplicate or stale event cannot leave the state wrong). A link is usable only if both ends are up. On a real state change it deletes the IPv4 forwarding rules on all switches (the table-miss rule stays), and the next packets are routed over the current topology.
- **Statistics.** Flow statistics are requested from every switch every 2 s and appended to `results/sdn_flow_stats.csv` (columns: `timestamp_unix, dpid, in_port, eth_type, ip_proto, ipv4_src, ipv4_dst, udp_src, udp_dst, packet_count, byte_count`).
- **Errors.** OpenFlow error messages are logged as `OPENFLOW ERROR`; packets with no route are logged as `No route`.
- **Static baseline.** Starting the controller with `SDN_MODE=static` makes it ignore all link events and never reroute.

## 4. UDP application and analysis

- `udp_sender.py` sends datagrams carrying a 15-digit sequence number at `--pps` packets per second for `--duration` seconds (`--payload-size`, default 800 bytes) and writes a JSON result.
- `udp_receiver.py` counts datagrams, unique sequence numbers and bytes, lists the missing sequence ranges, and writes a JSON result.
- `analyze_results.py` combines both JSON files with `sdn_flow_stats.csv`. Cumulative flow counters are converted to per-poll deltas for the h1-to-h2 flow only, restricted to the test window, and a counter reset (after a flush or flow expiry) is treated as a new flow. It reports loss, loss bursts (with start time and duration), which switches carried the flow, and packets and bytes per switch (bytes include 42 bytes of Ethernet, IP and UDP headers per packet). It also writes `bandwidth_timeline.csv` (Mbps per 2 s interval, per switch).

## 5. Requirements and setup

Tested on an Ubuntu virtual machine (VirtualBox) with Python 3.12, Mininet, Open vSwitch and OS-Ken.

```bash
sudo apt install mininet openvswitch-switch      # if not already installed
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt                  # installs the Python dependencies
chmod +x run_demo.sh
```

Mininet and Open vSwitch are installed system-wide (not in the venv). Run the topology with `sudo` and the system Python, and the controller inside the venv.

## 6. Running the demo

Use three terminals from the project folder. `./run_demo.sh help` prints the same steps.

```bash
# Terminal A: controller
./run_demo.sh controller

# Terminal B: Mininet (wait for the mininet> prompt)
./run_demo.sh topology

# Terminal C: once
./run_demo.sh prepare
```

At the `mininet>` prompt run one command file at a time and wait for it to finish (type nothing while it runs):

| Command | What it does | Time |
|---|---|---|
| `source demo/udp_primary.txt` | UDP at 500 pps for 20 s on the primary path | about 35 s |
| `source demo/udp_failover_s1s2.txt` | UDP for 35 s; s1-s2 goes down at 10 s and up at 22 s | about 55 s |
| `source demo/udp_failover_s2s4.txt` | same with the s2-s4 link | about 55 s |
| `source demo/load_series.txt` | six runs at 100, 500, 1000, 2000, 2500, 3000 pps | about 3 min |

Then, in Terminal C: `./run_demo.sh analyze`. Results are written under `results/`.

**Static baseline:** stop the controller (Ctrl+C), start it with `./run_demo.sh controller static`, restart Mininet, and run `source demo/udp_baseline_static.txt`. Restart the controller without `static` afterwards.

Tip: type or paste one command at a time at the `mininet>` prompt; pasting several lines can garble them. The command files avoid this.

Flow tables can be inspected with `sudo ovs-ofctl -O OpenFlow13 dump-flows s1` (also s2 to s4). Rules expire 15 s after the last matching packet, so inspect them while traffic is running.

## 7. Results (measured in the VM)

### Connectivity, flow installation and failover (ping and flow-table tests)

| Test | Result |
|---|---|
| h1 to h2 ping with empty ARP caches, primary path | 0% loss; rules on s1, s2, s4 in both directions |
| s1-s2 down, ARP caches flushed | 0% loss; rules on s1, s3, s4 only (the original failure case) |
| s1-s2 restored | 0% loss; traffic returns to s2, s3 has no rules |
| s2-s4 down, then restored | 0% loss while down and after restore |
| Five down/up cycles of s1-s2 | all ten pings 0% loss |
| Idle for 20 s | all rules expired; the next ping reinstalls them |

### UDP on the primary path (500 pps, 20 s)

10,000 packets sent and received, 0% loss. Flow counters on s1, s2 and s4 each show 10,000 packets and 8,420,000 bytes (10,000 x 842), matching the application exactly.

### Different traffic loads (15 s each)

| Rate | Offered (payload Mbps) | Sent | Received | Loss | Received (Mbps) |
|---|---|---|---|---|---|
| 100 pps | 0.64 | 1,500 | 1,500 | 0% | 0.64 |
| 500 pps | 3.2 | 7,492 | 7,492 | 0% | 3.19 |
| 1000 pps | 6.4 | 15,000 | 15,000 | 0% | 6.38 |
| 2000 pps | 12.8 | 29,998 | 23,444 | 21.8% | 9.42 |
| 2500 pps | 16.0 | 37,500 | 26,166 | 30.2% | 10.94 |
| 3000 pps | 19.2 | 44,906 | 25,394 | 43.5% | 9.96 |

Up to 1000 pps the application and switch counters agree exactly. At 2500 and 3000 pps the counters on s1, s2 and s4 all equal the receiver's count, so the missing packets were dropped before s1 (on the h1 access link), not inside the switched network. At 3000 pps the loss and throughput vary a lot from run to run (four runs: 6% to 45% loss, 10 to 17.5 Mbps received); the cause has not been established.

### UDP during link failure (500 pps, 35 s; link down at about 10 s, up at about 22 s)

| Run | Failed link | Sent | Lost | Loss | Outage |
|---|---|---|---|---|---|
| 1 | s1-s2 | 17,500 | 28 | 0.16% | 0.06 s |
| 2 | s1-s2 | 17,500 | 109 | 0.62% | 0.22 s |
| 3 | s1-s2 | 17,473 | 20 | 0.11% | 0.04 s |
| 4 | s1-s2 | 17,395 | 11 | 0.06% | 0.02 s |
| 5 | s2-s4 | 17,480 | 15 | 0.09% | 0.03 s |

Each run had a single short loss burst. For run 1 the burst was confirmed (from the per-switch timeline) to coincide with the link going down, and restoring the link caused no loss.

### Static baseline against dynamic failover

| Approach | Failed link | Runs | Loss | Outage |
|---|---|---|---|---|
| Static (no rerouting) | s1-s2 | 1 | 34.4% (6,022 of 17,500) | 12.04 s (until the link returned) |
| Dynamic failover | s1-s2 | 4 | 0.24% pooled (168 of 69,868) | 0.02 to 0.22 s |
| Dynamic failover | s2-s4 | 1 | 0.09% (15 of 17,480) | 0.03 s |

## 8. Known limitations

- Only two hosts and one measurement flow; the topology and controller are not tested with several competing flows.
- The dynamic behaviour reacts to link events only. It does not react to congestion or to application events, and a congested access link (as in the 2000+ pps runs) cannot be fixed by rerouting.
- Flow counters reset whenever the controller flushes rules, so during a link event the switch counters undercount. Use the receiver's missing-sequence ranges to measure loss around a failure.
- At 3000 pps the loss and throughput vary a lot from run to run (four runs: 6% to 45% loss, 10 to 17.5 Mbps received); the cause has not been established.
- Each failover figure comes from a single link failure at 500 pps in a virtual machine; the static baseline was run once. Timings vary between runs.
- Mininet prints `sch_htb: quantum of class ... is big` warnings; they are harmless.

## 9. Repository layout

```
sdn_controller.py       OS-Ken controller (routing, failover, statistics, static mode)
mininet_topology.py     Mininet topology (4 switches, 2 hosts, 20 Mbps / 5 ms links)
udp_sender.py           UDP traffic generator
udp_receiver.py         UDP receiver and sequence tracker
analyze_results.py      Application vs SDN comparison and bandwidth timeline
run_demo.sh             Helper to start the controller, topology and analysis
demo/                   Mininet command files for each experiment
requirements.txt        Python dependencies
results/                Experiment outputs (CSV and JSON)
```

## 10. Troubleshooting

- `osken-manager: command not found`: activate the venv first (`source .venv/bin/activate`), or use `./run_demo.sh controller`.
- Ping works but later says `Destination Host Unreachable`: check the controller log for `No route` or `OPENFLOW ERROR` lines.
- A flow dump shows only the table-miss rule: the rules expired (15 s idle). Dump while traffic is running.
- Command files fail with a missing-directory error: run `./run_demo.sh prepare` first.
- Results from `/tmp` disappear after a reboot; the command files write into `results/` instead.
