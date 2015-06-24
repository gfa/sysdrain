#!/bin/bash
#set -e
#set -x

TEMP5=`mktemp -d`
export TMPDIR=$TEMP5
TEMP=$TEMP5/nova_show
TEMP2=$TEMP5/nova_hypervisor_show_dst
TEMP3=$TEMP5/nova_hypervisor_show_src
TEMPRUN=$TEMP5/nova-run

which jq &>/dev/null
if [ $? != 0 ]; then
	echo "you need jq installed"
	exit 1
fi

SSH='/usr/bin/ssh -p 9022 -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no '

if [ -z $2 ]; then
	echo "i need 2 args vm uuid and destination hypervisor hostname"
	exit 1
fi

nova show $1 2>/dev/null >$TEMP
if [ $? != 0 ]; then
	echo "vm does not exists or openrc not parsed"
	exit 1
fi

TOKEN=`keystone token-get  |head -5 |tail -1 |awk '{print $4}'`
TENANT_ID=`keystone tenant-get $OS_TENANT_NAME | grep id | awk '{print $4}'`
NOVA_CURL=`keystone endpoint-list |grep $OS_REGION_NAME|grep 8774 | awk '{print $6}' | sed  "s/%(tenant_id)s/$TENANT_ID/g"`

nova hypervisor-show $2 2>/dev/null >$TEMP2
if [ $? != 0 ]; then
	echo "hv does not exists or openrc not parsed"
	exit 1
fi

curl "$NOVA_CURL/servers/$1" -X GET -H "Accept: application/json" -H "User-Agent: python-novaclient" -H "X-Auth-Project-Id: admin" -H "X-Auth-Token: $TOKEN" > $TEMP5/nova_curl

DST_HV=`grep host_ip $TEMP2 |awk '{print $4}'`
$SSH $DST_HV "/bin/true"
if [ $? != 0 ]; then
	echo "can't ssh to $SRC_HV $2"
	echo $SSH $SRC_HV
	exit 1
fi
SRC_HV_DNS=`grep OS-EXT-SRV-ATTR:host $TEMP | awk  '{print $4}'`

nova hypervisor-show $SRC_HV_DNS 2>/dev/null >$TEMP3
SRC_HV=`grep host_ip $TEMP3 |awk '{print $4}'`

$SSH $SRC_HV "/bin/true"
if [ $? != 0 ]; then
	echo "can't ssh to $SRC_HV"
	echo $SSH $SRC_HV
	exit 1
fi

$SSH $SRC_HV "sudo -u nova $SSH $DST_HV /bin/true"
if [ $? != 0 ]; then
	echo "can't ssh from $SRC_HV to $DST_HV as nova"
	exit 1
fi

$SSH $SRC_HV "sudo -u nova $SSH $DST_HV /bin/mkdir -p /data/var/lib/nova/tmp"
if [ $? != 0 ]; then
	echo "can't mkdir from $SRC_HV to $DST_HV as nova"
	exit 1
fi

ports=0
for port in `neutron  port-list --device-id $1 |grep  ip_address | awk '{print $2}'`
do
	ports=$((ports+1))
	neutron port-show $port > $TEMP5/$ports
	neutron port-show $port | grep '| id' |awk '{print $4}'> $TEMP5/$ports-id
done

# catch failures now
set -e
#set -x

unset port
for port in `seq 1 $ports` #{1..$ports}
do
	ip=`cat $TEMP5/$port | grep fixed_ips |awk '{print $7}' | tr -cd '[[:digit:].-]'`
	mac=`cat $TEMP5/$port | grep '| mac_address' |awk '{print $4}'`
	allowed_ip=`cat $TEMP5/$port | grep allowed_address_pairs | awk '{print $5}' |  tr -cd '[[:digit:].-]'`
	sg=`cat $TEMP5/$port | grep security_groups |awk '{print $4}'`
	netid=`cat $TEMP5/$port | grep network_id |awk '{print $4}'`
	subnetid=`cat $TEMP5/$port | grep fixed_ips |awk '{print $5}' | sed 's/"//g' | sed 's/,//'`
	echo $ip > $TEMP5/$port-ip
	echo $sg > $TEMP5/$port-sg
	echo $mac > $TEMP5/$port-mac
	echo $netid > $TEMP5/$port-netid
	echo $subnetid > $TEMP5/$port-subnetid
	echo -n $allowed_ip > $TEMP5/$port-allowed_ip
done

tenantid=`cat $TEMP | grep tenant_id | awk '{print $4}'`
tenantname=`keystone tenant-get $tenantid |grep name |awk '{print $4}'`
AZ=`cat $TEMP | grep OS-EXT-AZ:availability_zone | awk '{print $4}'`
flavor=`cat $TEMP | grep flavor |awk '{print $5}' |sed -r 's/\(|\)//g'`
image=`cat $TEMP | grep image |awk '{print $5}' |sed -r 's/\(|\)//g'`
name=`cat $TEMP  | grep '| name ' |awk '{print $4}'`
JSON_KEYS=`cat $TEMP5/nova_curl  | jq -r '.server.metadata' |awk -F : '{print $1}' |sed 's/"//g' | grep -v { | grep -v } | sed 's/ //g'`

metakeys=0
for metakey in $JSON_KEYS
do
	metakeys=$((metakeys+1))
	cat $TEMP5/nova_curl  | jq -r -c ".server.metadata.$metakey" > $TEMP5/metadata-$metakey
	NOVA_OPTS="${NOVA_OPTS} --meta '$metakey'='`cat $TEMP5/metadata-$metakey`'"

done
NOVA_OPTS="${NOVA_OPTS} --image $image --flavor $flavor"
set +e

echo "$1 will be stopped and (offline) migrated to $2, ready?"
echo "you need to have admin role on $tenantname for this to work"
echo "y/n"
read continue
if [ x$continue != xy ]; then
	echo "bye bye"
	rm -f $TEMP5
	exit 1
fi


nova stop $1 &>/dev/null

until `nova show $1 |grep -q SHUTOFF`
do
	sleep 1
done

set -e
$SSH $SRC_HV "sudo -i chown nova /data/var/lib/nova/instances/$1/console.log"
if [ $? != 0 ]; then
	echo "can't chmod $SRC_HV/data/var/lib/nova/instances/$1/console.log to nova"
	exit 1
fi

$SSH $SRC_HV "sudo -u nova /usr/bin/rsync -e \"$SSH\" -rav /data/var/lib/nova/instances/$1 $DST_HV:/data/var/lib/nova/tmp/"
if [ $? != 0 ]; then
	echo "can't rsync from $SRC_HV to $DST_HV as nova"
	exit 1
fi


#nova delete $1 &>/dev/null

for port in `seq 1 $ports`
do
	nova interface-detach $1 `cat $TEMP5/$port-id`
	sleep 1
	neutron port-create --security-group `cat $TEMP5/$port-sg` --fixed-ip subnet_id=`cat $TEMP5/$port-subnetid`,ip_address=`cat $TEMP5/$port-ip` `cat $TEMP5/$port-netid` | grep '| id' | awk '{print $4}' > $TEMP5/$port-id
	NEUTRON_OPTS="--nic port-id=`cat $TEMP5/$port-id`"
done

echo \#!/bin/bash > $TEMPRUN
echo unset OS_SERVICE_TOKEN OS_SERVICE_ENDPOINT OS_TENANT_NAME OS_USERNAME OS_PASSWORD OS_AUTH_URL >>$TEMPRUN
echo export OS_USERNAME=$OS_USERNAME >> $TEMPRUN
echo export OS_PASSWORD=$OS_PASSWORD >> $TEMPRUN
echo export OS_AUTH_URL=${OS_AUTH_URL} >> $TEMPRUN
echo export OS_REGION_NAME=$OS_REGION_NAME >> $TEMPRUN
echo export OS_NO_CACHE=1 >> $TEMPRUN
echo export OS_TENANT_NAME=$tenantname >> $TEMPRUN
echo nova boot ${NOVA_OPTS} ${NEUTRON_OPTS} --availability-zone nova:$2 $name >> $TEMPRUN
bash $TEMPRUN | tee $TEMP5/new-vm

NEWUUID=`cat $TEMP5/new-vm  |grep '| id ' |awk '{print $4}'`

until `nova show $NEWUUID |grep -q ACTIVE`
do
	sleep 1
done

nova stop $NEWUUID &>/dev/null

until `nova show $1 |grep -q SHUTOFF`
do
	sleep 1
done

DST_HV=`grep host_ip $TEMP2 |awk '{print $4}'`
$SSH $DST_HV "sudo -u nova /bin/mv -f /data/var/lib/nova/tmp/$1/disk /data/var/lib/nova/instances/$NEWUUID/"
if [ $? != 0 ]; then
	echo "can't move disk from old $1 to new $NEWUUID"
	exit 1
fi

nova start $NEWUUID &>/dev/null

echo "$1 has been migrated to $2, its new uuid is $NEWUUID, can i delete $1?"
echo "y/n"
read continue
if [ x$continue != xy ]; then
	echo "bye bye"
	rm -f $TEMP5
	exit 1
fi

# cleanup
rm -rf $TEMP $TEMP2 $TEMP3 $TEMP5 $TEMPRUN
nova delete $1
