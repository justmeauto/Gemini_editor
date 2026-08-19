"""
Utilities/github_secret_updater.py
==================================
Automatically synchronizes refreshed and newly authenticated YouTube OAuth tokens
(token.json) to GitHub Repository Secrets using a GitHub Personal Access Token (PAT).

Supports:
- Automatic repository discovery from .git/config or GITHUB_REPOSITORY env var
- Multi-token mapping (Root token, Fashion niche, NSFW niche, General niche)
- Libsodium (PyNaCl) SealedBox encryption matching GitHub Actions secret spec
"""

import os
import sys
import re
import json
import logging
from typing import Optional, Tuple
from base64 import b64encode
import urllib.request
import urllib.parse
import urllib.error

logger = logging.getLogger("github_secret_updater")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env_tokens():
    """Loads PAT token and config from environment and .env files."""
    try:
        from dotenv import load_dotenv
        for env_path in [
            os.path.join(_REPO_ROOT, "Credentials", ".env"),
            os.path.join(_REPO_ROOT, ".env")
        ]:
            if os.path.exists(env_path):
                load_dotenv(env_path, override=False)
    except Exception:
        pass


def _get_github_pat() -> Optional[str]:
    """Retrieves GitHub Personal Access Token from environment."""
    _load_env_tokens()
    for var in ["GH_PAT_TOKEN", "GH_PAT", "GITHUB_PAT", "GITHUB_TOKEN", "GH_TOKEN"]:
        val = os.getenv(var, "").strip()
        if val and val != "your_github_pat_here":
            return val
    return None


def _get_github_repo() -> Optional[str]:
    """
    Resolves GitHub repository in 'owner/repo' format.
    Checks GITHUB_REPOSITORY env var first, then parses .git/config.
    """
    _load_env_tokens()
    repo_env = os.getenv("GITHUB_REPOSITORY", "").strip()
    if repo_env and "/" in repo_env:
        return repo_env

    git_config_path = os.path.join(_REPO_ROOT, ".git", "config")
    if os.path.exists(git_config_path):
        try:
            with open(git_config_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Look for github.com URLs in remotes (prefer origin, then any other)
            matches = re.findall(r"url\s*=\s*(?:https://github\.com/|git@github\.com:)([^/\s]+)/([^\s]+?)(?:\.git)?\s*$", content, re.MULTILINE)
            if matches:
                owner, repo_name = matches[0]
                repo_name = repo_name.rstrip(".git")
                return f"{owner}/{repo_name}"
        except Exception as e:
            logger.debug(f"Failed to parse .git/config: {e}")

    return None


def _map_token_path_to_secret_name(token_path: str) -> list:
    """Maps token file path to corresponding GitHub Actions Secret name(s)."""
    norm = os.path.abspath(token_path).replace("\\", "/").lower()
    if "fashion" in norm:
        return ["FASHION_YOUTUBE_TOKEN", "YOUTUBE_TOKEN_FASHION"]
    elif "nsfw" in norm:
        return ["NSFW_YOUTUBE_TOKEN", "YOUTUBE_TOKEN_NSFW"]
    elif "general" in norm:
        return ["GENERAL_YOUTUBE_TOKEN", "YOUTUBE_TOKEN_GENERAL"]
    return ["YOUTUBE_TOKEN_JSON", "TOKEN_JSON"]


def _get_repo_public_key(repo: str, pat: str) -> Optional[Tuple[str, str]]:
    """
    Fetches the repository's public key for Actions secret encryption.
    Returns (key_id, public_key_b64) or None on failure.
    """
    url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "AMTCE-Secret-Updater"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("key_id"), data.get("key")
    except Exception as e:
        logger.warning(f"⚠️ Failed to get GitHub public key for {repo}: {e}")
        return None


def _encrypt_secret(public_key_b64: str, secret_value: str) -> Optional[str]:
    """Encrypts secret_value using libsodium SealedBox with public_key_b64."""
    try:
        import nacl.encoding
        import nacl.public

        public_key = nacl.public.PublicKey(public_key_b64.encode("utf-8"), nacl.encoding.Base64Encoder())
        sealed_box = nacl.public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        return b64encode(encrypted).decode("utf-8")
    except ImportError:
        logger.warning("⚠️ PyNaCl is not installed. Install with 'pip install pynacl' to enable GitHub Secret sync.")
        return None
    except Exception as e:
        logger.error(f"⚠️ Encryption failed: {e}")
        return None


def _put_github_secret(repo: str, secret_name: str, encrypted_value: str, key_id: str, pat: str) -> bool:
    """Uploads encrypted secret to GitHub Actions repository secrets."""
    url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
    payload = json.dumps({
        "encrypted_value": encrypted_value,
        "key_id": key_id
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="PUT",
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "AMTCE-Secret-Updater"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (201, 204):
                return True
            return False
    except Exception as e:
        logger.warning(f"⚠️ Failed to upload GitHub secret '{secret_name}': {e}")
        return False


def sync_token_to_github_secret(token_path: str, token_content: Optional[str] = None) -> bool:
    """
    Main entry point: Synchronizes a token.json file to GitHub Repository Secrets.
    
    Args:
        token_path: Path to token.json
        token_content: Optional string content of token.json. If None, read from token_path.
        
    Returns:
        True if at least one secret was successfully updated, False otherwise.
    """
    pat = _get_github_pat()
    if not pat:
        logger.debug("ℹ️ No GH_PAT_TOKEN / GITHUB_TOKEN found in environment. Skipping GitHub Secret sync.")
        return False

    repo = _get_github_repo()
    if not repo:
        logger.debug("ℹ️ Could not determine GitHub repository (owner/repo). Skipping GitHub Secret sync.")
        return False

    if not token_content:
        if not os.path.exists(token_path):
            logger.warning(f"⚠️ Token file not found: {token_path}")
            return False
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                token_content = f.read()
        except Exception as e:
            logger.error(f"⚠️ Failed to read {token_path}: {e}")
            return False

    # Get repository encryption key
    key_info = _get_repo_public_key(repo, pat)
    if not key_info:
        return False

    key_id, public_key = key_info
    encrypted = _encrypt_secret(public_key, token_content)
    if not encrypted:
        return False

    secret_names = _map_token_path_to_secret_name(token_path)
    if isinstance(secret_names, str):
        secret_names = [secret_names]

    success = False
    for s_name in secret_names:
        if _put_github_secret(repo, s_name, encrypted, key_id, pat):
            logger.info(f"🔒 [GITHUB SECRETS] Successfully synced token to GitHub Secret: {repo} -> {s_name}")
            print(f"🔒 [GITHUB SECRETS] Successfully synced token to GitHub Secret: {repo} -> {s_name}")
            success = True

    return success


def sync_custom_secret_to_github(secret_name: str, secret_value: str) -> bool:
    """
    Synchronizes any custom secret (e.g. USER_1363193987_GEMINI_API_KEY)
    to GitHub Repository Secrets using GH_PAT.
    """
    if not secret_name or not secret_value:
        return False

    pat = _get_github_pat()
    if not pat:
        logger.debug("ℹ️ No GH_PAT_TOKEN / GITHUB_TOKEN found in environment. Skipping custom secret sync.")
        return False

    repo = _get_github_repo()
    if not repo:
        logger.debug("ℹ️ Could not determine GitHub repository (owner/repo). Skipping custom secret sync.")
        return False

    key_info = _get_repo_public_key(repo, pat)
    if not key_info:
        return False

    key_id, public_key = key_info
    encrypted = _encrypt_secret(public_key, secret_value)
    if not encrypted:
        return False

    if _put_github_secret(repo, secret_name, encrypted, key_id, pat):
        logger.info(f"🔒 [GITHUB SECRETS] Successfully synced custom secret: {repo} -> {secret_name}")
        return True
    return False
