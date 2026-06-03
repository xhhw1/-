import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_frontend_production_scaffold_exists() -> None:
    expected = [
        "package.json",
        "vite.config.ts",
        "tsconfig.json",
        "eslint.config.js",
        "src/main.tsx",
        "src/App.tsx",
        "src/api/client.ts",
        "src/api/types.ts",
        "src/api/queries.ts",
        "src/styles/tokens.css",
        "src/styles/app.css",
    ]

    for relative in expected:
        assert (FRONTEND / relative).exists(), relative


def test_frontend_package_has_production_scripts_and_stack() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))

    assert package["type"] == "module"
    assert package["scripts"]["build"] == (
        "tsc -p tsconfig.json --noEmit && vite build --config vite.config.ts"
    )
    assert "typecheck" in package["scripts"]
    assert "lint" in package["scripts"]
    assert "react" in package["dependencies"]
    assert "vite" in package["dependencies"]
    assert "typescript" in package["dependencies"]
    assert "@tanstack/react-query" in package["dependencies"]


def test_frontend_typescript_is_strict() -> None:
    tsconfig = json.loads((FRONTEND / "tsconfig.json").read_text(encoding="utf-8"))

    assert tsconfig["compilerOptions"]["strict"] is True
    assert tsconfig["compilerOptions"]["allowJs"] is False
    assert tsconfig["compilerOptions"]["jsx"] == "react-jsx"


def test_backend_mounts_react_frontend_as_app_next_when_built() -> None:
    main = (ROOT / "src/ai_visual_agent/main.py").read_text(encoding="utf-8")

    assert 'frontend" / "dist"' in main
    assert '"/app-next"' in main
    assert "production_console" in main


def test_frontend_vite_base_matches_mount_path() -> None:
    vite_config = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")

    assert 'base: "/app-next/"' in vite_config


def test_react_frontend_uses_legacy_styles_for_visual_parity() -> None:
    main_tsx = (FRONTEND / "src/main.tsx").read_text(encoding="utf-8")
    app_tsx = (FRONTEND / "src/App.tsx").read_text(encoding="utf-8")

    assert "../../src/ai_visual_agent/web/styles.css" in main_tsx
    assert 'className="app-shell"' in app_tsx
    assert 'className="sidebar"' in app_tsx
    assert 'className="chat-area"' in app_tsx
    assert 'className="chat-input-box"' in app_tsx
