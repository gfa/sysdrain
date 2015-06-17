#!/usr/bin/env python

import os
import sys
import keystoneclient.v2_0.client as ksclient
import novaclient.v1_1.client as novaclient
import time
import argparse
import json
import subprocess
import pprint
import random
from distutils.util import strtobool

# How often to check with the API to see if the instance has returned to an
# active  state (in seconds).
live_migration_poll_interval = 5

# How long to wait after the instance shows "active" before we attempt to
# ping it.  Strictly speaking, this should only ever need to be zero,
# but a few seconds to make sure everything's works never hurt anybody.
sleep_between_hosts_time = 3

#  End of configurable parameters  #

argparser = argparse.ArgumentParser(description='Move instances between OpenStack hypervisors')
argparser.add_argument('-H', '--draining-hypervisor', nargs=1, default=None, help='Specify a particular hypervisor from which to drain all instances')
argparser.add_argument('-T', '--tenant', nargs=1, default=None, help='Specify a particular tenant from which to drain all instances')
argparser.add_argument('-P', '--ping', default=False, action='store_true', help='ping the vm before and after the move, needs -N')
argparser.add_argument('-D', '--destination', nargs=1, default=None, help='Destination hypervisor, if none scheduler will pick one')
argparser.add_argument('-N', '--network', nargs=1, default=None, help='network to ping over (eth0, eth1)')
argparser.add_argument('-Z', '--az', default=False, action='store_true', help='disable AZ detection')
args = argparser.parse_args()

if args.draining_hypervisor:
    draining_hv = args.draining_hypervisor[0]
else:
    print "i need an hypervisor to evacuate from"
    print sys.argv[0] + " -h to check the help"
    sys.exit(1)

if args.az:
    detectaz = False
else:
    detectaz = True

if args.network:
    network = args.network[0]
else:
    network = None
    if args.ping:
        print "pings need network, -N"
        sys.exit(1)

if args.tenant:
    target_tenant = args.tenant[0]
else:
    target_tenant = 'ALL'

if args.destination:
    destination = args.destination[0]
    detectaz = False
else:
    destination = None

if args.ping:
    ping_is_enabled = True
else:
    ping_is_enabled = False

os_auth_url = os.environ.get('OS_AUTH_URL')
os_username = os.environ.get('OS_USERNAME')
os_password = os.environ.get('OS_PASSWORD')
os_tenant_name = os.environ.get('OS_TENANT_NAME')
os_region_name = os.environ.get('OS_REGION_NAME')
tenant_key_name = "bla"

if os_auth_url is None or os_username is None or os_password is None or os_tenant_name is None:
    print "Undefined variable.  You probably need to source 'openrc' before running this program."
    exit(1)


def get_creds(type):
    password_key_name = "password"
    if type == "nova":
        password_key_name = "api_key"
        global tenant_key_name
        tenant_key_name = "tenant_name"
    if type == "nova":
        tenant_key_name = "project_id"
    os_creds = {}
    os_creds['auth_url'] = os_auth_url
    os_creds[password_key_name] = os_password
    os_creds['username'] = os_username
    os_creds[tenant_key_name] = os_tenant_name
    os_creds['region_name'] = os_region_name
    return os_creds


def get_nova_creds():
    return get_creds("nova")


def get_keystone_creds():
    return get_creds("keystone")

def user_yes_no_query(question):
    sys.stdout.write('%s [y/n]\n' % question)
    while True:
        try:
            return strtobool(raw_input().lower())
        except ValueError:
            sys.stdout.write('Please respond with \'y\' or \'n\'.\n')

keystone = ksclient.Client(**get_keystone_creds())

nova = novaclient.Client(**get_nova_creds())

#for az in nova.availability_zones.list(): ---> does not work, if AZ is not UP it won't show
aggregates = {}
#aggregates_filter = {}

aggrlist = nova.aggregates.list()
for aggr in aggrlist:
  aggrname = aggr._info['name']
  aggrhosts = aggr._info['hosts']
  aggraz = aggr._info['availability_zone']
  if aggraz is not None:
      aggregates[aggrname] = aggrhosts
#      aggregates_filter.append(aggregates[aggrname])

# A Set of hosts that have been previously moved in a drain operation.  We won't attempt to move them again when draining; they're probably bugged somehow if
# they are still on the host we're trying to drain.  Note that hosts that get stuck in "MIGRATING" will block the script.  Thus, this only handles those that
# return to ACTIVE, but on the original, pre-move host (it's more common than you might guess, sadly).
moved_hosts = set()
# To hard-exclude a particular host from ever moving, add it here, as: moved_hosts.add('some-uuid')

if target_tenant is 'ALL':
    serverlist = nova.servers.list(search_opts={'all_tenants': 1, 'host': draining_hv})
else:
    # mapear tenant a tenant_id
    serverlist = nova.servers.list(search_opts={'all_tenants': 1, 'tenant_id': target_tenant, 'host': draining_hv})

# DEBUG
#print "server list: %s" % serverlist
#print "target tenant(s): %s" % target_tenant
#print "selected hv: %s" % draining_hv
#print "ping is enabled: %s" % ping_is_enabled


for server in serverlist:
    serverinfo = server._info.copy()

    if network:
        bla = serverinfo['addresses']
        bla2 = json.dumps(bla)
        bla3 = json.loads(bla2)
        try:
            ipaddr = bla3[network][0]['addr']
            print ipaddr
        except:
            true

    server_name = serverinfo['name']
    server_uuid = serverinfo['id']

    server_host = serverinfo['OS-EXT-SRV-ATTR:host']
    server_hvhn = serverinfo['OS-EXT-SRV-ATTR:hypervisor_hostname']

    server_status = serverinfo['status']
    server_task_state = serverinfo['OS-EXT-STS:task_state']

    server_flavor = serverinfo['flavor']['id']

    if server_status != 'ACTIVE':
        print "Skipping '" + server_name + "' [" + server_uuid + "] due to non-ACTIVE state (" + server_status + ")."
        continue

    if server_task_state is not None:
        print "Skipping '" + server_name + "' [" + server_uuid + "] due to active task state (" + server_task_state + ")."
        continue

    flavor = nova.flavors.get(server_flavor)

    if server is None:
        print "No vm from tenant tenant on hv"
        break

    #if server_uuid in moved_hosts and draining_hv is not None and draining_hv == server_hvhn:
    if server_uuid in moved_hosts:
        print server_uuid + " does not appear to have been moved successfully, earlier.    This is probably a bug that you want to investigate."

    # Keep track of the fact that we have tried to move this host, so we don't try again (in draining mode) if it errors out.
    if draining_hv is not None:
        moved_hosts.add(server)

    if ping_is_enabled:
        server_name_fqdn = server_name

    if ping_is_enabled:
        ret = subprocess.call("ping -c 1 %s" % ipaddr,
                              shell=True,
                              stdout=open('/dev/null', 'w'),
                              stderr=subprocess.STDOUT)
        if ret == 0:
            print "%s: is alive" % ipaddr
        else:
            print "%s: did not respond" % ipaddr

    # get vm sizes, in case we need to select where to migrate
    flavor = nova.flavors.get(server_flavor)
    #print flavor._info
    vm_ram = flavor._info['ram']
    float(vm_ram)
    vm_swap = flavor._info['swap']
    try:
        float(vm_swap)
    except:
        vm_swap = 0
        float(vm_swap)
    vm_disk = flavor._info['disk']
    float(vm_disk)
    try:
        vm_ephemeral = flavor._info['ephemeral']
    except:
        vm_ephemeral = 0
    float(vm_ephemeral)
    vm_vcpus = flavor._info['vcpus']
    float(vm_vcpus)

    # TODO: allow to specify the destination hv
    # TODO: allow to choose block_migration yes/no

    # ACA VIENE -Z
# 1 - listarelos aggregates y ver en cual AZ esta en hv DONE
# 2 - listar los otros hv de la misma AZ DONE
# 3 - traerse la memoria, disco y cpu para cada hv
# 4 - ordenar de menor a mayor, memoria, cpu, disco
# 5 - agarrar al primero y almacenarlo como 'destination'
# 6 - no puede estar deshabilitado o down
# aggregates[aggrname] have the hosts on each aggregate
# aggrlist has the list of aggregates
# hostsAZ all the servers sharing the AZ with the -H hypervisor
# currentAZ the AZ of -H hypervisor

    if detectaz is True:
        for a in aggregates:
            for hosts in aggregates[a]:
                if draining_hv in hosts:
                    currentAZ = a
                    hostsAZ = aggregates[a]

        posiblehv = []
        hvresources = {}
        hvlist = nova.hypervisors.list()
        for hv in hvlist:
             hvname = hv._info['hypervisor_hostname']
             if hvname in hostsAZ:
               # check if hv is
               # resources
               # all the hv that pass that. pick one random
               srvlist = {}
               srvlist = nova.services.list(host=hvname)
               for srv in srvlist:
                   data = srv._info
                   if data['host'] == draining_hv:
                        continue
                   if data['status'] == 'disabled':
                        #print hvname + ' is disabled'
                        continue
                   if data['state'] == 'down':
                        #print hvname + ' is down'
                        continue
                   posiblehv.append(hvname)
               # aca ver si alcanza la ram
               hvresources[hvname] = {}
               hvresources[hvname]['info'] = hv._info.copy()
               hvram = hvresources[hvname]['info']['free_ram_mb']
               hvdisk = hvresources[hvname]['info']['disk_available_least']
               float(hvram)
               float(hvdisk)
               #print "hvname"
               #print hvname
               #print "hvram"
               #print hvram
               #print "vm_ram"
               #print vm_ram
               #print server_uuid
               #print "vm_disk"
               #print vm_disk
               #print "hvdisk"
               #print hvdisk
               #print "vm_swap"
               #print vm_swap
               #print "vm_ephemeral"
               #print vm_ephemeral
               if hvram < vm_ram + 10:
                   try:
                       posiblehv.remove(hvname)
                       print "hvname no ok, ram"
                   except:
                       pass
               if hvdisk < vm_disk + vm_swap + vm_ephemeral + 10:
                   try:
                       posiblehv.remove(hvname)
                       print "hvname no ok, disk"
                   except:
                       pass

        #print(posiblehv)
        try:
            destination = random.choice(posiblehv)
            print server_name + destination
        except:
            print "no hv can host the vm: " + server_name + " uuid: " + server_uuid
            continue
    print "vm: " + str(server_name) + " uuid: " + str(server_uuid) + " will move to " + str(destination)

    answer = user_yes_no_query('Continue?')

    if answer is 0:
        print 'do not continue'
        continue

    try:
        nova.servers.live_migrate(server=server, host=destination, disk_over_commit=False, block_migration=True)
    except:
        print "ERROR: live-migration can't be started, server name: " + server_name + "server uuid: " + server_uuid + "\n"
        continue

    sys.stdout.write('Live migration of ' + server_name + ' commenced.    Polling on instance returning to active state. \n')

    while True:
        donor_object = nova.servers.get(server)._info
        if donor_object['status'] != "MIGRATING":
            break
        sys.stdout.write('.')
        sys.stdout.flush()
        time.sleep(live_migration_poll_interval)

    if donor_object['status'] == "ACTIVE":
        print "Migration complete!"

    if donor_object['status'] == "ERROR":
        print "Migration completed with ERROR!"

    # Let the migration settle a bit before looping again.
    # Courtesy of http://stackoverflow.com/questions/3160699/python-progress-bar
    sys.stdout.write("Pausing " + str(sleep_between_hosts_time) + " seconds to let HVs settle: ")
    sys.stdout.write("[%s]" % (" " * sleep_between_hosts_time))
    sys.stdout.flush()
    sys.stdout.write("\b" * (sleep_between_hosts_time+1)) # return to start of line, after '['

    for i in xrange(sleep_between_hosts_time):
        time.sleep(1)
        # update the bar
        sys.stdout.write("*")
        sys.stdout.flush()

    sys.stdout.write("\n")
    sys.stdout.flush()

    if ping_is_enabled:
        ret = subprocess.call("ping -c 1 %s" % ipaddr,
                              shell=True,
                              stdout=open('/dev/null', 'w'),
                              stderr=subprocess.STDOUT)
        if ret == 0:
            print "%s: is alive" % ipaddr
        else:
            print "%s: did not respond" % ipaddr
            print "EMERGENCY!    " + server_name_fqdn + " is not pingable post-migration. FIX THIS!"
            #exit(1)
