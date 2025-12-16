{
  pkgs,
  ...
}:
{
  name = "nebula-service";

  clan = {
    test.useContainers = false;
    directory = ./.;
    inventory = {
      machines.lighthouse = { };
      machines.node1 = { };
      machines.node2 = { };

      instances = {
        test-vpn = {
          module.name = "nebula-service";
          module.input = "self";

          roles.lighthouse.machines.lighthouse = {
            settings = {
              # Use a local address for testing
              endpoint = "192.168.1.1";
              nebulaIp = "10.87.0.1";
              networkCidr = "10.87.0.0/16";
              port = 4242;
            };
          };

          roles.node.machines.node1 = {
            settings = {
              nebulaIp = "10.87.0.10";
            };
          };

          roles.node.machines.node2 = {
            settings = {
              nebulaIp = "10.87.0.11";
            };
          };
        };
      };
    };
  };

  nodes = {
    lighthouse = {
      virtualisation.vlans = [ 1 ];
      networking.interfaces.eth1.ipv4.addresses = [
        {
          address = "192.168.1.1";
          prefixLength = 24;
        }
      ];
    };
    node1 = {
      virtualisation.vlans = [ 1 ];
      networking.interfaces.eth1.ipv4.addresses = [
        {
          address = "192.168.1.10";
          prefixLength = 24;
        }
      ];
    };
    node2 = {
      virtualisation.vlans = [ 1 ];
      networking.interfaces.eth1.ipv4.addresses = [
        {
          address = "192.168.1.11";
          prefixLength = 24;
        }
      ];
    };
  };

  testScript = ''
    start_all()

    # Wait for Nebula services to start
    lighthouse.wait_for_unit("nebula@test-vpn.service")
    node1.wait_for_unit("nebula@test-vpn.service")
    node2.wait_for_unit("nebula@test-vpn.service")

    # Check that services are running
    lighthouse.succeed("systemctl status nebula@test-vpn.service")
    node1.succeed("systemctl status nebula@test-vpn.service")
    node2.succeed("systemctl status nebula@test-vpn.service")

    # Give Nebula time to establish connections
    import time
    time.sleep(5)

    # Check that the Nebula interface exists
    lighthouse.succeed("${pkgs.iproute2}/bin/ip link show nebula.test-vpn")
    node1.succeed("${pkgs.iproute2}/bin/ip link show nebula.test-vpn")
    node2.succeed("${pkgs.iproute2}/bin/ip link show nebula.test-vpn")

    # Check that hosts can ping each other over Nebula
    # Lighthouse should be able to reach nodes
    lighthouse.wait_until_succeeds("ping -c 1 10.87.0.10", 30)
    lighthouse.wait_until_succeeds("ping -c 1 10.87.0.11", 30)

    # Nodes should be able to reach lighthouse
    node1.wait_until_succeeds("ping -c 1 10.87.0.1", 30)
    node2.wait_until_succeeds("ping -c 1 10.87.0.1", 30)

    # Nodes should be able to reach each other (through lighthouse discovery)
    node1.wait_until_succeeds("ping -c 1 10.87.0.11", 30)
    node2.wait_until_succeeds("ping -c 1 10.87.0.10", 30)

    # Check host entries are set up correctly
    lighthouse.succeed("getent hosts lighthouse.nebula | grep -q '10.87.0.1'")
    node1.succeed("getent hosts node1.nebula | grep -q '10.87.0.10'")
    node2.succeed("getent hosts node2.nebula | grep -q '10.87.0.11'")
  '';
}
