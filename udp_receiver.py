#!/usr/bin/env python3
"""Receive numbered UDP datagrams and write application-level statistics."""
import argparse
import json
import socket
import time

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5005)
    p.add_argument("--duration", type=float, default=25.0)
    p.add_argument("--output", default="/tmp/udp_receiver.json")
    args = p.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    sock.bind((args.host, args.port))
    start = time.time()
    deadline = start + args.duration
    received = 0
    received_bytes = 0
    seen = set()
    first_time = None
    last_time = None

    print(f"UDP receiver listening on {args.host}:{args.port} for {args.duration}s")
    while time.time() < deadline:
        try:
            data, _addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        now = time.time()
        if first_time is None:
            first_time = now
        last_time = now
        received += 1
        received_bytes += len(data)
        try:
            seq = int(data.split(b"|", 1)[0])
            seen.add(seq)
        except (ValueError, IndexError):
            pass

    sock.close()
    missing = []
    if seen:
        ordered = sorted(seen)
        for a, b in zip(ordered, ordered[1:]):
            if b - a > 1:
                missing.append([a + 1, b - 1, b - a - 1])
    elapsed = max((last_time - first_time), 0.0) if first_time and last_time else 0.0
    result = {
        "role": "receiver",
        "listen_host": args.host,
        "port": args.port,
        "duration_seconds": args.duration,
        "received_packets": received,
        "received_payload_bytes": received_bytes,
        "unique_sequence_numbers_received": len(seen),
        "missing_ranges": missing,
        "first_sequence": min(seen) if seen else None,
        "last_sequence": max(seen) if seen else None,
        "measurement_span_seconds": elapsed,
        "receive_throughput_mbps": (received_bytes * 8 / elapsed / 1_000_000) if elapsed > 0 else 0.0,
        "finished_unix_time": time.time()
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    shown = dict(result)
    if len(missing) > 10:
        shown["missing_ranges"] = "%d ranges, %d packets missing (full list in %s)" % (
            len(missing), sum(m[2] for m in missing), args.output)
    print(json.dumps(shown, indent=2))
    print(f"Saved receiver results to {args.output}")

if __name__ == "__main__":
    main()
