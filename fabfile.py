#!/usr/bin/env python
from __future__ import print_function
"""
fabric commands module which are used by the REST API, but can also be used directly.
"""
__author__='Guy Romm'


from future import standard_library
standard_library.install_aliases()
from builtins import str
import os
import re
import uuid
import datetime
import itertools
import io
import prettytable
import tempfile
from time import sleep
import time
from macgen import genmacs,randomMAC
from collections import defaultdict
from fabric.api import *
env.use_ssh_config = True
env.reject_unknown_hosts = False
env.disable_known_hosts = False

from fabric.contrib.files import exists, contains,append, comment, upload_template, sed
from fabric.context_managers import nested, shell_env
import fabric.contrib.files


from config import (HOSTS, VLAN_GATEWAYS, VLAN_RANGES, FLOATING_IPS,IPV6,DEFAULT_GATEWAY,HOST_GATEWAYS,
                    DEFAULT_RAM, DEFAULT_VCPU, OVPN_HOST, main_network, ovpn_client_network,ovpn_internal_addr,ssh_passwords,
                    ovpn_client_netmask, DIGEST_REALM, SECRET_KEY, IMAGES, LOWERED_PRIVILEGES, snmpd_network, OVPN_KEY_SENDER, JUMPHOST_EXTERNAL_IP, DEFAULT_SEARCH, DNS_HOST, OVPN_KEYDIR,FORWARDED_PORTS,
                    SSH_HOST_KEYNAME,SSH_VIRT_KEYNAME,SSH_KEYNAMES,IMAGE_FORMAT,DHCPD_DOMAIN_NAME,HYPERVISOR_HOSTNAME_PREFIX
)
from config import MAIL_LOGIN,MAIL_PASSWORD,MAIL_SERVER,MAIL_PORT
env.passwords=ssh_passwords

#make sure that key config settings are assigned
assert main_network and ovpn_client_network and ovpn_client_netmask,"%s , %s , %s"%(main_network, ovpn_client_network, ovpn_client_netmask)
assert DIGEST_REALM and SECRET_KEY
assert IMAGES

import os
#NETWORKING_RESTART_CMD='/etc/init.d/networking restart' #12.04
NETWORKING_RESTART_CMD='ifdown br0 && ifup br0' #14.04

@parallel
def iptables_allow_gw_subnet_ssh():
    netw = ".".join(DEFAULT_GATEWAY.split(".")[0:3]+['0']) + '/24'
    run('iptables -A INPUT --proto tcp --dport 22 -s %s -m comment --comment "ssh access from gw subnet" -j ACCEPT && iptables-save > /etc/iptables.save'%netw)

@parallel
def dhcpd_restart():
    run('service isc-dhcp-server restart')

@parallel
def dhcpd_status():
    run('service isc-dhcp-server status')

# """ intended usage: 
# for H in $(grep 'Host ' ssh_config | awk '{print $2}' | egrep 'hyperv' | sort ) ; do echo "doing "$H ; fab -H $H external_network_ssh | sed -E 's/\[hyperv([0-9]+)\]/hostname/g' > "/tmp/"$H".log" & done ; echo 'waiting' ; wait
# """
@parallel
def external_network_ssh():
    run('''for H in $(grep 'Host ' .ssh/config | awk '{print $2}' | egrep '^%s') ; do echo $H ; ssh -o StrictHostKeyChecking=no -o 'connecttimeout 1' $H 'lsb_release -s -d' ; done'''%HYPERVISOR_HOSTNAME_PREFIX)

# for H in $(grep 'Host ' ssh_config | awk '{print $2}' | egrep 'hyperv' | sort ); do echo "doing $H" ; fab -H $H internal_network_pings | sed -E 's/\[hyperv([0-9]+)\]( out: | )//g' | sed -E 's/\.([0-9]{3})/\.000/g' > /tmp/internal-$H".log" & done ; echo 'waiting'  ; wait
@parallel
def internal_network_pings():
    for h,ip in list(VLAN_GATEWAYS.items()):
        run('ping -q -c1 %s #pinging %s'%(ip,h))

@parallel
def network_restart():
    run(NETWORKING_RESTART_CMD+ ' ; ip a show br0')

@parallel
def uptime():
    run('uptime')

@parallel
def gdrive_install():
    if not exists('/usr/local/bin/gdrive'): run("cd /usr/local/bin && curl -L -o gdrive 'https://drive.google.com/uc?id=0B3X9GlR6EmbnUWZGRmYxVUU2M00' && chmod +x gdrive")
    if not exists('~/.gdrive'): run('mkdir ~/.gdrive')
    put('conf_repo/gdrive/config.json','/root/.gdrive/')
    put('conf_repo/gdrive/token.json','/root/.gdrive/')

@parallel
def gdrive_get_image(imgid,fn):
    run("cd /var/lib/libvirt/images && gdrive download -i '%s' && gzip -d %s"%(imgid,fn))

@parallel
def hostname():
    run('hostname')

@parallel
def search_host(hostname):
    run('''virsh list --all | grep %s ||:'''%hostname)
@parallel
def search_macaddr(macaddr):
    run('''grep '%s' /etc/dhcp/dhcpd.conf ||:'''%macaddr)
    run('''grep '%s' /etc/libvirt/qemu/*xml ||:'''%macaddr)


@parallel
def group_auth(group,pubkey):
    """ add a public key to a whole group of hosts """ 
    pks = local('cat %s'%pubkey,capture=True).strip().split("\n")
    for pk in pks:
        op = run('''virsh list | awk '{print $2}' | egrep '^%s-' ||:'''%group)
        for n in [n.strip() for n in op.split("\n") if n.strip()!='']:
            mac = macaddr_by_nodename(n)
            continue
            ip = ip_by_macaddr(mac)
            print ('%s => %s => %s'%(n,mac,ip))
            execute(append,
                    '/root/.ssh/authorized_keys',
                    pk,
                    host=ip)

@parallel
def node_auth(pk):
    """ add a public key to a particular host """ 
    append('/root/.ssh/authorized_keys',pk)

    
def auth_add(host,pubkey):
    pk = local('cat %s'%pubkey,capture=True).strip()
    mac = macaddr_by_nodename(host)
    ip = ip_by_macaddr(mac)
    execute(append,
            '/root/.ssh/authorized_keys',
            pk,
            host=ip
            )

@parallel
def group_wipe(group,do=False):
    op = run("""virsh list --all | awk '{print $2}' | egrep '^%s-' ||:"""%group)
    for n in [n.strip() for n in op.split("\n") if n.strip()!='']:
        if 'control' in n: continue #do not wipe control nodes!
        print("going to wipe",n)
        if do:
            with settings(warn_only=True):
                run("virsh destroy %s"%n)
            run("virsh undefine %s"%n)
            run("rm /var/lib/libvirt/images/%s.img"%n)

@parallel
def group_start(group):
    op = run("""virsh list --all | awk '{print $2}' | egrep '^%s-' ||:"""%group)
    for n in [n.strip() for n in op.split("\n") if n.strip()!='']:
        run("virsh start %s"%n)

@parallel
def group_destroy(group):
    op = run("""virsh list --all | awk '{print $2}' | egrep '^%s-' ||:"""%group)
    for n in [n.strip() for n in op.split("\n") if n.strip()!='']:
        run("virsh destroy %s"%n)

@parallel
def check_hostnames(halt=False,rename=False):
    op = run("""virsh list --all | awk '{print $2}' | egrep -v Name""")
    vhosts = [n.strip() for n in op.split('\n') if n.strip()!='']
    for n in vhosts:
        macaddr = run("""virsh dumpxml %s  | xpath -q -e  '/domain/devices/interface/mac/@address' | cut -f2 -d'"'"""%n).strip()
        ipaddr = run("""grep %s /etc/dhcp/dhcpd.conf | cut -f2 -d';' | cut -f3 -d' '"""%macaddr).strip()
        inthn = run('ssh  -o "LogLevel quiet" -o "StrictHostkeychecking no" %s hostname'%ipaddr).strip()
        if inthn!=n:
            print('mismatch between libvirt hostname ',n,'and internal machine hostname',inthn,macaddr,ipaddr)
            if halt: raise Exception('mismatch found ; aborting')
            if not rename: continue
            # make sure target image does not exist
            assert not exists('/var/lib/libvirt/images/%s.img'%inthn),"image for %s already exists"%inthn
            # make sure target vhost with such a name does not exist
            assert inthn not in vhosts,"vhost %s already exists!"%inthn
            # come up with the rename commands to run for both vhost and image. reboot required
            cmds = ['virsh destroy %s'%n,
                    """virsh dumpxml %(old)s | sed 's/%(old)s/%(new)s/g' > /tmp/%(new)s.tmp.xml"""%{'old':n,'new':inthn},
                    "mv /var/lib/libvirt/images/%s.img /var/lib/libvirt/images/%s.img"%(n,inthn),
                    "virsh undefine %s"%n,
                    "virsh define /tmp/%s.tmp.xml"%inthn,
                    "virsh start %s"%inthn]
            for cmd in cmds:
                run(cmd)

# here's how one could obtain netmasks for all used ips
# fab -R kvm list_ips | egrep -v '^\[' | egrep -v '^$' | egrep -v '(Disconnecting|Done)' | awk '{print $3}' | cut -f1-3 -d'.' | sort | uniq -c | sort -n
def list_ips():
    # fab -R kvm list_ips | awk '{print $3}'  | egrep -v '^$' | cut -f1-3 -d'.' | sort | uniq -c | sort -n
    with hide('warnings','running','output'):
        ips = run("""ifconfig  | grep 'inet addr:'| egrep -v '(127.0.0.1|192.168)' | cut -d: -f2 | awk '{ print $1}'""").strip().split("\n")
    for ip in ips:
        print('host',env.host_string,ip)
    for fip in FLOATING_IPS:
        if fip[0]!=env.host_string: continue
        print('service',fip[0],fip[2])
    #raise Exception(dict([k,v for i[0],i[1] in FLOATING_IPS]),env.host_string)
@parallel
def backup_nodedefs():
    tdir = 'node-confs/%s'%env.host_string
    local('mkdir -p %s'%tdir)
    get('/etc/libvirt/qemu/*xml',tdir)
    op = run('ls -s /var/lib/libvirt/images | sort -k2')
    fp = open(os.path.join(tdir,'images.txt'),'w')
    fp.write(op)
    fp.close()
@parallel
def setup_network(snmpd_network=snmpd_network,writecfg=True,restart=True,runbraddcmd=True):
    main_ip = HOSTS[env.host_string]
    my_floating_ips = [x for x in FLOATING_IPS if x[0]==env.host_string]

    apnd=[]
    for _,virt_ip,real_ip in my_floating_ips:
        apnd.append("up ip route add %s/32 via %s"%(real_ip,virt_ip))
        apnd.append("down ip route del %s/32"%(real_ip))
    apnd='\n  '.join(apnd)

    gw = HOST_GATEWAYS.get(env.host_string) and HOST_GATEWAYS.get(env.host_string)  or DEFAULT_GATEWAY
    #raise Exception('main_ip for',env.host_string,'is',main_ip)
    varss = {'main_ip':main_ip,
             'ovpn_internal_addr':ovpn_internal_addr,
             'ovpn_client_network':ovpn_client_network,
             'gateway':gw,
             'main_ipv6_ip':IPV6.get(env.host_string),
             'main_network': main_network,
             'vlan_gw':VLAN_GATEWAYS[env.host_string],
             'vlan_bcast': main_network+'.0.255',
             'my_floating_ips':apnd,
             'extra':'',}
    badaddr = '.'.join(ovpn_internal_addr.split('.')[0:2])
    assert not main_ip.startswith(badaddr),"achtung - setting main ip to internal addr %s"%main_ip
    with cd('/etc/network'):
        upload_template('server-confs/interfaces','interfaces.install',varss)
        with settings(warn_only=True):
            if run('diff /etc/network/interfaces /etc/network/interfaces.install'):
                if writecfg:
                    run('cp /etc/network/interfaces /etc/network/interfaces.backup')
                    run('cp /etc/network/interfaces.install /etc/network/interfaces')
                else:
                    print('NOT COPYING NEW CONFIG IN PLACE. it stays in /etc/network/interfaces')
                tries = 0
                if restart:
                    while not run(NETWORKING_RESTART_CMD):
                        if tries >= 5:
                            break
                        tries += 1
                        sleep(1)
                else:
                    print('NOT RUNNING NETWORKING RESTART:',NETWORKING_RESTART_CMD)
            braddcmd = '''ip link | grep -o "vnet[0-9]*"  > /tmp/vnets ; brctl show br0 | egrep -v '^(bridge name|br0)' | awk '{print $1}' >> /tmp/vnets ; cat /tmp/vnets | sort | uniq -u | xargs -r -L1 brctl addif br0 ; %s'''%NETWORKING_RESTART_CMD
            if runbraddcmd:
                run(braddcmd)
            else:
                print('NOT RUNNING BRADDCMD',braddcmd)
            run('service isc-dhcp-server restart')
    install_snmpd(snmpd_network)


def setup_dhcpd():

    vlan_gw = VLAN_GATEWAYS[env.host_string]
    vlan_range = VLAN_RANGES[env.host_string]
    virt_defs = genmacs(only_host=env.host_string)

    dhcpd_config_fn = os.path.join('dhcpd-confs',env.host_string+'.conf')
    if os.path.exists(dhcpd_config_fn):
        source_tpl = dhcpd_config_fn
        base_vars={}
    else:
        source_tpl = 'server-confs/dhcpd-ovpn.conf'

        classless_static_routes = \
                                  '16, '+\
                                  ', '.join(ovpn_client_network.split('.')[0:2])+', '+\
                                  ', '.join(ovpn_internal_addr.split('.'))
        base_vars = {
            'vlan_gw': vlan_gw,
            'dhcpd_domain_name':DHCPD_DOMAIN_NAME,
            'classless_static_routes':classless_static_routes,
            'main_network': main_network,
            'vlan_range': vlan_range,
            'virt_defs': virt_defs
        }


    with cd('/etc/dhcp/'):
        put(source_tpl,'dhcpd.conf.install',base_vars)
        with settings(warn_only=True):
            if run('diff /etc/dhcp/dhcpd.conf.install /etc/dhcp/dhcpd.conf'):
                run('cp /etc/dhcp/dhcpd.conf /etc/dhcp/dhcpd.conf.backup')
                run('cp /etc/dhcp/dhcpd.conf.install /etc/dhcp/dhcpd.conf')
                run('service isc-dhcp-server restart')


@parallel
def install_xsltproc():
    run('sudo apt-get -q -y install xsltproc libxml-xpath-perl')
    #put xslt stylesheet for parsing node definitions
    put('server-confs/stylesheet.xsl','/etc/stylesheet.xsl')

@parallel
def put_ssh_privkey(kfn,force_put=False):
    rkfn = './.ssh/%s'%kfn
    if not force_put and env.host_string in LOWERED_PRIVILEGES:
        if fabric.contrib.files.exists(rkfn):
            run('rm %s'%rkfn)
    else:
        if not fabric.contrib.files.exists(rkfn):
            put(os.path.join('conf_repo',kfn),rkfn)
            run('chmod 400 %s'%rkfn)

@parallel
def install_ssh_config():
    if not env.host_string in LOWERED_PRIVILEGES:
        if not fabric.contrib.files.exists('./.ssh/ssh_config'):
            sshc = open('ssh_config','r').read().replace(' %s'%SSH_HOST_KEYNAME,' ~/.ssh/%s'%SSH_HOST_KEYNAME).replace(' %s'%SSH_VIRT_KEYNAME,' ~/.ssh/%s'%SSH_VIRT_KEYNAME)
            unc = io.BytesIO(sshc)
            put(unc,'.ssh/config')

    for kfn in SSH_KEYNAMES:
        put_ssh_privkey(kfn)

@parallel
def install(apt_update=False,snmpd_network=snmpd_network,stop_before_network=False):
    if apt_update or not fabric.contrib.files.exists('/var/cache/apt/pkgcache.bin'): run('sudo apt-get -q update')
    #install kvm
    run('sudo apt-get -q -y install qemu-kvm libvirt-bin ubuntu-vm-builder bridge-utils isc-dhcp-server zile pigz tcpdump pv sendemail sysstat htop iftop nload xmlstarlet ncdu mosh')
    run('sudo adduser `id -un` libvirtd')
    run("echo '%s' > /etc/hostname"%env.host_string)
    run ("hostname %s"%env.host_string)
    #download an image
    imgdir = '/var/lib/libvirt/images'

    #pub a key in for the virtual machines
    if not fabric.contrib.files.exists('.ssh'):
        run('mkdir .ssh')

    #ssh_config to access other hosts
    install_ssh_config() 

    if stop_before_network: 
        print('STOPPING BEFORE NETWORK CONFIG') 
        return
    setup_network(snmpd_network)
    setup_port_forwarding()
    setup_dhcpd()
    install_xsltproc()

    #this works for a host machine:
    #ip addr add 10.0.1.2 dev br0
    #ip route add 10.0.1.0/16 via 10.0.1.2 dev br0

    #this sets up manual networking for a guest
    #ip addr add 10.0.1.4 dev eth0
    #ip route add 10.0.1.0/16 dev eth0 via 10.0.1.4


def setup_port_forwarding():
    myipt = init_ipt()
    print('myipt is',myipt)
    if not env.host_string in FORWARDED_PORTS: return
    for fp in FORWARDED_PORTS[env.host_string]:
        cmd = 'iptables -tnat -A PREROUTING -d %(endpoint_host)s/32 -p tcp -m tcp --dport %(endpoint_port)s -j DNAT --to-destination %(redirect_host)s:%(redirect_port)s'%fp
        cont = [ln.strip() for ln in open(myipt,'r').read().split("\n") if ln.strip()!='']
        if cmd not in cont:
            cont.append(cmd)
            run(cmd) # execute immediately in case we can't find it in the file
    fp = open(myipt,'w') ; fp.write("\n".join(cont)) ; fp.close()
    if os.path.exists(myipt):
        put(myipt,'/etc/iptables.sh')
@parallel
def destroy(node):
    rt=run('virsh destroy %s ||:'%node)
    return rt


def start(node=None):
    if fabric.contrib.files.exists(os.path.join('/etc/libvirt/qemu/','%s.xml'%(node))):
        rt = run('virsh start %s'%node)
        return rt


def resume(node):
    rt=run('virsh resume %s'%node)
    return rt

def reboot(node):
    rt=run('virsh reboot %s'%node)
    return rt


def parse_dhcp_line(line):
    exp = re.compile('host\s+virt-(.+)-(\d+).*ethernet\s+(.+);.*fixed-address\s+(.+);')
    m = exp.search(line)
    if not m:
        return None
    origin = m.group(1)
    num = int(m.group(2))
    mac = m.group(3)
    ip = m.group(4)
    clean = 'host virt-%s-%s { hardware ethernet %s; fixed-address %s; }' \
            % (origin, num, mac, ip)
    return (clean, origin, num, mac, ip)


def dhcp_move(src_host, dest_host, mac_addr, image_name, setup=False):
    assert src_host!=dest_host,"%s == %s"%(src_host,dest_host)
    #sort out dhcpd
    srcfn = os.path.join('dhcpd-confs', '%s.conf' % src_host)
    dstfn = os.path.join('dhcpd-confs', '%s.conf' % dest_host)
    src_dhcpd = open(srcfn, 'r').read().split('\n')
    dst_dhcpd = open(dstfn, 'r').read().split('\n')
    src_ip_lines = [x for x in src_dhcpd if mac_addr in x]
    dst_ip_lines = [x for x in dst_dhcpd if mac_addr in x]
    if len(src_ip_lines) == 1 and len(dst_ip_lines) == 0:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        args = {
            'image_name': image_name,
            'src_host': src_host,
            'dest_host': dest_host,
            'timestamp': now,
        }
        raw_line = src_ip_lines[0]
        orig_line, origin, _, _, _ = parse_dhcp_line(raw_line)
        src_dhcpd.remove(raw_line)
        if origin == dest_host:
            dst_dhcpd.append(orig_line)
        else:
            dst_dhcpd.append(raw_line + \
            ' #migration of %(image_name)s [%(src_host)s -> %(dest_host)s on %(timestamp)s]'\
            % args)
        with open(srcfn,'w') as fp:
            fp.write('\n'.join(src_dhcpd) + '\n')
        with open(dstfn,'w') as fp:
            fp.write('\n'.join(dst_dhcpd) + '\n')
        if setup:
            with settings(host_string=src_host):
                setup_dhcpd()
            with settings(host_string=dest_host):
                setup_dhcpd()
    elif len(src_ip_lines) == 0 and len(dst_ip_lines) == 1:
        print('assume the restore after a broken migration for', mac_addr)
    else:
        assert len(dst_ip_lines) == 0, "found %s in %s" % (mac_addr, dstfn)
        assert len(src_ip_lines) == 1, "cannot find %s in %s" % (mac_addr, srcfn)


def undefine(node, target, del_image=True):
    srcfn = os.path.join('dhcpd-confs', '%s.conf' % target)
    src_lines = []
    pattern = ' of ' + node + ' ['
    with open(srcfn, 'r') as infile:
        for line in infile:
            if pattern in line:
                src_lines.append(line)
    if src_lines:  # node was migrated
        assert len(src_lines) == 1,\
               "should be one and only %s in %s" % (node, srcfn)
        _, origin, _, mac, _ = parse_dhcp_line(src_lines[0])
        dhcp_move(target, origin, mac, node, setup=True)
    rt = run('virsh undefine %s' % node)
    if del_image: run('rm -f /var/lib/libvirt/images/%(image_name)s.img'\
                      % {'image_name': node})
    return rt


def migrate(image_name, dest_host, src_host=None, mac_addr=None,nocopy=False):
    #find out where image_name resides
    if not src_host or not mac_addr:
        alst = _lst(al=True)
        rt = alst[image_name]
        assert rt,"could not find %s,\n%s" % (image_name, rt)
        src_host = rt['host']
        mac_addr = rt['mac']
        if rt['state'] == 'running':
            with settings(host_string=src_host):
                run('virsh destroy %s' % image_name)
    else:
        rt={'host': dest_host,
            'mac': mac_addr,
            'name': image_name}
    dhcp_move(src_host, dest_host, mac_addr, image_name, setup=True)
    #run on source
    try:
        xml_node_description = '/tmp/' + uuid.uuid4().hex
        print('xml_node_description>', xml_node_description)
        with settings(host_string=src_host):
            get(os.path.join('/etc/libvirt/qemu', '%s.xml' % image_name),
                local_path=xml_node_description)
            #rsynccmd = 'rsync -e "ssh -F ssh_config" %(src_host)s:/var/lib/libvirt/images/%(image_name)s.img %(dest_host)s:/var/lib/libvirt/images/%(image_name)s.img' % 

            
            rsynccmd = 'scp -3 -F ssh_config %(src_host)s:/var/lib/libvirt/images/%(image_name)s.img.gz %(dest_host)s:/var/lib/libvirt/images/%(image_name)s.img.gz' % \
                {'image_name': image_name,
                 'dest_host': dest_host,
                 'src_host':src_host}
            if not nocopy:
                pigzsrcname = '/var/lib/libvirt/images/%s.img'%image_name
                pigzdstname = pigzsrcname+'.gz'
                if exists(pigzdstname) and not exists(pigzsrcname):
                    print('skipping pigz - compressed file exists without source file.')
                else:
                    start = time.time()
                    run('pigz %s'%pigzsrcname)
                    end = time.time()
                    print('pigz took',(end - start),'seconds')

                start = time.time()
                local(rsynccmd)
                end = time.time()
                print('rsync/scp took',(end - start),'seconds')

                start = time.time()
                with settings(host_string=dest_host):
                    run('pigz -d /var/lib/libvirt/images/%s.img.gz'%image_name)
                end = time.time()
                print('pigz -d took',(end - start),'seconds')



        with settings(host_string=dest_host):
            put(xml_node_description, '/tmp/%s.xml' % image_name)

        with settings(host_string=dest_host):
            run('virsh define /tmp/%s.xml' % image_name)
        with settings(host_string=src_host):
            run('virsh undefine %s' % image_name)
            if not nocopy: run('rm /var/lib/libvirt/images/%(image_name)s.img.gz' % {'image_name': image_name})
    finally:
        if os.path.exists(xml_node_description):
            os.unlink(xml_node_description)
    return rt


@runs_once
def download_image(from_host,image_name,save_as=None):
    hn = env.host_string
    if not save_as:
        save_as = '%s-%s'%(from_host,image_name)
    execute(get,
            os.path.join('/var/lib/libvirt/images/',image_name),
            save_as,
            host=from_host)


@parallel
def upload_image(image_name,save_as=None):
    to_host = env.host_string
    if not save_as: save_as = os.path.basename(image_name)
    remotefn = os.path.join('/var/lib/libvirt/images',save_as)
    assert not fabric.contrib.files.exists(remotefn),  Exception('remote file %s exists.'%remotefn)
    put(image_name,remotefn)


def get_existing_macs(byhost=False):
    grpcmd = """egrep "<mac address='([^']*)" /etc/libvirt/qemu/*xml -o 2>/dev/null | egrep -o '([a-f0-9\:]{17})'"""
    byh={}
    with settings(warn_only=True):
        with hide('warnings','running','output'):
            op = execute(run,grpcmd,roles=['kvm'])
            macs=[]
            for host,mac in op.items():
                if host not in byh: byh[host]=[]
                if mac=='': continue
                if not hasattr(mac,'split'): raise Exception('wtf %s ret %s'%(host,mac))
                tmacs = mac.split('\r\n')
                for tmac in tmacs:
                    macs.append(tmac)
                    if tmac not in byh[host]: byh[host].append(tmac)
    if byhost:
        print(byh)
        return byh
    return macs


@runs_once
def arp():
    with settings(parallel=True):
        alines=[]
        with hide('running','output'):
            rta = execute(run,'arp')
            for h,rt in rta.items():
                lines = [ln for ln in rt.split('\n') if not ln.startswith('Address')]
                for line in lines:
                    line = line.strip()
                    if line not in alines:
                        alines.append(line)
        print('\n'.join(alines))


def new_mac():
    #ANALLY FENCED
    macs = get_existing_macs()
    dom_counters={}
    with hide('running', 'output'):
        ourmacs = run("""cat /etc/dhcp/dhcpd.conf | egrep -i -o '([0-9A-F]{2}:){5}([0-9A-F]){2}'""").split('\r\n')
    for mac in ourmacs:
        if mac not in macs:
            break
    assert mac
    #/ANALLY FENCED
    return mac


def create_node(node_name,
                template_name=list(IMAGES.items())[0][0],
                memory=DEFAULT_RAM, 
                vcpu=DEFAULT_VCPU, 
                configure=True,
                simulate=False):
    tplfn = os.path.join('/var/lib/libvirt/images',template_name)
    nodefn = os.path.join('/var/lib/libvirt/images','%s.img'%node_name)
    assert fabric.contrib.files.exists(tplfn),"%s does not exist"%tplfn
    assert not fabric.contrib.files.exists(nodefn),"%s exists"%nodefn
    ns = uuid.NAMESPACE_DNS
    print('about to create uuid for node with ns %s, node name %s'%(ns,node_name))
    uuidi = uuid.uuid5(namespace=ns,name=node_name)
    variables = {
        'uuid':str(uuidi),
        'name':node_name,
        'image':node_name,
        'mac':new_mac(),
        'memory':memory,
        'vcpu': vcpu,
        'imgfmt':IMAGE_FORMAT,
        'simulate':simulate,
    }
    if simulate=='1': return variables

    run('cp %s %s'%(tplfn,nodefn))
    if simulate=='2': return variables

    upload_template('server-confs/node-tpl.xml','/tmp/%s.xml'%node_name,variables)
    if simulate=='3': return variables
    run('virsh define /tmp/%s.xml'%node_name)
    if simulate=='4': return variables
    rt=  True
    if configure:
        sleep(5)
        rt = configure_node(node_name)
        if simulate=='5': return variables
    return rt

def macaddr_by_nodename(name):
    macaddr = run("""virsh dumpxml %s | xpath -q -e '/domain/devices/interface/mac/@address' | cut -f2 -d'"'"""%name).strip()
    return macaddr

def ip_by_macaddr(mac):
    with hide('running','output'):
        grepcmd  = """cat /etc/dhcp/dhcpd.conf | grep -i "%s" | egrep -o '([[:digit:]]+)\.([[:digit:]]+)\.([[:digit:]]+)\.([[:digit:]]+)'"""%mac
        #print 'about to run %s on %s'%(grepcmd,env.host_string)
        try:
            ip = run(grepcmd).strip()
        except:
            ip = '-'
            print('failed node_network_info on %s'%mac)
    return ip

def node_network_info(ourhost):
    infos={}
    key = '-'.join([ourhost['host'],ourhost['id'],ourhost['name']])

    #print 'getting node network info for %s'%ourhost
    #find out node's mac addr
    with settings(warn_only=True):
        nodefn = os.path.join('/etc/libvirt/qemu/',ourhost['name']+'.xml')
        if not fabric.contrib.files.exists(nodefn): return
        with hide('running','output'):
            rt = run('xsltproc /etc/stylesheet.xsl %s'%nodefn)
    if not rt: return

    ourhost['mac'] = rt.strip()
    #print 'virtual node %s was spotted with mac %s in file %s on host %s'%(ourhost['name'],ourhost['mac'],nodefn,env.host_string)
    #find out node's ip addr
    ip = ip_by_macaddr(ourhost['mac'])

    ourhost['virt_ip'] = ip
    infos[key] = ourhost
    return ourhost

@parallel
#@serial
def virt_nodes():
    #with settings(hide('running','output','stdout','stderr','status','aborts','debug','exceptions')):
    op = run("virsh list | awk '{print $2}' | egrep -v '^Name'")
    fp = open('/tmp/%s-nodeslist.txt'%env.host_string,'w')
    spl = op.split('\n')
    print('iterating over',len(spl),'elements')
    spl = [n.strip() for n in spl]
    ips={}
    for nn in spl:
        if not nn: continue
        mac = execute(macaddr_by_nodename,nn,host=env.host_string)[env.host_string]
        ip = execute(ip_by_macaddr,mac,host=env.host_string)[env.host_string]
        ips[nn]=ip
        fp.write(" ".join([nn,mac,ip,env.host_string])+"\n")
    fp.close()
    return ips

@runs_once
def list_(al=False, display=True,network_info=True,memory=False):
    rt = _lst(al, display=display,network_info=network_info,memory=memory)
 

def hostnames_add_dns(simulate=False):
    l = virt_nodes()
    for h,ip in list(l.items()):
        add_hostname_dns(h,ip,simulate=simulate)

def add_hostname_dns(h,ip,simulate=False):
    s = DEFAULT_SEARCH
    dom = '%s.%s'%(h,s)
    print('setting',ip,dom,'for',h,'on',env.host_string,'with DNS_HOST=',DNS_HOST)
    if not simulate:
        execute(add_dns,dom,ip,host=DNS_HOST)
        
def group_configure(group,only_pub_ips=True):
    l = _lst(True,False,True,False)
    for hn,pubip in [(k,v['public_ip']) for k,v in l.items() if k.startswith(group+'-') and (not only_pub_ips or  v['public_ip'])]:
        print('fab -H %s configure_node:%s #owner of %s'%(env.host,hn,pubip))
        #execute(configure_node,node_name=hn)

def list_org(s):
    def srt(fn):
            splt = fn.split('|')
            return splt[1].strip()+','+splt[3].strip()
    sa = s.split('\n')
    rt = [sa[1],sa[2].replace('+---','|---').replace('---+','---|')]
    rt+=sorted(sa[3:-1],key=srt)
    return ('\n'.join(rt))
    
def _lst(al=False, display=True,network_info=True,prefix_re=None,memory=False,recurs=0):
    if type(network_info)==str and network_info.lower() in ['0','false']:
        network_info=False
    else:
        network_info=True

    cmd = 'virsh list'
    linere = re.compile('^\s*(\-|\d+)\s+([\w\.\-]+)\s+([\w ]+)')
    dct=defaultdict(lambda:{})
    if al: cmd+= ' --all'
    with settings(parallel=True,warn_only=True):
        with hide('output','running'):
            rt = execute(run,cmd) #.split('\n')
    hdr = ['host','node id','node name','node state','node mac','node virt ip','public ip']
    if memory: hdr+=['mem']
    pt = prettytable.PrettyTable(hdr)
    for hn,op in rt.items():
        if hn=='<local-only>': hn=env.host_string
        for ln in op.split('\n'):
            if ln.startswith('---') or ln.startswith('Id'): continue
            parsed = linere.search(ln)
            assert parsed,ln
            hid,hname,hstate = parsed.groups()

            #print 'going over %s , %s, %s which was found in %s'%(hid,hname,hstate,hn)
            #filtering by group which is prefixed to the name
            if prefix_re and not prefix_re.search(hname): continue

            dct[hname]={'id':hid,'name':hname,'state':hstate,'host':hn}

            if memory:
                cmd = """virsh dumpxml %s | xpath -q -e '/domain/memory//text()'"""%hname
                print('running %s on %s'%(cmd,hn))
                mem = execute(run,cmd,hosts=[dct[hname]['host']])[hn].strip()
            else:
                mem = None

            if network_info:
                with hide('running'):
                    execute(node_network_info,dct[hname],hosts=[dct[hname]['host']])
            my_floating_ips = ', '.join([x[2] for x in [x for x in FLOATING_IPS if x[1]==dct[hname].get('virt_ip')]])
            rw = [hn,hid,hname,hstate,dct[hname].get('mac'),dct[hname].get('virt_ip'),my_floating_ips]
            dct[hname]['public_ip']=my_floating_ips
            if memory: rw+=[mem]
            pt.add_row(rw)

    if display:
        print(list_org(str(pt)))
    return dct


def runon(virt_ip,cmd):
    cmd = 'ssh %s %s'%(virt_ip,cmd)
    return run(cmd)


def configure_node(node_name,fresh_check=True):
    ah = _lst(al=True,display=False)
    ourhost = ah[node_name]
    print(ourhost)
    assert 'host' in ourhost,"%s does not have 'host'"%ourhost
    if ourhost['host'] != env.host_string:
        return
    to_cnt=0
    if  ourhost['state']!='running':
        startcmd = 'virsh start %s'%node_name
        execute(run,startcmd,host=ourhost['host'])
        while True:
            print('ourhost state is %s ..'%ourhost['state'])
            sleep(1)
            ourhost = _lst(al=True)[node_name]
            if ourhost['state']=='running':
                break
            to_cnt+=1
            assert to_cnt<40,"timeout reached"
        print('host reported as running.')
    with settings(warn_only=True,hide=['running','output']):
        assert 'virt_ip' in ourhost and ourhost['virt_ip'],"list ran shitty and did not give us back virt_ip field: %s"%ourhost
        print('ourhost=',ourhost)
        while execute(run,'nc -z %s 22' % ourhost['virt_ip'], host=ourhost['host'])[ourhost['host']].failed:
            to_cnt+=1
            sleep(1)
            assert to_cnt<80,"port test timeout reached"
    print('***', ourhost)
    myhostname = execute(run,'ssh -o "LogLevel quiet" -o "StrictHostkeychecking no" -o "StrictHostkeyChecking no" %s hostname'%ourhost['virt_ip'],host=ourhost['host'])[ourhost['host']].strip()
    # DIRTY HACK - FIXME: need to investigate  why hostname could be 'tst'
    #if fresh_check:
    #    assert (myhostname in ('tst', 'ubuntu')),"My hostname is %s"%myhostname

    #1. assign any floating ips that need be on this machine
    my_floating_ips = [x for x in FLOATING_IPS if x[1]==ourhost['virt_ip']]

    floating_ips_cont=''
    tpl ="""
auto eth0:%(cnt)s
  iface eth0:%(cnt)s inet static
  address %(floating_ip)s
"""
    cnt=0
    for fip in my_floating_ips:
        floating_ips_cont+=tpl%{'cnt':cnt,'floating_ip':fip[2]}
        cnt+=1
    varss = {'floating_ips':floating_ips_cont}
    interfacestmp = '/tmp/interfaces-%s'%node_name
    upload_template('node-confs/interfaces',interfacestmp,varss)
    execute(run,'scp -o "StrictHostkeyChecking no" %s %s:/etc/network/interfaces'%(interfacestmp,ourhost['virt_ip']),host=ourhost['host'])
    with settings(warn_only=True):
        execute(run,\
                ('ssh -o "StrictHostkeyChecking no" %(virt_ip)s '%ourhost)+NETWORKING_RESTART_CMD,host=ourhost['host'])

    #2. set up a correct hostname
    execute(run,'ssh -o "StrictHostkeyChecking no" %(virt_ip)s "hostname %(name)s"'%ourhost,host=ourhost['host'])
    execute(run,'ssh -o "StrictHostkeyChecking no" %(virt_ip)s "echo %(name)s > /etc/hostname"'%ourhost,host=ourhost['host'])
    execute(run,'ssh -o "StrictHostkeyChecking no" %(virt_ip)s "echo 127.0.0.1 %(name)s >> /etc/hosts"'%ourhost,host=ourhost['host'])
    if DNS_HOST:
        add_hostname_dns(ourhost['name'],ourhost['virt_ip'])
    return ourhost


def install_optional():
    run('apt-get -q -y install emacs23-nox tcpdump tmux')


def status():
    run('virsh -c qemu:///system list')


@parallel
def uname():
    run('uname -a')

def lsb_release():
    run('lsb_release -a')

def noop():
    pass


def host_reboot():
    run('reboot')


def setup_openvpn(node_name):
    ourhost = list_(al=True, display=False)[node_name]
    print(ourhost)
    if ourhost['host'] != env.host_string:
        return

    #hackish but fuckit.
    run('echo "nameserver 8.8.8.8" > /etc/resolv.conf')
    # first of all!!
    run('sysctl -w net.ipv4.ip_forward=1')
    append('/etc/sysctl.conf','net.ipv4.ip_forward=1')
    
    #1. assign any floating ips that need be on this machine
    my_floating_ips = [x for x in FLOATING_IPS if x[1]==ourhost['virt_ip']]

    floating_ips_cont=''
    tpl ="""
auto eth0:%(cnt)s
  iface eth0:%(cnt)s inet static
  address %(floating_ip)s
"""
    cnt=0
    for fip in my_floating_ips:
        floating_ips_cont+=tpl%{'cnt':cnt,'floating_ip':fip[2]}
        cnt+=1

    vlan_gw = VLAN_GATEWAYS[env.host_string]
    varss = {
        'virt_ip': ourhost['virt_ip'],
        'floating_ips':floating_ips_cont,
        'gateway': vlan_gw}
    
    interfacestmp = '/tmp/interfaces-%s'%node_name
    upload_template('node-confs/static-interfaces',
                    interfacestmp,
                    varss)
    execute(run,'scp -o "StrictHostkeyChecking no" %s %s:/etc/network/interfaces'%(interfacestmp,ourhost['virt_ip']),host=ourhost['host'])
    with settings(warn_only=True):
        execute(run,\
                ('ssh -o "StrictHostkeyChecking no" %(virt_ip)s '%ourhost)+NETWORKING_RESTART_CMD,host=ourhost['host'])

    with settings(shell='ssh -t -o "StrictHostkeyChecking no" %s' % ourhost['virt_ip']):
        run("apt-get -y --force-yes install openvpn sendemail")
        with cd('/etc/openvpn'):
            if not exists('easy-rsa'): run('mkdir easy-rsa')
            sdirs = ['/usr/share/doc/openvpn/examples/easy-rsa/2.0/','/usr/share/doc/openvpn/examples/sample-keys/',None]
            for dd in sdirs:
                if exists(dd): break
            assert dd,"could not find start dir for keys"
            run('cp -R %s* easy-rsa/'%dd)
            with cd('easy-rsa'):
                if not exists('openssl.cnf') and exists('openssl-1.0.0.cnf'):
                    run('ln -f -s openssl-1.0.0.cnf openssl.cnf')
                #sudo('cp %s/data/openvpn/vars .' % dirname)
                with prefix("source vars"):
                    run('./clean-all')
                    run('./build-dh')
                    run('./pkitool --initca')
                    run('./pkitool --server server')
                with cd('keys'):
                    run('openvpn --genkey --secret ta.key')
                    run('cp ca.crt dh1024.pem ta.key server.key server.crt ../../')
            append('/etc/openvpn/easy-rsa/keys/index.txt.attr','unique_subject = no')

    my_floating_ips = [x for x in FLOATING_IPS if x[1]==ourhost['virt_ip']]
    varss = {'bind_ip': my_floating_ips[0][2],
             'vlan_network':main_network+'.0.0',
             'ovpn_client_network':ovpn_client_network,
             'ovpn_client_netmask':ovpn_client_netmask,
             'vlan_netmask':'255.255.0.0'
             }
    openvpn_conf_tmp = '/tmp/openvpn.conf-%s'%node_name
    upload_template('node-confs/openvpn_server.conf',openvpn_conf_tmp,varss)
    run('scp -o "StrictHostkeyChecking no" %s %s:/etc/openvpn/server.conf' % (openvpn_conf_tmp,ourhost['virt_ip']))

    varss = {'server_ip': my_floating_ips[0][2]}
    openvpn_client_conf_tmp = '/tmp/openvpn_client.conf-%s'%node_name
    upload_template('node-confs/openvpn_client.conf',openvpn_conf_tmp,varss)
    run('scp -o "StrictHostkeyChecking no" %s %s:/etc/openvpn/client.example' % (openvpn_conf_tmp,ourhost['virt_ip']))

    with settings(shell='ssh -t -o "StrictHostkeyChecking no" %s' % ourhost['virt_ip']):
        run('service openvpn restart')

def openvpn_status():
    op = run('cat /etc/openvpn/openvpn-status.log')
    rows = [r.strip().split(',') for r in op.split('\n')]
    head = rows[0]
    upd = rows[1]
    clients_head = rows[2][0:]
    clients = rows[3:]
    clients_d={}
    for c in clients:
        if c[0]=='ROUTING TABLE': break
        try:
            cd = dict([(clients_head[i],c[i]) for i in range(len(clients_head))])
            if cd['Common Name'] not in clients_d:
                clients_d[cd['Common Name']]=[]
                #,"common name %s is in %s"%(cd['Common Name'],clients_d)
            clients_d[cd['Common Name']].append(cd)
        except IndexError:
            #print('cannot parse',c,'with',clients_head)
            raise
    rt= {'head':head,
         'upd':upd[1],
         'clients':clients_d}

    return rt

def openvpn_ipp():
    op = run('cat /etc/openvpn/ipp.txt')
    hd=['name','internal_ip']
    rows = [r.strip().split(',') for r in op.split('\n')]
    rows_d={}
    
    for k,v in rows:
        if k not in rows_d: rows_d[k]=[]
        if v not in rows_d[k]: rows_d[k].append(v)

    return rows_d

def openvpn_all():
    ips = openvpn_ipp()
    status = openvpn_status()
    lst = list_openvpn()
    lst['missing_keys']={}

    mp = {'ips':ips.items(),
          'status':status['clients'].items()}
    for mk,mv in mp.items():
        for k,v in mv:
            if k in lst['keys']: 
                lst['keys'][k][mk]=v
            elif k in lst['missing_keys']:
                lst['missing_keys'][k][mk]=v
            else:
                lst['missing_keys'][k]={mk:v}

    return lst

def list_openvpn():
    defs = [[r.strip() for r in i.split(' ')] for i in open('conf_repo/openvpnusers.txt','r').read().split('\n') if i!='' and not i.startswith('#')]
    ovpndirs = run('find /etc/openvpn/easy-rsa/keys -maxdepth 1  -type d')
    dirs = [i.strip() for i in unicode(ovpndirs).split('\n') if not i.strip().endswith('easy-rsa/keys')]
    k1 = [d[0] for d in defs] 
    k2 = [d.split('/')[-1] for d in dirs]
    dirsp = dict([(d.split('/')[-1],d) for d in dirs])
    defsp = dict([(k[0],k[1:]) for k in defs])
    keys = set(k1+k2)
    sets={}
    # converted back to lists for json
    sets['in_both'] = list(set(k1).intersection(set(k2)))
    sets['users_not_keys'] = list(set(k1).difference(set(k2)))
    sets['keys_not_users'] = list(set(k2).difference(set(k1)))
    for k in sets:
        for i in sets[k]:
            print(k,i)

    rt={}
    for k in keys:
        rt[k]={'id':k,
               'path':dirsp.get(k),
               'email':defsp.get(k) and defsp.get(k)[0],
               'comment':defsp.get(k) and len(defsp.get(k))>1 and defsp.get(k)[1],
               }
    return {'sets':sets,
            'keys':rt}
def append_openvpn(client_name,email,comment):
    un = client_name ;em = email
    dt = datetime.datetime.now().isoformat()
    cmnt = comment and '# %s , %s'%(comment,dt) or '# %s'%dt
    apnd = '%s %s %s'%(un,em,cmnt)
    li = execute(list_openvpn,host=OVPN_HOST)[OVPN_HOST]
    assert un not in list(li.keys()),"%s already exists: %s"%(un,li[un])
    fp = open('conf_repo/openvpnusers.txt','a')
    fp.write(apnd+"\n")
    fp.close()
    execute(client_openvpn_exec,
            client_name=client_name,
            inlined=True,
            email=email,
            host=OVPN_HOST)
    return apnd

def client_openvpn(node_name, client_name, inlined=True, email=None):
    ourhost = _lst(al=True, display=False)[node_name]
    if ourhost['host'] != env.host_string:
        return
    with settings(shell='ssh -t -o "StrictHostkeyChecking no" %s' % ourhost['virt_ip']):
        client_openvpn_exec(client_name,inlined,email)

def client_openvpn_exec(client_name,inlined,email):
    with cd('/etc/openvpn/easy-rsa'):
        with nested(prefix("source vars"%{'cn':client_name})
                    #, shell_env(KEY_NAME=client_name,KEY_CN=client_name,SOME_VAR='some_value')
                    ):
            from config import HOST_PREFIX as hpf
            envs={'org':hpf,
                  'email':email,
                  'cn':client_name,
                  'name':client_name,
                  'ou':client_name}
            pref = "; ".join(['export KEY_'+k.upper()+'='+v for k,v in envs.items()])+'; '
            gencmd = pref+'./pkitool %s' % client_name
            print(gencmd)
            op = run(gencmd)
            assert 'failed to update database' not in op
        run('mkdir -p keys/%s' % client_name)
        with cd('keys/%s' % client_name):
            run('cp ../ca.crt ../dh1024.pem ../ta.key ../%(client)s.crt ../%(client)s.key .' % {'client':client_name})
            run('cp /etc/openvpn/client.example client.conf')
            if inlined:
                run('a=`cat ca.crt`; echo -e "\n<ca>\n$a\n</ca>" >> client.conf; unset a;')
                run('a=`cat ta.key`; echo -e "<tls-auth>\n$a\n</tls-auth>" >> client.conf; unset a;')
                run('a=`cat %s.crt`; echo -e "<cert>\n$a\n</cert>" >> client.conf; unset a;' % client_name)
                run('a=`cat %s.key`; echo -e "<key>\n$a\n</key>" >> client.conf; unset a;' % client_name)
                run('echo -e "#up /etc/openvpn/update-resolv-conf" >> client.conf')
                run('echo -e "#down /etc/openvpn/update-resolv-conf" >> client.conf')

            run('sed -i "s/@CLIENT@/%s/" client.conf' % client_name)
            if inlined:
                run('cp client.conf client.ovpn')
                run('sed -i "/^ca /d" client.ovpn')
                run('sed -i "/^cert /d" client.ovpn')
                run('sed -i "/^key /d" client.ovpn')
                run('sed -i "/^tls-auth /d" client.ovpn')
                apnd='-a client.ovpn'
            else:
                apnd=''
            if not fabric.contrib.files.exists(OVPN_KEYDIR): run('mkdir %s'%OVPN_KEYDIR)
            tgzfn = os.path.join(OVPN_KEYDIR,'%s.tgz'%client_name)
            run('tar czf %(tgzfn)s ca.crt client.conf dh1024.pem'\
                ' ta.key %(client)s.crt %(client)s.key' % {'client':client_name,'tgzfn':tgzfn})
            if email:
                params = {'email': email, 
                          'sender': OVPN_KEY_SENDER,
                          'mail_login':MAIL_LOGIN,
                          'mail_password':MAIL_PASSWORD,
                          'mail_host':MAIL_SERVER,
                          'mail_port':MAIL_PORT,
                          'tgzfn': tgzfn,
                          'apnd':apnd,
                          'client':client_name}
                run('sendemail -f %(sender)s -t %(email)s -m "is attached." -u "%(client)s openvpn key" -a %(tgzfn)s %(apnd)s -xu %(mail_login)s -xp %(mail_password)s -s %(mail_host)s:%(mail_port)s' % params)


def setup_dns():
    with shell_env(DEBIAN_FRONTEND='noninteractive', DEBCONF_TERSE='yes',
                   DEBIAN_PRIORITY='critical'):
        sudo("apt-get -qqyu --force-yes install dnsmasq")
    servers = '/etc/dnsmasq.d/servers'
    if not exists(servers):
        sudo('touch %s' % servers)
    cfg = '/etc/openvpn/server.conf'
    bind_ip = sudo("ifconfig tun0 | grep 'inet addr:' | cut -d: -f2| cut -d' ' -f1")
    # delete all dns servers in openvpn config and set dnsmasq as new one
    sudo("sed -ri '/dhcp-option DNS/d' %s" % cfg)
    sudo("echo 'push \"dhcp-option DNS %s\"' >> %s" % (bind_ip, cfg))
    put('node-confs/dnsmasq.conf','/etc/dnsmasq.conf')
    sudo("service dnsmasq restart")
    sudo("service openvpn restart")


def get_dns():
    if not exists('/etc/dnsmasq.d/servers'):
        return
    result = run('cat /etc/dnsmasq.d/servers | cut -f 2,3 -d/')
    return dict((s.split('/') for s in result.splitlines()))


def add_dns(domain, addr):
    if exists('/etc/dnsmasq.d/servers'):
        servers = '/etc/dnsmasq.d/servers'
        sudo("sed -ri '/%s/d' %s" % (re.escape('address=/%s/' % domain), servers))
        sudo("echo 'address=/%s/%s' >> %s" % (domain, addr, servers))
        sudo("service dnsmasq restart")



def del_dns(domain):
    if exists('/etc/dnsmasq.d/servers'):
        servers = '/etc/dnsmasq.d/servers'
        sudo("sed -ri '/%s/d' %s" % (re.escape('address=/%s/' % domain), servers))
        sudo("service dnsmasq restart")


## FIXME:
## rework to 1. be in /home/restapi , 2. read off github/sandstormholdings/kvm-restapi 3. work with py3.5
# def setup_restapi():
#     run('mkdir -p /root/.ssh')
#     put('node-confs/openvpn_ssh_config','/root/.ssh/config')
#     #put('node-confs/id_rsa-git','/root/.ssh/id_rsa-git')
#     run('chmod 600 /root/.ssh/*')
#     run('apt-get -q -y install git-core python-setuptools python-virtualenv python-dev libevent-dev python-pip')
#     run('adduser restapi')
#     with cd('/home/restapi/'):
#         if not fabric.contrib.files.exists('sandstorm-kvm'):
#             run('git clone git:/kvm-restapi.git')
#         with cd('kvm-restapi'):
#             if not fabric.contrib.files.exists('tmp'):
#                 run('mkdir tmp')
#                 run('chmod 777 tmp')
#             run('git pull')
#             run('git submodule update --init')
#             if not fabric.contrib.files.exists('venv'):
#                 run('virtualenv venv')
#             run('venv/bin/pip install -r requirements.txt')
#             put('node-confs/restapi_auth.json','auth.json')
#             run('chmod -R a+w ./dhcpd-confs')
#     put('node-confs/restapi.conf','/etc/init/restapi.conf')
#     put('node-confs/30-restapi.conf','/etc/rsyslog.d/30-restapi.conf')
#     with settings(warn_only=True):
#         run('initctl restart restapi')
#         run('initctl start restapi')

def enlarge_lvm(target, new_size='50G'):
    def wait_for(cmd):
        puts('waiting for guest for %s...'%cmd)
        cycles = 0
        while not run(cmd, quiet=True).succeeded:
            if cycles > 30:
                abort('failed to startup %s' % target)
            sleep(1)
            cycles += 1

    guests = _lst(al=True, display=False)
    if target not in guests:
        abort('guest machine "%s" not found in %s' % (target,list(guests.keys())))

    target_ip = guests[target].get('virt_ip')
    print('target ip for %s is %s'%(target,target_ip))
    image = '/var/lib/libvirt/images/%s.img' % target
    if not exists(image):
        abort('missing image for "%s" guest' % target)

    ssh_options = ' '.join(('-o %s=%s ' % (k,v)) for k,v in {
        'StrictHostkeyChecking': 'no',
        'UserKnownHostsFile': '/dev/null',
        'LogLevel': 'quiet'
    }.items())

    shellcmd = 'ssh -t %s %s' % (ssh_options, target_ip)
    print(shellcmd)
    with settings(shell=shellcmd):
        rel_codename = run('lsb_release -s -c').strip()

    if 'off' not in guests[target]['state']:
        sudo('virsh shutdown %s' % target)
        shutoff = 'virsh list --all | grep %s | grep off' % target
        wait_for(shutoff)
    sudo('qemu-img resize %s +%s' % (image, new_size))
    sudo('virsh start %s' % target)

    boot_check = 'nc -z %s 22' % target_ip
    wait_for(boot_check)

    # resize disk partitions on target
    with settings(shell=shellcmd):
        # get start sector of extended partition
        pebin='parted' #-2.3'
        start_extended = run(
            '%s -m -s /dev/vda unit s print | grep "^2" |cut -f2 -d:'%pebin)
        # get start sector of lvm logical partition
        start_logical = run(
            '%s -m -s /dev/vda unit s print | grep "^5" |cut -f2 -d:'%pebin)
        # resize extended partition up to max available space
        #12.04 syntax is a little different
        if rel_codename=='precise':
            run('%s -s /dev/vda "unit s resize 2 %s -1"' % (pebin,start_extended), warn_only=True)
        else:
            run('%s -s /dev/vda "unit s resizepart 2 -1"' % (pebin), warn_only=True)
        # re-create lvm partition with new size (data remains saved)
        run('%s -s /dev/vda "unit s rm 5 mkpart logical %s -1 toggle 5 lvm"'
            % (pebin,start_logical), warn_only=True)
        puts('error above is OK, we need to reboot guest now')
        with settings(warn_only=True):
            run('reboot')
    puts( 'waiting for boot check')
    wait_for(boot_check)
    with settings(shell=shellcmd):
        puts( 'about to pvresize')
        run('pvresize /dev/vda5')
        run('lvextend -l +100%FREE /dev/ubuntu/root')
        run('resize2fs /dev/ubuntu/root')
        puts('pvresize + lvextend + resize2fs are done.')


def get_tmux_sessions():
    with settings(sudo_prefix = "sudo -H -S -p '%(sudo_prompt)s' " % env):
        sessions = sudo("tmux ls -F '#{session_name} #{session_created}' 2>&1| grep -E '^(GS|LS)'",
                        user='www-data', quiet=True)
        build_num = sudo("test -f %(storage)s && awk '{ print $2 }' %(storage)s || echo '0'" % \
                         {'storage': '/var/www/buildnum'},
                         user='www-data', quiet=True)
    return {'sessions': [tuple(l.split(' ')) for l in sessions.splitlines()],
            'age': build_num}

def kill_tmux_session(name):
    with settings(sudo_prefix = "sudo -H -S -p '%(sudo_prompt)s' " % env):
        return sudo("tmux kill-session -t %s" % name, user='www-data', warn_only=True)

def create_user(name):
    with settings(fabric.api.hide('warnings','stderr','stdout','running'),warn_only=True):
        user_data = run("cat /etc/passwd | egrep '^%s:' ; true" % name).strip()
    if not len(user_data):
        run('adduser --disabled-password --force-badname --gecos "" %s'%name)
        return True
    else:
        return False

def lock_user(name):
    ud=os.path.join('/home',name)
    for akfn in ['authorized_keys','authorized_keys2']:
        if exists(os.path.join(ud,'.ssh',akfn)):
            run('mv %(ak)s %(ak)s.bck'%{'ak':os.path.join(ud,'.ssh',akfn)})
    run('passwd -l %s'%name)

# install a jumplogs collector user, with service et al. mandatory to pass on a coma-separated list of jumphosts to work with.
def jumphost_collector(jumphosts,username='jumplogs'):
    if not exists('/usr/local/bin/asciinema'): run('curl -sL https://asciinema.org/install | sh')
    assert username=='jumplogs',"username currently hardcoded in /etc/init/jumplogs.conf"
    put('node-confs/asciinema-process.sh','/usr/local/bin/')
    run('chmod a+x /usr/local/bin/asciinema-process.sh')

    create_user(username)
    jumphosts = jumphosts.split(',')
    for jh in jumphosts:
        append('/home/%s/jumphosts.txt'%username,jh)
    if not exists('/home/%s/.ssh'%username):
        run('mkdir /home/%s/.ssh'%username)

    put('node-confs/jumplogs-list.sh','/home/%s/list.sh'%username)
    put('conf_repo/id_rsa-jumphost-logcollector','/home/%s/.ssh/id_rsa-jumphost-logcollector'%username)
    run('chmod 600 /home/%s/.ssh/id_rsa-jumphost-logcollector'%username)
    run('chown -R %s:%s /home/%s/.ssh'%(username,username,username))
    put('node-confs/jumplogs.conf','/etc/init/jumplogs.conf')
    run('service jumplogs restart')

    
# create a jumhost user on a particular host
def jumphost_user(name,authkey_fn=None,reconf=False,email=None):
    #asciinema
    if reconf or not exists('/usr/local/bin/asciinema'): run('curl -sL https://asciinema.org/install | sh')
    run('mkdir -p /var/log/asciinema/%s'%name)
    run('chmod o+rwx /var/log/asciinema/%s'%name)
    for fn in ['asciinema.sh',]: #'asciinema-process.sh']:
        if reconf or not exists('/usr/local/bin/%s'%fn):
            put('node-confs/%s'%fn,'/usr/local/bin/%s'%fn)
            run('chmod +x /usr/local/bin/%s'%fn)

    #make sure a key is installed for the asciinema-collector to collect logs
    jhpubk = local('cat conf_repo/id_rsa-jumphost-logcollector.pub',capture=True).strip()
    if not contains('/root/.ssh/authorized_keys',jhpubk): run('echo "%s" >> /root/.ssh/authorized_keys'%jhpubk)

    run('apt-get install -q -y zile emacs23-nox mc htop')
 
    #user-specific
    AUTOGEN_TOKEN = '# Auto-generated beyond this point'
    if not exists('/etc/jumpers.txt') or not contains('/etc/jumpers.txt',name,exact=True):
        run('echo "%s" >> /etc/jumpers.txt'%name)

    if not exists('/etc/ssh/sshd_config.tpl'):
        run('cp /etc/ssh/sshd_config /etc/ssh/sshd_config.tpl')
    #safety measure - make sure working config and template are the same
    strp=' \t\n\r'
    excfg = run('cat /etc/ssh/sshd_config').split(AUTOGEN_TOKEN)[0].strip(strp)
    tpl = run('cat /etc/ssh/sshd_config.tpl').strip(strp)
    assert excfg==tpl,'ssh template and config mismatch! aborting to avoid destructive changes! (%s vs %s)'%(len(excfg),len(tpl))
    if reconf or not contains('/etc/ssh/sshd_config','Match User %s'%name):
        run('''
(cat /etc/ssh/sshd_config.tpl ;
echo "%s"
for f in $(cat /etc/jumpers.txt) ; do echo "Match User $f
      ForceCommand /usr/local/bin/asciinema.sh
" ; done) > /etc/ssh/sshd_config'''%AUTOGEN_TOKEN)
        run('service ssh restart')
    create_user(name)
    if authkey_fn:
        if not os.path.exists(authkey_fn):
            local('ssh-keygen -t rsa -N "" -f %s'%authkey_fn.replace('.pub',''))
        authkey = local('cat %s'%authkey_fn,capture=True).strip()
    else: authkey=None

    if authkey and not contains('/home/%s/.ssh/authorized_keys'%name,authkey,exact=True):
        if not exists('/home/%s/.ssh'%name): 
            run('mkdir /home/%s/.ssh'%name)
        put(authkey_fn,'/tmp/authkey')
        run('cat /tmp/authkey >> /home/%s/.ssh/authorized_keys'%(name))
        run('chown -R %s:%s /home/%s/.ssh'%(name,name,name))
    if not contains('/home/%s/.bashrc'%name,'/usr/local/bin/asciinema.sh'):
        append('/home/%s/.bashrc'%name,'/usr/local/bin/asciinema.sh')
    user_ssh_config_fn_tpl = 'conf_repo/jumphost_user_ssh_config'
    user_ssh_config_fn = '/home/%s/.ssh/config'%name
    if not exists(user_ssh_config_fn) and os.path.exists(user_ssh_config_fn_tpl):
        put(user_ssh_config_fn_tpl,user_ssh_config_fn)
        run('chown -R %s:%s %s'%(name,name,user_ssh_config_fn))
    ssh_key_fn = '/home/%s/.ssh/id_rsa'%name
    if not os.path.exists(ssh_key_fn):
        run("sudo -u %s ssh-keygen -f %s -t rsa -N ''"%(name,ssh_key_fn))
        run('cat /home/%s/.ssh/id_rsa.pub'%name)
    privkeyfn = authkey_fn.replace('.pub','')

    params = {'email':email,
              'hostname':JUMPHOST_EXTERNAL_IP,
              'sender':OVPN_KEY_SENDER,
              'mail_login':MAIL_LOGIN,
              'mail_password':MAIL_PASSWORD,
              'mail_server':MAIL_SERVER,
              'mail_port':MAIL_PORT,
              'ssh_key':privkeyfn,
              'client':name}
    if email and os.path.exists(privkeyfn):
        local('sendemail -f %(sender)s -t %(email)s -m "add to your ~/.ssh/config an entry with User %(client)s ; HostName %(hostname)s and IdentityFile being the key attached to this message." -u "ssh jumphost access for %(client)s" -a %(ssh_key)s -xu %(mail_login)s -xp %(mail_password)s -s %(mail_server)s:%(mail_port)s' % params)
    elif email:
        local('sendemail -f %(sender)s -t %(email)s -m "add to your ~/.ssh/config an entry with User %(client)s ; HostName %(hostname)s and IdentityFile being your existing git ssh key." -u "ssh jumphost access for %(client)s" -xu %(mail_login)s -xp %(mail_password)s -s %(mail_server)s:%(mail_port)s' % params)        

def htdigest_upload():
    put('conf_repo/digest.pw','/etc/apache2/digest.pw')
    
def install_staticwebserver(authorized_keys_fn=None,
                            vhost='static.ezd.lan',
                            user='static',
                            ):
    put('node-confs/static/nologin','/sbin/nologin')
    run('chmod +x /sbin/nologin')

    with settings(warn_only=True):
        run('groupadd sftpusers')
        run('adduser --disabled-password --gecos --home=/home/{user}/www --shell=/sbin/nologin {user}'.format(user=user))
        run('usermod -a --group=%s sftpusers'%user)
        run('usermod -a --group=%s www-data'%user)

    with settings(sudo_user=user):
        sudo('mkdir -p /home/%s/.ssh'%user)

        if not authorized_keys_fn:
            local("awk 'FNR==1{print ""}1' conf_repo/keydir/*.pub > /tmp/authkeydir")
            authorized_keys_fn = '/tmp/authkeydir'

        put(authorized_keys_fn,'/home/%s/.ssh/authorized_keys'%user)
        sudo('mkdir -p /home/%s/www'%user)
        put('node-confs/static/static-README.txt','/home/%s/www/README.txt'%user)




    comment('/etc/ssh/sshd_config',"""^Subsystem sftp /usr/lib/openssh/sftp-server""")
    append('/etc/ssh/sshd_config',"""Subsystem sftp internal-sftp""")
    run('service ssh restart')
    run('usermod -a -G %s www-data'%user)
    run('apt-get install -y -q apache2')
    run('a2enmod auth_digest')
    upload_template('node-confs/static/static.httpd.conf',
                    '/etc/apache2/sites-available/static.httpd.conf',
                    {'vhost':vhost,
                     'digest realm':DIGEST_REALM,
                     'user':user})
    run('a2ensite static.httpd.conf')
    run('chown -R {user}:{user} /home/{user}'.format(user=user))

    run('service apache2 restart')


def certbot_xenial():
    from config import CERTBOT_GITOLITE_OWNER
    cmds=['apt-get update',
          'apt-get install software-properties-common',
          'add-apt-repository ppa:certbot/certbot',
          'apt-get update',
          'apt-get install python-certbot-apache',
          'certbot --apache -m %s'%CERTBOT_GITOLITE_OWNER
    ]
    for cmd in cmds:
        run(cmd)

# this is a small dhcp workaround needed for 16.04 that undergo upgrades
def dhclient_script_fix(node):
    tfn = '/tmp/dhclient-script.diff'
    put('node-confs/dhclient-script.diff',tfn)
    run('scp %s %s:%s'%(tfn,node,tfn))    
    with settings(shell='ssh -t -o "StrictHostkeyChecking no" %s'%node):
        run('( cd / ; patch -p0 ) < %s'%tfn)
    
def gitweb_patch():
    put('node-confs/gitweb-additions.diff','/tmp/gitweb-additions.diff')
    run('cd / ; patch -p0 < /tmp/gitweb-additions.diff')

def install_gitserver(gitolite=True,
                      gitweb=True,
                      user='git',
                      vhost=None,
                      certbot=False):
    lkeyname = ('-'.join(['id_rsa',user+'@'+env.host_string]))
    keyname=os.path.join('conf_repo',lkeyname)
    if not os.path.exists(keyname):
        cmd = 'ssh-keygen -N "" -f %s'%keyname
        local(cmd)
    if gitolite:
        with settings(warn_only=True):
            run('adduser {user} --disabled-password --gecos ""'.format(user=user))
            put(keyname+'.pub','/home/%s/admin.pub'%(user))
        run('apt-get install -q -y git-core')
        with settings(sudo_user=user):
            with cd('/home/%s'%user):
                if not exists('gitolite'):
                    sudo('git clone git://github.com/sitaramc/gitolite')
                sudo('mkdir -p bin')
                sudo('/home/%s/gitolite/install -ln /home/%s/bin/'%(user,user),shell=True)
                sudo('HOME=/home/%s /home/%s/bin/gitolite setup -pk admin.pub'%(user,user))
    if gitweb:
        assert vhost,"No vhost specified"

        run('apt-get install -q -y highlight gitweb libapache2-mod-perl2 make')
        gitweb_patch()
        append('/etc/gitweb.conf','''$feature{'highlight'}{'default'} = [1];''')
        append('/etc/gitweb.conf',"""$projectroot = '/home/%s/repositories'"""%user)
        upload_template('node-confs/gitweb.httpd.conf',
                        '/etc/apache2/sites-available/gitweb.httpd.conf',
                        {'vhost':vhost,
                         'digest realm':DIGEST_REALM})
        if not exists('/etc/apache2/digest.pw'):
            put('conf_repo/digest.pw','/etc/apache2/digest.pw')
        run('usermod -a -G %s www-data'%user)
        run('chmod g+r /home/%s/projects.list'%user)
        run('chmod -R g+rx /home/%s/repositories'%user)
        run('a2enmod auth_digest')
        run('a2enmod cgi')
        run('a2enmod ssl')
        run('a2ensite gitweb.httpd.conf')
        run('cpan install CGI.pm')
        if certbot:
            cn = run('lsb_release -c -s')
            assert cn=='xenial',"certbot only works with xenial atm"
            certbot_xenial()
        run('service apache2 restart')
    

def iftop_settings():
    put('node-confs/iftoprc','/root/.iftoprc')

def init_ipt():
    myipt = 'server-confs/iptables/%s.sh'%env.host_string
    if not os.path.exists(myipt):
        local('cp server-confs/iptables/iptables.sh %s'%myipt)
    return myipt

def install_ipt(myipt=None):
    if not myipt: myipt = init_ipt()
    print(('myipt=',myipt))
    if os.path.exists(myipt): 
        print(('putting ',myipt,'/etc/iptables.sh'))
        put(myipt,'/etc/iptables.sh')
    if exists('/etc/iptables.sh'):
        run('chmod +x /etc/iptables.sh')
        put('server-confs/host-rc.local','/etc/rc.local')
        run('/etc/rc.local')
    
# example: fab -R kvm install_snmpd:10.98.0.0/16
def install_snmpd(network=snmpd_network):
    run('apt-get -y -q install snmpd')
    upload_template('node-confs/snmpd.conf',
                    '/etc/snmp/snmpd.conf',
                    {'network':network})
    run('service snmpd restart')
    myipt = init_ipt()
    install_ipt(myipt)

# fab -R kvm authorized_keys_get
def authorized_keys_get(tdir='authorized_keys',usehostname=False):
    assert os.path.exists(tdir)
    lckf = os.path.join(tdir,'locks',env.host_string+'.lock')
    lckd = os.path.join(tdir,'locks')
    local('mkdir -p %s'%lckd)
    local('touch %s'%lckf)

    run("""find $(cut -f6 -d ':' /etc/passwd |sort |uniq | tr '\n' ' ') -maxdepth 3 -iname 'authorized_keys*' ! -iname '*.txt' ! -iname '*.bck' ! -iname '*.diff' ! -iname '*.sh' ! -iname '*py' ! -iname '*~' -exec egrep -n -v -H "^$" {} \; > /tmp/authkeys.txt ||:""")
    with settings(hide('warnings', 'running', 'stdout', 'stderr')):
        if usehostname:
            hn = run('hostname').strip()
            tgtfn = os.path.join(tdir,'%s.pub'%hn)
        else:
            tgtfn = os.path.join(tdir,'%s.pub'%env.host_string)
        get('/tmp/authkeys.txt',tgtfn)
        print('-> %s'%tgtfn)
    run("""rm /tmp/authkeys.txt""")
    local('rm %s'%lckf)

def getmem(node):
    mem = run("""virsh dumpxml %s | xmlstarlet sel -t -m '//memory[1]' -v . -n"""%node)
    curmem = run("""virsh dumpxml %s | xmlstarlet sel -t -m '//currentMemory[1]' -v . -n"""%node)
    return {'memory':mem,'currentMemory':curmem}

def authorized_keys_add(dstfn,pubkeyfn):
    sfn = pubkeyfn #os.path.join(OVPN_KEYDIR,pubkeyfn)
    assert os.path.exists(sfn),"%s does not exist"%sfn
    cont = open(sfn,'r').read().strip()
    assert exists(dstfn)
    append(dstfn,cont)

def authorized_keys_del(dstfn,pubkeyfn):
    sfn = pubkeyfn #os.path.join(OVPN_KEYDIR,pubkeyfn)
    assert os.path.exists(sfn),"%s does not exist"%sfn
    cont = open(sfn,'r').read().strip().replace('+','\+')
    assert exists(dstfn)
    sed(dstfn,cont,'',backup='')
