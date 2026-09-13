"""firewall 模块测试:规则构造、地址/协议匹配、端口解析。"""

import unittest

from panel_cli import firewall
from panel_cli.firewall import (
    IpOptions,
    ListOptions,
    PortOptions,
    address_eq,
    parse_docker_port,
    parse_target,
    port_rules,
    protocols,
    scopes,
    validate_strategy,
)


class TestScopes(unittest.TestCase):
    def test_builds_new_firewall_scopes(self):
        self.assertEqual(len(scopes("iptables")), 6)
        self.assertEqual(len(scopes("firewalld")), 1)
        self.assertEqual(len(scopes("ufw")), 2)
        self.assertEqual(scopes("unknown"), [])


class TestPortRules(unittest.TestCase):
    def test_expands_tcp_udp_port_rule(self):
        opts = PortOptions(protocol="tcp/udp", strategy="accept", source="", desc="test")
        rules = port_rules("iptables", "8080", opts)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["scope"]["chain"], "1PANEL_BASIC")
        self.assertEqual(rules[0]["protocol"], "tcp")
        self.assertEqual(rules[1]["protocol"], "udp")
        self.assertEqual(rules[0]["destinationPort"], "8080")

    def test_firewalld_inet_family(self):
        opts = PortOptions(protocol="tcp", strategy="accept", source="", desc="")
        rules = port_rules("firewalld", "80", opts)
        self.assertEqual(rules[0]["scope"]["family"], "inet")
        self.assertEqual(rules[0]["scope"]["zone"], "public")


class TestMatching(unittest.TestCase):
    def test_matches_normalized_host_addresses(self):
        self.assertTrue(address_eq("1.2.3.4/32", "1.2.3.4"))
        self.assertTrue(address_eq("2001:db8::1/128", "2001:db8::1"))
        self.assertFalse(address_eq("1.2.3.4", "1.2.3.5"))

    def test_protocol_eq(self):
        self.assertTrue(firewall.protocol_eq("tcp", "tcp/udp"))
        self.assertTrue(firewall.protocol_eq("udp", "tcp/udp"))
        self.assertFalse(firewall.protocol_eq("sctp", "tcp/udp"))

    def test_validate_strategy(self):
        self.assertEqual(validate_strategy("accept"), "accept")
        self.assertEqual(validate_strategy("ALLOW"), "accept")
        self.assertEqual(validate_strategy("deny"), "drop")
        self.assertEqual(validate_strategy("block"), "drop")
        with self.assertRaises(Exception):
            validate_strategy("reject")

    def test_protocols(self):
        self.assertEqual(protocols("tcp"), ["tcp"])
        self.assertEqual(protocols("TCP/UDP"), ["tcp", "udp"])
        with self.assertRaises(Exception):
            protocols("icmp")

    def test_matches_filter(self):
        opts = ListOptions(type="port", info="", strategy="accept")
        rule = {
            "destinationPort": "8080",
            "protocol": "tcp",
            "action": "accept",
            "sourceAddress": "",
            "description": "web",
        }
        item = {"state": "active"}
        self.assertTrue(firewall.matches_filter(rule, item, opts))
        opts_drop = ListOptions(type="port", info="", strategy="drop")
        self.assertFalse(firewall.matches_filter(rule, item, opts_drop))
        opts_addr = ListOptions(type="address", info="", strategy="")
        self.assertFalse(firewall.matches_filter(rule, item, opts_addr))
        opts_info = ListOptions(type="port", info="web", strategy="")
        self.assertTrue(firewall.matches_filter(rule, item, opts_info))
        opts_miss = ListOptions(type="port", info="zzz", strategy="")
        self.assertFalse(firewall.matches_filter(rule, item, opts_miss))


class TestParse(unittest.TestCase):
    def test_parse_docker_port(self):
        self.assertEqual(parse_docker_port("8080"), ("", 8080))
        self.assertEqual(parse_docker_port("0.0.0.0:8080"), ("0.0.0.0", 8080))
        self.assertEqual(parse_docker_port("[::]:8080"), ("::", 8080))
        with self.assertRaises(Exception):
            parse_docker_port("abc")
        with self.assertRaises(Exception):
            parse_docker_port("host:xyz")

    def test_parse_target(self):
        self.assertEqual(parse_target("10.0.0.2:8080"), ("10.0.0.2", "8080"))
        with self.assertRaises(Exception):
            parse_target("10.0.0.2")


if __name__ == "__main__":
    unittest.main()
