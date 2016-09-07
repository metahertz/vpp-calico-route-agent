import time
import sys
import logging
import socket
import json
import re
import vpp_papi
from conman.conman_etcd import ConManEtcd
from netaddr import IPAddress, EUI, mac_bare



class Program(object):
    def __init__(self,
                 key,
                 vpp_uplink_interface_index,
                 uplink_ip,
                 uplink_subnet,
                 protocol='http',
                 host='127.0.0.1',
                 port=4001,
                 username=None,
                 password=None):

        # logging
        logging.basicConfig(filename='vpp-calico-routeagent.log',level=logging.DEBUG)

        # ConMan setup.
        self.conman = ConManEtcd(protocol=protocol,
                                 host=host,
                                 port=int(port),
                                 username=username,
                                 password=password,
                                 on_change=self.on_configuration_change,
                                 watch_timeout=200)

        # Need to know our hostname
        self.hostname = socket.gethostname()

        # This is the interface and IP this host's VPP uses for the outside world.
        # We'll publish this in ETCD to allow other VPP's to form adjacencys.
        self.vpp_uplink_interface_index = int(vpp_uplink_interface_index)
        self.uplink_ip = uplink_ip
        self.uplink_subnet = uplink_subnet

        # Publish our VPP uplink IP to /vpp-calico/hosts/<hostname>/peerip/ipv4/1
        self.etcd_cli = self.conman.client
        self.host_uplink_info_key = '/vpp-calico/hosts/' + self.hostname + '/peerip/ipv4/1'
        self.etcd_cli.write(self.host_uplink_info_key, value=uplink_ip)

        # Connect to VPP API
        self.r = vpp_papi.connect("vpp-calico")
        if self.r != 0:
            logging.critical("vppapi: could not connect to vpp")
            return
        logging.debug('Connected to VPP API %s', type(self.r))

        # Set VPP uplink interface to 'up'
        logging.debug('Configuring VPP Uplink interface.')
        flags_r = vpp_papi.sw_interface_set_flags(self.vpp_uplink_interface_index,1,1,0)
        if type(flags_r) == list or flags_r.retval != 0:
            logging.critical("Failed to bring up our UPLINK VPP interface. Failing.")
            return
        logging.debug("vppapi: VPP Uplink interface UP!")

        # Configure Uplink IP address based on agent configuration (uplink_ip, uplink_subnet)
        uplink_ip = uplink_ip.encode('utf-8', 'ignore')
        uplink_ip = socket.inet_pton(socket.AF_INET, uplink_ip)
        uplinkip_r = vpp_papi.sw_interface_add_del_address(self.vpp_uplink_interface_index,True,False,False,int(uplink_subnet),uplink_ip)
        if type(uplinkip_r) == list or uplinkip_r.retval != 0:
            logging.critical("Failed to add IPv4 address to uplink")
            return
        logging.debug("vppapi: VPP Uplink IPv4 Configured!")

        #ConMan Vars for watching IP blocks.
        self.key = key
        self.last_change = None
        self.run()

    def on_configuration_change(self, key, action, value):
        # Sometimes the same change is reported multiple times. Ignore repeats.
        if self.last_change == (key, action, value):
            logging.debug('Duplicate Update, Ingore! Key: %s Action: %s',key,action)
            return
        # Calico regularly read+updates key contents to track IPAM. We just want new routes.
        if action != 'create':
            logging.debug('Ignoring all actions apart from create. Key: %s Action: %s', key, action)
            return

        logging.debug('Valid Update, Key: %s Action: %s Value: %s',key,action,value)
        self.last_change = (key, action, value)
        self.conman.refresh(self.key)

        # Convert our value data (json) into a dict.
        update_dict = json.loads(value)
        ourhost='host:'+ socket.gethostname()

        # Check if the route update is for us
        if update_dict['affinity'] == str(ourhost):
           logging.debug('Block is on our host, ignoring update. Key: %s', key)
           return
        else:
           logging.debug('Update IS for us, processing route: %s', key)

           # Update VPP Routing Table
           # Which host is our next hop? Translate hostname to reachable IP via ETCD.
           # Strip 'host:' from 'affinity' record, leave us with hostname.
           # Lookup hostname <> IP mapping in ETCD /vpp-calico Tree

           regex_host = re.compile(ur'(?:host:)(.*)')
           re_result = re.search(regex_host, update_dict['affinity'])
           host_path = "/vpp-calico/hosts/" + re_result.group(1) + "/peerip/ipv4/1"
           route_via_ip = self.etcd_cli.read(host_path).value

           if route_via_ip == "":
             logging.debug('We failed to resolve the remote host via etcd /vpp-calico tree')
             return

           #Split CIDR into network and subnet components
           route_components = update_dict['cidr'].split("/")
           cidr = int(route_components[1])
           network = str(route_components[0])
           #Route-via destination in Binary format
           via_address = route_via_ip.encode('utf-8', 'ignore')
           via_address = socket.inet_pton(socket.AF_INET, via_address)
           #Subnet CIDR and Network in binary format.
           dst_address = network.encode('utf-8', 'ignore')
           dst_address = socket.inet_pton(socket.AF_INET, dst_address)
           #Other VPP API vars
           vpp_vrf_id = 0
           is_add = True
           is_ipv6 = False
           is_static = False
           print('calling vpp_papi')
           route_r = vpp_papi.ip_add_del_route(self.vpp_uplink_interface_index,
                                             vpp_vrf_id,
                                             False, 9, 0,
                                             False, True,
                                             is_add, False,
                                             is_ipv6, False,
                                             False, False,
                                             False, 1,
                                             cidr, dst_address,
                                             via_address)
           if type(route_r) != list and route_r.retval == 0:
               logging.debug("vpp-route-agent: added static route for %s/%s via %s", network, cidr, route_via_ip)
           else:
               logging.critical("vpp-route-agent: Could not add route to %s/%s via %s", network, cidr, route_via_ip)
               return
    def run(self):
        self.conman.refresh(self.key)
        print 'Refreshed Tree: %s', self.key
        self.conman.watch(self.key)
        print 'Watching Tree: %s', self.key
        while True:
            if self.conman[self.key].get('vppagentstop') == '1':
                open(self.filename, 'a').write('Stopping...\n')
                self.conman.stop_watchers()
                return
            time.sleep(1)

if __name__ == '__main__':
    Program(*sys.argv[1:])
