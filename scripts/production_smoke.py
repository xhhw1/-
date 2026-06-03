from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass
class HttpResult:
    status: int
    body: Any
    text: str


def main() -> int:
    parser = argparse.ArgumentParser(description="Run low-risk production smoke checks.")
    parser.add_argument("--base-url", default=os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL", "1173817292@qq.com"))
    parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD", ""))
    parser.add_argument("--allow-not-ready", action="store_true")
    parser.add_argument("--skip-auth", action="store_true")
    parser.add_argument("--create-project", action="store_true")
    args = parser.parse_args()

    runner = SmokeRunner(
        base_url=args.base_url,
        email=args.email,
        password=args.password,
        allow_not_ready=args.allow_not_ready,
        skip_auth=args.skip_auth,
        create_project=args.create_project,
    )
    try:
        runner.run()
    except SmokeFailure as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1
    return 0


class SmokeFailure(RuntimeError):
    pass


class SmokeRunner:
    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        password: str,
        allow_not_ready: bool,
        skip_auth: bool,
        create_project: bool,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.allow_not_ready = allow_not_ready
        self.skip_auth = skip_auth
        self.create_project = create_project
        self.token = ""

    def run(self) -> None:
        health = self._expect("GET", "/health", expected={200})
        self._log("health", f"ok app_env={health.body.get('app_env')} auth={health.body.get('auth_enabled')}")

        ready = self._request("GET", "/health/ready")
        if ready.status != 200:
            message = _readiness_message(ready.body)
            if not self.allow_not_ready:
                raise SmokeFailure(f"readiness failed with {ready.status}: {message}")
            self._log("readiness", f"not ready but allowed: {message}")
        else:
            self._log("readiness", "ready")

        auth_enabled = bool(health.body.get("auth_enabled"))
        if auth_enabled and not self.skip_auth:
            self._login()
            self._expect("GET", "/api/auth/me", expected={200}, auth=True)
            self._log("auth", f"logged in as {self.email}")
        elif auth_enabled:
            self._log("auth", "skipped by --skip-auth")
        else:
            self._log("auth", "disabled")

        self._expect("GET", "/api/tasks", expected={200}, auth=auth_enabled and not self.skip_auth)
        self._log("tasks", "list ok")

        self._expect("GET", "/api/projects", expected={200}, auth=auth_enabled and not self.skip_auth)
        self._log("projects", "list ok")

        if self.create_project:
            self._create_and_delete_project(auth=auth_enabled and not self.skip_auth)

    def _login(self) -> None:
        if not self.password:
            raise SmokeFailure("AUTH_ENABLED=true, but no password was supplied via --password or ADMIN_PASSWORD.")
        result = self._expect(
            "POST",
            "/api/auth/login",
            expected={200},
            body={"email": self.email, "password": self.password},
        )
        self.token = str(result.body.get("access_token") or "")
        if not self.token:
            raise SmokeFailure("login response did not include an access_token.")

    def _create_and_delete_project(self, *, auth: bool) -> None:
        project = self._expect(
            "POST",
            "/api/projects",
            expected={200},
            auth=auth,
            body={
                "workflow_type": "packaging",
                "brief": {
                    "category": "smoke-test",
                    "target_user": "operator",
                    "user_expectations": ["deployment check"],
                    "user_metrics": [],
                    "value_proposition": "verify basic project persistence",
                    "core_product_definition": "temporary smoke test project",
                    "raw_text": "temporary smoke test project",
                },
                "assets": [],
            },
        )
        project_id = str(project.body.get("id") or "")
        if not project_id:
            raise SmokeFailure("created project response did not include id.")
        self._expect("DELETE", f"/api/projects/{project_id}", expected={204}, auth=auth)
        self._log("project-crud", f"created and deleted {project_id}")

    def _expect(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        auth: bool = False,
        body: dict[str, Any] | None = None,
    ) -> HttpResult:
        result = self._request(method, path, auth=auth, body=body)
        if result.status not in expected:
            raise SmokeFailure(f"{method} {path} returned {result.status}: {result.text[:500]}")
        return result

    def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = False,
        body: dict[str, Any] | None = None,
    ) -> HttpResult:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            if not self.token:
                raise SmokeFailure(f"{method} {path} requires auth but no token is available.")
            headers["Authorization"] = f"Bearer {self.token}"
        req = request.Request(
            url=f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return HttpResult(status=response.status, body=_json_or_text(raw), text=raw)
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return HttpResult(status=exc.code, body=_json_or_text(raw), text=raw)
        except error.URLError as exc:
            raise SmokeFailure(f"{method} {path} connection failed: {exc}") from exc

    def _log(self, name: str, message: str) -> None:
        print(f"[ok] {name}: {message}")


def _json_or_text(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _readiness_message(body: Any) -> str:
    if not isinstance(body, dict):
        return str(body)
    failures = body.get("failures") or []
    if failures:
        return "; ".join(str(item) for item in failures)
    return str(body.get("status") or body)


if __name__ == "__main__":
    raise SystemExit(main())
