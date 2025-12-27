{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/release-25.11";
  outputs =
    { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    rec {
      packages.${system}.default = pkgs.python3.pkgs.buildPythonApplication {
        pname = "whatthemovie";
        version = "1.0";
        pyproject = true;
        build-system = [ pkgs.python3.pkgs.setuptools ];
        src = ./.;
        doCheck = false;
        nativeBuildInputs = with pkgs.python3.pkgs; [
          setuptools
          pip
        ];

        propagatedBuildInputs = with pkgs.python3.pkgs; [
          httpx
          beautifulsoup4
          discordpy
        ];
      };
    };
}
