#!/usr/bin/env python3
"""Combine UDP application JSON results with per-flow SDN counter deltas.

Flow counters in sdn_flow_stats.csv are cumulative and reset whenever the
controller flushes flows (link events) or a flow expires. This script turns
them into per-interval deltas for the h1->h2 flow only, restricted to the
time window of the UDP test.
"""
import argparse
import csv
import json
import os
from collections import defaultdict

POLL_NOMINAL = 2.0   # controller polls every 2 s
POLL_GAP = 3.0       # larger gap between samples => flow was removed/re-added
TAIL = 2.5           # include the first poll after the sender finished


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_rows(path):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "t": float(r["timestamp_unix"]),
                    "dpid": int(r["dpid"]),
                    "in_port": r["in_port"],
                    "src": r["ipv4_src"],
                    "dst": r["ipv4_dst"],
                    "pk": int(r["packet_count"]),
                    "by": int(r["byte_count"]),
                })
            except (ValueError, KeyError, TypeError):
                continue
    return rows


def flow_intervals(rows, src, dst, lo, hi):
    """Yield per-poll deltas for the src->dst flow inside [lo, hi + TAIL]."""
    series = defaultdict(list)
    for r in rows:
        if r["src"] == src and r["dst"] == dst:
            series[(r["dpid"], r["in_port"])].append(r)

    out = []
    for (dpid, in_port), samples in series.items():
        samples.sort(key=lambda r: r["t"])
        prev = None
        for cur in samples:
            if (prev is None or cur["t"] - prev["t"] > POLL_GAP
                    or cur["pk"] < prev["pk"]):
                # New flow instance: counters started from zero.
                dp, db = cur["pk"], cur["by"]
                t_prev = cur["t"] - POLL_NOMINAL
            else:
                dp = cur["pk"] - prev["pk"]
                db = cur["by"] - prev["by"]
                t_prev = prev["t"]
            prev = cur
            if cur["t"] > lo and cur["t"] <= hi + TAIL:
                out.append({
                    "t": cur["t"], "dpid": dpid, "in_port": in_port,
                    "packets": dp, "bytes": db,
                    "interval": max(cur["t"] - t_prev, 0.001),
                })
    out.sort(key=lambda x: (x["t"], x["dpid"]))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sender", required=True)
    p.add_argument("--receiver", required=True)
    p.add_argument("--sdn", default="results/sdn_flow_stats.csv")
    p.add_argument("--output", default="results/comparison.csv")
    p.add_argument("--src", default="10.0.0.1")
    p.add_argument("--dst", default="10.0.0.2")
    args = p.parse_args()

    sender = read_json(args.sender)
    receiver = read_json(args.receiver)
    sent = int(sender.get("sent_packets", 0))
    received = int(receiver.get("received_packets", 0))
    lost = max(sent - received, 0)
    loss_pct = (lost / sent * 100.0) if sent else 0.0

    s_end = float(sender.get("finished_unix_time", 0))
    s_start = s_end - float(sender.get("measurement_span_seconds", 0))
    lo, hi = s_start, s_end

    rows = load_rows(args.sdn)
    ivs = flow_intervals(rows, args.src, args.dst, lo, hi)

    per_sw = defaultdict(lambda: [0, 0])
    for iv in ivs:
        per_sw[iv["dpid"]][0] += iv["packets"]
        per_sw[iv["dpid"]][1] += iv["bytes"]
    path = ",".join("s%d" % d for d in sorted(per_sw) if per_sw[d][0] > 0)

    report = [
        ("metric", "application_value", "sdn_value_or_note"),
        ("sent_packets", sent, "UDP sender"),
        ("received_packets", received, "UDP receiver"),
        ("estimated_lost_packets", lost, "sent - received"),
        ("estimated_packet_loss_percent", round(loss_pct, 3), "percentage of sent packets not received"),
        ("sender_throughput_mbps", round(float(sender.get("send_throughput_mbps", 0)), 6), "application payload bytes"),
        ("receiver_throughput_mbps", round(float(receiver.get("receive_throughput_mbps", 0)), 6), "application payload bytes"),
        ("sdn_flow", "%s->%s" % (args.src, args.dst), "IPv4 flow rows only; table-miss rows excluded"),
        ("sdn_switches_that_carried_flow", path, "switches with packet counts > 0 in the test window"),
    ]
    for d in sorted(per_sw):
        pk, by = per_sw[d]
        report.append(("sdn_s%d_packets" % d, pk,
                       "flow-counter delta in test window"))
        report.append(("sdn_s%d_bytes" % d, by,
                       "includes Ethernet/IP/UDP headers (payload + 42 B per packet)"))

    pps = float(sender.get("target_packets_per_second", 0) or 0)
    ranges = list(receiver.get("missing_ranges") or [])
    last_seq = receiver.get("last_sequence")
    if last_seq is not None and last_seq < sent - 1:
        ranges.append([last_seq + 1, sent - 1, sent - 1 - last_seq])
    if pps > 0 and len(ranges) <= 10:
        for i, (a, b, n) in enumerate(ranges, 1):
            report.append(("loss_burst_%d" % i, n,
                           "packets %d-%d, starts about %.1fs after sender start, lasts about %.2fs"
                           % (a, b, a / pps, n / pps)))
    elif pps > 0:
        big = max(ranges, key=lambda r: r[2])
        report.append(("loss_burst_count", len(ranges),
                       "scattered loss (sustained congestion); per-burst rows omitted"))
        report.append(("loss_largest_burst_packets", big[2],
                       "packets %d-%d, about %.1fs after sender start"
                       % (big[0], big[1], big[0] / pps)))
        report.append(("loss_first_at_s", round(ranges[0][0] / pps, 1),
                       "seconds after sender start"))
        report.append(("loss_last_at_s", round(ranges[-1][1] / pps, 1),
                       "seconds after sender start"))
    both_paths = per_sw.get(2, [0, 0])[0] > 0 and per_sw.get(3, [0, 0])[0] > 0
    if 1 in per_sw and not both_paths:
        report.append(("lost_before_first_switch_s1", max(sent - per_sw[1][0], 0),
                       "sent - s1 flow count; valid only if no link event during the test"))
        report.append(("lost_after_s1_to_receiver", per_sw[1][0] - received,
                       "s1 flow count - received; valid only if no link event during the test"))

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(report)

    tl_path = os.path.join(os.path.dirname(os.path.abspath(args.output)),
                           "bandwidth_timeline.csv")
    with open(tl_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_unix", "seconds_from_start", "dpid", "in_port",
                    "packets", "bytes", "interval_s", "mbps"])
        for iv in ivs:
            mbps = iv["bytes"] * 8 / iv["interval"] / 1e6
            w.writerow([round(iv["t"], 3), round(iv["t"] - s_start, 1),
                        iv["dpid"], iv["in_port"], iv["packets"], iv["bytes"],
                        round(iv["interval"], 3), round(mbps, 3)])

    print(f"Comparison report written to {args.output}")
    print(f"Bandwidth timeline written to {tl_path}")
    for row in report:
        print(f"{row[0]}: {row[1]} | {row[2]}")
    if not ivs:
        print("WARNING: no flow rows for %s->%s in the test window. "
              "Check that the controller was running and the sender used "
              "the same clock." % (args.src, args.dst))


if __name__ == "__main__":
    main()
