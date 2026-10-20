"""firewall 模块测试:规则构造、地址/协议匹配、端口解析。"""

import unittest

from panel_cli import firewall
from panel_cli.firewall import (
    IpOptions,
    ListOptions,
    PortOptions,
    address_eq,
    delete_request,
    docker_mode_zh,
    item_state,
    parse_docker_port,
    parse_target,
    port_rules,
    protocols,
    scopes,
    v240_delete_target,
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


class TestDeleteRequest(unittest.TestCase):
    def _v240_item(self, protected=False):
        rule = {
            "scope": {
                "provider": "iptables",
                "family": "ipv4",
                "table": "filter",
                "chain": "1PANEL_BASIC",
                "direction": "input",
            },
            "protocol": "tcp",
            "destinationPort": "45999",
            "action": "accept",
        }
        observed = {
            "rule": rule,
            "locator": {"provider": "iptables", "scopeKey": "k"},
            "instanceKey": "sha256:abc",
            "parseStatus": "supported",
            "protected": protected,
        }
        return {"rule": rule, "observed": observed, "isWhitelist": protected}

    def test_v240_target_includes_observed(self):
        item = self._v240_item()
        target = v240_delete_target(item)
        self.assertEqual(target["instanceKey"], "sha256:abc")
        self.assertEqual(target["scope"]["chain"], "1PANEL_BASIC")
        self.assertIn("observed", target)

    def test_v240_skips_protected_and_whitelist(self):
        self.assertIsNone(v240_delete_target(self._v240_item(protected=True)))

    def test_delete_request_uses_targets_for_v240(self):
        body = delete_request("v240", [self._v240_item()])
        self.assertIn("targets", body)
        self.assertNotIn("uuids", body)
        self.assertEqual(len(body["targets"]), 1)

    def test_delete_request_uses_uuids_for_v231(self):
        item = {"desired": {"uuid": "rule-uuid"}, "observed": {}}
        body = delete_request("v231", [item])
        self.assertEqual(body, {"uuids": ["rule-uuid"]})

    def test_item_state_covers_v240_fields(self):
        self.assertEqual(item_state({"isWhitelist": True}), "whitelist")
        self.assertEqual(item_state({"observed": {"protected": True}}), "protected")
        self.assertEqual(item_state({"state": "managed"}), "managed")
        self.assertEqual(item_state({}), "-")

    def test_docker_modes_include_v240_accept(self):
        self.assertEqual(docker_mode_zh("accept_sources"), "接受来源")
        self.assertEqual(docker_mode_zh("accept_all"), "接受所有")


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
