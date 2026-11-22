"""The computer under the sessions: what it is and how loaded it is (cairn/machine.py)."""

import threading
import time
import unittest
from unittest import mock

from cairn import machine

GB = 2**30


class LoadLevelTests(unittest.TestCase):
    def test_idle_is_light_and_gives_no_reasons(self):
        self.assertEqual(machine.load_level(12, 40, 35), ("light", []))

    def test_a_busy_processor_or_fuller_memory_is_medium(self):
        self.assertEqual(machine.load_level(50, 40), ("medium", ["CPU 50% busy"]))
        self.assertEqual(machine.load_level(10, 75), ("medium", ["memory 75% full"]))
        self.assertEqual(machine.load_level(49, 74), ("light", []))

    def test_flat_out_is_heavy_and_lists_the_worst_first(self):
        self.assertEqual(machine.load_level(85, 60), ("heavy", ["CPU 85% busy"]))
        self.assertEqual(machine.load_level(60, 94), ("heavy", ["memory 94% full", "CPU 60% busy"]))
        level, reasons = machine.load_level(20, 50, 92)
        self.assertEqual(level, "heavy")
        self.assertIn("92%", reasons[0])

    def test_a_missing_reading_is_left_out_rather_than_guessed(self):
        self.assertEqual(machine.load_level(None, 91), ("heavy", ["memory 91% full"]))
        self.assertEqual(machine.load_level(None, None), ("light", []))


class NameTests(unittest.TestCase):
    def test_processor_names_lose_the_legal_marks(self):
        self.assertEqual(machine.clean_cpu_name("Intel(R) Core(TM) Ultra 7 155H"), "Intel Core Ultra 7 155H")
        self.assertEqual(machine.clean_cpu_name("Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz"), "Intel Core i7-9750H")
        self.assertEqual(machine.clean_cpu_name("AMD Ryzen 7 5800X 8-Core Processor"), "AMD Ryzen 7 5800X")
        self.assertEqual(machine.clean_cpu_name(None), "")

    def test_the_brand_is_said_once(self):
        self.assertEqual(
            machine.machine_name("ASUSTeK COMPUTER INC.", "ASUS VIVOBOOK X1_X1"),
            "ASUS VIVOBOOK X1",
        )
        self.assertEqual(machine.machine_name("LENOVO", "ThinkPad X1 Carbon"), "LENOVO ThinkPad X1 Carbon")
        self.assertEqual(machine.machine_name("Dell Inc.", "Dell XPS 13"), "Dell XPS 13")

    def test_placeholders_from_a_self_built_pc_are_dropped(self):
        self.assertEqual(machine.machine_name("System manufacturer", "System Product Name"), "")
        self.assertEqual(machine.machine_name("Gigabyte", "To Be Filled By O.E.M."), "Gigabyte")
        self.assertEqual(machine.machine_name("", ""), "")


class ProcessTests(unittest.TestCase):
    def test_copies_of_one_program_count_as_one_app(self):
        rows = [
            (1, "python.exe", 1 * GB, 30.0),
            (2, "python.exe", 1 * GB, 14.0),
            (3, "llama-server.exe", 12 * GB, 5.0),
            (4, "notepad.exe", GB // 10, 0.0),
        ]
        previous = {1: 10.0, 2: 10.0, 3: 5.0}
        # 10 seconds on 4 cores is 40 processor-seconds: python used 24 of them.
        groups = machine.group_processes(rows, previous, 10, 4)
        self.assertEqual([g["name"] for g in groups], ["llama-server", "python", "notepad"])
        python = groups[1]
        self.assertEqual((python["count"], python["ram_gb"], python["cpu_pct"]), (2, 2.0, 60))
        self.assertEqual(machine.top_text(python), "python x2: 2.0 GB, 60% CPU")
        self.assertEqual(machine.top_text(groups[0]), "llama-server: 12.0 GB")

    def test_uptime_reads_in_minutes_then_hours_then_hours_with_days(self):
        self.assertEqual(machine.uptime_text(12 * 60 + 59), "up 12 min since restart")
        self.assertEqual(machine.uptime_text(5 * 3600 + 3599), "up 5 h since restart")
        self.assertEqual(machine.uptime_text(47 * 3600), "up 47 h since restart")
        self.assertEqual(machine.uptime_text(76 * 3600 + 60), "up 76 h (3 d 4 h) since restart")
        self.assertEqual(machine.uptime_text(None), "")
        self.assertEqual(machine.uptime_text(-1), "")

    def test_a_first_look_has_memory_but_no_processor_share(self):
        groups = machine.group_processes([(1, "node.exe", 2 * GB, 99.0)], {}, 0, 8)
        self.assertEqual(groups, [{"name": "node", "count": 1, "ram_gb": 2.0, "cpu_pct": 0}])

    def test_a_small_app_that_eats_the_processor_still_shows(self):
        rows = [(pid, f"big{pid}.exe", (10 - pid) * GB, 0.0) for pid in range(1, 6)]
        rows.append((9, "spinner.exe", GB // 100, 8.0))
        groups = machine.group_processes(rows, {9: 0.0}, 10, 1, limit=4)
        self.assertEqual(len(groups), 5)
        self.assertEqual(groups[-1]["name"], "spinner")
        self.assertEqual(groups[-1]["cpu_pct"], 80)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        saved = (machine._specs, list(machine._cpu_samples), dict(machine._processes))
        self.addCleanup(self.restore, saved)
        machine._specs = None
        machine._cpu_samples.clear()
        machine._processes.update(at=0.0, times={}, top=[])

    def restore(self, saved):
        machine._specs = saved[0]
        machine._cpu_samples.clear()
        machine._cpu_samples.extend(saved[1])
        machine._processes.clear()
        machine._processes.update(saved[2])

    def patched(self, memory, cpu_times):
        registry = {"SystemManufacturer": "ACME Corp", "SystemProductName": "Roadrunner 14", "ProcessorNameString": "Intel(R) Core(TM) i5 CPU @ 2.40GHz"}
        return (
            mock.patch.object(machine, "_memory", return_value=memory),
            mock.patch.object(machine, "_cpu_times", side_effect=cpu_times),
            mock.patch.object(machine, "_process_rows", return_value=[(1, "python.exe", 3 * GB, 1.0)]),
            mock.patch.object(machine, "_power", return_value="on battery 62%"),
            mock.patch.object(machine, "_uptime_seconds", return_value=76 * 3600.0),
            mock.patch.object(machine, "_registry", side_effect=lambda key, name: registry[name]),
            mock.patch.object(machine.time, "sleep"),
            mock.patch.object(machine, "IS_MAC", False),  # the registry stands in for the model on any system
            mock.patch.object(machine, "network_soon", return_value=None),  # the network has tests of its own, off the network
            mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": ""}),
        )

    def test_a_reading_has_the_specs_the_load_and_what_uses_it(self):
        memory = {"total": 16 * GB, "free": 1 * GB, "ram_pct": 94, "commit_pct": 70}
        patches = self.patched(memory, [(100, 1000), (190, 1100)])
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        reading = machine.snapshot()
        self.assertEqual((reading["name"], reading["cpu"], reading["ram_gb"]), ("ACME Roadrunner 14", "Intel Core i5", 16.0))
        self.assertGreater(reading["cores"], 0)
        self.assertEqual((reading["cpu_pct"], reading["ram_pct"], reading["ram_free_gb"]), (90, 94, 1.0))
        self.assertEqual(reading["level"], "heavy")
        self.assertEqual(reading["reasons"], ["CPU 90% busy", "memory 94% full"])
        self.assertEqual(reading["power"], "on battery 62%")
        self.assertEqual(reading["uptime"], "up 76 h (3 d 4 h) since restart")
        self.assertEqual(reading["top"], ["python: 3.0 GB"])
        self.assertRegex(reading["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d$")
        # Nothing that names the person or this one computer. The network it is on comes under "net".
        self.assertEqual(
            set(reading),
            {"name", "cpu", "cores", "ram_gb", "at", "cpu_pct", "ram_pct", "ram_free_gb", "commit_pct", "power", "uptime", "level", "reasons", "top", "net"},
        )
        self.assertIsNone(reading["net"])

    def test_no_reading_where_memory_cannot_be_read(self):
        patches = self.patched(None, [(100, 1000)])
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.assertIsNone(machine.snapshot())

    def test_a_failing_reader_never_raises(self):
        with mock.patch.object(machine, "_memory", side_effect=OSError("denied")):
            self.assertIsNone(machine.snapshot())


# What a MacBook Pro on macOS 15.7 printed on an iPhone's hotspot (2026-11-22), cut to the part that is read.
MAC_AIRPORT = """Wi-Fi:

      Software Versions:
          CoreWLAN: 16.0 (1657)
      Interfaces:
        en0:
          Card Type: Wi-Fi  (0x14E4, 0x870)
          Supported PHY Modes: 802.11 a/b/g/n/ac
          Status: Connected
          Current Network Information:
            <redacted>:
              PHY Mode: 802.11ac
              Channel: 149 (5GHz, 20MHz)
              Country Code: ID
              Network Type: Infrastructure
              Security: WPA2/WPA3 Personal
              Signal / Noise: -37 dBm / -95 dBm
              Transmit Rate: 192
              MCS Index: 9
        awdl0:
          MAC Address: da:93:4a:8b:9a:32
          Current Network Information:
              Network Type: Infrastructure
"""

MAC_AIRPORT_NAMED = """Wi-Fi:
      Interfaces:
        en0:
          Status: Connected
          Current Network Information:
            Home: 5G:
              PHY Mode: 802.11ax
              Channel: 6 (2GHz, 20MHz)
              Security: WPA3 Personal
              Signal / Noise: -71 dBm / -90 dBm
              Transmit Rate: 86
          Other Local Wi-Fi Networks:
            Neighbour:
              PHY Mode: 802.11n
              Channel: 11 (2GHz, 20MHz)
"""

MAC_AIRPORT_OFF = """Wi-Fi:
      Interfaces:
        en0:
          Card Type: Wi-Fi  (0x14E4, 0x870)
          Status: Off
"""

WIN_WLAN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6E AX211 160MHz
    GUID                   : 0f1e2d3c-0000-0000-0000-000000000000
    Physical address       : 00:11:22:33:44:55
    Interface type         : Primary
    State                  : connected
    SSID                   : Office 5G
    AP BSSID               : 66:77:88:99:aa:bb
    Band                   : 5 GHz
    Channel                : 44
    Network type           : Infrastructure
    Radio type             : 802.11ax
    Authentication         : WPA2-Personal
    Cipher                 : CCMP
    Connection mode        : Auto Connect
    Receive rate (Mbps)    : 1201
    Transmit rate (Mbps)   : 864.7
    Signal                 : 86%
    Profile                : Office 5G

    Hosted network status  : Not available
"""

WIN_WLAN_OFF = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6E AX211 160MHz
    State                  : disconnected
    Radio status           : Hardware On
                             Software On
"""

WIN_WLAN_LOCATION = """Network shell commands need location permission to access WLAN information. Turn on Location services on the Location page in Privacy & security settings.
"""

WIN_INTERFACES = """
Admin State    State          Type             Interface Name
-------------------------------------------------------------------------
Enabled        Connected      Dedicated        Tailscale
Enabled        Disconnected   Dedicated        Ethernet
Enabled        Connected      Dedicated        vEthernet (WSL)
Enabled        Connected      Dedicated        Wi-Fi
"""

MAC_ROUTE = """   route to: default
destination: default
       mask: default
    gateway: 172.20.10.1
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
"""

MAC_PORTS = """
Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: 82:47:69:89:0c:01

Hardware Port: Wi-Fi
Device: en0
Ethernet Address: a4:83:e7:39:10:71

Hardware Port: USB 10/100/1000 LAN
Device: en5
Ethernet Address: 00:e0:4c:68:00:01

VLAN Configurations
===================
"""

MAC_PACKET = """op = BOOTREPLY
htype = 1
yiaddr = 172.20.10.11
siaddr = 172.20.10.1
sname = iPhone
file =
options:
Options count is 7
dhcp_message_type (uint8): ACK 0x5
server_identifier (ip): 172.20.10.1
subnet_mask (ip): 255.255.255.240
router (ip_mult): {172.20.10.1}
domain_name_server (ip_mult): {172.20.10.1}
end (none):
"""

MAC_NETSTAT = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
en0        1500  <Link#9>    6e:08:05:56:8b:1d 36275409     0 38924015646 19572288     0 10937965597     0
en0        1500  fe80::103f: fe80:9::103f:aa14 36275409     - 38924015646 19572288     - 10937965597     -
en0        1500  172.20.10/28  172.20.10.11    36275409     - 38924015646 19572288     - 10937965597     -
"""

WIN_NETSTAT = """Interface Statistics

                           Received            Sent

Bytes                    3221225472      1073741824
Unicast packets             1234567         7654321
Non-unicast packets            4321            1234
Discards                          0               0
Errors                            0               0
Unknown protocols                 0
"""


class NetworkParseTests(unittest.TestCase):
    """What the system's tools print about the network, read on any computer."""

    def test_a_mac_whose_system_keeps_the_name_still_gives_the_rest(self):
        self.assertEqual(machine.parse_airport(MAC_AIRPORT), {
            "connected": True, "name": "", "name_hidden": True, "radio": "802.11ac", "channel": 149, "band": "5 GHz",
            "security": "WPA2/WPA3 Personal", "signal_dbm": -37, "link_mbps": 192.0,
        })

    def test_a_mac_that_is_told_the_name_reads_it_even_with_a_colon_in_it(self):
        found = machine.parse_airport(MAC_AIRPORT_NAMED)
        self.assertEqual((found["name"], found["name_hidden"]), ("Home: 5G", False))
        self.assertEqual((found["radio"], found["band"], found["channel"], found["signal_dbm"], found["link_mbps"]), ("802.11ax", "2.4 GHz", 6, -71, 86.0))

    def test_a_mac_on_no_wifi_gives_nothing(self):
        self.assertEqual(machine.parse_airport(MAC_AIRPORT_OFF), {})
        self.assertEqual(machine.parse_airport(""), {})
        self.assertEqual(machine.parse_airport(None), {})

    def test_windows_wifi_has_the_name_the_signal_and_the_transmit_rate(self):
        self.assertEqual(machine.parse_netsh_wlan(WIN_WLAN), {
            "connected": True, "name": "Office 5G", "name_hidden": False, "radio": "802.11ax", "band": "5 GHz", "channel": 44,
            "security": "WPA2-Personal", "signal_pct": 86, "link_mbps": 864.7,
        })

    def test_windows_without_wifi_or_without_location_permission(self):
        self.assertEqual(machine.parse_netsh_wlan(WIN_WLAN_OFF), {})
        self.assertEqual(machine.parse_netsh_wlan(""), {})
        self.assertEqual(machine.parse_netsh_wlan(WIN_WLAN_LOCATION), {"name_hidden": True})

    def test_windows_picks_the_wifi_then_a_real_cable_and_never_a_tunnel(self):
        connected = machine.parse_netsh_interfaces(WIN_INTERFACES)
        self.assertEqual(connected, ["Tailscale", "vEthernet (WSL)", "Wi-Fi"])
        self.assertEqual(machine.windows_link(machine.parse_netsh_wlan(WIN_WLAN), [])["name"], "Office 5G")
        self.assertEqual(machine.windows_link({}, connected), {"kind": "wifi", "name": "", "name_hidden": False})
        self.assertEqual(machine.windows_link({"name_hidden": True}, connected), {"kind": "wifi", "name": "", "name_hidden": True, "hidden_by": "Windows"})
        self.assertEqual(machine.windows_link({}, ["Tailscale", "Ethernet 2"]), {"kind": "wired", "via": "Ethernet 2"})
        self.assertEqual(machine.windows_link({}, ["Tailscale", "vEthernet (WSL)"]), {"kind": "none"})
        self.assertEqual(machine.windows_link({}, []), {"kind": "none"})

    def test_a_mac_says_which_port_it_goes_out_through(self):
        self.assertEqual(machine.parse_default_route(MAC_ROUTE), "en0")
        self.assertEqual(machine.parse_default_route("route: writing to routing socket: not in table\n"), "")
        self.assertEqual(machine.parse_hardware_ports(MAC_PORTS), {"bridge0": "Thunderbolt Bridge", "en0": "Wi-Fi", "en5": "USB 10/100/1000 LAN"})

    def test_a_phones_hotspot_is_told_from_the_address_it_hands_out(self):
        self.assertEqual(machine.hotspot_text(MAC_PACKET), "iPhone hotspot")
        self.assertEqual(machine.hotspot_text("router (ip_mult): {192.168.43.1}\n"), "phone hotspot")
        self.assertEqual(machine.hotspot_text("sname = \nrouter (ip_mult): {192.168.1.1}\n"), "")
        self.assertEqual(machine.hotspot_text(""), "")

    def test_bytes_in_and_out_from_either_system(self):
        self.assertEqual(machine.parse_netstat_bytes(MAC_NETSTAT), (38924015646, 10937965597))
        self.assertEqual(machine.parse_netstat_bytes(WIN_NETSTAT), (3221225472, 1073741824))
        self.assertIsNone(machine.parse_netstat_bytes(""))
        self.assertIsNone(machine.parse_netstat_bytes("Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll\n"))

    def test_the_rate_in_use_is_the_bytes_between_two_looks(self):
        self.assertEqual(machine.traffic_rate((0.0, 0, 0), (10.0, 2_500_000, 250_000)), (2.0, 0.2))
        self.assertEqual(machine.traffic_rate(None, (10.0, 5, 5)), (None, None))
        self.assertEqual(machine.traffic_rate((0.0, 900, 900), (10.0, 5, 5)), (None, None), "the counters started again")
        self.assertEqual(machine.traffic_rate((0.0, 0, 0), (0.5, 5, 5)), (None, None), "too close together to say")

    def test_signal_words_and_rates(self):
        self.assertEqual([machine.signal_word(dbm=value) for value in (-37, -60, -70, -80)], ["strong", "good", "fair", "weak"])
        self.assertEqual([machine.signal_word(pct=value) for value in (95, 86, 55, 30)], ["strong", "good", "fair", "weak"])
        self.assertEqual(machine.signal_word(), "")
        self.assertEqual([machine.mbit(value) for value in (0.23, 7.46, 12.4, 192.0)], ["0.2", "7.5", "12", "192"])

    def test_the_level_is_what_an_online_assistant_would_feel(self):
        self.assertEqual(machine.net_level("ok", 85), ("good", ""))
        self.assertEqual(machine.net_level("ok", 499), ("good", ""))
        self.assertEqual(machine.net_level("ok", 500), ("slow", "the internet takes 500 ms to answer"))
        self.assertEqual(machine.net_level("ok", None)[0], "good")
        self.assertEqual(machine.net_level("none", None), ("offline", "the internet does not answer"))
        self.assertEqual(machine.net_level("sign-in", None)[0], "offline")

    def test_the_two_lines_the_page_shows(self):
        hotspot = {"kind": "wifi", "name": "", "name_hidden": True, "hidden_by": "macOS", "via": "iPhone hotspot", "radio": "802.11ac", "band": "5 GHz",
                   "signal": "strong", "signal_dbm": -37, "link_mbps": 192.0}
        self.assertEqual(machine.link_text(hotspot), "Wi-Fi (macOS keeps its name from apps) · iPhone hotspot · 802.11ac 5 GHz · signal strong (-37 dBm) · link 192 Mbit/s")
        office = {"kind": "wifi", "name": "Office 5G", "name_hidden": False, "radio": "802.11ax", "band": "5 GHz", "signal": "good", "signal_pct": 86, "link_mbps": 864.7}
        self.assertEqual(machine.link_text(office), "Wi-Fi Office 5G · 802.11ax 5 GHz · signal good (86%) · link 865 Mbit/s")
        self.assertEqual(machine.link_text({"kind": "wifi", "name": "", "name_hidden": False}), "Wi-Fi")
        self.assertEqual(machine.link_text({"kind": "wired", "via": "USB 10/100/1000 LAN"}), "Cable (USB 10/100/1000 LAN)")
        self.assertEqual(machine.link_text({"kind": "other"}), "Connected")
        self.assertEqual(machine.link_text({"kind": "none"}), "Not on a network")
        self.assertEqual(machine.reach_text({"internet": "ok", "delay_ms": 85, "down_mbps": 1.2, "up_mbps": 0.1}), "Internet answers in 85 ms · in use now 1.2 down, 0.1 up Mbit/s")
        self.assertEqual(machine.reach_text({"internet": "ok", "delay_ms": 85, "down_mbps": None, "up_mbps": None}), "Internet answers in 85 ms")
        self.assertEqual(machine.reach_text({"internet": "none"}), "No internet")
        self.assertEqual(machine.reach_text({"internet": "sign-in"}), "This network asks for a sign-in page first")
        self.assertEqual(machine.speed_text({"down_mbps": 12.0, "at": "2026-11-22T10:42"}), "measured 12 Mbit/s down at 10:42")
        self.assertEqual(machine.speed_text(None), "")


class FakeReply:
    """What urlopen hands back: a status and a body that arrives in pieces."""

    def __init__(self, status=204, pieces=()):
        self.status = status
        self.pieces = list(pieces)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _size=-1):
        return self.pieces.pop(0) if self.pieces else b""

    read1 = read


class NetworkReadingTests(unittest.TestCase):
    """The reading the page gets, with the system's tools and the internet replaced: no test goes on the network."""

    def setUp(self):
        saved = {**machine._net, "delays": list(machine._net["delays"])}
        self.addCleanup(self.restore, saved)
        machine._net.update(at=float("-inf"), value=None, thread=None, wifi_at=float("-inf"), wifi={}, bytes=None, speed=None)
        machine._net["delays"].clear()

    def restore(self, saved):
        delays = saved.pop("delays")
        machine._net.update(saved)
        machine._net["delays"].clear()
        machine._net["delays"].extend(delays)

    def on_a_mac(self, probe, link=None, counted=(1000, 1000)):
        link = link or {"kind": "wifi", **machine.parse_airport(MAC_AIRPORT), "device": "en0", "via": "iPhone hotspot", "hidden_by": "macOS"}
        for patch in (
            mock.patch.object(machine, "IS_MAC", True),
            mock.patch.object(machine, "_mac_link", return_value=link),
            mock.patch.object(machine, "_bytes_now", return_value=counted),
            mock.patch.object(machine, "_probe_internet", side_effect=probe),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_reading_says_which_network_and_how_the_internet_answers(self):
        self.on_a_mac([("ok", 85)])
        reading = machine.network()
        self.assertEqual(reading["link"], "Wi-Fi (macOS keeps its name from apps) · iPhone hotspot · 802.11ac 5 GHz · signal strong (-37 dBm) · link 192 Mbit/s")
        self.assertEqual((reading["reach"], reading["level"], reading["why"], reading["speed"]), ("Internet answers in 85 ms", "good", "", ""))
        self.assertEqual((reading["kind"], reading["name"], reading["name_hidden"], reading["via"], reading["signal"]), ("wifi", "", True, "iPhone hotspot", "strong"))
        self.assertRegex(reading["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d$")
        # The name of the Wi-Fi is the one thing that says where the computer is: no address, no access point id.
        self.assertEqual(
            set(reading),
            {"kind", "name", "name_hidden", "hidden_by", "via", "radio", "band", "channel", "security", "signal", "signal_dbm", "link_mbps",
             "internet", "delay_ms", "down_mbps", "up_mbps", "level", "why", "at", "link", "reach", "speed"},
        )

    def test_one_slow_answer_does_not_flip_the_level_but_the_middle_of_three_does(self):
        self.on_a_mac([("ok", 90), ("ok", 95), ("ok", 2400), ("ok", 1800), ("none", None)])
        self.assertEqual([machine.network()["level"] for _ in range(2)], ["good", "good"])
        spike = machine.network()
        self.assertEqual((spike["delay_ms"], spike["reach"]), (2400, "Internet answers in 2400 ms"), "the figure shown is the newest")
        self.assertEqual(spike["level"], "good", "one slow answer among three is a hiccup")
        slow = machine.network()
        self.assertEqual((slow["level"], slow["why"]), ("slow", "the internet takes 1800 ms to answer"))
        gone = machine.network()
        self.assertEqual((gone["level"], gone["reach"], gone["delay_ms"]), ("offline", "No internet", None), "no answer is offline at once")

    def test_the_rate_in_use_needs_a_second_look(self):
        self.on_a_mac([("ok", 85), ("ok", 85)])
        first = machine.network()
        self.assertEqual((first["down_mbps"], first["up_mbps"]), (None, None))
        machine._net["bytes"] = (time.monotonic() - 10, 1000, 1000)  # the first look, ten seconds ago
        machine._bytes_now.return_value = (2_501_000, 251_000)
        second = machine.network()
        self.assertEqual((second["down_mbps"], second["up_mbps"]), (2.0, 0.2))
        self.assertTrue(second["reach"].endswith("in use now 2.0 down, 0.2 up Mbit/s"))

    def test_off_every_network(self):
        self.on_a_mac([("none", None)], link={"kind": "none"}, counted=None)
        reading = machine.network()
        self.assertEqual((reading["link"], reading["reach"], reading["level"], reading["signal"]), ("Not on a network", "No internet", "offline", ""))

    def test_a_failing_reader_never_raises_and_other_systems_get_nothing(self):
        self.on_a_mac(OSError("denied"))
        self.assertIsNone(machine.network())
        with mock.patch.object(machine, "IS_MAC", False), mock.patch.object(machine.os, "name", "posix"):
            self.assertIsNone(machine.network())

    def test_the_internet_is_asked_with_one_small_request(self):
        with mock.patch.object(machine.urllib.request, "urlopen", return_value=FakeReply(204)) as urlopen:
            state, delay = machine._probe_internet()
        self.assertEqual(state, "ok")
        self.assertGreaterEqual(delay, 0)
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(urlopen.call_args.args[0].full_url.startswith("http://"), "plain http, so a sign-in page can answer in its place")
        with mock.patch.object(machine.urllib.request, "urlopen", return_value=FakeReply(200, [b"<html>Sign in</html>"])):
            self.assertEqual(machine._probe_internet(), ("sign-in", None))
        with mock.patch.object(machine.urllib.request, "urlopen", side_effect=machine.urllib.error.URLError("no route")) as urlopen:
            self.assertEqual(machine._probe_internet(), ("none", None))
        self.assertEqual(urlopen.call_count, len(machine.NET_PROBES), "every page is tried before it is called offline")

    def test_the_page_never_waits_on_the_network(self):
        release = threading.Event()
        calls = []

        def slow():
            calls.append(1)
            release.wait(5)
            return {"level": "slow"}

        with mock.patch.object(machine, "network", slow):
            started = time.monotonic()
            self.assertIsNone(machine.network_soon(0.05))
            self.assertIsNone(machine.network_soon(0.05))
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(len(calls), 1)  # one look at a time, however often the page asks
            release.set()
            for _ in range(200):
                if machine._net["thread"] is None:
                    break
                time.sleep(0.01)
            self.assertEqual(machine.network_soon(), {"level": "slow"})
            self.assertEqual(len(calls), 1)  # fresh: served from the last look

    def test_a_speed_measurement_counts_what_came_and_stops_on_time(self):
        pieces = [b"x" * 500_000] * 40  # more than the time allows
        clock = iter([0.0] + [0.5 * step for step in range(1, 60)])
        with (
            mock.patch.object(machine.urllib.request, "urlopen", return_value=FakeReply(200, pieces)),
            mock.patch.object(machine.time, "perf_counter", side_effect=lambda: next(clock)),
        ):
            speed = machine.measure_speed()
        self.assertEqual((speed["mb"], speed["seconds"], speed["down_mbps"]), (4.0, 4.0, 8.0), "4 MB in the 4 seconds it may take")
        self.assertRegex(speed["text"], r"^measured 8\.0 Mbit/s down at \d\d:\d\d$")
        self.assertEqual(machine.speed_text(machine._net["speed"]), speed["text"], "the next reading carries it")

    def test_a_measured_speed_shows_on_the_readings_the_page_already_holds(self):
        saved = dict(machine._soon)
        self.addCleanup(machine._soon.update, saved)
        machine._net["value"] = {"level": "good", "speed": ""}
        machine._soon["value"] = {"level": "light", "net": machine._net["value"]}
        with mock.patch.object(machine.urllib.request, "urlopen", return_value=FakeReply(200, [b"x" * 1_000_000])):
            speed = machine.measure_speed()
        self.assertEqual(machine._net["value"], {"level": "good", "speed": speed["text"]})
        self.assertEqual(machine._soon["value"], {"level": "light", "net": {"level": "good", "speed": speed["text"]}})

    def test_a_speed_measurement_with_no_internet_says_so(self):
        with mock.patch.object(machine.urllib.request, "urlopen", side_effect=machine.urllib.error.URLError("no route")):
            with self.assertRaises(OSError):
                machine.measure_speed()
        with mock.patch.object(machine.urllib.request, "urlopen", return_value=FakeReply(200, [])):
            with self.assertRaises(OSError):
                machine.measure_speed()
        self.assertIsNone(machine._net["speed"])


class SnapshotSoonTests(unittest.TestCase):
    """The live page must not wait on a reading: `snapshot` is replaced by a slow one."""

    def setUp(self):
        saved = dict(machine._soon)
        self.addCleanup(machine._soon.update, saved)
        machine._soon.update(at=float("-inf"), value=None, thread=None)

    def _settle(self):
        for _ in range(200):
            if machine._soon["thread"] is None:
                return
            time.sleep(0.01)
        self.fail("background reading did not finish")

    def test_a_slow_reading_does_not_block_and_arrives_later(self):
        release = threading.Event()
        calls = []

        def slow():
            calls.append(1)
            release.wait(5)
            return {"level": "heavy"}

        with mock.patch.object(machine, "snapshot", slow), mock.patch.object(machine, "SOON_FIRST_WAIT_SECONDS", 0.05):
            started = time.monotonic()
            self.assertIsNone(machine.snapshot_soon())
            self.assertIsNone(machine.snapshot_soon())
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(len(calls), 1)  # one run at a time, however often the page asks
            release.set()
            self._settle()
            self.assertEqual(machine.snapshot_soon(), {"level": "heavy"})
            self.assertEqual(len(calls), 1)  # fresh: served from the last reading

    def test_a_failed_reading_keeps_the_last_one(self):
        with mock.patch.object(machine, "snapshot", return_value={"level": "light"}):
            self.assertEqual(machine.snapshot_soon(), {"level": "light"})
            self._settle()
        machine._soon["at"] = float("-inf")  # stale: the next call reads again
        with mock.patch.object(machine, "snapshot", return_value=None):
            self.assertEqual(machine.snapshot_soon(), {"level": "light"})
            self._settle()


VM_STAT = """Mach Virtual Memory Statistics: (page size of 4096 bytes)
Pages free:                              100000.
Pages active:                           1500000.
Pages inactive:                          800000.
Pages speculative:                       148576.
Pages throttled:                              0.
Pages wired down:                        700000.
Pages purgeable:                          20000.
"Translation faults":                 123456789.
Pages occupied by compressor:            945728.
"""

PS = """    1  12345   1:02.50 /sbin/launchd
  412 524288  83:20.00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  413 262144   0:10.00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  900   2048 1-00:00:10 opencode
garbage line
"""


class MacTests(unittest.TestCase):
    """macOS readings are parsed from what its own tools print. No Mac runs these tests: the samples are the
    documented output formats, so a real Mac is still the last word."""

    def test_the_model_reads_with_spaces(self):
        self.assertEqual(machine.mac_model_name("MacBookPro15,1\n"), "MacBook Pro 15,1")
        self.assertEqual(machine.mac_model_name("Macmini8,1"), "Mac mini 8,1")
        self.assertEqual(machine.mac_model_name("Mac14,2"), "Mac 14,2")
        self.assertEqual(machine.mac_model_name("VMware7,1"), "VMware7,1")
        self.assertEqual(machine.mac_model_name(""), "")

    def test_memory_counts_cached_pages_as_free(self):
        total = 16 * GB  # 4,194,304 pages of 4096 bytes
        memory = machine.parse_vm_stat(VM_STAT, total)
        self.assertEqual(memory["free"], (100000 + 800000 + 148576) * 4096)
        self.assertEqual((memory["total"], memory["ram_pct"], memory["commit_pct"]), (total, 75, None))
        self.assertIsNone(machine.parse_vm_stat("", total))
        self.assertIsNone(machine.parse_vm_stat(VM_STAT, 0))

    def test_power_and_processes(self):
        battery = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t62%; discharging; 3:10 remaining present: true\n"
        self.assertEqual(machine.parse_pmset(battery), "on battery 62%")
        self.assertEqual(machine.parse_pmset(battery.replace("Battery Power", "AC Power")), "plugged in 62%")
        self.assertEqual(machine.parse_pmset("Now drawing from 'AC Power'\n"), "", "a Mac mini has no battery")
        self.assertEqual(machine.clock_seconds("1:02.50"), 62.5)
        self.assertEqual(machine.clock_seconds("12:34:56"), 45296)
        self.assertEqual(machine.clock_seconds("nonsense"), 0.0)
        rows = machine.parse_ps(PS)
        self.assertEqual(rows[0], (1, "launchd", 12345 * 1024, 62.5))
        self.assertEqual(rows[1][:3], (412, "Google Chrome", 524288 * 1024), "a path with spaces keeps its last part")
        self.assertEqual(rows[3], (900, "opencode", 2048 * 1024, 86410.0))
        self.assertEqual(len(rows), 4)
        groups = machine.group_processes(rows, {}, 0, 8)
        self.assertEqual(machine.top_text(groups[0]), "Google Chrome x2: 0.8 GB")

    def test_a_mac_reading_from_its_tools(self):
        answers = {
            "hw.memsize": str(16 * GB), "kern.boottime": "{ sec = 1000, usec = 0 } Thu Jan  1 00:16:40 1970",
            "hw.model": "MacBookPro15,1", "machdep.cpu.brand_string": "Intel(R) Core(TM) i9-9880H CPU @ 2.30GHz",
        }

        def run(command):
            if command[0] == "sysctl":
                return answers[command[-1]] + "\n"
            return {"vm_stat": VM_STAT, "ps": PS, "pmset": "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged;\n"}[command[0]]

        saved = (machine._specs, machine._mac_total)
        machine._specs, machine._mac_total = None, 0
        self.addCleanup(lambda: (setattr(machine, "_specs", saved[0]), setattr(machine, "_mac_total", saved[1])))
        with mock.patch.object(machine, "IS_MAC", True), mock.patch.object(machine, "_run", side_effect=run), \
                mock.patch.object(machine.time, "time", return_value=1000 + 7200.0), \
                mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": ""}):
            self.assertEqual(machine._mac_memory()["ram_pct"], 75)
            self.assertEqual(machine._mac_uptime_seconds(), 7200.0)
            with mock.patch.object(machine, "_memory", side_effect=machine._mac_memory):
                specs = machine.specs()
                self.assertEqual((specs["name"], specs["cpu"], specs["ram_gb"]), ("MacBook Pro 15,1", "Intel Core i9-9880H", 16.0))
                self.assertEqual(machine.device_name(), "MacBook Pro 15,1")

    def test_a_tool_that_is_missing_gives_no_reading(self):
        with mock.patch.object(machine.subprocess, "run", side_effect=FileNotFoundError("no such tool")):
            self.assertEqual(machine._run(["vm_stat"]), "")
        with mock.patch.object(machine, "_run", return_value=""):
            self.assertIsNone(machine.parse_vm_stat(machine._run(["vm_stat"]), 16 * GB))
            self.assertIsNone(machine._mac_uptime_seconds())
            self.assertEqual(machine.parse_ps(machine._run(["ps"])), [])


class DeviceNameTests(unittest.TestCase):
    def test_the_model_names_the_computer_unless_you_name_it(self):
        with mock.patch.object(machine, "specs", return_value={"name": "ACME Roadrunner 14"}):
            with mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": ""}):
                self.assertEqual(machine.device_name(), "ACME Roadrunner 14")
            with mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": "  Work   laptop "}):
                self.assertEqual(machine.device_name(), "Work laptop")
        with mock.patch.dict(machine.os.environ, {"CAIRN_DEVICE": ""}):
            with mock.patch.object(machine, "specs", return_value={"name": ""}):
                self.assertEqual(machine.device_name(), "This computer")
            with mock.patch.object(machine, "specs", side_effect=OSError("denied")):
                self.assertEqual(machine.device_name(), "This computer")


if __name__ == "__main__":
    unittest.main()
