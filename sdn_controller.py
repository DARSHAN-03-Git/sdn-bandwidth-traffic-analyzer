#!/usr/bin/env python3
"""SDN traffic controller with primary/backup routing and flow statistics."""

import csv
import os
import time
from collections import deque

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib import hub
from os_ken.lib.packet import packet, ethernet, ipv4, arp

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
OUT_CSV = os.path.join(OUT_DIR, "sdn_flow_stats.csv")

HOST_PORT = 1          # host-facing port on s1 and s4
IDLE_TIMEOUT = 15
FLOW_PRIORITY = 10


class TrafficStatsController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # dpid -> {local_port: (neighbor_dpid, neighbor_port)}
    # Primary: h1-s1-s2-s4-h2   Backup: h1-s1-s3-s4-h2
    LINKS = {
        1: {2: (2, 1), 3: (3, 1)},
        2: {1: (1, 2), 2: (4, 2)},
        3: {1: (1, 3), 2: (4, 3)},
        4: {2: (2, 2), 3: (3, 2)},
    }

    HOST_IPS = {
        "10.0.0.1": 1,
        "10.0.0.2": 4,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        os.makedirs(OUT_DIR, exist_ok=True)

        self.datapaths = {}
        self.failed_ports = set()   # {(dpid, port_no)} with link/admin down
        self.ip_to_mac = {}
        # SDN_MODE=static -> baseline: ignore link events, never reroute.
        self.static_mode = os.environ.get("SDN_MODE", "dynamic").lower() == "static"
        self.logger.warning("Controller mode: %s",
                            "STATIC (baseline, no rerouting)" if self.static_mode
                            else "DYNAMIC (failover rerouting)")
        self._ensure_csv_header()
        self.poller = hub.spawn(self._poll_stats)

    # ------------------------------------------------------------------ CSV
    def _ensure_csv_header(self):
        if not os.path.exists(OUT_CSV) or os.path.getsize(OUT_CSV) == 0:
            with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    "timestamp_unix", "dpid", "in_port", "eth_type",
                    "ip_proto", "ipv4_src", "ipv4_dst", "udp_src", "udp_dst",
                    "packet_count", "byte_count"
                ])

    # ------------------------------------------------------- flow helpers
    def _flush_ipv4_flows(self, dp):
        """Delete only IPv4 forwarding flows; table-miss (priority 0) stays."""
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp,
            table_id=ofp.OFPTT_ALL,
            command=ofp.OFPFC_DELETE,
            out_port=ofp.OFPP_ANY,
            out_group=ofp.OFPG_ANY,
            match=parser.OFPMatch(eth_type=0x0800),
        ))

    def _install_table_miss(self, dp):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        actions = [
            parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)
        ]
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp,
            priority=0,
            match=parser.OFPMatch(),
            instructions=[
                parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)
            ],
        ))

    def _flush_all(self, reason):
        self.logger.warning("Topology change (%s): flushing IPv4 flows", reason)
        for dp in list(self.datapaths.values()):
            self._flush_ipv4_flows(dp)

    # ------------------------------------------------- switch connection
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        self._install_table_miss(dp)

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, CONFIG_DISPATCHER],
    )
    def state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            self._request_port_desc(dp)
        elif self.datapaths.get(dp.id) is dp:
            del self.datapaths[dp.id]

    # ------------------------------------------------------- error logging
    @set_ev_cls(ofp_event.EventOFPErrorMsg, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def error_handler(self, ev):
        msg = ev.msg
        self.logger.error(
            "OPENFLOW ERROR: switch=%s type=%s code=%s data=%s",
            msg.datapath.id, msg.type, msg.code,
            bytes(msg.data[:64]).hex() if msg.data else "",
        )

    # --------------------------------------------------------- port state
    @staticmethod
    def _desc_is_down(desc):
        return bool(
            (desc.state & ofproto_v1_3.OFPPS_LINK_DOWN)
            or (desc.config & ofproto_v1_3.OFPPC_PORT_DOWN)
        )

    def _set_port(self, dpid, port_no, down):
        """Update tracked state. Return True only if the state changed."""
        key = (dpid, port_no)
        was_down = key in self.failed_ports
        if down == was_down:
            return False
        if down:
            self.failed_ports.add(key)
            self.logger.warning("Port DOWN: switch=%s port=%s", dpid, port_no)
        else:
            self.failed_ports.discard(key)
            self.logger.info("Port UP: switch=%s port=%s", dpid, port_no)
        return True

    def _request_port_desc(self, dp):
        try:
            dp.send_msg(dp.ofproto_parser.OFPPortDescStatsRequest(dp, 0))
        except Exception as exc:
            self.logger.warning("Could not request port desc: %s", exc)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
        if self.static_mode:
            self.logger.warning("STATIC mode: ignoring port event switch=%s port=%s",
                                ev.msg.datapath.id, ev.msg.desc.port_no)
            return
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        port_no = msg.desc.port_no
        if port_no >= ofp.OFPP_MAX:
            return

        down = (msg.reason == ofp.OFPPR_DELETE) or self._desc_is_down(msg.desc)
        if self._set_port(dp.id, port_no, down):
            self._flush_all("port %s/%s %s" % (
                dp.id, port_no, "down" if down else "up"))
        # Re-read authoritative state to heal any stale/duplicate event.
        self._request_port_desc(dp)

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def port_desc_reply_handler(self, ev):
        if self.static_mode:
            return
        dp = ev.msg.datapath
        changed = False
        for p in ev.msg.body:
            if p.port_no >= dp.ofproto.OFPP_MAX:
                continue
            if self._set_port(dp.id, p.port_no, self._desc_is_down(p)):
                changed = True
        if changed:
            self._flush_all("port resync switch=%s" % dp.id)

    # ------------------------------------------------------------ routing
    def _port_is_up(self, dpid, port_no):
        return (dpid, port_no) not in self.failed_ports

    def _neighbors(self, dpid):
        for port, (nbr, nbr_port) in self.LINKS.get(dpid, {}).items():
            if self._port_is_up(dpid, port) and self._port_is_up(nbr, nbr_port):
                yield nbr, port

    def _next_hop_port(self, source, destination):
        """BFS over live links; return first-hop output port or None."""
        if source == destination:
            return None
        queue = deque([(source, None)])
        visited = {source}
        while queue:
            current, first_port = queue.popleft()
            for nbr, port in self._neighbors(current):
                if nbr in visited:
                    continue
                fp = port if first_port is None else first_port
                if nbr == destination:
                    return fp
                visited.add(nbr)
                queue.append((nbr, fp))
        return None

    def _route_port(self, dpid, in_port, dst_ip):
        dst_switch = self.HOST_IPS.get(dst_ip)
        if dst_switch is None:
            return None
        if dpid == dst_switch:
            return HOST_PORT if in_port != HOST_PORT else None
        return self._next_hop_port(dpid, dst_switch)

    # ----------------------------------------------------------- packet-in
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        in_port = msg.match.get("in_port")

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        dpid = dp.id
        arp_pkt = pkt.get_protocol(arp.arp)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        if arp_pkt:
            # Route ARP (request or reply) toward the target host over the
            # current live path, exactly like IPv4. No flooding, no loops.
            self.ip_to_mac[arp_pkt.src_ip] = eth.src
            out_port = self._route_port(dpid, in_port, arp_pkt.dst_ip)
            kind = "ARP"
            dst = arp_pkt.dst_ip
        elif ip_pkt:
            self.ip_to_mac[ip_pkt.src] = eth.src
            out_port = self._route_port(dpid, in_port, ip_pkt.dst)
            kind = "FLOW ROUTE"
            dst = ip_pkt.dst
        else:
            return

        if out_port is None or out_port == in_port:
            self.logger.warning(
                "No route (%s): switch=%s in_port=%s dst=%s out=%s",
                kind, dpid, in_port, dst, out_port)
            return

        self.logger.info(
            "%s: switch=%s in_port=%s dst=%s out_port=%s",
            kind, dpid, in_port, dst, out_port)
        actions = [parser.OFPActionOutput(out_port)]

        if ip_pkt:
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp,
                priority=FLOW_PRIORITY,
                match=parser.OFPMatch(
                    in_port=in_port,
                    eth_type=0x0800,
                    ipv4_src=ip_pkt.src,
                    ipv4_dst=ip_pkt.dst,
                ),
                instructions=[
                    parser.OFPInstructionActions(
                        ofp.OFPIT_APPLY_ACTIONS, actions)
                ],
                idle_timeout=IDLE_TIMEOUT,
            ))

        data = None if msg.buffer_id != ofp.OFP_NO_BUFFER else msg.data
        dp.send_msg(parser.OFPPacketOut(
            datapath=dp,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        ))

    # --------------------------------------------------------------- stats
    def _poll_stats(self):
        while True:
            for dp in list(self.datapaths.values()):
                try:
                    dp.send_msg(dp.ofproto_parser.OFPFlowStatsRequest(dp))
                except Exception as exc:
                    self.logger.warning("Could not request flow stats: %s", exc)
                self._request_port_desc(dp)   # periodic port-state resync
            hub.sleep(2)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        msg = ev.msg
        rows = []
        now = time.time()
        for stat in msg.body:
            match = stat.match
            rows.append([
                now, msg.datapath.id, match.get("in_port", ""),
                match.get("eth_type", ""), match.get("ip_proto", ""),
                match.get("ipv4_src", ""), match.get("ipv4_dst", ""),
                match.get("udp_src", ""), match.get("udp_dst", ""),
                stat.packet_count, stat.byte_count,
            ])
        if rows:
            with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(rows)
