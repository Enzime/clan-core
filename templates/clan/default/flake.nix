{
  inputs.clan-core.url = "https://git.clan.lol/clan/clan-core/archive/main.tar.gz";
  inputs.nixpkgs.follows = "clan-core/nixpkgs";

  outputs =
    {
      self,
      clan-core,
      nixpkgs,
      ...
    }@inputs:
    let
      # Usage see: https://docs.clan.lol
      clan = clan-core.lib.clan {
        inherit self;
        imports = [ ./clan.nix ];
        specialArgs = { inherit inputs; };

        # Customize nixpkgs
        # pkgsForSystem =
        #   system:
        #   import nixpkgs {
        #     inherit system;
        #     config = {
        #       allowUnfree = true;
        #     };
        #     overlays = [];
        #   };
      };
    in
    {
      inherit (clan.config) nixosConfigurations nixosModules clanInternals;
      clan = clan.config;
      # Add the Clan cli tool to the dev shell.
      # Use "nix develop" to enter the dev shell.
      devShells =
        nixpkgs.lib.genAttrs
          [
            "x86_64-linux"
            "aarch64-linux"
            "aarch64-darwin"
            "x86_64-darwin"
          ]
          (system: {
            default =
              let
                pkgs = clan-core.inputs.nixpkgs.legacyPackages.${system};
                clan-cli = clan-core.packages.${system}.clan-cli;
              in
              pkgs.mkShell {
                packages = [ clan-cli ];
                shellHook = ''
                  # Add clan-cli share to XDG_DATA_DIRS for shell completion discovery
                  # - fish auto-scans $XDG_DATA_DIRS/fish/vendor_completions.d/
                  # - bash-completion scans $XDG_DATA_DIRS/bash-completion/completions/
                  export XDG_DATA_DIRS="${clan-cli}/share''${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"

                  # For interactive bash shells (nix develop), source completions directly
                  # This is needed because bash-completion only scans at startup
                  if [[ -n "''${BASH_VERSION:-}" && -n "''${PS1:-}" ]]; then
                    source "${clan-cli}/share/bash-completion/completions/clan"
                  fi
                '';
              };
          });
    };
}
