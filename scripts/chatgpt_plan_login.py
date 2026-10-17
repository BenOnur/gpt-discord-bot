"""One-shot SIWC login for the Discord VDS. Never imports Hermes credentials."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import jwt
from bot.llm.chatgpt_plan import save_credentials, TOKEN_URL


def validate_identity(tokens, client_id, nonce, previous=None):
    signing_key = jwt.PyJWKClient("https://auth.openai.com/.well-known/jwks.json").get_signing_key_from_jwt(tokens["id_token"])
    identity = jwt.decode(tokens["id_token"], signing_key.key, algorithms=["RS256"],
                          audience=client_id, issuer="https://auth.openai.com",
                          options={"require": ["sub", "exp", "iat", "nonce"]}, leeway=5)
    if not secrets.compare_digest(identity["nonce"], nonce):
        raise ValueError("OIDC nonce mismatch")
    if previous and identity["sub"] != previous["subject"]:
        raise ValueError("Different ChatGPT identity; existing connection preserved")
    if "chatgpt.tokens.use.direct" not in tokens.get("scope", "").split():
        raise ValueError("ChatGPT plan permission was not granted")
    return identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--port", type=int, default=1456)
    args = parser.parse_args()
    path = Path(args.credentials)
    path.parent.mkdir(parents=True, exist_ok=True)
    host_path = path.with_name("chatgpt-host.json")
    if not host_path.exists():
        save_credentials(host_path, {"host_id": "urn:uuid:" + str(uuid.uuid4())})
    host = json.loads(host_path.read_text())["host_id"]
    previous = json.loads(path.read_text()) if path.exists() else None
    state, nonce, verifier = (secrets.token_urlsafe(48) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect = f"http://127.0.0.1:{args.port}/auth/callback"
    issued = previous["client_id"] if previous else "dynamic_agent_client"
    params = dict(client_id=issued, ext_agent_host_id=host, response_type="code", redirect_uri=redirect,
                  scope="openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
                  resource="https://api.openai.com/v1", state=state, nonce=nonce,
                  code_challenge_method="S256", code_challenge=challenge)
    if not previous:
        params["agent_name_hint"] = "Discord Ortak Asistan"
    # No id_token_hint in printed URLs; returning sign-in uses account selector.
    outcome = {"done": False, "ok": False}
    deadline = time.time() + 600

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback URLs contain one-time codes. Never log them.

        def do_GET(self):
            parsed = urlparse(self.path)
            query = {key: value[0] for key, value in parse_qs(parsed.query).items()}
            if parsed.path != "/auth/callback" or not secrets.compare_digest(query.get("state", ""), state):
                self.send_error(400, "Invalid login state")
                return
            outcome["done"] = True
            try:
                if time.time() > deadline or query.get("error"):
                    raise ValueError("Login expired or declined")
                client_id = query.get("client_id", issued)
                if client_id == "dynamic_agent_client" or (previous and client_id != issued):
                    raise ValueError("Invalid issued client registration")
                response = httpx.post(TOKEN_URL, data=dict(grant_type="authorization_code", client_id=client_id,
                    code=query["code"], code_verifier=verifier, redirect_uri=redirect,
                    resource="https://api.openai.com/v1"), timeout=30)
                if response.status_code != 200:
                    raise ValueError(f"Code exchange HTTP {response.status_code}")
                tokens = response.json()
                identity = validate_identity(tokens, client_id, nonce, previous)
                save_credentials(path, {**tokens, "subject": identity["sub"], "client_id": client_id,
                    "ext_agent_host_id": host, "scopes": tokens["scope"].split(),
                    "expires_at": time.time() + int(tokens["expires_in"])})
                outcome["ok"] = True
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ChatGPT connected to Discord assistant. You can close this tab.")
                print("LOGIN_SUCCEEDED", flush=True)
            except Exception as exc:
                self.send_error(400, "Login not completed; existing connection preserved")
                print("LOGIN_FAILED:" + type(exc).__name__, flush=True)

    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.timeout = 1
        print("Continue with ChatGPT: https://auth.openai.com/api/accounts/authorize?" + urlencode(params), flush=True)
        while not outcome["done"] and time.time() < deadline:
            server.handle_request()
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
