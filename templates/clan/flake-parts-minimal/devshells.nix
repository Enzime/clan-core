_: {
  perSystem =
    {
      pkgs,
      inputs',
      ...
    }:
    let
      clan-cli = inputs'.clan-core.packages.default;
    in
    {
      devShells = {
        default = pkgs.mkShellNoCC {
          packages = [ clan-cli ];
          shellHook = ''
            # Set up shell completions for clan CLI
            source ${clan-cli}/share/bash-completion/completions/clan
          '';
        };
      };
    };
}
