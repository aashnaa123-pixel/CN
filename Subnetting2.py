import math

def ip_to_int(ip):
    parts = list(map(int, ip.split('.')))
    result = 0
    for part in parts:
        result = (result << 8) | part
    return result

def int_to_ip(ip):
    return ".".join(str((ip >> (8 * i)) & 255) for i in reversed(range(4)))

def get_class(first_octet):
    if 1 <= first_octet <= 126:
        return 'A'
    elif 128 <= first_octet <= 191:
        return 'B'
    elif 192 <= first_octet <= 223:
        return 'C'
    elif 224 <= first_octet <= 239:
        return 'D'
    else:
        return 'E'

def get_default_prefix(ip_class):
    if ip_class == 'A':
        return 8
    elif ip_class == 'B':
        return 16
    elif ip_class == 'C':
        return 24
    else:
        return 0

def prefix_to_mask(prefix):
    if prefix == 0:
        return 0
    return (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF

def main():
    ip = input("Enter IP Address: ")
    required_subnets = int(input("Enter required number of subnets: "))

    ip_int = ip_to_int(ip)
    first_octet = (ip_int >> 24) & 255
    ip_class = get_class(first_octet)

    if ip_class in ['D', 'E']:
        print(f"\nClass {ip_class} does not support normal subnetting.")
        return

    default_prefix = get_default_prefix(ip_class)

    borrowed_bits = 0
    while (1 << borrowed_bits) < required_subnets:
        borrowed_bits += 1

    new_prefix = default_prefix + borrowed_bits
    if new_prefix > 30:
        print("\nToo many subnets requested for this IP class.")
        return

    default_mask = prefix_to_mask(default_prefix)
    new_mask = prefix_to_mask(new_prefix)

    total_subnets = 2 ** borrowed_bits
    host_bits = 32 - new_prefix
    hosts_per_subnet = (2 ** host_bits) - 2
    total_hosts = total_subnets * hosts_per_subnet

    network_address = ip_int & default_mask
    first_ip = network_address
    last_ip = network_address + (2 ** (32 - default_prefix)) - 1

    print("\nIP Address :", ip)
    print("IP Class : Class", ip_class)
    print("Default Subnet :", int_to_ip(default_mask), f"/{default_prefix}")
    print("New Subnet Mask :", int_to_ip(new_mask), f"/{new_prefix}")
    print("Bits Borrowed :", borrowed_bits)

    print("\nIP ADDRESS RANGE")
    print("Network Address :", int_to_ip(first_ip))
    print("First IP :", int_to_ip(first_ip + 1))
    print("Last IP :", int_to_ip(last_ip - 1))
    print("Broadcast Address :", int_to_ip(last_ip))

    print("\nSUBNET INFORMATION")
    print("Required Subnets :", required_subnets)
    print("Actual Subnets :", total_subnets)
    print("Hosts per Subnet :", hosts_per_subnet)
    print("Total Usable Hosts:", total_hosts)

    print("\nLIST OF ALL SUBNETS")
    subnet_size = 2 ** host_bits
    for i in range(total_subnets):
        subnet_network = network_address + (i * subnet_size)
        subnet_broadcast = subnet_network + subnet_size - 1
        first_host = subnet_network + 1
        last_host = subnet_broadcast - 1

        print(f"\nSubnet {i + 1}:")
        print(" Network :", int_to_ip(subnet_network))
        print(" First Host:", int_to_ip(first_host))
        print(" Last Host :", int_to_ip(last_host))
        print(" Broadcast :", int_to_ip(subnet_broadcast))

if __name__ == "__main__":
    main()
