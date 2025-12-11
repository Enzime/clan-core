_: {
  perSystem =
    { config, ... }:
    {
      # Use the clan devShell which includes the CLI with shell completions
      devShells.default = config.devShells.clan;
    };
}
