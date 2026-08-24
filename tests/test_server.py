"""FastAPI adapter tests. Skipped when the optional server extra is not installed."""

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from qbwc_kit import qbxml  # noqa: E402
from qbwc_kit.qbxml import QBXMLRequest  # noqa: E402
from qbwc_kit.server import SOAP_CONTENT_TYPE, create_app  # noqa: E402
from qbwc_kit.service import QBWCService  # noqa: E402
from qbwc_kit.session import StaticAuthenticator  # noqa: E402
from qbwc_kit.testing import CUSTOMERS_FIXTURE, FakeQuickBooks, FakeWebConnector  # noqa: E402


class CountCustomers:
    name = "count"

    def __init__(self):
        self.total = 0

    def run(self, ctx):
        result = yield QBXMLRequest([qbxml.query("Customer")])
        self.total = len(result.first().records)


@pytest.fixture
def client_and_task():
    task = CountCustomers()
    service = QBWCService(authenticator=StaticAuthenticator("qbwc", "s3cret", [task]))
    app = create_app(service, endpoint_url="https://example.com/qbwc")
    return TestClient(app), task


def test_get_serves_the_wsdl(client_and_task):
    client, _ = client_and_task
    response = client.get("/qbwc")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/xml")
    assert "wsdl:definitions" in response.text
    assert 'location="https://example.com/qbwc"' in response.text


def test_post_dispatches_to_the_service(client_and_task):
    client, task = client_and_task
    connector = FakeWebConnector(
        transport=lambda body: (
            client.post("/qbwc", content=body, headers={"Content-Type": SOAP_CONTENT_TYPE}).text
        ),
        username="qbwc",
        password="s3cret",
    )

    result = connector.run_update(FakeQuickBooks(entities={"Customer": CUSTOMERS_FIXTURE}))

    assert result.authenticated
    assert result.progress[-1] == 100
    assert task.total == len(CUSTOMERS_FIXTURE)


def test_garbage_post_returns_a_fault_with_200(client_and_task):
    # SOAP faults ride on a normal response; QBWC does not read status codes.
    client, _ = client_and_task
    response = client.post("/qbwc", content="junk")
    assert response.status_code == 200
    assert "faultstring" in response.text


def test_can_mount_on_an_existing_app():
    service = QBWCService(authenticator=StaticAuthenticator("u", "p", []))
    app = fastapi.FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    create_app(service, endpoint_url="https://example.com/x", path="/x", app=app)
    client = TestClient(app)
    assert client.get("/health").json() == {"ok": True}
    assert "wsdl:definitions" in client.get("/x").text
