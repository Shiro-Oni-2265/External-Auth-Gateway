from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_healthz_endpoint(temp_db_path, fernet_key):
    settings = Settings(
        APP_ENV="development",
        PUBLIC_BASE_URL="http://localhost:8080",
        ODOO_URL="https://mycompany.odoo.com",
        ODOO_DB="test_db",
        DATABASE_URL=f"sqlite:///{temp_db_path}",
        ENCRYPTION_KEY=fernet_key,
    )
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
