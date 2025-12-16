# Nebula Mesh VPN Service

[Nebula](https://github.com/slackhq/nebula) is a scalable overlay networking tool
with a focus on performance, simplicity and security. It lets you seamlessly
connect any number of hosts across different networks and cloud providers.

## Overview

This service configures a Nebula mesh VPN for your Clan machines. It handles:

- Certificate Authority (CA) generation and management
- Node certificate signing
- Lighthouse and node configuration
- Firewall rules
- Host entries for easy DNS resolution

## Roles

### `lighthouse`

Lighthouse nodes are publicly reachable servers that help other nodes discover
each other. Every Nebula network needs at least one lighthouse.

**Required settings:**

- `endpoint`: Public IP or hostname where this lighthouse is reachable
- `nebulaIp`: IP address for this node within the Nebula network

**Optional settings:**

- `networkCidr`: Network range (default: `10.87.0.0/16`)
- `port`: UDP port (default: `4242`)
- `isRelay`: Whether to relay traffic for nodes that can't connect directly
- `dns.enable`: Enable DNS resolution on this lighthouse
- `firewallInbound`/`firewallOutbound`: Nebula firewall rules

### `node`

Regular nodes that connect to the mesh through lighthouses. They can be behind
NAT and don't need public IP addresses.

**Required settings:**

- `nebulaIp`: IP address for this node within the Nebula network

**Optional settings:**

- `port`: UDP port (default: `0` for random)
- `firewallInbound`/`firewallOutbound`: Nebula firewall rules

## Example Configuration

```nix
{
  clan.inventory.instances.my-vpn = {
    module.name = "nebula";

    roles.lighthouse.machines.server1 = {
      settings = {
        endpoint = "vpn.example.com";
        nebulaIp = "10.87.0.1";
        networkCidr = "10.87.0.0/16";
        isRelay = true;
      };
    };

    roles.node.machines.laptop = {
      settings = {
        nebulaIp = "10.87.0.10";
      };
    };

    roles.node.machines.desktop = {
      settings = {
        nebulaIp = "10.87.0.11";
      };
    };
  };
}
```

## How It Works

1. **CA Generation**: A shared Certificate Authority is generated for the
   network instance. This CA signs all node certificates.

2. **Node Certificates**: Each machine gets its own certificate signed by the CA,
   with its Nebula IP embedded in the certificate.

3. **Peer Discovery**: Nodes connect to lighthouses to discover other peers.
   Once discovered, nodes establish direct encrypted tunnels.

4. **NAT Traversal**: Nebula uses UDP hole punching to establish connections
   between nodes behind NAT. If direct connection fails, traffic can be relayed
   through lighthouse nodes marked as relays.

## Network Priority

This service has a network priority of 850, placing it between Mycelium (800)
and WireGuard (900) in the routing order.

## Host Resolution

All machines in the network automatically get `/etc/hosts` entries in the format
`<nebula-ip> <hostname>.nebula`, allowing easy access via hostname.

## Security Notes

- The CA private key is stored securely using Clan's vars system
- Node private keys are also stored securely per-machine
- Certificates are automatically generated and signed
- All traffic between nodes is encrypted using Noise protocol (same as WireGuard)

## Firewall

By default, the service allows all traffic between Nebula nodes. You can
restrict this using the `firewallInbound` and `firewallOutbound` settings:

```nix
{
  settings = {
    nebulaIp = "10.87.0.10";
    firewallInbound = [
      { port = 22; proto = "tcp"; host = "any"; }
      { port = "any"; proto = "icmp"; host = "any"; }
    ];
    firewallOutbound = [
      { port = "any"; proto = "any"; host = "any"; }
    ];
  };
}
```
