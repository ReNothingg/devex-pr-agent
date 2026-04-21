from __future__ import annotations

import base64
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlparse

import jwt
import requests
from dotenv import dotenv_values, load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

load_dotenv(ROOT_DIR / ".env")
load_dotenv(BASE_DIR / ".env", override=True)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
DEFAULT_BRANCH_PREFIX = "devex"
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_RETRY_TOTAL = 3
AUTH_SESSION_KEY = "devex_auth_session_id"
INSTALLATION_TOKEN_REFRESH_SECONDS = 60
AUTH_RETRY_STATUS_CODES = {401, 403, 404}
BAD_CREDENTIALS_HINT = (
    "GitHub отклонил токен. Публичные данные загружены только для чтения. "
    "Обновите GITHUB_TOKEN перед коммитом или созданием PR."
)
AUTH_SESSION_STORE: dict[str, dict[str, Any]] = {}


@dataclass(slots=True)
class GitHubAPIError(Exception):
    status_code: int
    message: str
    payload: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    if not repo_url or not repo_url.strip():
        raise ValueError("Нужна ссылка на репозиторий.")

    clean_url = repo_url.strip()

    if clean_url.startswith("git@github.com:"):
        repo_path = clean_url.split(":", 1)[1]
    else:
        normalized = clean_url if "://" in clean_url else f"https://{clean_url}"
        parsed = urlparse(normalized)
        hostname = parsed.netloc.lower()
        if hostname not in {"github.com", "www.github.com"}:
            raise ValueError("Поддерживаются только ссылки github.com.")
        repo_path = parsed.path

    repo_path = repo_path.strip("/")
    if repo_path.endswith(".git"):
        repo_path = repo_path[:-4]

    parts = [segment for segment in repo_path.split("/") if segment]
    if len(parts) < 2:
        raise ValueError("Ссылка должна содержать владельца и имя репозитория.")

    return parts[0], parts[1]


def slugify_branch_suffix(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._/-]+", "-", value.strip().lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-/.")
    return cleaned or "session"


def resolve_env_token() -> str | None:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    return token or None


def build_branch_name(suffix: str) -> str:
    return f"{DEFAULT_BRANCH_PREFIX}/{slugify_branch_suffix(suffix)}"


def iso_timestamp_from_unix(value: str | None) -> str | None:
    if not value:
        return None
    try:
        timestamp = int(value)
    except ValueError:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def mask_token(token: str | None) -> str | None:
    if not token:
        return None
    if len(token) <= 12:
        return token
    return f"{token[:8]}...{token[-4:]}"


def build_nested_tree(tree_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root: dict[str, Any] = {"type": "dir", "name": "", "path": "", "children": []}
    node_index: dict[str, dict[str, Any]] = {"": root}

    def ensure_directory(path: str) -> dict[str, Any]:
        normalized = path.strip("/")
        if normalized in node_index:
            return node_index[normalized]

        parent_path, _, name = normalized.rpartition("/")
        parent = ensure_directory(parent_path) if normalized else root
        node = {
            "type": "dir",
            "name": name,
            "path": normalized,
            "children": [],
        }
        parent["children"].append(node)
        node_index[normalized] = node
        return node

    for item in sorted(
        (item for item in tree_items if item["type"] in {"tree", "blob"}),
        key=lambda item: (item["path"].count("/"), item["path"].lower()),
    ):
        normalized_path = item["path"].strip("/")
        parent_path, _, name = normalized_path.rpartition("/")
        parent = ensure_directory(parent_path)

        if item["type"] == "tree":
            ensure_directory(normalized_path)
            continue

        if normalized_path in node_index:
            continue

        node = {
            "type": "file",
            "name": name,
            "path": normalized_path,
        }
        parent["children"].append(node)
        node_index[normalized_path] = node

    def sort_children(node: dict[str, Any]) -> None:
        if node["type"] != "dir":
            return
        node["children"].sort(key=lambda child: (child["type"] != "dir", child["name"].lower()))
        for child in node["children"]:
            sort_children(child)

    sort_children(root)
    return root["children"]


def summarize_tree(nodes: list[dict[str, Any]]) -> dict[str, int]:
    files = 0
    directories = 0
    max_depth = 0

    def walk(items: list[dict[str, Any]], depth: int) -> None:
        nonlocal files, directories, max_depth
        max_depth = max(max_depth, depth)
        for item in items:
            if item["type"] == "dir":
                directories += 1
                walk(item.get("children", []), depth + 1)
            else:
                files += 1

    walk(nodes, 1)
    return {
        "files": files,
        "directories": directories,
        "nodes": files + directories,
        "max_depth": max_depth,
    }


def is_rate_limit_error(error: GitHubAPIError) -> bool:
    message = (error.message or "").lower()
    if error.status_code == 403 and "rate limit exceeded" in message:
        return True
    remaining = (error.headers or {}).get("X-RateLimit-Remaining", "")
    return error.status_code == 403 and remaining == "0"


def should_retry_with_next_source(error: GitHubAPIError, source: str) -> bool:
    return source != "anonymous" and error.status_code in AUTH_RETRY_STATUS_CODES


def auth_warning_from_attempts(selected_source: str, errors: list[tuple[str, GitHubAPIError]]) -> str | None:
    if selected_source == "anonymous" and any(source != "anonymous" for source, _ in errors):
        return BAD_CREDENTIALS_HINT
    return None


def shape_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "login": user["login"],
        "name": user.get("name") or "",
        "html_url": user["html_url"],
        "avatar_url": user.get("avatar_url") or "",
    }


def shape_installation(installation: dict[str, Any]) -> dict[str, Any]:
    account = installation.get("account") or {}
    return {
        "id": installation["id"],
        "target_type": installation.get("target_type") or "",
        "repository_selection": installation.get("repository_selection") or "",
        "account_login": account.get("login") or "",
        "account_html_url": account.get("html_url") or "",
        "account_avatar_url": account.get("avatar_url") or "",
    }


def shape_repository(repo: dict[str, Any]) -> dict[str, Any]:
    permissions = repo.get("permissions") or {}
    return {
        "full_name": repo["full_name"],
        "html_url": repo["html_url"],
        "default_branch": repo.get("default_branch") or "main",
        "private": bool(repo.get("private")),
        "permissions": {
            "pull": bool(permissions.get("pull")),
            "push": bool(permissions.get("push")),
            "admin": bool(permissions.get("admin")),
        },
    }


def shape_app_metadata(app: dict[str, Any]) -> dict[str, Any]:
    owner = app.get("owner") or {}
    slug = app.get("slug") or os.getenv("GITHUB_APP_SLUG", "").strip()
    return {
        "name": app.get("name") or slug,
        "slug": slug,
        "description": app.get("description") or "",
        "owner_login": owner.get("login") or "",
        "owner_html_url": owner.get("html_url") or "",
        "page_url": build_github_app_page_url(slug),
        "install_url": build_github_app_install_url(slug),
        "visibility_hint": (
            "Если другой аккаунт видит 404 при установке, приложение GitHub App, вероятно, "
            "остается приватным. Включите Visibility = 'Any account' в настройках приложения."
        ),
    }


def get_config_diagnostics() -> dict[str, Any]:
    keys = (
        "GITHUB_APP_ID",
        "GITHUB_APP_CLIENT_ID",
        "GITHUB_APP_SLUG",
        "GITHUB_APP_PRIVATE_KEY_PATH",
    )
    root_values = dotenv_values(ROOT_DIR / ".env") if (ROOT_DIR / ".env").exists() else {}
    backend_values = dotenv_values(BASE_DIR / ".env") if (BASE_DIR / ".env").exists() else {}

    differing_keys = [
        key
        for key in keys
        if (root_values.get(key) or "").strip() != (backend_values.get(key) or "").strip()
        and ((root_values.get(key) or "").strip() or (backend_values.get(key) or "").strip())
    ]

    source = ".env"
    if backend_values:
        source = "backend/.env"

    return {
        "active_source": source,
        "has_backend_override": bool(backend_values),
        "has_conflict": bool(differing_keys),
        "conflicting_keys": differing_keys,
        "effective_client_id": os.getenv("GITHUB_APP_CLIENT_ID", "").strip(),
        "effective_slug": os.getenv("GITHUB_APP_SLUG", "").strip(),
    }


def get_auth_session_data() -> dict[str, Any]:
    session_id = session.get(AUTH_SESSION_KEY)
    if not session_id or session_id not in AUTH_SESSION_STORE:
        session_id = secrets.token_urlsafe(24)
        AUTH_SESSION_STORE[session_id] = {}
        session[AUTH_SESSION_KEY] = session_id
    return AUTH_SESSION_STORE[session_id]


def clear_auth_session() -> None:
    session_id = session.pop(AUTH_SESSION_KEY, None)
    if session_id:
        AUTH_SESSION_STORE.pop(session_id, None)


def reset_installation_state(auth_session: dict[str, Any]) -> None:
    auth_session.pop("installation_id", None)
    auth_session.pop("installation", None)
    auth_session.pop("installation_token", None)
    auth_session.pop("installation_token_expires_at", None)
    auth_session.pop("installation_repositories", None)


def clear_connected_user(auth_session: dict[str, Any]) -> None:
    auth_session.pop("user_token", None)
    auth_session.pop("user", None)
    auth_session.pop("oauth_state", None)
    auth_session.pop("after_auth_action", None)
    auth_session.pop("pending_installation_id", None)
    auth_session.pop("installations", None)
    reset_installation_state(auth_session)


def github_app_private_key() -> str | None:
    inline_key = os.getenv("GITHUB_APP_PRIVATE_KEY", "").strip()
    if inline_key:
        return inline_key.replace("\\n", "\n")

    key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
    if not key_path:
        return None

    resolved_path = Path(key_path)
    if not resolved_path.is_absolute():
        resolved_path = ROOT_DIR / resolved_path
    if not resolved_path.exists():
        return None
    return resolved_path.read_text(encoding="utf-8")


def github_app_missing_fields() -> list[str]:
    missing: list[str] = []
    if not os.getenv("GITHUB_APP_ID", "").strip():
        missing.append("GITHUB_APP_ID")
    if not os.getenv("GITHUB_APP_CLIENT_ID", "").strip():
        missing.append("GITHUB_APP_CLIENT_ID")
    if not os.getenv("GITHUB_APP_CLIENT_SECRET", "").strip():
        missing.append("GITHUB_APP_CLIENT_SECRET")
    if not os.getenv("GITHUB_APP_SLUG", "").strip():
        missing.append("GITHUB_APP_SLUG")
    if not github_app_private_key():
        missing.append("GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH")
    return missing


def github_app_configured() -> bool:
    return not github_app_missing_fields()


def build_github_app_jwt() -> str:
    app_id = os.getenv("GITHUB_APP_ID", "").strip()
    private_key = github_app_private_key()
    if not app_id or not private_key:
        raise ValueError("Данные GitHub App заполнены не полностью. Проверьте .env.")

    issued_at = int(time.time())
    payload = {
        "iat": issued_at - 60,
        "exp": issued_at + 540,
        "iss": app_id,
    }
    encoded = jwt.encode(payload, private_key, algorithm="RS256")
    return encoded if isinstance(encoded, str) else encoded.decode("utf-8")


def build_github_login_url() -> str:
    params = {
        "client_id": os.getenv("GITHUB_APP_CLIENT_ID", "").strip(),
        "redirect_uri": url_for("github_auth_callback", _external=True),
        "state": get_auth_session_data().get("oauth_state") or "",
    }
    return f"{GITHUB_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def build_github_app_page_url(slug: str | None = None) -> str:
    resolved_slug = (slug or os.getenv("GITHUB_APP_SLUG", "").strip()).strip()
    if not resolved_slug:
        raise ValueError("GITHUB_APP_SLUG is required to open the GitHub App page.")
    return f"https://github.com/apps/{resolved_slug}"


def build_github_app_install_url(slug: str | None = None) -> str:
    resolved_slug = (slug or os.getenv("GITHUB_APP_SLUG", "").strip()).strip()
    if not resolved_slug:
        raise ValueError("GITHUB_APP_SLUG is required to open the GitHub App installation page.")
    return f"{build_github_app_page_url(resolved_slug)}/installations/new"


def build_github_install_url() -> str:
    slug = os.getenv("GITHUB_APP_SLUG", "").strip()
    if not slug:
        raise ValueError("GITHUB_APP_SLUG is required to open the GitHub App installation page.")
    return build_github_app_page_url(slug)


def exchange_oauth_code_for_user_token(code: str) -> str:
    response = requests.post(
        GITHUB_OAUTH_ACCESS_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": os.getenv("GITHUB_APP_CLIENT_ID", "").strip(),
            "client_secret": os.getenv("GITHUB_APP_CLIENT_SECRET", "").strip(),
            "code": code,
            "redirect_uri": url_for("github_auth_callback", _external=True),
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()

    access_token = (payload.get("access_token") or "").strip()
    if access_token:
        return access_token

    error = payload.get("error") or "oauth_exchange_failed"
    description = payload.get("error_description") or "GitHub не вернул пользовательский токен."
    raise ValueError(f"Ошибка GitHub OAuth: {error} ({description})")


def load_github_app_metadata() -> dict[str, Any]:
    slug = os.getenv("GITHUB_APP_SLUG", "").strip()
    fallback = {
        "name": slug or "GitHub App",
        "slug": slug,
        "description": "",
        "owner_login": "",
        "owner_html_url": "",
        "page_url": build_github_app_page_url(slug) if slug else "",
        "install_url": build_github_app_install_url(slug) if slug else "",
        "visibility_hint": (
            "Если другой аккаунт видит 404 при установке, приложение GitHub App, вероятно, "
            "остается приватным. Включите Visibility = 'Any account' в настройках приложения."
        ),
    }

    if not github_app_configured():
        return fallback

    try:
        app_client = GitHubClient(build_github_app_jwt())
        return shape_app_metadata(app_client.get_app())
    except Exception:
        return fallback


def set_selected_installation(auth_session: dict[str, Any], installation: dict[str, Any]) -> None:
    reset_installation_state(auth_session)
    auth_session["installation_id"] = installation["id"]
    auth_session["installation"] = installation


def sync_user_installations(
    auth_session: dict[str, Any],
    preferred_installation_id: int | None = None,
) -> list[dict[str, Any]]:
    user_token = auth_session.get("user_token")
    if not user_token:
        auth_session["installations"] = []
        reset_installation_state(auth_session)
        return []

    client = GitHubClient(user_token)
    shaped_installations = [
        shape_installation(installation)
        for installation in client.list_user_installations()
    ]
    auth_session["installations"] = shaped_installations

    if preferred_installation_id is not None:
        preferred = next(
            (item for item in shaped_installations if item["id"] == preferred_installation_id),
            None,
        )
        if not preferred:
            raise ValueError(
                "Подключенный GitHub-аккаунт не видит эту установку GitHub App."
            )
        set_selected_installation(auth_session, preferred)
        auth_session.pop("pending_installation_id", None)
        return shaped_installations

    pending_installation_id = auth_session.get("pending_installation_id")
    if pending_installation_id is not None:
        pending_match = next(
            (item for item in shaped_installations if item["id"] == pending_installation_id),
            None,
        )
        if pending_match:
            set_selected_installation(auth_session, pending_match)
            auth_session.pop("pending_installation_id", None)

    selected_installation_id = auth_session.get("installation_id")
    if selected_installation_id is not None:
        selected = next(
            (item for item in shaped_installations if item["id"] == selected_installation_id),
            None,
        )
        if selected:
            auth_session["installation"] = selected
        else:
            reset_installation_state(auth_session)

    if auth_session.get("installation_id") is None and len(shaped_installations) == 1:
        set_selected_installation(auth_session, shaped_installations[0])

    return shaped_installations


def get_connected_installation_token(auth_session: dict[str, Any]) -> str | None:
    installation_id = auth_session.get("installation_id")
    if not installation_id:
        return None

    cached_token = auth_session.get("installation_token")
    cached_expires_at = parse_iso_datetime(auth_session.get("installation_token_expires_at"))
    now = datetime.now(tz=timezone.utc)
    if (
        cached_token
        and cached_expires_at is not None
        and (cached_expires_at - now).total_seconds() > INSTALLATION_TOKEN_REFRESH_SECONDS
    ):
        return cached_token

    app_token = build_github_app_jwt()
    app_client = GitHubClient(app_token)
    token_payload = app_client.create_installation_access_token(int(installation_id))
    auth_session["installation_token"] = token_payload["token"]
    auth_session["installation_token_expires_at"] = token_payload["expires_at"]
    return token_payload["token"]


def list_connected_installation_repositories(auth_session: dict[str, Any]) -> list[dict[str, Any]]:
    installation_token = get_connected_installation_token(auth_session)
    if not installation_token:
        return []

    client = GitHubClient(installation_token)
    repositories = [
        shape_repository(repository)
        for repository in client.list_installation_repositories()
    ]
    repositories.sort(key=lambda repository: repository["full_name"].lower())
    auth_session["installation_repositories"] = repositories
    return repositories


def runtime_token_candidates(
    override_token: str | None,
    include_anonymous: bool = False,
) -> list[tuple[str | None, str]]:
    candidates: list[tuple[str | None, str]] = []
    seen_tokens: set[str] = set()

    def add_candidate(token: str | None, source: str) -> None:
        normalized = (token or "").strip()
        if not normalized:
            return
        if normalized in seen_tokens:
            return
        seen_tokens.add(normalized)
        candidates.append((normalized, source))

    add_candidate(override_token, "override")

    auth_session = get_auth_session_data()
    if github_app_configured() and auth_session.get("installation_id"):
        try:
            add_candidate(get_connected_installation_token(auth_session), "github-app")
        except Exception:
            pass

    add_candidate(resolve_env_token(), ".env")

    if include_anonymous:
        candidates.append((None, "anonymous"))

    return candidates


def execute_with_runtime_clients(
    override_token: str | None,
    include_anonymous: bool,
    operation: Callable[[GitHubClient, str], Any],
) -> tuple[Any, str, list[tuple[str, GitHubAPIError]]]:
    candidates = runtime_token_candidates(override_token, include_anonymous=include_anonymous)
    if not candidates:
        raise ValueError(
            "Подключите GitHub или укажите токен перед записью изменений."
        )

    errors: list[tuple[str, GitHubAPIError]] = []
    for token, source in candidates:
        try:
            return operation(GitHubClient(token), source), source, errors
        except GitHubAPIError as error:
            errors.append((source, error))
            if not should_retry_with_next_source(error, source):
                raise

    if errors:
        raise errors[-1][1]

    raise ValueError("Не удалось найти подходящие учетные данные GitHub для этого действия.")


def build_auth_status_payload() -> dict[str, Any]:
    missing_config = github_app_missing_fields()
    config_diagnostics = get_config_diagnostics()
    payload: dict[str, Any] = {
        "configured": not missing_config,
        "missing_config": missing_config,
        "app": load_github_app_metadata(),
        "config_diagnostics": config_diagnostics,
        "user": None,
        "installation": None,
        "installations": [],
        "repositories": [],
        "connection_error": None,
        "auth_source": None,
        "urls": {
            "connect": url_for("github_auth_login"),
            "install": url_for("github_auth_install"),
            "disconnect": url_for("disconnect_github"),
            "select_installation": url_for("select_installation"),
            "app_page": build_github_app_page_url() if not missing_config else "",
        },
    }

    if missing_config:
        return payload

    auth_session = get_auth_session_data()
    if not auth_session.get("user_token"):
        return payload

    payload["user"] = auth_session.get("user")

    try:
        installations = sync_user_installations(auth_session)
    except GitHubAPIError:
        clear_connected_user(auth_session)
        payload["connection_error"] = "Сессия GitHub истекла или была отозвана. Подключите GitHub снова."
        payload["user"] = None
        return payload
    except ValueError as error:
        payload["connection_error"] = str(error)
        return payload

    payload["user"] = auth_session.get("user")
    payload["installations"] = installations
    payload["installation"] = auth_session.get("installation")

    if auth_session.get("installation_id"):
        try:
            payload["repositories"] = list_connected_installation_repositories(auth_session)
            payload["auth_source"] = "github-app"
        except Exception as error:
            payload["connection_error"] = str(error)

    return payload


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.session = self._build_session()

    def _build_session(self) -> requests.Session:
        session_client = requests.Session()
        retry_policy = Retry(
            total=REQUEST_RETRY_TOTAL,
            connect=REQUEST_RETRY_TOTAL,
            read=REQUEST_RETRY_TOTAL,
            status=REQUEST_RETRY_TOTAL,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"DELETE", "GET", "PATCH", "POST", "PUT"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_policy)
        session_client.mount("https://", adapter)
        session_client.mount("http://", adapter)
        session_client.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "Connection": "close",
                "User-Agent": "devex-editor/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        if self.token:
            session_client.headers["Authorization"] = f"Bearer {self.token}"
        return session_client

    def request(self, method: str, path: str, return_headers: bool = False, **kwargs: Any) -> Any:
        url = path if path.startswith("http") else f"{GITHUB_API_BASE}{path}"

        for attempt in range(2):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    **kwargs,
                )
                break
            except requests.exceptions.SSLError:
                if attempt == 1:
                    raise
                self.session.close()
                self.session = self._build_session()

        if response.status_code >= 400:
            message = f"GitHub API вернул статус {response.status_code}."
            payload: dict[str, Any] | None = None
            try:
                payload = response.json()
                message = payload.get("message", message)
            except ValueError:
                message = response.text.strip() or message

            raise GitHubAPIError(response.status_code, message, payload, dict(response.headers))

        if response.status_code == 204:
            return (None, dict(response.headers)) if return_headers else None

        payload = response.json()
        if return_headers:
            return payload, dict(response.headers)
        return payload

    def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return self.request("GET", f"/repos/{owner}/{repo}")

    def get_app(self) -> dict[str, Any]:
        return self.request("GET", "/app")

    def get_current_user(self) -> tuple[dict[str, Any], dict[str, str]]:
        return self.request("GET", "/user", return_headers=True)

    def list_user_installations(self) -> list[dict[str, Any]]:
        payload = self.request("GET", "/user/installations", params={"per_page": 100})
        return payload.get("installations", [])

    def create_installation_access_token(self, installation_id: int) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            json={},
        )

    def list_installation_repositories(self) -> list[dict[str, Any]]:
        payload = self.request("GET", "/installation/repositories", params={"per_page": 100})
        return payload.get("repositories", [])

    def get_branch(self, owner: str, repo: str, branch: str) -> dict[str, Any]:
        encoded_branch = quote(branch, safe="")
        return self.request("GET", f"/repos/{owner}/{repo}/branches/{encoded_branch}")

    def get_branch_ref(self, owner: str, repo: str, branch: str) -> dict[str, Any]:
        encoded_branch = quote(branch, safe="/")
        return self.request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{encoded_branch}")

    def list_directory_contents(self, owner: str, repo: str, path: str, ref: str) -> list[dict[str, Any]]:
        endpoint = f"/repos/{owner}/{repo}/contents"
        if path:
            endpoint = f"{endpoint}/{quote(path, safe='/')}"
        payload = self.request("GET", endpoint, params={"ref": ref})
        if not isinstance(payload, list):
            raise GitHubAPIError(400, f"Ожидалось содержимое папки по пути {path or '/'}")
        return payload

    def get_contents_tree(self, owner: str, repo: str, ref: str, path: str = "") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for entry in self.list_directory_contents(owner, repo, path, ref):
            entry_type = entry.get("type")
            entry_path = entry.get("path", "").strip("/")
            if entry_type == "dir":
                items.append({"type": "tree", "path": entry_path})
                items.extend(self.get_contents_tree(owner, repo, ref, entry_path))
            elif entry_type == "file":
                items.append({"type": "blob", "path": entry_path})
        return items

    def get_tree(self, owner: str, repo: str, branch: str) -> dict[str, Any]:
        branch_data = self.get_branch(owner, repo, branch)
        commit_sha = branch_data["commit"]["sha"]
        commit = self.request("GET", f"/repos/{owner}/{repo}/git/commits/{commit_sha}")
        tree_sha = commit["tree"]["sha"]
        tree = self.request(
            "GET",
            f"/repos/{owner}/{repo}/git/trees/{tree_sha}",
            params={"recursive": "1"},
        )
        raw_items = tree.get("tree", [])
        source = "git"
        was_truncated = bool(tree.get("truncated"))

        if was_truncated:
            raw_items = self.get_contents_tree(owner, repo, branch)
            source = "contents"

        nested_tree = build_nested_tree(raw_items)
        return {
            "tree": nested_tree,
            "stats": summarize_tree(nested_tree),
            "truncated": was_truncated,
            "source": source,
            "sha": tree_sha,
        }

    def get_text_file(self, owner: str, repo: str, path: str, ref: str) -> dict[str, Any]:
        encoded_path = quote(path, safe="/")
        payload = self.request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{encoded_path}",
            params={"ref": ref},
        )

        if isinstance(payload, list):
            raise GitHubAPIError(400, "Выбранный путь ведет к папке, а не к файлу.")
        if payload.get("type") != "file":
            raise GitHubAPIError(400, "Можно редактировать только обычные файлы.")
        if payload.get("encoding") != "base64":
            raise GitHubAPIError(400, "Неподдерживаемая кодировка содержимого GitHub.")

        raw_bytes = base64.b64decode(payload.get("content", ""))
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GitHubAPIError(400, "Бинарные файлы и файлы не в UTF-8 не поддерживаются.") from exc

        return {
            "name": payload["name"],
            "path": payload["path"],
            "sha": payload["sha"],
            "size": payload["size"],
            "content": text,
        }

    def ensure_branch(self, owner: str, repo: str, base_branch: str, new_branch: str) -> str:
        base_ref = self.get_branch_ref(owner, repo, base_branch)
        base_sha = base_ref["object"]["sha"]

        try:
            self.request(
                "POST",
                f"/repos/{owner}/{repo}/git/refs",
                json={"ref": f"refs/heads/{new_branch}", "sha": base_sha},
            )
        except GitHubAPIError as exc:
            if exc.status_code != 422 or "Reference already exists" not in exc.message:
                raise

        return new_branch

    def upsert_file(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: str,
        message: str,
        content: str,
    ) -> dict[str, Any]:
        sha: str | None = None
        try:
            existing = self.get_text_file(owner, repo, path, branch)
            sha = existing["sha"]
        except GitHubAPIError as exc:
            if exc.status_code != 404:
                raise

        encoded_path = quote(path, safe="/")
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        return self.request(
            "PUT",
            f"/repos/{owner}/{repo}/contents/{encoded_path}",
            json=payload,
        )

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
            },
        )


app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "devex-local-secret-key")


@app.get("/")
def index() -> str:
    return render_template("index.html", branch_prefix=DEFAULT_BRANCH_PREFIX)


@app.get("/auth/github/login")
def github_auth_login() -> Response:
    if not github_app_configured():
        raise ValueError(
            "GitHub App еще не настроен. Сначала заполните значения в .env."
        )

    auth_session = get_auth_session_data()
    auth_session["oauth_state"] = secrets.token_urlsafe(24)

    after_action = (request.args.get("after") or "").strip()
    if after_action:
        auth_session["after_auth_action"] = after_action

    return redirect(build_github_login_url())


@app.get("/auth/github/callback")
def github_auth_callback() -> Response:
    if not github_app_configured():
        raise ValueError(
            "GitHub App еще не настроен. Сначала заполните значения в .env."
        )

    auth_session = get_auth_session_data()
    expected_state = auth_session.get("oauth_state")
    actual_state = (request.args.get("state") or "").strip()
    code = (request.args.get("code") or "").strip()

    if not expected_state or not actual_state or actual_state != expected_state:
        raise ValueError("Ошибка состояния GitHub OAuth. Попробуйте подключиться заново.")
    if not code:
        raise ValueError("GitHub не вернул код авторизации.")

    user_token = exchange_oauth_code_for_user_token(code)
    auth_session["oauth_state"] = None
    auth_session["user_token"] = user_token

    client = GitHubClient(user_token)
    user, _ = client.get_current_user()
    auth_session["user"] = shape_user(user)
    sync_user_installations(auth_session)

    after_auth_action = auth_session.pop("after_auth_action", None)
    if after_auth_action == "install":
        return redirect(build_github_install_url())

    return redirect(url_for("index"))


@app.get("/auth/github/install")
def github_auth_install() -> Response:
    if not github_app_configured():
        raise ValueError(
            "GitHub App еще не настроен. Сначала заполните значения в .env."
        )

    auth_session = get_auth_session_data()
    if not auth_session.get("user_token"):
        auth_session["after_auth_action"] = "install"
        return redirect(url_for("github_auth_login"))

    return redirect(build_github_install_url())


@app.get("/auth/github/setup")
def github_auth_setup() -> Response:
    if not github_app_configured():
        raise ValueError(
            "GitHub App еще не настроен. Сначала заполните значения в .env."
        )

    installation_id = request.args.get("installation_id", type=int)
    if not installation_id:
        raise ValueError("GitHub не вернул installation_id после установки приложения.")

    auth_session = get_auth_session_data()
    if not auth_session.get("user_token"):
        auth_session["pending_installation_id"] = installation_id
        return redirect(url_for("github_auth_login"))

    sync_user_installations(auth_session, preferred_installation_id=installation_id)
    return redirect(url_for("index"))


@app.get("/api/auth/status")
def auth_status() -> Any:
    return jsonify(build_auth_status_payload())


@app.post("/api/auth/installation/select")
def select_installation() -> Any:
    payload = request.get_json(silent=True) or {}
    installation_id = payload.get("installation_id")
    if installation_id is None:
        raise ValueError("Нужно передать installation_id.")

    if not github_app_configured():
        raise ValueError("GitHub App еще не настроен.")

    auth_session = get_auth_session_data()
    if not auth_session.get("user_token"):
        raise ValueError("Сначала подключите GitHub.")

    sync_user_installations(auth_session, preferred_installation_id=int(installation_id))
    return jsonify(build_auth_status_payload())


@app.post("/api/auth/disconnect")
def disconnect_github() -> Any:
    clear_auth_session()
    return jsonify({"ok": True})


@app.get("/static/style.css")
def legacy_style_asset() -> Response:
    return Response('@import url("/static/styles.css");\n', mimetype="text/css")


@app.get("/static/main.js")
def legacy_script_asset() -> Response:
    script = """
if (!window.__DEVEX_APP_LOADED__) {
  const script = document.createElement("script");
  script.src = "/static/app.js";
  document.head.appendChild(script);
}
"""
    return Response(script.strip() + "\n", mimetype="application/javascript")


@app.post("/api/repository/load")
def load_repository() -> Any:
    payload = request.get_json(silent=True) or {}
    owner, repo = parse_repo_url(payload.get("repo_url", ""))

    def operation(client: GitHubClient, _: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
        repo_data = client.get_repo(owner, repo)
        base_branch = (payload.get("base_branch") or repo_data["default_branch"]).strip()
        tree_payload = client.get_tree(owner, repo, base_branch)
        return repo_data, base_branch, tree_payload

    (repo_data, base_branch, tree_payload), auth_source, errors = execute_with_runtime_clients(
        payload.get("token"),
        include_anonymous=True,
        operation=operation,
    )

    auth_warning = auth_warning_from_attempts(auth_source, errors)

    return jsonify(
        {
            "repository": {
                "owner": owner,
                "name": repo,
                "full_name": repo_data["full_name"],
                "private": repo_data["private"],
                "default_branch": repo_data["default_branch"],
                "base_branch": base_branch,
                "html_url": repo_data["html_url"],
            },
            "tree": tree_payload["tree"],
            "tree_stats": tree_payload["stats"],
            "truncated": tree_payload["truncated"],
            "tree_source": tree_payload["source"],
            "branch_prefix": DEFAULT_BRANCH_PREFIX,
            "auth_warning": auth_warning,
            "auth_source": auth_source,
        }
    )


@app.post("/api/token/check")
def check_token() -> Any:
    payload = request.get_json(silent=True) or {}
    override_token = payload.get("token")
    owner = (payload.get("owner") or "").strip()
    repo = (payload.get("repo") or "").strip()
    token = (override_token or "").strip() or resolve_env_token()

    if not token:
        raise ValueError("Токен GitHub не найден. Добавьте GITHUB_TOKEN в .env или вставьте его в интерфейс.")

    client = GitHubClient(token)
    user, headers = client.get_current_user()
    scopes = [scope.strip() for scope in headers.get("X-OAuth-Scopes", "").split(",") if scope.strip()]
    repository_access: dict[str, Any] | None = None

    if owner and repo:
        repo_payload = client.get_repo(owner, repo)
        permissions = repo_payload.get("permissions") or {}
        repository_access = {
            "full_name": repo_payload["full_name"],
            "private": repo_payload["private"],
            "permissions": {
                "pull": bool(permissions.get("pull")),
                "push": bool(permissions.get("push")),
                "admin": bool(permissions.get("admin")),
            },
        }

    return jsonify(
        {
            "login": user["login"],
            "name": user.get("name") or "",
            "html_url": user["html_url"],
            "token_source": "override" if (override_token or "").strip() else ".env",
            "token_masked": mask_token(token),
            "scopes": scopes,
            "rate_limit": {
                "limit": headers.get("X-RateLimit-Limit"),
                "remaining": headers.get("X-RateLimit-Remaining"),
                "reset_at": iso_timestamp_from_unix(headers.get("X-RateLimit-Reset")),
            },
            "repository_access": repository_access,
        }
    )


@app.post("/api/file/read")
def read_file() -> Any:
    payload = request.get_json(silent=True) or {}
    owner = (payload.get("owner") or "").strip()
    repo = (payload.get("repo") or "").strip()
    path = (payload.get("path") or "").strip()
    ref = (payload.get("ref") or "").strip()

    if not all([owner, repo, path, ref]):
        raise ValueError("Нужны owner, repo, path и ref.")

    def operation(client: GitHubClient, _: str) -> dict[str, Any]:
        return client.get_text_file(owner, repo, path, ref)

    file_payload, auth_source, errors = execute_with_runtime_clients(
        payload.get("token"),
        include_anonymous=True,
        operation=operation,
    )

    return jsonify(
        {
            **file_payload,
            "auth_warning": auth_warning_from_attempts(auth_source, errors),
            "auth_source": auth_source,
        }
    )


@app.post("/api/file/save")
def save_file() -> Any:
    payload = request.get_json(silent=True) or {}
    owner = (payload.get("owner") or "").strip()
    repo = (payload.get("repo") or "").strip()
    path = (payload.get("path") or "").strip()
    content = payload.get("content")
    base_branch = (payload.get("base_branch") or "").strip()
    commit_message = (payload.get("commit_message") or "").strip()
    branch_suffix = (payload.get("branch_suffix") or "").strip()

    if not all([owner, repo, path, base_branch, branch_suffix]):
        raise ValueError("Нужны owner, repo, path, base_branch и branch_suffix.")
    if content is None:
        raise ValueError("Нужно передать content.")

    branch_name = build_branch_name(branch_suffix)
    message = commit_message or f"Обновить {path}"
    repo_payload: dict[str, Any] | None = None

    def operation(client: GitHubClient, _: str) -> dict[str, Any]:
        nonlocal repo_payload
        repo_payload = client.get_repo(owner, repo)
        client.ensure_branch(owner, repo, base_branch, branch_name)
        return client.upsert_file(owner, repo, path, branch_name, message, content)

    try:
        result, auth_source, _ = execute_with_runtime_clients(
            payload.get("token"),
            include_anonymous=False,
            operation=operation,
        )
    except GitHubAPIError as error:
        permissions = (repo_payload or {}).get("permissions") or {}
        if repo_payload is not None and permissions and not permissions.get("push"):
            raise ValueError(
                "Эти учетные данные GitHub могут читать репозиторий, но не могут пушить в него. "
                "Установите GitHub App на этот репозиторий с правами на запись или используйте токен "
                "с доступом на запись."
            ) from error
        if error.status_code == 404:
            raise ValueError(
                "GitHub вернул 404 во время сохранения. Если используете Connect GitHub, проверьте, "
                "что GitHub App установлен на этот репозиторий. Если используете токен, проверьте "
                f"доступ на запись в {owner}/{repo}."
            ) from error
        raise

    return jsonify(
        {
            "branch": branch_name,
            "path": result["content"]["path"],
            "content_sha": result["content"]["sha"],
            "commit_sha": result["commit"]["sha"],
            "commit_url": result["commit"].get("html_url"),
            "auth_source": auth_source,
        }
    )


@app.post("/api/pull-request")
def open_pull_request() -> Any:
    payload = request.get_json(silent=True) or {}
    owner = (payload.get("owner") or "").strip()
    repo = (payload.get("repo") or "").strip()
    base_branch = (payload.get("base_branch") or "").strip()
    branch_suffix = (payload.get("branch_suffix") or "").strip()
    pr_title = (payload.get("title") or "").strip()
    pr_body = payload.get("body") or ""

    if not all([owner, repo, base_branch, branch_suffix]):
        raise ValueError("Нужны owner, repo, base_branch и branch_suffix.")

    branch_name = build_branch_name(branch_suffix)
    repo_payload: dict[str, Any] | None = None

    def operation(client: GitHubClient, _: str) -> dict[str, Any]:
        nonlocal repo_payload
        repo_payload = client.get_repo(owner, repo)
        return client.create_pull_request(
            owner=owner,
            repo=repo,
            title=pr_title or f"{branch_name} -> {base_branch}",
            head=branch_name,
            base=base_branch,
            body=pr_body,
        )

    try:
        pull_request, auth_source, _ = execute_with_runtime_clients(
            payload.get("token"),
            include_anonymous=False,
            operation=operation,
        )
    except GitHubAPIError as error:
        permissions = (repo_payload or {}).get("permissions") or {}
        if repo_payload is not None and permissions and not permissions.get("push"):
            raise ValueError(
                "Эти учетные данные GitHub могут читать репозиторий, но не могут открывать PR. "
                "Установите GitHub App с правами на запись или используйте токен с доступом на запись."
            ) from error
        if error.status_code == 404:
            raise ValueError(
                "GitHub вернул 404 при создании PR. Проверьте доступ текущей установки GitHub App "
                "или токена к этому репозиторию и ветке."
            ) from error
        raise

    return jsonify(
        {
            "number": pull_request["number"],
            "url": pull_request["html_url"],
            "title": pull_request["title"],
            "auth_source": auth_source,
        }
    )


@app.errorhandler(GitHubAPIError)
def handle_github_error(error: GitHubAPIError) -> tuple[Any, int]:
    message = error.message
    if error.status_code == 401:
        message = (
            "GitHub отклонил текущие учетные данные. Подключите GitHub заново или обновите "
            "токен в .env или в поле токена."
        )
    elif is_rate_limit_error(error):
        message = (
            "Лимит GitHub API для текущих учетных данных исчерпан. Подключите GitHub заново "
            "или используйте рабочий токен."
        )
    return jsonify({"error": message, "details": error.payload or {}}), error.status_code


@app.errorhandler(ValueError)
def handle_value_error(error: ValueError) -> tuple[Any, int]:
    return jsonify({"error": str(error)}), 400


@app.errorhandler(requests.RequestException)
def handle_request_error(error: requests.RequestException) -> tuple[Any, int]:
    if isinstance(error, requests.exceptions.SSLError):
        message = (
            "Не удалось установить SSL-соединение с GitHub. Повторите попытку. Если ошибка "
            "остается, проверьте VPN, proxy, антивирус и сетевые настройки."
        )
    else:
        message = f"Сетевая ошибка при обращении к GitHub: {error}"
    return jsonify({"error": message}), 502


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=int(os.getenv("PORT", "5000")))
