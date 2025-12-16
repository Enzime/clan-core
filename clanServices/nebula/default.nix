# Nebula mesh VPN service for Clan
# Nebula is a scalable overlay networking tool with a focus on performance,
# simplicity and security. https://github.com/slackhq/nebula
{
  clanLib,
  lib,
  config,
  directory,
  ...
}:
let
  # Helper to get all machines from all roles
  getAllMachines = roles:
    lib.unique (
      lib.concatLists [
        (lib.attrNames (roles.lighthouse.machines or { }))
        (lib.attrNames (roles.node.machines or { }))
      ]
    );

  # Helper to get lighthouse machines
  getLighthouseMachines = roles: lib.attrNames (roles.lighthouse.machines or { });
in
{
  _class = "clan.service";
  manifest.name = "clan-core/nebula";
  manifest.description = "Nebula mesh VPN - secure overlay network with lighthouse-based peer discovery";
  manifest.categories = [
    "Network"
    "Security"
  ];
  manifest.readme = builtins.readFile ./README.md;

  # Service-level exports for network priority
  exports = lib.mapAttrs' (instanceName: _: {
    name = clanLib.buildScopeKey {
      inherit instanceName;
      serviceName = config.manifest.name;
    };
    value = {
      networking.priority = 850;
    };
  }) config.instances;

  # Lighthouse role - public nodes that help with peer discovery
  roles.lighthouse = {
    description = "A lighthouse node that helps other nodes discover each other. Must be publicly reachable.";

    interface =
      { lib, ... }:
      {
        options = {
          endpoint = lib.mkOption {
            type = lib.types.str;
            description = ''
              The public endpoint (IP or hostname) where this lighthouse is reachable.
              Can include port, e.g., "1.2.3.4:4242" or "vpn.example.com:4242".
              If no port is specified, 4242 is used.
            '';
            example = "vpn.example.com:4242";
          };

          nebulaIp = lib.mkOption {
            type = lib.types.str;
            description = ''
              The Nebula IP address for this lighthouse within the mesh network.
              Must be within the network CIDR range and unique across all nodes.
            '';
            example = "10.87.0.1";
          };

          networkCidr = lib.mkOption {
            type = lib.types.str;
            default = "10.87.0.0/16";
            description = ''
              The CIDR range for the Nebula network.
              All node IPs must be within this range.
            '';
            example = "10.87.0.0/16";
          };

          port = lib.mkOption {
            type = lib.types.port;
            default = 4242;
            description = "UDP port for Nebula to listen on.";
          };

          isRelay = lib.mkOption {
            type = lib.types.bool;
            default = false;
            description = "Whether this lighthouse also acts as a relay for nodes that cannot establish direct connections.";
          };

          dns.enable = lib.mkOption {
            type = lib.types.bool;
            default = false;
            description = "Whether to enable DNS resolution on this lighthouse.";
          };

          firewallInbound = lib.mkOption {
            type = lib.types.listOf lib.types.attrs;
            default = [
              {
                port = "any";
                proto = "any";
                host = "any";
              }
            ];
            description = "Nebula firewall inbound rules.";
          };

          firewallOutbound = lib.mkOption {
            type = lib.types.listOf lib.types.attrs;
            default = [
              {
                port = "any";
                proto = "any";
                host = "any";
              }
            ];
            description = "Nebula firewall outbound rules.";
          };
        };
      };

    perInstance =
      {
        instanceName,
        settings,
        roles,
        machine,
        mkExports,
        ...
      }:
      let
        allMachines = getAllMachines roles;
        lighthouseMachines = getLighthouseMachines roles;
        # Get network CIDR from first lighthouse (all lighthouses should use the same)
        networkCidr =
          if lighthouseMachines != [ ] then
            let
              firstLighthouse = builtins.head lighthouseMachines;
            in
            roles.lighthouse.machines.${firstLighthouse}.settings.networkCidr
          else
            "10.87.0.0/16";
      in
      {
        exports = mkExports {
          peer.hosts = [
            {
              plain = settings.nebulaIp;
            }
          ];
        };

        nixosModule =
          {
            config,
            pkgs,
            lib,
            ...
          }:
          let
            # Build static host map for all lighthouses
            staticHostMap = lib.listToAttrs (
              map (
                lhName:
                let
                  lhSettings = roles.lighthouse.machines.${lhName}.settings;
                  endpoint =
                    if lib.hasInfix ":" lhSettings.endpoint then
                      lhSettings.endpoint
                    else
                      "${lhSettings.endpoint}:${toString lhSettings.port}";
                in
                {
                  name = lhSettings.nebulaIp;
                  value = [ endpoint ];
                }
              ) lighthouseMachines
            );

            # Get other lighthouse IPs (for relays config)
            otherLighthouseIps = lib.filter (ip: ip != settings.nebulaIp) (
              map (lhName: roles.lighthouse.machines.${lhName}.settings.nebulaIp) lighthouseMachines
            );
          in
          {
            # Generator for CA certificate (shared across instance)
            clan.core.vars.generators."nebula-${instanceName}-ca" = {
              share = true;
              files."ca.crt".secret = false;
              files."ca.key" = { };
              runtimeInputs = [ pkgs.nebula ];
              script = ''
                nebula-cert ca -name "${instanceName}" -out-crt "$out/ca.crt" -out-key "$out/ca.key"
              '';
            };

            # Generator for node certificate
            clan.core.vars.generators."nebula-${instanceName}-node" = {
              files."node.crt".secret = false;
              files."node.key" = { };
              dependencies = [ config.clan.core.vars.generators."nebula-${instanceName}-ca" ];
              runtimeInputs = [ pkgs.nebula ];
              script = ''
                # Get CA files
                ca_crt="${config.clan.core.vars.generators."nebula-${instanceName}-ca".files."ca.crt".path}"
                ca_key="${config.clan.core.vars.generators."nebula-${instanceName}-ca".files."ca.key".path}"

                # Sign node certificate
                nebula-cert sign \
                  -name "${machine.name}" \
                  -ip "${settings.nebulaIp}/${lib.last (lib.splitString "/" networkCidr)}" \
                  -ca-crt "$ca_crt" \
                  -ca-key "$ca_key" \
                  -out-crt "$out/node.crt" \
                  -out-key "$out/node.key"
              '';
            };

            services.nebula.networks.${instanceName} = {
              enable = true;
              isLighthouse = true;
              isRelay = settings.isRelay;

              listen = {
                host = "0.0.0.0";
                port = settings.port;
              };

              ca = config.clan.core.vars.generators."nebula-${instanceName}-ca".files."ca.crt".path;
              cert = config.clan.core.vars.generators."nebula-${instanceName}-node".files."node.crt".path;
              key = config.clan.core.vars.generators."nebula-${instanceName}-node".files."node.key".path;

              staticHostMap = staticHostMap;

              # Lighthouses can relay to other lighthouses
              relays = lib.optionals settings.isRelay otherLighthouseIps;

              firewall = {
                inbound = settings.firewallInbound;
                outbound = settings.firewallOutbound;
              };

              settings = {
                lighthouse = lib.optionalAttrs settings.dns.enable {
                  dns = {
                    host = settings.nebulaIp;
                    port = 53;
                  };
                };
              };
            };

            # Open firewall for Nebula UDP
            networking.firewall.allowedUDPPorts = [ settings.port ];

            # Add extra hosts entries for all machines in the network
            networking.extraHosts = lib.strings.concatStringsSep "\n" (
              lib.filter (s: s != "") (
                # Lighthouse hosts
                (map (
                  lhName:
                  let
                    lhSettings = roles.lighthouse.machines.${lhName}.settings;
                  in
                  "${lhSettings.nebulaIp} ${lhName}.nebula"
                ) lighthouseMachines)
                ++
                  # Node hosts
                  (map (
                    nodeName:
                    let
                      nodeSettings = roles.node.machines.${nodeName}.settings or null;
                    in
                    if nodeSettings != null then "${nodeSettings.nebulaIp} ${nodeName}.nebula" else ""
                  ) (lib.attrNames (roles.node.machines or { })))
              )
            );
          };
      };
  };

  # Node role - regular nodes that connect through lighthouses
  roles.node = {
    description = "A regular node in the Nebula mesh network that connects through lighthouses.";

    interface =
      { lib, ... }:
      {
        options = {
          nebulaIp = lib.mkOption {
            type = lib.types.str;
            description = ''
              The Nebula IP address for this node within the mesh network.
              Must be within the network CIDR range and unique across all nodes.
            '';
            example = "10.87.0.10";
          };

          port = lib.mkOption {
            type = lib.types.port;
            default = 0;
            description = ''
              UDP port for Nebula to listen on.
              Default of 0 means a random port will be used (recommended for non-lighthouse nodes).
            '';
          };

          firewallInbound = lib.mkOption {
            type = lib.types.listOf lib.types.attrs;
            default = [
              {
                port = "any";
                proto = "any";
                host = "any";
              }
            ];
            description = "Nebula firewall inbound rules.";
          };

          firewallOutbound = lib.mkOption {
            type = lib.types.listOf lib.types.attrs;
            default = [
              {
                port = "any";
                proto = "any";
                host = "any";
              }
            ];
            description = "Nebula firewall outbound rules.";
          };
        };
      };

    perInstance =
      {
        instanceName,
        settings,
        roles,
        machine,
        mkExports,
        ...
      }:
      let
        lighthouseMachines = getLighthouseMachines roles;
        # Get network CIDR from first lighthouse
        networkCidr =
          if lighthouseMachines != [ ] then
            let
              firstLighthouse = builtins.head lighthouseMachines;
            in
            roles.lighthouse.machines.${firstLighthouse}.settings.networkCidr
          else
            "10.87.0.0/16";
      in
      {
        exports = mkExports {
          peer.hosts = [
            {
              plain = settings.nebulaIp;
            }
          ];
        };

        nixosModule =
          {
            config,
            pkgs,
            lib,
            ...
          }:
          let
            # Build static host map for all lighthouses
            staticHostMap = lib.listToAttrs (
              map (
                lhName:
                let
                  lhSettings = roles.lighthouse.machines.${lhName}.settings;
                  endpoint =
                    if lib.hasInfix ":" lhSettings.endpoint then
                      lhSettings.endpoint
                    else
                      "${lhSettings.endpoint}:${toString lhSettings.port}";
                in
                {
                  name = lhSettings.nebulaIp;
                  value = [ endpoint ];
                }
              ) lighthouseMachines
            );

            # Get lighthouse IPs for the lighthouses config
            lighthouseIps = map (
              lhName: roles.lighthouse.machines.${lhName}.settings.nebulaIp
            ) lighthouseMachines;

            # Get relay lighthouse IPs
            relayIps = lib.filter (ip: ip != null) (
              map (
                lhName:
                let
                  lhSettings = roles.lighthouse.machines.${lhName}.settings;
                in
                if lhSettings.isRelay then lhSettings.nebulaIp else null
              ) lighthouseMachines
            );
          in
          {
            # Generator for CA certificate (shared across instance)
            clan.core.vars.generators."nebula-${instanceName}-ca" = {
              share = true;
              files."ca.crt".secret = false;
              files."ca.key" = { };
              runtimeInputs = [ pkgs.nebula ];
              script = ''
                nebula-cert ca -name "${instanceName}" -out-crt "$out/ca.crt" -out-key "$out/ca.key"
              '';
            };

            # Generator for node certificate
            clan.core.vars.generators."nebula-${instanceName}-node" = {
              files."node.crt".secret = false;
              files."node.key" = { };
              dependencies = [ config.clan.core.vars.generators."nebula-${instanceName}-ca" ];
              runtimeInputs = [ pkgs.nebula ];
              script = ''
                # Get CA files
                ca_crt="${config.clan.core.vars.generators."nebula-${instanceName}-ca".files."ca.crt".path}"
                ca_key="${config.clan.core.vars.generators."nebula-${instanceName}-ca".files."ca.key".path}"

                # Sign node certificate
                nebula-cert sign \
                  -name "${machine.name}" \
                  -ip "${settings.nebulaIp}/${lib.last (lib.splitString "/" networkCidr)}" \
                  -ca-crt "$ca_crt" \
                  -ca-key "$ca_key" \
                  -out-crt "$out/node.crt" \
                  -out-key "$out/node.key"
              '';
            };

            services.nebula.networks.${instanceName} = {
              enable = true;
              isLighthouse = false;

              listen = {
                host = "0.0.0.0";
                port = settings.port;
              };

              ca = config.clan.core.vars.generators."nebula-${instanceName}-ca".files."ca.crt".path;
              cert = config.clan.core.vars.generators."nebula-${instanceName}-node".files."node.crt".path;
              key = config.clan.core.vars.generators."nebula-${instanceName}-node".files."node.key".path;

              staticHostMap = staticHostMap;
              lighthouses = lighthouseIps;
              relays = relayIps;

              firewall = {
                inbound = settings.firewallInbound;
                outbound = settings.firewallOutbound;
              };
            };

            # Open firewall for Nebula if using a specific port
            networking.firewall.allowedUDPPorts = lib.optionals (settings.port != 0) [ settings.port ];

            # Add extra hosts entries for all machines in the network
            networking.extraHosts = lib.strings.concatStringsSep "\n" (
              lib.filter (s: s != "") (
                # Lighthouse hosts
                (map (
                  lhName:
                  let
                    lhSettings = roles.lighthouse.machines.${lhName}.settings;
                  in
                  "${lhSettings.nebulaIp} ${lhName}.nebula"
                ) lighthouseMachines)
                ++
                  # Node hosts
                  (map (
                    nodeName:
                    let
                      nodeSettings = roles.node.machines.${nodeName}.settings or null;
                    in
                    if nodeSettings != null then "${nodeSettings.nebulaIp} ${nodeName}.nebula" else ""
                  ) (lib.attrNames (roles.node.machines or { })))
              )
            );
          };
      };
  };
}
