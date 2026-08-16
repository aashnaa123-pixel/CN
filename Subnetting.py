import ipaddress 
print("SUBNETTING CALCULATOR") 
ip = input("Enter IPv4 Address (Example: 192.168.1.10): ") 
prefix = int(input("Enter Prefix Length (Example: 26): ")) 
network = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False) 
address = ipaddress.IPv4Address(ip) 
first_octet = int(ip.split(".")[0]) 
# Class Detection
if 1 <= first_octet <= 126:
    ip_class = "Class A"
    default_mask = "255.0.0.0"
    default_prefix = 8
elif 128 <= first_octet <= 191:
    ip_class = "Class B"
    default_mask = "255.255.0.0"
    default_prefix = 16
elif 192 <= first_octet <= 223:
    ip_class = "Class C"
    default_mask = "255.255.255.0"
    default_prefix = 24
elif 224 <= first_octet <= 239:
    ip_class = "Class D (Multicast)"
    default_mask = "Not Applicable"
    default_prefix = None
else:
    ip_class = "Class E (Experimental)"
    default_mask = "Not Applicable"
    default_prefix = None
# Binary First Octet
binary_octet = format(first_octet, '08b') 
# Hosts Calculation
host_bits = 32 - prefix
if host_bits > 0:
    hosts = (2 ** host_bits) - 2
else:
    hosts = 1

# Number of Subnets
if default_prefix is not None and prefix >= default_prefix:
    borrowed_bits = prefix - default_prefix
    networks = 2 ** borrowed_bits
else:
    borrowed_bits = 0
    networks = "Not Applicable"
# Output 
print("RESULT") 
print("IP Address:", address) 
print("IP Class:", ip_class) 
print("Default Subnet Mask:", default_mask) 
print("Subnet Mask:", network.netmask) 
print("Prefix Length: /", prefix) 
print("Network ID:", network.network_address) 
print("Broadcast Address:", network.broadcast_address) 
if host_bits > 0: 
    hosts_list = list(network.hosts()) 
    print("First Host Address:", hosts_list[0]) 
    print("Last Host Address:", hosts_list[-1]) 
    print("Host Bits:", host_bits) 
    print("Borrowed Bits:", borrowed_bits) 
    print("Hosts per Network:", hosts) 
    print("Number of Networks:", networks) 
    print("First Binary Octet:", binary_octet) 
    print("FORMULAS USED") 
    print("Hosts per Network Formula") 
    print("= 2^(Host Bits) - 2") 
    print(f"= 2^{host_bits} - 2") 
    print("=", hosts) 
    if default_prefix is not None: 
        print("\nNumber of Networks Formula") 
        print("= 2^(Borrowed Bits)") 
        print(f"= 2^{borrowed_bits}") 
        print("=", networks) 
    print("Definition of Subnet Mask") 
    print("A subnet mask is a 32-bit number used to") 
    print("separate the Network ID and Host ID of") 
    print("an IP address. It helps divide a network") 
    print("into smaller subnetworks.") 
    print("\nDefinition of Loopback Address:") 
    print("A loopback address is a special IP address") 
    print("used by a computer to communicate with itself.") 
    print("It is used to test whether the local") 
    print("TCP/IP network configuration is working properly.") 
    print("The loopback address range is") 
    print("127.0.0.0 to 127.255.255.255.") 
    print("The most commonly used loopback address is 127.0.0.1.") 
    print("\nWhy is -2 used?") 
    print("One address is reserved as the network address.") 
    print("One address is reserved as the broadcast address.") 
    print("Therefore, 2 addresses cannot be assigned to hosts.")