{
  inputs.clan-core.url = "https://git.clan.lol/clan/clan-core/archive/main.tar.gz";
  inputs.nixpkgs.follows = "clan-core/nixpkgs";
  inputs.flake-parts.url = "github:hercules-ci/flake-parts";
  inputs.flake-parts.inputs.nixpkgs-lib.follows = "clan-core/nixpkgs";

  outputs =
    inputs@{
      flake-parts,
      ...
    }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      imports = [
        inputs.clan-core.flakeModules.default
      ];

      # https://docs.clan.lol/guides/flake-parts
      clan = {
        imports = [ ./clan.nix ];
      };

      perSystem =
        { config, ... }:
        {
          # Use the clan devShell which includes the CLI with shell completions
          devShells.default = config.devShells.clan;

          # Customize nixpkgs
          # _module.args.pkgs = import inputs.nixpkgs {
          #   inherit system;
          #   config.allowUnfree = true;
          #   overlays = [ ];
          # };
          # clan.pkgs = pkgs;
        };
    };
}
