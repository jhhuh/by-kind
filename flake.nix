{
  description = "by-kind — a browsable category axis over nixpkgs pkgs/by-name";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3.withPackages (ps: with ps; [
          pyyaml      # taxonomy.yaml
          anthropic   # stage (5) LLM improvement loop
          pytest
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = [
            python
            pkgs.jq        # pre-filter the 394 MB packages.json
            pkgs.brotli    # decompress packages.json.br
            pkgs.git       # blobless sparse clone of nixpkgs
            pkgs.sqlite
            pkgs.overmind  # per-project process management
            pkgs.tmux      # overmind dependency
          ];
        };
      });
}
