"""Mint a per-tool pull token for the PSM read API.

    python -m mint_token acuity-agent                  # pull (default)
    python -m mint_token acuity-agent --scopes pull
    python -m mint_token acuity-agent --revoke

On the box (the service runs as container `psm-web`):

    docker exec psm-web python -m mint_token acuity-agent --scopes pull

Prints the raw token once. Store it in the consuming app's PSM_TOOL_TOKEN env var.

PSM is read-only: the only meaningful scope is `pull`. Minting also requires enforcement to be on
(PSM_REQUIRE_TOOL_TOKEN=1) for the token to actually be checked — otherwise reads stay open behind
Caddy forward-auth and the token is simply ignored.
"""
import argparse

import psm_auth
import psm_tokens


def main() -> None:
    ap = argparse.ArgumentParser(description="Mint/revoke a PSM tool token.")
    ap.add_argument("tool", help="subject (the consuming tool, e.g. acuity-agent)")
    ap.add_argument("--scopes", nargs="+", default=["pull"], help="token scopes (default: pull)")
    ap.add_argument("--revoke", action="store_true", help="revoke this tool's tokens instead of minting")
    args = ap.parse_args()

    store = psm_auth.tokens_path()
    if args.revoke:
        n = psm_tokens.revoke_tool(store, args.tool)
        print(f"revoked {n} token(s) for {args.tool!r}")
        return
    token = psm_tokens.mint_tool(store, args.tool, args.scopes)
    print(f"tool:   {args.tool}")
    print(f"scopes: {','.join(sorted(set(args.scopes)))}")
    print(f"store:  {store}")
    print(f"token:  {token}")
    print("(shown once — store it now in the consumer's PSM_TOOL_TOKEN)")


if __name__ == "__main__":
    main()