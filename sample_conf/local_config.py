# DNS for virtual nodes network/ Default use None
DNS_HOST=None

# Host Prefix used as part of virtual nodes name. Default use /etc/hostname.
HOST_PREFIX = "host"

# Host domain name
DHCPD_DOMAIN_NAME='test.com'

# Mask for Nodes virtual network
main_network = "192.168"

# OpenVPN parameters
ovpn_client_network = "192.168.1.0"
ovpn_internal_addr = "192.168.1.1"
ovpn_client_netmask = "255.255.255.0"

# Digest authorization parameters
DIGEST_REALM = "user_name@gmail.com"
SECRET_KEY = "user_password"

# Image name and path used for virtual node creation
IMAGES = {'/var/lib/libvirt/images/ubuntu-16.04-large.img': 'ubuntu', '/var/lib/libvirt/images/debian.img': 'debian'}

# Kill the process if change is observed in the sources
AUTO_STOP = True

# Redis
REDIS_HOST="localhost"
# REDIS_PASSWORD

# Some notes for host
NOTES="Test notes"

# List of host addresses [(hostname,internal ip, external ip)]
FLOATING_IPS=[('hostname','192.168.1.1','10.96.X.X')]

# hosts list for fabric kvm role
from fabric.api import env
env.roledefs['kvm']=['hostname']
