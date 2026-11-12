"""Model promote / rollback CLI + admin activate endpoint."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from src.models.registry import list_versions, promote_version, read_registry, rollback_version, write_registry
from src.runtime_settings import RuntimeSettings
from src.service.api import create_app
from tests.helpers import write_tiny_models


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path
    models = write_tiny_models(root / "artifacts" / "models", stages=("A",))
    v1 = root / "artifacts" / "versions" / "v-one"
    v2 = root / "artifacts" / "versions" / "v-two"
    shutil.copytree(models, v1)
    # Distinct second version: copy then touch a marker file name by re-writing.
    shutil.copytree(models, v2)
    write_registry({"active": "v-one", "previous": None, "versions": {"v-one": {}}, "history": []}, root / "artifacts" / "registry.json")
    # Seed live dir as v-one
    for p in models.glob("*.joblib"):
        p.unlink()
    for p in v1.glob("*.joblib"):
        (models / p.name).write_bytes(p.read_bytes())
    return root, models, v2


def test_promote_then_rollback(tmp_path: Path):
    root, models, _ = _layout(tmp_path)
    promote_version("v-two", root=root, models_dir=models)
    reg = read_registry(root / "artifacts" / "registry.json")
    assert reg["active"] == "v-two"
    assert reg["previous"] == "v-one"
    rolled = rollback_version(root=root, models_dir=models)
    assert rolled["active"] == "v-one"
    reg2 = read_registry(root / "artifacts" / "registry.json")
    assert reg2["active"] == "v-one"
    listed = list_versions(root=root, models_dir=models)
    assert "v-one" in listed["on_disk"] or listed["active"] == "v-one"


def test_admin_activate_and_models_endpoint(tmp_path: Path, monkeypatch):
    root, models, _ = _layout(tmp_path)
    monkeypatch.setenv("PRPULSE_ADMIN_TOKEN", "test-admin-token-xyz")
    runtime = RuntimeSettings(admin_token="test-admin-token-xyz")
    app = create_app(
        models_dir=models,
        db_path=tmp_path / "admin.db",
        webhook_secret="",
        start_recovery=False,
        runtime_settings=runtime,
    )
    with TestClient(app) as client:
        denied = client.post(
            "/admin/models/activate",
            json={"version": "v-two", "action": "promote"},
            headers={"X-PRPulse-Admin-Token": "wrong-token"},
        )
        assert denied.status_code == 401, denied.text
        ok = client.post(
            "/admin/models/activate",
            json={"version": "v-two", "action": "promote"},
            headers={"X-PRPulse-Admin-Token": "test-admin-token-xyz"},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["registry_active"] == "v-two"
        models_resp = client.get("/models").json()
        assert models_resp["registry_active"] == "v-two"
        back = client.post(
            "/admin/models/activate",
            json={"action": "rollback"},
            headers={"X-PRPulse-Admin-Token": "test-admin-token-xyz"},
        )
        assert back.status_code == 200, back.text
        assert back.json()["registry_active"] == "v-one"
        assert client.get("/models").json()["registry_active"] == "v-one"


def test_require_models_refuses_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRPULSE_REQUIRE_MODELS", "1")
    empty = tmp_path / "artifacts" / "models"
    empty.mkdir(parents=True)
    app = create_app(models_dir=empty, db_path=tmp_path / "x.db", webhook_secret="", start_recovery=False)
    try:
        with TestClient(app) as client:
            client.get("/health")
        raised = False
    except Exception:
        raised = True
    assert raised
