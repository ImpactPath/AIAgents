"""Create or update a Hugging Face Space for this app from the terminal.

Usage (run inside youtube-subtitles/ with the virtualenv active):

    pip install -U "huggingface_hub[cli]"
    hf auth login                      # paste a WRITE token from https://huggingface.co/settings/tokens
    python scripts/deploy_hf_space.py --space <hf-username>/youtube-subtitles

What it does:
  1. creates the Space (Docker SDK, private by default) if it does not exist
  2. sets the MCP_API_KEY secret (generated and printed once unless --mcp-key is given)
  3. sets the PUBLIC_BASE_URL variable to the Space URL
  4. uploads this folder (app/, static/, Dockerfile, requirements.txt, README.md) to the Space
  5. prints the web URL and the MCP connector URL to paste into claude.ai / ChatGPT

Re-run it any time to push the latest code; secrets are only set when the flag is given.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

try:
    from huggingface_hub import HfApi
    from huggingface_hub.utils import HfHubHTTPError
except ImportError:  # pragma: no cover
    sys.exit("huggingface_hub is not installed. Run: pip install -U \"huggingface_hub[cli]\"")

ROOT = Path(__file__).resolve().parent.parent
IGNORE = [".venv/*", "__pycache__/*", "*/__pycache__/*", ".pytest_cache/*", "tests/*", "scripts/*",
          ".git/*", "*.pyc", "cookies.txt", ".gitignore"]


def space_url(space: str) -> str:
    owner, name = space.split("/", 1)
    slug = re.sub(r"[^a-z0-9-]", "-", f"{owner}-{name}".lower())
    return f"https://{slug}.hf.space"


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy youtube-subtitles to a Hugging Face Space")
    parser.add_argument("--space", required=True, help="<hf-username>/<space-name>")
    parser.add_argument("--public", action="store_true", help="make the Space public (default: private)")
    parser.add_argument("--mcp-key", help="set MCP_API_KEY to this value (default: generate on first run)")
    parser.add_argument("--cookies-file", help="path to cookies.txt to store as YTDLP_COOKIES_CONTENT")
    parser.add_argument("--skip-upload", action="store_true", help="only create the Space and set secrets")
    args = parser.parse_args()

    api = HfApi()
    who = api.whoami()["name"]
    print(f"Logged in as {who}")

    created = False
    try:
        api.repo_info(args.space, repo_type="space")
        print(f"Space {args.space} already exists")
    except HfHubHTTPError:
        api.create_repo(args.space, repo_type="space", space_sdk="docker", private=not args.public)
        created = True
        print(f"Created Space {args.space} ({'public' if args.public else 'private'})")

    url = space_url(args.space)
    mcp_key = args.mcp_key or (secrets.token_urlsafe(32) if created else None)
    if mcp_key:
        api.add_space_secret(args.space, "MCP_API_KEY", mcp_key)
        print("Set secret MCP_API_KEY")
    api.add_space_variable(args.space, "PUBLIC_BASE_URL", url)
    print(f"Set variable PUBLIC_BASE_URL={url}")
    if args.cookies_file:
        content = Path(args.cookies_file).read_text(encoding="utf-8")
        api.add_space_secret(args.space, "YTDLP_COOKIES_CONTENT", content)
        print("Set secret YTDLP_COOKIES_CONTENT")

    if not args.skip_upload:
        api.upload_folder(folder_path=str(ROOT), repo_id=args.space, repo_type="space",
                          ignore_patterns=IGNORE, commit_message="Deploy youtube-subtitles")
        print("Uploaded app files; the Space is now building (2 to 5 minutes)")

    print()
    print(f"Space page:   https://huggingface.co/spaces/{args.space}")
    print(f"Web app:      {url}")
    if mcp_key:
        print(f"MCP URL:      {url}/mcp?key={mcp_key}")
        print("Save the MCP URL now; the key is not shown again. Re-run with --mcp-key to rotate it.")
    else:
        print(f"MCP URL:      {url}/mcp?key=<your MCP_API_KEY>")


if __name__ == "__main__":
    main()
