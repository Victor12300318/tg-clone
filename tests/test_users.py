import pytest
import tempfile
from pathlib import Path

from httpx import AsyncClient, ASGITransport

from app import app
from core.database import init_db
from core.security import hash_password, verify_password, create_token, verify_token


def test_password_hashing():
    h = hash_password("senha12345")
    assert h != "senha12345"
    assert verify_password("senha12345", h)
    assert not verify_password("errada", h)


def test_token_roundtrip():
    token = create_token(42)
    assert verify_token(token) == 42
    assert verify_token("garbage") is None
    assert verify_token(token + "x") is None


@pytest.mark.asyncio
async def test_register_login_and_duplicate(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("core.database.DATABASE_PATH", Path(tmpdir) / "test_users.db")
        await init_db()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Register
            res = await client.post("/api/users/register", json={"email": "a@test.com", "password": "senha12345"})
            assert res.status_code == 200
            token = res.json()["token"]
            assert token

            # Duplicate email
            res = await client.post("/api/users/register", json={"email": "a@test.com", "password": "outra12345"})
            assert res.status_code == 409

            # Invalid email
            res = await client.post("/api/users/register", json={"email": "sem-arroba", "password": "senha12345"})
            assert res.status_code == 400

            # Short password
            res = await client.post("/api/users/register", json={"email": "b@test.com", "password": "curta"})
            assert res.status_code == 400

            # Wrong password
            res = await client.post("/api/users/login", json={"email": "a@test.com", "password": "errada"})
            assert res.status_code == 401

            # Unknown email
            res = await client.post("/api/users/login", json={"email": "ninguem@test.com", "password": "senha12345"})
            assert res.status_code == 401

            # Login ok
            res = await client.post("/api/users/login", json={"email": "a@test.com", "password": "senha12345"})
            assert res.status_code == 200
            assert res.json()["token"]

            # Protected route without token
            res = await client.get("/api/tasks")
            assert res.status_code == 401

            # Protected route with invalid token
            res = await client.get("/api/tasks", headers={"Authorization": "Bearer garbage"})
            assert res.status_code == 401

            # Protected route with valid token
            res = await client.get("/api/tasks", headers={"Authorization": f"Bearer {token}"})
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_default_user_from_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("core.database.DATABASE_PATH", Path(tmpdir) / "test_default_user.db")
        monkeypatch.setattr("core.config.DEFAULT_USER_EMAIL", "customadmin@mydomain.com")
        monkeypatch.setattr("core.config.DEFAULT_USER_PASSWORD", "SuperPass999")
        await init_db()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Login with full email
            res = await client.post("/api/users/login", json={"email": "customadmin@mydomain.com", "password": "SuperPass999"})
            assert res.status_code == 200
            assert res.json()["token"]
            assert res.json()["email"] == "customadmin@mydomain.com"

            # Login with prefix
            res2 = await client.post("/api/users/login", json={"email": "customadmin", "password": "SuperPass999"})
            assert res2.status_code == 200
            assert res2.json()["token"]

            # Login with generic 'admin' alias
            res3 = await client.post("/api/users/login", json={"email": "admin", "password": "SuperPass999"})
            assert res3.status_code == 200
            assert res3.json()["token"]

            # Wrong password fails
            res4 = await client.post("/api/users/login", json={"email": "customadmin", "password": "wrong"})
            assert res4.status_code == 401
