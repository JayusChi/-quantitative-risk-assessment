from __future__ import annotations

from pathlib import Path

import pytest

from db_qra.admin_ui import admin_html
from db_qra.project_ui import project_workspace_html
from db_qra.review_ui import review_workbench_html
from db_qra.server import _validate_deployment_security


def test_non_loopback_and_production_deployments_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("QRA_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("QRA_DEPLOYMENT_MODE", "development")
    assert (
        _validate_deployment_security(
            "127.0.0.1", tls_cert=None, tls_key=None, trust_proxy_tls=False
        )
        == "development"
    )
    with pytest.raises(ValueError, match="QRA_ADMIN_TOKEN"):
        _validate_deployment_security(
            "0.0.0.0", tls_cert=None, tls_key=None, trust_proxy_tls=False
        )

    monkeypatch.setenv("QRA_DEPLOYMENT_MODE", "production")
    with pytest.raises(ValueError, match="QRA_ADMIN_TOKEN"):
        _validate_deployment_security(
            "127.0.0.1", tls_cert=None, tls_key=None, trust_proxy_tls=False
        )
    monkeypatch.setenv("QRA_ADMIN_TOKEN", "test-only-secret")
    with pytest.raises(ValueError, match="TLS"):
        _validate_deployment_security(
            "127.0.0.1", tls_cert=None, tls_key=None, trust_proxy_tls=False
        )
    assert (
        _validate_deployment_security(
            "0.0.0.0", tls_cert=None, tls_key=None, trust_proxy_tls=True
        )
        == "production"
    )


def test_tls_certificate_and_key_must_be_paired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QRA_DEPLOYMENT_MODE", "development")
    with pytest.raises(ValueError, match="同时提供"):
        _validate_deployment_security(
            "127.0.0.1",
            tls_cert=Path("server.crt"),
            tls_key=None,
            trust_proxy_tls=False,
        )


def test_admin_token_is_not_persisted_in_browser_storage() -> None:
    pages = "\n".join(
        (
            admin_html().decode("utf-8"),
            project_workspace_html().decode("utf-8"),
            review_workbench_html("JOB-TEST").decode("utf-8"),
        )
    )
    assert "localStorage.getItem('qra-admin-token')" not in pages
    assert "localStorage.setItem('qra-admin-token'" not in pages
    assert "adminToken" in pages
