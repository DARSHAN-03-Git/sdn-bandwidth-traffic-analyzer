#!/usr/bin/env bash
# Helper for the SDN Bandwidth Meter and Traffic Analyzer demo.
# Usage: ./run_demo.sh {prepare|controller [static]|topology|analyze|help}
set -e
cd "$(dirname "$0")"

usage() {
cat <<'TXT'
Run each step in its own terminal, from the project folder.

  Terminal A:  ./run_demo.sh controller          (dynamic failover controller)
               ./run_demo.sh controller static   (static baseline, no rerouting)
  Terminal B:  ./run_demo.sh topology            (Mininet; wait for the mininet> prompt)
  Terminal C:  ./run_demo.sh prepare             (create results folders, once)

At the mininet> prompt, run ONE command file at a time and wait for it to finish:
  source demo/udp_primary.txt          UDP test on the primary path   (~35 s)
  source demo/udp_failover_s1s2.txt    UDP test, s1-s2 down then up   (~55 s)
  source demo/udp_failover_s2s4.txt    UDP test, s2-s4 down then up   (~55 s)
  source demo/load_series.txt          6 traffic loads, 100-3000 pps  (~3 min)
  source demo/udp_baseline_static.txt  baseline run (controller must be in static mode)
  source demo/ping_failover.txt        ping test: normal, s1-s2 down, s1-s2 restored (~40 s)
  source demo/idle_expiry.txt          flow rules expire after 20 s idle, then reinstall (~30 s)
  source demo/load_quick.txt           two loads only, 1000 and 3000 pps (~50 s)
  source demo/unknown_host.txt         ping to a host that does not exist (~5 s)

Then, in Terminal C:  ./run_demo.sh analyze
TXT
}

analyze_one() {
  name=$1
  dir=results/$name
  if [ -f "$dir/udp_sender.json" ] && [ -f "$dir/udp_receiver.json" ]; then
    echo "=== $name"
    python3 analyze_results.py --sender "$dir/udp_sender.json" \
      --receiver "$dir/udp_receiver.json" --output "$dir/comparison.csv" \
      | grep -E "^(sent_packets|received_packets|estimated_packet_loss|sdn_switches|loss_|lost_)" || true
  else
    echo "(skipping $name: no results yet)"
  fi
}

case "${1:-help}" in
  prepare)
    mkdir -p results/demo_primary results/demo_failover_s1s2 \
             results/demo_failover_s2s4 results/demo_load results/baseline_static
    echo "Results folders ready."
    ;;
  controller)
    if [ ! -d .venv ]; then
      echo "No .venv found. Create it with:"
      echo "  python3 -m venv --system-site-packages .venv"
      echo "  source .venv/bin/activate && pip install -r requirements.txt"
      exit 1
    fi
    source .venv/bin/activate
    if [ "${2:-}" = "static" ]; then
      export SDN_MODE=static
      echo "Starting STATIC baseline controller (no rerouting)"
    else
      echo "Starting DYNAMIC controller (failover rerouting)"
    fi
    exec osken-manager sdn_controller.py
    ;;
  topology)
    sudo mn -c
    exec sudo python3 mininet_topology.py
    ;;
  analyze)
    for n in demo_primary demo_failover_s1s2 demo_failover_s2s4 demo_failover_s1s3 demo_double_failure baseline_static; do
      analyze_one "$n"
    done
    for p in 100 500 1000 2000 2500 3000; do
      d=results/demo_load
      if [ -f "$d/load_${p}_s.json" ] && [ -f "$d/load_${p}_r.json" ]; then
        echo "=== load $p pps"
        python3 analyze_results.py --sender "$d/load_${p}_s.json" \
          --receiver "$d/load_${p}_r.json" --output "$d/load_${p}.csv" \
          | grep -E "^(sent_packets|received_packets|estimated_packet_loss|sdn_s1_packets|sdn_s4_packets|loss_burst_count|lost_)" || true
      fi
    done
    ;;
  help|*)
    usage
    ;;
esac
