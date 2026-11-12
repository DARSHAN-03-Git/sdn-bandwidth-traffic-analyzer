#!/usr/bin/env python3
"""Send numbered UDP datagrams at a configurable packet rate."""
import argparse
import json
import socket
import time

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="10.0.0.2")
    p.add_argument("--port", type=int, default=5005)
    p.add_argument("--duration", type=float, default=20.0)
    p.add_argument("--pps", type=float, default=500.0, help="target packets per second")
    p.add_argument("--payload-size", type=int, default=800, help="UDP payload bytes, minimum 16")
    p.add_argument("--output", default="/tmp/udp_sender.json")
    args = p.parse_args()

    if args.pps <= 0:
        p.error("--pps must be greater than zero")
    if args.payload_size < 16:
        p.error("--payload-size must be at least 16")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    prefix_len = 16
    filler = b"x" * (args.payload_size - prefix_len)
    interval = 1.0 / args.pps
    start = time.monotonic()
    deadline = start + args.duration
    sent = 0
    sent_bytes = 0
    next_send = start

    while time.monotonic() < deadline:
        payload = f"{sent:015d}|".encode("ascii") + filler
        try:
            sock.sendto(payload, (args.host, args.port))
            sent += 1
            sent_bytes += len(payload)
        except OSError as exc:
            print(f"Send error after {sent} packets: {exc}")
            break
        next_send += interval
        delay = next_send - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    elapsed = max(time.monotonic() - start, 0.000001)
    sock.close()
    result = {
        "role": "sender",
        "destination_host": args.host,
        "port": args.port,
        "duration_seconds": args.duration,
        "target_packets_per_second": args.pps,
        "payload_size_bytes": args.payload_size,
        "sent_packets": sent,
        "sent_payload_bytes": sent_bytes,
        "measurement_span_seconds": elapsed,
        "send_throughput_mbps": sent_bytes * 8 / elapsed / 1_000_000,
        "finished_unix_time": time.time()
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    print(f"Saved sender results to {args.output}")

if __name__ == "__main__":
    main()
