{
  self,
  inputs,
  lib,
  ...
}:
let
  module = ./default.nix;
in
{
  clan.modules = {
    nebula = module;
  };
  perSystem =
    { ... }:
    let
      unit-test-module = (
        self.clanLib.test.flakeModules.makeEvalChecks {
          inherit module;
          inherit inputs;
          fileset = lib.fileset.unions [
            ../../clanServices/nebula
            ../../nixosModules
          ];
          testName = "nebula";
          tests = ./tests/eval-tests.nix;
          testArgs = { };
        }
      );
    in
    {
      imports = [ unit-test-module ];

      clan.nixosTests.nebula-service = {
        imports = [ ./tests/vm/default.nix ];
        clan.modules.nebula-service = module;
      };
    };
}
