#!/usr/bin/env python3
"""Mininet topology with primary and backup paths for SDN rerouting."""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.topo import Topo
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI


class BandwidthTopo(Topo):
    def build(self):
        h1 = self.addHost("h1", ip="10.0.0.1/24")
        h2 = self.addHost("h2", ip="10.0.0.2/24")

        s1 = self.addSwitch("s1", protocols="OpenFlow13")
        s2 = self.addSwitch("s2", protocols="OpenFlow13")
        s3 = self.addSwitch("s3", protocols="OpenFlow13")
        s4 = self.addSwitch("s4", protocols="OpenFlow13")

        # Host access links
        self.addLink(h1, s1, cls=TCLink, bw=20, delay="5ms")
        self.addLink(s4, h2, cls=TCLink, bw=20, delay="5ms")

        # Primary route: s1 -> s2 -> s4
        self.addLink(s1, s2, cls=TCLink, bw=20, delay="5ms")
        self.addLink(s2, s4, cls=TCLink, bw=20, delay="5ms")

        # Backup route: s1 -> s3 -> s4
        self.addLink(s1, s3, cls=TCLink, bw=20, delay="5ms")
        self.addLink(s3, s4, cls=TCLink, bw=20, delay="5ms")


def main():
    topo = BandwidthTopo()

    net = Mininet(
        topo=topo,
        controller=None,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        build=False
    )

    net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653
    )

    net.build()
    net.start()

    info("*** Topology ready: h1=10.0.0.1, h2=10.0.0.2\n")
    info("*** Primary path: h1-s1-s2-s4-h2\n")
    info("*** Backup path: h1-s1-s3-s4-h2\n")
    info("*** Run traffic tests from the mininet> prompt.\n")

    CLI(net)
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    main()
