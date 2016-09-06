import time
import sys
import logging
import socket
import json
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
        self.vpp_uplink_interface_index = vpp_uplink_interface_index
        self.uplink_ip = uplink_ip
        self.uplink_subnet = uplink_subnet

        # Publish our VPP uplink IP to /vpp-calico/hosts/<hostname>/peerip/ipv4/1
        self.etcd_cli = self.conman.client
        self.host_uplink_info_key = '/vpp-calico/hosts/' + self.hostname + '/peerip/ipv4/1'
        self.etcd_cli.write(self.host_uplink_info_key, value=uplink_ip)

        # Configure our VPP Uplink Interface
        ## MJTODO

        #ConMan Vars for watching IP blocks.
        self.key = key
        self.last_change = None
        self.run()

    def on_configuration_change(self, key, action, value):
        # Sometimes the same change is reported multiple times. Ignore repeats.
        if self.last_change == (key, action, value):
            logging.debug('Duplicate Update, Ingore! Key: %s Action: %s',key,action)
            return
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
           logging.debug('Processing new block for host: %s Cidr: %s', update_dict['affinity'], update_dict['cidr'] )

           # Update VPP Routing Table


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


    def vpp_add_route(self, isv6, destcidr, nexthop, dev):
        _log.debug('Adding VPP Route, v6?: %s Dest: %s Nexthop %s Interface %s',isv6, destcidr, nexthop, dev)


if __name__ == '__main__':
    Program(*sys.argv[1:])
