# Test cases: SDN Bandwidth Meter and Traffic Analyzer

All tests run in Mininet with the OS-Ken controller running (`./run_demo.sh controller` and `./run_demo.sh topology`). Type commands only when the `mininet>` prompt is back. "Measured" shows what we observed in our virtual machine.

**Live flow-table display** (run in a separate terminal before a test; stop with Ctrl+C). Rules expire 15 s after the last packet, so they only show while traffic is running:

```bash
while true; do clear; date +%T; for s in s1 s2 s3 s4; do echo "== $s"; sudo ovs-ofctl -O OpenFlow13 dump-flows $s | grep priority=10 | sed -E 's/.*priority=10,ip,in_port=([0-9]+),nw_src=([0-9.]+),nw_dst=([0-9.]+) actions=output:([0-9]+)/  in:\1  \2 -> \3  out:\4/'; done; sleep 1; done
```

## Summary

| ID | Test case | How to run | Expected result | Measured |
|---|---|---|---|---|
| TC1 | Connectivity with empty ARP caches | `h1 ip neigh flush all`, `h2 ip neigh flush all`, `h1 ping -c 3 h2` | 0% loss; controller log shows `ARP:` then `FLOW ROUTE:` lines | 0% loss |
| TC2 | Flow rules installed on every switch of the path | live display while pinging | rules in both directions on s1, s2, s4; none on s3 | as expected |
| TC3 | UDP on the primary path: application against network counters | `source demo/udp_primary.txt`, then `./run_demo.sh analyze` | sent = received; s1, s2, s4 counts equal the application counts | 10,000 of 10,000 sent and received; 10,000 packets and 8,420,000 bytes on s1, s2, s4 |
| TC4 | Different traffic loads | `source demo/load_quick.txt` (1000 and 3000 pps) or `source demo/load_series.txt` (six loads) | no loss at low load; loss when the link saturates | table below |
| TC5 | Link failure (ping): s1-s2 down, then restored | `source demo/ping_failover.txt` with the live display running | 0% loss in all three parts; rules on s1, s3, s4 while down; back on s2 after restore | 0% loss; backup path used |
| TC6 | Link failure during UDP traffic (s1-s2) | `source demo/udp_failover_s1s2.txt`, then analyze | under 1% loss, one short loss burst, s1 to s4 all carry the flow | 0.06% to 0.62% in 7 runs |
| TC7 | Link failure during UDP traffic (s2-s4) | `source demo/udp_failover_s2s4.txt`, then analyze | same as TC6 | 15 of 17,480 lost (0.086%), 0.03 s |
| TC8 | Rules expire when idle, then reinstall | `source demo/idle_expiry.txt` with the live display running | rules visible during the pings, empty after about 15 s idle, back after the second ping | all rules gone after 20 s; second ping 0% loss |
| TC9 | Static baseline against dynamic failover | controller started with `./run_demo.sh controller static`, then `source demo/udp_baseline_static.txt` | heavy loss for as long as the link is down | static: 34.4% lost, 12.04 s outage; dynamic: 0.24% pooled (4 runs) |
| TC10 | Unknown destination | `source demo/unknown_host.txt` | ping fails; controller log shows `No route (ARP)` | not run yet |

## Load results (TC4), 15 s per level

| Rate | Offered (payload Mbps) | Sent | Received | Loss | Received (Mbps) |
|---|---|---|---|---|---|
| 100 pps | 0.64 | 1,500 | 1,500 | 0% | 0.64 |
| 500 pps | 3.2 | 7,492 | 7,492 | 0% | 3.19 |
| 1000 pps | 6.4 | 15,000 | 15,000 | 0% | 6.38 |
| 2000 pps | 12.8 | 29,998 | 23,444 | 21.8% | 9.42 |
| 2500 pps | 16.0 | 37,500 | 26,166 | 30.2% | 10.94 |
| 3000 pps | 19.2 | 44,906 | 25,394 | 43.5% | 9.96 |

At 2500 and 3000 pps the counters on s1, s2 and s4 all equal the receiver's count, so the packets were dropped on the h1 access link before s1. At 3000 pps the loss and throughput vary a lot from run to run (four runs: 6% to 45% loss, 10 to 17.5 Mbps received); the cause has not been established.

## Failover runs (TC6 and TC7), 500 pps, 35 s, link down at about 10 s and up at about 22 s

| Run | Failed link | Sent | Lost | Loss | Outage |
|---|---|---|---|---|---|
| 1 | s1-s2 | 17,500 | 28 | 0.16% | 0.06 s |
| 2 | s1-s2 | 17,500 | 109 | 0.62% | 0.22 s |
| 3 | s1-s2 | 17,473 | 20 | 0.11% | 0.04 s |
| 4 | s1-s2 | 17,395 | 11 | 0.06% | 0.02 s |
| 5 | s2-s4 | 17,480 | 15 | 0.09% | 0.03 s |
| 6 | s1-s2 | 17,500 | 43 | 0.25% | about 0.09 s |
| 7 | s1-s2 | 17,404 | 11 | 0.06% | 0.02 s |

## Suggested order for the demo (about 12 minutes)

1. TC1 and TC2 (2 min): ping with the live display.
2. TC3 (1 min): `source demo/udp_primary.txt`, then analyze.
3. TC5 (2 min): `source demo/ping_failover.txt` with the live display.
4. TC6 (2 min): `source demo/udp_failover_s1s2.txt`, then analyze.
5. TC8 (1 min): `source demo/idle_expiry.txt`.
6. TC4, TC7 and TC9 (3 min): show the saved results above, or run `source demo/load_quick.txt`.

If a live run fails, show the matching saved results from the tables above and from the `results/` folder.
