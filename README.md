OpenStack-Hypervisor-Balance
============================

sysdrain.py can be used to drain an hypervisor, it will live migrate the vm (moving the storage too).


it can take the following args

```
  -h, --help            show this help message and exit
  -H DRAINING_HYPERVISOR, --draining-hypervisor DRAINING_HYPERVISOR
                        Specify a particular hypervisor from which to drain
                        all instances
  -T TENANT, --tenant TENANT
                        Specify a particular tenant from which to drain all
                        instances
  -P, --ping            ping the vm before and after the move, needs -N
  -D DESTINATION, --destination DESTINATION
                        Destination hypervisor, if none scheduler will pick
                        one
  -N NETWORK, --network NETWORK
                        network to ping over (eth0, eth1)
  -Z, --az              disable AZ detection

```

-H is mandatory and specifies which hv we want to drain.
-P needs pings the vm ip before and after the movement. needs -N
-N what network of the vm should it ping, mandatory if -P even if the vm has only one nic.
-Z disable availability zone detection, the script will select the destination for you
retrieving information from nova (cpu, ram, disk, AZ) it will select the destination which
has enough ram, disk and is on the same AZ. if availability zone detection is disabled nova-scheduler will do it (and possibly break AZ)


needs openstack admin credentials sourced before run and network access to nova-api (example: nova list --all-tenants should work  before run this)


based on BMDan/OpenStack-Hypervisor-Balance
