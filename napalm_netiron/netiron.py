#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License.  You may obtain a copy of
# the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
# License for the specific language governing permissions and limitations under
# the License.

""" 
  Extreme-Netiron Driver 

  This driver provides support for Netiron MLXe routers
"""
from netmiko import ConnectHandler
from napalm_base.base import NetworkDriver
from napalm_base.exceptions import ConnectionException, MergeConfigException, \
    ReplaceConfigException, SessionLockedException, CommandErrorException
import napalm_base.helpers

import re

class NetironDriver(NetworkDriver):
    """Napalm Driver for Vendor Extreme/Netiron."""

    def __init__(self, hostname, username, password, timeout=60,
                 optional_args=None):

        if optional_args is None:
            optional_args = {}

        self.device = None
        self.hostname = hostname
        self.username = username
        self.password = password
        self.timeout = timeout
        self.port = optional_args.get('port', 22)

    def open(self):
        try:
            # FIXME: Needs to be changed to extreme_netiron (?)
            self.device = ConnectHandler(device_type='brocade_netiron',
                                         ip=self.hostname,
                                         port=self.port,
                                         username=self.username,
                                         password=self.password,
                                         timeout=self.timeout,
                                         verbose=True)
        except Exception:
            raise ConnectionException("Cannot connect to switch: %s:%s" \
                                          % (self.hostname, self.port))

    def close(self):
        self.device.disconnect()
        return

    def cli(self, commands):
        cli_output = dict()

        if type(commands) is not list:
            raise TypeError('Please enter a valid list of commands!')

        for command in commands:
            output = self.device.send_command(command)
            if 'Invalid input' in output:
                raise ValueError(
                    'Unable to execute command "{}"'.format(command))
            cli_output.setdefault(command, {})
            cli_output[command] = output

        return cli_output

    def get_arp_table(self):

        arp_table = list()

        arp_cmd = 'show arp'
        output = self.device.send_command(arp_cmd)
        output = output.split('\n')
        output = output[7:]

        for line in output:
            fields = line.split()

            if len(fields) == 6:
                num, address, mac, typ, age, interface = fields
                try:
                    if age == 'None':
                        age = 0
                    age = float(age)
                except ValueError:
                    print(
                        "Unable to convert age value to float: {}".format(age)
                        )

                if "None" in mac:
                    mac = "00:00:00:00:00:00"
                else:
                    mac = napalm_base.helpers.mac(mac)

                entry = {
                    'interface': interface,
                    'mac': mac,
                    'ip': address,
                    'age': age
                }
                arp_table.append(entry)

        return arp_table

    def _parse_port_change(self, last_str):

		#(3 days 11:27:46 ago)	
        r1 = re.match("(\d+) days (\d+):(\d+):(\d+)", last_str)
        if r1:
            days = int(r1.group(1))
            hours = int(r1.group(2))
            mins = int(r1.group(3))
            secs = int(r1.group(4))

            return float(secs + (mins*60) + (hours*60*60) + (days*24*60*60))
        else:
        	return (float(-1.0))

    def _get_interface_detail(self, port):

        if port == "mgmt1":
            command = "show interface management1"
        else:
            command = "show interface ethernet {}".format(port)
        output = self.device.send_command(command)
        output = output.split('\n')

        for line in output:
            r0 = re.match(r"\s+Port state change time: \S+\s+\d+\s+\S+\s+\((.*) ago\)", line)
            if r0:
                last_flap = self._parse_port_change(r0.group(1))

            r1 = re.match(r"\s+No port name", line)
            if r1:
                description = ""
        
            r2 = re.match(r"\s+Port name is (.*)", line)
            if r2:
                description = r2.group(1)

            r3 = re.match(r"\s+Hardware is \S+, address is (\S+) (.+)", line)
            if r3:
                mac = r3.group(1)

            r4 = re.match(r"\s+Configured speed (\d+)(G|M)bit,.+", line)
            if r4:
                speed = int(r4.group(1))
                if r4.group(2) == "G":
                    speed = speed*1000

        return [last_flap, description, speed, mac]

    def get_interfaces(self):

        interface_list = {}

        iface_cmd = 'show interface brief wide'
        output = self.device.send_command(iface_cmd)
        output = output.split('\n')
        output = output[2:]

        for line in output:
            fields = line.split()

            if len(line) == 0:
                continue
            elif len(fields) >= 6:
                port, link, state, speed, tag, mac = fields[:6]
            else:
                raise ValueError(u"Unexpected Response from the device")

            # Physical interfaces only
            if re.match("\d+/\d+|mgmt1", port):
                port_detail = self._get_interface_detail(port)

                state = state.lower()
                is_up = bool('forward' in state)

                link = link.lower()
                is_enabled = not bool('disabled' in link)
            	
            else:
                continue

            interface_list[port] = {
                'is_up': is_up,
                'is_enabled': is_enabled,
                'description': unicode(port_detail[1]),
                'last_flapped': float(port_detail[0]),
                'speed': int(port_detail[2]),
                'mac_address': unicode(port_detail[3]),
            }
        return interface_list
 
    def get_interfaces_counters(self):

        cmd = "show statistics"
        lines = self.device.send_command(cmd)
        lines = lines.split('\n')

        counters = {}
        for line in lines:
            port_block = re.match('\s*PORT (\S+) Counters:.*', line)
            if port_block:
                interface = port_block.group(1)
                counters.setdefault(interface, {})
            elif len(line)==0:
                continue
            else:
                octets = re.match(r"\s+InOctets\s+(\d+)\s+OutOctets\s+(\d+)\.*", line)
                if octets:
                   counters[interface]['rx_octets'] = octets.group(1)
                   counters[interface]['tx_octets'] = octets.group(2)
                   continue

                packets = re.match(r"\s+InPkts\s+(\d+)\s+OutPkts\s+(\d+)\.*", line)
                if packets:
                   counters[interface]['rx_unicast_packets'] = packets.group(1)
                   counters[interface]['tx_unicast_packets'] = packets.group(2)
                   continue

                broadcast = re.match(r"\s+InBroadcastPkts\s+(\d+)\s+OutBroadcastPkts\s+(\d+)\.*", line)
                if broadcast:
                   counters[interface]['rx_broadcast_packets'] = broadcast.group(1)
                   counters[interface]['tx_broadcast_packets'] = broadcast.group(2)
                   continue

                multicast = re.match(r"\s+InMulticastPkts\s+(\d+)\s+OutMulticastPkts\s+(\d+)\.*", line)
                if multicast:
                   counters[interface]['rx_multicast_packets'] = multicast.group(1)
                   counters[interface]['tx_multicast_packets'] = multicast.group(2)
                   continue

                error = re.match(r"\s+InErrors\s+(\d+)\s+OutErrors\s+(\d+)\.*", line)
                if error:
                   counters[interface]['rx_errors'] = error.group(1)
                   counters[interface]['tx_errors'] = error.group(2)
                   continue

                discard = re.match(r"\s+InDiscards\s+(\d+)\s+OutDiscards\s+(\d+)\.*", line)
                if discard:
                   counters[interface]['rx_discards'] = discard.group(1)
                   counters[interface]['tx_discards'] = discard.group(2)

        return counters

    def get_mac_address_table(self):

        cmd = "show mac-address"
        lines = self.device.send_command(cmd)
        lines = lines.split('\n')

        mac_address_table = []
        lines = lines[5:]

        for line in lines:
            fields = line.split()

            if len(fields) == 4:
                mac_address, port, age, vlan = fields
            
                is_static = bool('Static' in age)
                mac_address = napalm_base.helpers.mac(mac_address)

                entry = {
                   'mac': mac_address,
                   'interface': unicode(port),
                   'vlan': int(vlan),
                   'active': bool(1),
                   'static': is_static,
                   'moves': None,
                   'last_move': None
                }
                mac_address_table.append(entry)
            
        return mac_address_table
	           
    def get_ntp_stats(self):

        output = self.device.send_command("show ntp associations")
        output = output.split("\n")

        ntp_stats = list()
        output = output[1:-1]

        for line in output:
            fields = line.split()
            if len(line) == 0:
                return {}

            if len(fields) == 9:
                remote, ref, st, when, poll, reach, delay, offset, disp = fields
                synch = "*" in remote

                match = re.search("(\d+\.\d+\.\d+\.\d+)", remote)
                ip = match.group(1)

                when = when if when != '-' else 0

                ntp_stats.append({
                    "remote": unicode(ip),
                    "referenceid": unicode(ref),
                    "synchronized": bool(synch),
                    "stratum": int(st),
                    "type": unicode("NA"),
                    "when": int(when),
                    "hostpoll": int(poll),
                    "reachability": int(reach),
                    "delay": float(delay),
                    "offset": float(offset),
                    "jitter": float(disp)
                })

        return ntp_stats

    def _lldp_detail_parser(self, interface):
        """ Parse a single detailed entry """

        command = "show lldp neighbors detail ports eth {}".format(interface)
        output = self.device.send_command(command)

        chassis_id = re.search(r"\s+\+ Chassis ID \(MAC address\): (\S+)", output).group(1)
        port_id = re.search(r"\s+\+ Port ID \(MAC address\): (\S+)", output, re.MULTILINE).group(1)
        system_name = re.search(r"\s+\+ System name\s+:\s+\"(.+)\"", output).group(1)
        port_description = re.search(r"\s+\+ Port description    :\s+\"(\S+)\"", output).group(1)
        system_capabilities = re.search(r"\s+\+ System capabilities :\s+(.+)", output).group(1)
        enabled_capabilities = re.search(r"\s+Enabled capabilities:\s+(.+)", output).group(1)
        remote_address = re.search(r"\s+\+ Management address \(IPv4\):\s+(.+)", output).group(1)

        return [port_id, port_description, chassis_id, system_name, 
            system_capabilities, enabled_capabilities, remote_address]

    def get_lldp_neighbors(self):

        command = 'show lldp neighbors detail'
        lines = self.device.send_command(command)
        lines = lines.split("\n")
        lines = lines[1:]

        lldp = {}

        local_port = port_desc = sys_name = ""
        new_port = 0

        for line in lines:
            # Cannot distinguish what is portid and/or portdesc from "show lldp neighbors"
            # Need to parse "show lldp neighbors detail" instead...
            r1 = re.match(r'^Local port: (\S+)',line)
            if r1:
                if new_port == 1:
                   entry = {
                       'hostname': sys_name,
                       'port': port_desc
                   }
                   lldp.setdefault(local_port, []) 
                   lldp[local_port].append(entry)

                local_port = r1.group(1)
                local_port = 'eth' + local_port
                new_port = 1

            r2 = re.match(r'^\s+\+ Port description\s+:\s+\"(.*)\"',line)
            if r2:
                port_desc = r2.group(1)
            r3 = re.match(r'^\s+\+ System name\s+:\s+\"(.*)\"',line)
            if r3:
                sys_name = r3.group(1)

        # Write the last port
        entry = {
           'hostname': sys_name,
           'port': port_desc
        }
        lldp.setdefault(local_port, []) 
        lldp[local_port].append(entry)
            
        return lldp

    def get_lldp_neighbors_detail(self, interface=''):

        lldp = {}      
        command = 'show lldp neighbors'
        lines = self.device.send_command(command)
        lines = lines.split("\n")

        lines = lines[2:]

        for line in lines:
            fields = line.split()

            # FIXME: portid, portdesc and name can be strings so it will not work for
            # 1/5      609c.9fde.1b14  Ethernet 0/47   Eth 0/47                Router1
            # Need to parse show lldp neighbors detail        
            if len(fields) == 5:
                local_port, chassis, portid, portdesc, name = fields

                lldp_detail = self._lldp_detail_parser(local_port)   
                entry = {
                    'parent_interface': u'N/A',
                    'remote_port': unicode(lldp_detail[0]),
                    'remote_port_description': unicode(lldp_detail[1]),
                    'remote_chassis_id': unicode(lldp_detail[2]),
                    'remote_system_name': unicode(lldp_detail[3]),
                    'remote_system_description': u'N/A',
                    'remote_system_capab': unicode(lldp_detail[4]),
                    'remote_system_enable_capab': unicode(lldp_detail[5])
                }

                if local_port == interface:
                    return {interface: lldp[local_port]}

                lldp.setdefault(local_port, [])	
                lldp[local_port].append(entry)

        return lldp

    def get_config(self, retrieve='all'):
        
        config = {
            'startup': '',
            'running': '',
            'candidate': unicode('')
        }

        if retrieve.lower() in ('running', 'all'):
            _cmd = 'show running-config'
            config['running'] = unicode(self.cli([_cmd]).get(_cmd))
        if retrieve.lower() in ('startup', 'all'):
            _cmd = 'show configuration'
            config['startup'] = unicode(self.cli([_cmd]).get(_cmd))
        return config

    def get_users(self):
        
        command = 'show users'
        lines = self.device.send_command(command)
        lines = lines.split("\n")

        lines = lines[2:]
        info = {}
        for line in lines:
            match = re.match("(\S+)\s+(\S+)\s+(\S+)\s+(\d+)", line)
            if match:
                user  = match.group(1)
                passw = match.group(2)
                level = match.group(4)

                info[user] = {
                   'level': int(level),
                   'password': passw,
                   'sshkeys': list()
                }

        return info

    def get_ntp_servers(self):

        command = "show running | begin ^ntp"
        lines = self.device.send_command(command)
        lines = lines.split("\n")

        ntp = {}
        for line in lines:

            if "!" in line:
    		   return ntp

            match = re.match("\s+server\s+(\S+)(.*)", line)
            if match:
               server = unicode(match.group(1))
               ntp[server] = {}

        return ntp

    def get_ntp_peers(self):

    	command = "show running | begin ^ntp"
    	lines = self.device.send_command(command)
    	lines = lines.split("\n")

        ntp = {}
    	for line in lines:
            if "!" in line:
    		   return ntp

            match = re.match("\s+peer\s+(\S+)(.*)", line)
            if match:
                peer = unicode(match.group(1))
                ntp[peer] = {}

    	return ntp

    def get_facts(self):

        command = 'show version'
        lines = self.device.send_command(command)
        for line in lines.splitlines():
            r1 = re.match(r'^Chassis:\s+(.*)\s+\(Serial #:\s+(\S+),(.*)', line)
            if r1:
                model = r1.group(1)
                serial = r1.group(2)

            r2 = re.match(r'^IronWare : Version\s+(\S+)\s+Copyright \(c\)\s+(.*)', line)
            if r2:
                version = r2.group(1)
                vendor = r2.group(2)

        command = 'show uptime'
        lines = self.device.send_command(command)
        for line in lines.splitlines():
            # Get the uptime from the Active MP module
            r1 = re.match(r'\s+Active MP(.*)Uptime\s+(\d+)\s+days'
                          r'\s+(\d+)\s+hours'
                          r'\s+(\d+)\s+minutes'
                          r'\s+(\d+)\s+seconds',line)
            if r1:
                days = int(r1.group(2))
                hours = int(r1.group(3))
                minutes = int(r1.group(4))
                seconds = int(r1.group(5))
                uptime = seconds + minutes*60 + hours*3600 + days*86400

        command = 'show running-config | include ^hostname'
        lines = self.device.send_command(command)
        for line in lines.splitlines():
            r1 = re.match(r'^hostname (\S+)', line)
            if r1:
                hostname = r1.group(1)
                
        facts = {
            'uptime': uptime,
            'vendor': unicode(vendor),
            'model': unicode(model),
            'hostname': unicode(hostname),
            # FIXME: fqdn
            'fqdn': unicode("Unknown"),
            'os_version': unicode(version),
            'serial_number': unicode(serial),
            'interface_list': []
        }

        iface = 'show interface brief wide'
        output = self.device.send_command(iface)
        output = output.split('\n')
        output = output[2:]

        for line in output:
            fields = line.split()

            if len(line) == 0:
                continue
            elif len(fields) >= 6:
                port, link, state, speed, tag, mac = fields[:6]

                r1 = re.match(r'^(\d+)/(\d+)', port)
                if r1:
                    port = 'e' + port
                    facts['interface_list'].append(port)
                elif re.match(r'^mgmt1', port):
                    facts['interface_list'].append(port)
                elif re.match(r'^ve(\d+)', port):
                    facts['interface_list'].append(port)
                elif re.match(r'^lb(\d+)', port):
                    facts['interface_list'].append(port)

        return facts

    def get_network_instances(self):

        vrfs = {}

        command = 'show vrf'
        lines = self.device.send_command(command)
        lines = lines[3:-3]
        for line in lines.splitlines():
            r1 = re.match(r'^(\S+)\s+(\d+):(\d+).*', line)
            if r1:
                name = r1.group(1)
                rd = u'{}.{}'.format(r1.group(2), r1.group(3))

                command = "show vrf {}".format(name)
                vlines = self.device.send_command(command)
                for l in vlines.splitlines():
                    continue

        vrfs[u'default'] = {
            u'name': u'default',
            u'type': u'DEFAULT_INSTANCE',
            u'state': {
                u'route_distinguisher': None,
            },
            u'interfaces': {
                u'interface': {
                    #k: {} for k in all_interfaces if k not in all_vrf_interfaces.keys()
                },
            },
        }

        return vrfs

    def get_bgp_neighbors(self):
        # FIXME: No VRF support and no IPv6 support for the time being
        # FIXME: Move the following expressions elsewhere
        IP_ADDR_REGEX = r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        IPV4_ADDR_REGEX = IP_ADDR_REGEX
        ASN_REGEX = r"[\d\.]+"

        bgp_data = dict()
        bgp_data['global'] = dict()
        bgp_data['global']['peers'] = dict()

        command = 'show ip bgp summary'
        lines_summary = self.device.send_command(command)
        local_as = 0
        for line in lines_summary.splitlines():
            # FIXME: re.match or re.compile
            r1 = re.match(r'^\s+Router ID:\s+(?P<router_id>({}))\s+'
                          r'Local AS Number:\s+(?P<local_as>({}))'.format(IPV4_ADDR_REGEX, ASN_REGEX), line)
            if r1:
                # FIXME: AS numbers check: napalm_base.helpers.as_number(
                # FIXME: How to export a variable to avoid initialization (local_as)
                # FIXME: code not used
                router_id = r1.group('router_id')
                local_as = r1.group('local_as')
                # FIXME check the router_id looks like an ipv4 address
                # router_id = napalm_base.helpers.ip(router_id, version=4)
                bgp_data['global']['router_id'] = router_id

            # Neighbor Address  AS#         State   Time          Rt:Accepted Filtered Sent     ToSend
            # 172.24.46.2       513         ESTAB   587d7h24m    0           0        255      0       
            # FIXME: uptime is not a single string!
            r2 = re.match(r'^\s+(?P<remote_addr>({}))\s+(?P<remote_as>({}))\s+(?P<state>\S+)\s+'
                                r'(?P<uptime>\S+)'
                                r'\s+(?P<accepted_prefixes>\d+)'
                                r'\s+(?P<filtered_prefixes>\d+)'
                                r'\s+(?P<sent_prefixes>\d+)'
                                r'\s+(?P<tosend_prefixes>\d+)'.format(IPV4_ADDR_REGEX, ASN_REGEX), line)
            if r2:
                remote_addr = r2.group('remote_addr')
                afi = "ipv4"
                # FIXME: Confirm how to get received prefixes in MLXe
                received_prefixes = int(r2.group('accepted_prefixes'))+int(r2.group('filtered_prefixes'))
                bgp_data['global']['peers'][remote_addr] = {
                        'local_as': local_as,
                        'remote_as': r2.group('remote_as'),
                        'address_family': {
                            afi: {
                                 'received_prefixes': received_prefixes,
                                 'accepted_prefixes': r2.group('accepted_prefixes'),
                                 'sent_prefixes': r2.group('sent_prefixes')
                            }
                        }
                }

        current = ""
        command = 'show ip bgp neighbors'
        lines_neighbors = self.device.send_command(command)
        for line in lines_neighbors.splitlines():
            r1 = re.match(r'^\d+\s+IP Address:\s+(?P<remote_addr>({})),'
                          r'\s+AS:\s+(?P<remote_as>({}))'
                          r'\s+\((IBGP|EBGP)\), RouterID:\s+(?P<remote_id>({})),'
                          r'\s+VRF:\s+(?P<vrf_name>\S+)'.format(IPV4_ADDR_REGEX, ASN_REGEX, IPV4_ADDR_REGEX), line)
            if r1:
                remote_addr = r1.group('remote_addr')
                remote_id = r1.group('remote_id')
                vrf = r1.group('vrf_name')
                bgp_data['global']['peers'][remote_addr]['remote_as'] = r1.group('remote_as')
                bgp_data['global']['peers'][remote_addr]['remote_id'] = remote_id
                current = remote_addr

            # line:       Description: ----> L513-B-RBRMX-2
            r2 = re.match(r'\s+Description:\s+(.*)', line)
            if r2:
                description = r2.group(1)
                bgp_data['global']['peers'][current]['description'] = description

            # line:    State: ESTABLISHED, Time: 587d7h24m52s, KeepAliveTime: 10, HoldTime: 30
            r3 = re.match(r'\s+State:\s+(\S+),\s+Time:\s+(\S+),'
                            r'\s+KeepAliveTime:\s+(\d+),'
                            r'\s+HoldTime:\s+(\d+)', line)
            if r3:
                # FIXME: compute is_up, is_enabled
                is_up = 1
                is_enabled = 1
                uptime = r3.group(2)
                bgp_data['global']['peers'][current]['is_up'] = is_up
                bgp_data['global']['peers'][current]['is_enabled'] = is_enabled
                bgp_data['global']['peers'][current]['is_up'] = uptime

        return bgp_data

    def get_interfaces_ip(self):

        interfaces = {}
       
        command = 'show ip interface'
        output = self.device.send_command(command)
        output = output.split('\n')
        output = output[2:]

        for line in output:
            fields = line.split()
            if len(fields) == 8:
               iface, ifaceid, address, ok, nvram, status, protocol, vrf = fields
               port = iface + ifaceid
               if port not in interfaces:
                  interfaces[port] = dict()

               interfaces[port]['ipv4'] = dict()
               interfaces[port]['ipv4'][address] = dict()

        # Get the prefix from the running-config interface in a single call
        iface = ""
        show_command = "show running-config interface"
        interface_output = self.device.send_command(show_command)
        for line in interface_output.splitlines():
                r1 = re.match(r'^interface\s+(ethernet|ve|management|loopback)\s+(\S+)\s*$', line)
                if r1:
                    port = r1.group(1)
                    if port == "ethernet":
                        port = "eth"
                    elif port == "management":
                        port = "mgmt"
                    iface = port + r1.group(2)

                if 'ip address ' in line:
                    fields = line.split()
                    # ip address a.b.c.d/x ospf-ignore|ospf-passive|secondary                 
                    if len(fields) in [3,4]:                     
                       address, subnet = fields[2].split(r'/')
                       interfaces[iface]['ipv4'][address] = { 'prefix_length': subnet }

        command = 'show ipv6 interface'
        output = self.device.send_command(command)
        output = output.split('\n')
        output = output[2:]

        port = ""
        for line in output:
            r1 = re.match(r'^(\S+)\s+(\S+).*fe80::(\S+).*', line)
            if r1:
               port = r1.group(1) + r1.group(2)
               address = "fe80::" + r1.group(3)
               if port not in interfaces:
                  # Interface with ipv6 only configuration
                  interfaces[port] = dict()

               interfaces[port]['ipv6'] = dict()
               interfaces[port]['ipv6'][address] = dict()
               interfaces[port]['ipv6'][address] = { 'prefix_length': 'N/A' }

            # Avoid matching: fd01:1458:300:2d::/64[Anycast]
            r2 = re.match(r'\s+(\S+)\/(\d+)\s*$', line)
            if r2:
                address = r2.group(1)
                subnet = r2.group(2)
                interfaces[port]['ipv6'][address] = { 'prefix_length': subnet }

        return interfaces     

    def get_bgp_neighbors_detail(self):

        bgp_data = dict()
        bgp_data['global'] = dict()

        command = 'show ip bgp neighbors'
        lines_neighbors = self.device.send_command(command)
        for line in lines_neighbors.splitlines():
            r1 = re.match(r'^\d+\s+IP Address:\s+(?P<remote_address>({})),'
                          r'\s+AS:\s+(?P<remote_as>({}))'
                          r'\s+\((IBGP|EBGP)\), RouterID:\s+(?P<router_id>({})),'
                          r'\s+VRF:\s+(?P<vrf_name>\S+)'.format(IPV4_ADDR_REGEX, ASN_REGEX, IPV4_ADDR_REGEX), line)
            if r1:
                remote_address = r1.group('remote_address')
                router_id = r1.group('router_id')
                vrf = r1.group('vrf_name')
 
            r2 = re.match(r'\s+State:\s+(\S+),\s+Time:\s+(\S+),'
                            r'\s+KeepAliveTime:\s+(\d+),'
                            r'\s+HoldTime:\s+(\d+)', line)
            if r2:
                connection_state = r2.group(1)
                uptime = r2.group(2)
                keepalive = r2.group(3)
                holdtime = r2.group(4)

            # Local host:  172.24.46.1, Local  Port: 8033
            # Remote host: 172.24.46.11, Remote Port: 179
            r3 = re.match(r'\s+(Remote|Local) host:\s+(\S+),\s+(Remote|Local)\s+Port:\s+(\d+),', line)
            if r3:
                if r3.group(1) == "Remote":
                    remote_port = r3.group(4)
                    remote_address = r3.group(2)
                elif r3.group(1) == "Local":
                    local_port = r3.group(4)
                    local_address = r3.group(2)

        return bgp_data

    def get_environment(self, interface=''):

        # FIXME: Partial implementation
        environment = {}      
        command = 'show chassis'
        lines = self.device.send_command(command)
        lines = lines.split("\n")

        lines = lines[3:]
        for line in lines:
            # Power 2: Installed (Failed or Disconnected)
            r1 = re.match(r'^Power\s+(\d+):\s+Installed \(Failed or Disconnected\)',line)
            # Power 7: (23-yyyyyyyy xxxxxxxxx  - AC 1800W): Installed (OK)
            r2 = re.match(r'^Power\s+(\d+):\s+.*AC\s+(\S+)\): Installed \(OK\)',line)
            if r1:
                psu = r1.group(1)
                environment[psu] = dict()
                environment[psu] = { 'status': False, 'capacity': 'N/A', 'output': 'N/A' }
            elif r2:
                psu = r2.group(1)
                environment[psu] = dict()
                environment[psu] = { 'status': True, 'capacity': r2.group(2), 'output': 'N/A' }

            # Back Fan A-1: Status = OK, Speed = MED (60%)
            r3 = re.match(r'^(.*):\s+Status = (\S+),\s+Speed\s+=\s+(\S+)\s+\((\d+)%\)',line)
            if r3:
                fan = r3.group(1)
                status = False
                if r3.group(2) == "OK":
                    status = True

                environment[fan] = { 'status': status } 

        return environment
