{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-21.05";
  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python39;
    in {
      defaultPackage.${system} = python.pkgs.buildPythonPackage {
        pname = "whatthemovie";
        version = "1.0";
        src = ./.;
        doCheck = false;
        propagatedBuildInputs = [
          python.pkgs.httpx
          python.pkgs.beautifulsoup4
          # discordpy is not maintained anymore (https://gist.github.com/Rapptz/4a2f62751b9600a31a0d3c78100287f1)
          # The nix package is broken because of its dependency to aiohttp < 3.8
          # If you want to use this package with NixOS >= 21.11 (which has
          # aiohttp > 3.8), then you’ll need the overrides below
          #
          # (python.pkgs.discordpy.override (oldAttrs: {
          #   aiohttp = ((python.pkgs.aiohttp.override (oldAioHttpAttrs: {
          #     async-timeout = (python.pkgs.async-timeout.overridePythonAttrs
          #       (oldPythonAttrs: rec {
          #         version = "3.0.1";
          #         src = python.pkgs.fetchPypi {
          #           inherit (oldPythonAttrs) pname;
          #           inherit version;
          #           sha256 =
          #             "sha256-DDyBagKNR/ZZ1v9cdFyyrPH5Ztof5cGcd6cCgrJfTF8=";
          #         };
          #       }));
          #   })).overridePythonAttrs (oldPythonAttrs: rec {
          #     version = "3.7.4.post0";
          #     src = python.pkgs.fetchPypi {
          #       inherit (oldPythonAttrs) pname;
          #       inherit version;
          #       sha256 = "sha256-XYTsxzFB0KDWHs4HQrt/9XUbBlfauEBfiZ086xBMx94=";
          #     };
          #   }));
          # }))
          python.pkgs.discordpy
        ];
      };
    };
}
