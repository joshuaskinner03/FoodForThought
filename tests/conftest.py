import pytest
from fastapi.testclient import TestClient

from foodforthought.app import create_app
from foodforthought.config import Config, KdfParams

PASSWORD = "correct horse battery"
BASE_URL = "http://127.0.0.1:8765"
FAST_KDF = KdfParams(time_cost=1, memory_cost=64, parallelism=1)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path / "data", kdf=FAST_KDF)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(cfg, clock):
    with TestClient(create_app(cfg, clock), base_url=BASE_URL, follow_redirects=False) as c:
        yield c


@pytest.fixture
def unlocked(client):
    response = client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    assert response.status_code == 303
    return client


def core_of(client):
    return client.app.state.core
