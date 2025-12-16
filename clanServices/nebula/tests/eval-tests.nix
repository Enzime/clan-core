{
  module,
  clanLib,
  ...
}:
let
  testClan = clanLib.clan {
    self = { };
    directory = ./..;

    machines.lighthouse1 = {
      nixpkgs.hostPlatform = "x86_64-linux";
    };
    machines.node1 = {
      nixpkgs.hostPlatform = "x86_64-linux";
    };

    modules.nebula = module;

    inventory.instances = {
      "test-vpn" = {
        module.name = "nebula";
        module.input = "self";

        roles.lighthouse.machines.lighthouse1 = {
          settings = {
            endpoint = "1.2.3.4";
            nebulaIp = "10.87.0.1";
            networkCidr = "10.87.0.0/16";
          };
        };

        roles.node.machines.node1 = {
          settings = {
            nebulaIp = "10.87.0.10";
          };
        };
      };
    };
  };
in
{
  test_lighthouse_is_lighthouse = {
    inherit testClan;
    expr = testClan.config.nixosConfigurations.lighthouse1.config.services.nebula.networks."test-vpn".isLighthouse;
    expected = true;
  };

  test_node_is_not_lighthouse = {
    inherit testClan;
    expr = testClan.config.nixosConfigurations.node1.config.services.nebula.networks."test-vpn".isLighthouse;
    expected = false;
  };

  test_node_knows_lighthouse = {
    inherit testClan;
    expr = testClan.config.nixosConfigurations.node1.config.services.nebula.networks."test-vpn".lighthouses;
    expected = [ "10.87.0.1" ];
  };

  test_static_host_map = {
    inherit testClan;
    expr = testClan.config.nixosConfigurations.node1.config.services.nebula.networks."test-vpn".staticHostMap;
    expected = {
      "10.87.0.1" = [ "1.2.3.4:4242" ];
    };
  };
}
