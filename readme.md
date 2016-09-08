# Calico to VPP Route Agent
## Multi-host reachability for Calico-VPP

The idea behind this agent was to provide a simple way of programming the FIB on VPP
Instances across a VPP-Calico cluster, directly from the Calico data stored in ETCD.

This means we are not relying on integrations with a routing protocol at this point (BGP)
and keeping the number of components in the PoC VPP-Calico project to a minimum.

This agent is PoC quality only. Please be careful.

### Usage
Lets assume a three node cluster, with the calico ETCD server running on Host1.
Let's also assume VPP is running on each host and there is an unconfigured ```IfIndex 1``` interface (for Example GigabitEthernet0/9/0) on each VPP instance.
These interfaces should have L2 connectivity between eachother (this is the 'uplink' for each VPP instance).
The following configuration will bring up the 'Uplink' interface and configure it inside VPP with the IP specified in the agent command.
The agents will then listen for calico address blocks belonging to other hosts in the cluster and configure a route to it from their VPP instance.

##### Host 1
```
python -i agent.py "calico/ipam/v2/assignment/ipv4/block" 1 172.16.0.1 24
# The agent will, by default connect to the local ETCD
```

##### Host 2
```
python -i agent.py "calico/ipam/v2/assignment/ipv4/block" 1 172.16.0.2 24 http 192.168.10.21
# We are telling Host2's agent the location of ETCD. See 'Optional Parameters' Below.
```

##### Host 3
```
python -i agent.py "calico/ipam/v2/assignment/ipv4/block" 1 172.16.0.3 24 http 192.168.10.21
# We are telling Host2's agent the location of ETCD. See 'Optional Parameters' Below.
```

##### Parameters explained.

* calico/ipam/v2/assignment/ipv4/block - The ETCD Tree to listen on for events (creates)
* 1 - The VPP Interface index number for our uplink / multi-host reachability.
* 172.16.0.x - The IP address we want to configure on our VPP uplink.
* 24 - The subnet in CIDR notation for our VPP Uplink interface.

#### Optional Parameters

```
python -i agent.py "calico/ipam/v2/assignment/ipv4/block" 1 192.168.1.10 24 <etcd_protocol> <etcd_host> <etcd_port> <etcd_user> <etcd_password>
```
Defaults to localhost:4000 via HTTP with no authentication.


### Investigation

While looking at the data structures within the ```/calico``` directory in ETCD, we appear to have exactly what we need in reasonably simple formats.
For example;

```
/calico/ipam
/calico/ipam/v2
/calico/ipam/v2/assignment
/calico/ipam/v2/assignment/ipv4
/calico/ipam/v2/assignment/ipv4/block
/calico/ipam/v2/assignment/ipv4/block/192.168.0.0-26
/calico/ipam/v2/assignment/ipv4/block/192.168.11.0-26
/calico/ipam/v2/host
/calico/ipam/v2/host/cni-worker1
/calico/ipam/v2/host/cni-worker1/ipv4
/calico/ipam/v2/host/cni-worker1/ipv4/block
/calico/ipam/v2/host/cni-worker1/ipv4/block/192.168.0.0-26
/calico/ipam/v2/host/cni-worker2
/calico/ipam/v2/host/cni-worker2/ipv4
/calico/ipam/v2/host/cni-worker2/ipv4/block
/calico/ipam/v2/host/cni-worker2/ipv4/block/192.168.11.0-26
```

This snippet is under ```/calico/ipam``` which is ideal, as the format will remain consistent regardless of the workload type (ie, docker, cni-plugin, etc).
Unlike other areas of the tree, such as; ```/calico/v1/host/cni-worker1/workload/docker/61757f6edaefa5087bb5e97e1ff51d45d724e8745887d843880ddc763b38502d/endpoint```

#### Calico 'Single Pool'
What we see in the output above, is calico dynamically creating 'sub-pools'/'blocks' of the cluster-wide subnet which it is dynamically assigning to each host, multiple blocks can be assigned to a host as demand on that host grows.

Also, it does not prevent a single workload migrating to another host (/32 route), which sadly does not show up in etcd and instead would be signalled via BGP (future/next steps).

However, unless manual IPAM crafting is being done, containers in need of an IP will always get an IP within one of the assigned blocks via the CNI-IPAM plugins etc; only if someone really felt the need to 'migrate' that workload (and migrate isnt really a word we see/like within the container ecosystem) would a divergent /32 be created at the routing level.

### Implementation

The script uses the machines hostname to know which entries in ```calico/assignment/ipv4/block``` to ignore (as it's own host entries are already local routes).
On startup, we configure VPP's uplink interface to the IP and subnet given and store into ETCD as```/vpp-calico/hosts/<hostname>/peerip/ipv4/1```.
This is then used by other hosts for building VPP FIB entries to our host.

Once running, the script simply waits (watcher on the ```/calico/ipam/v2/assignment/ipv4/block``` tree) for new blocks on other hosts and programs those into the local VPP FIB, using the next hop IP from ```/vpp-calico/hosts/<hostname>/peerip/ipv4/1``` for the relevant ```<hostname>```.

We're using the 'Configuration Manager / ConMan' module in python, (from: https://github.com/the-gigi/conman/ ) which builds on the regular python etcd client library. There are a few benefits here:
    * Takes care of threading for non-blocking watches of multiple etcd keys.
    * Allows us to simply specify our own function as a callback for a change event.

### Drawbacks
* We're currently not looking at deletions.
* If you're starting the agent at the same time as calico (new calico cluster); you will need to ```etcdctl mkdir /calico/ipam/v2/assignment/ipv4/block``` otherwise there is no key for the agent to 'watch'.
* We're still going to need BGP/Routing protocol in future to cover /32 and more complex usecases.
* We're putting more load on ETCD. However, this is read. See below.

Immediately, I can think of a re-write where this agent run's in one location and parses the needed routes into a single per-host ```/vpp-calico``` subtree vs each hosts having to listen on all *other* hosts IPAM tree's. However, the current scenario doesn't depend on a single host to do the processing, so will likley be more survivable, albeit creating more *READ* load on ETCD, etcd however is pretty good with read loads, just struggles with many writes.
