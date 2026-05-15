"""Job store unit tests with fakeredis."""
import pytest


@pytest.fixture
def fake_store():
    import fakeredis
    from app.core.job_store import JobStore, JobStatus
    client = fakeredis.FakeRedis(decode_responses=True)
    return JobStore(client), JobStatus


@pytest.mark.unit
def test_create_and_get(fake_store):
    store, JobStatus = fake_store
    job_id = store.create("https://example.com/careers")
    data = store.get(job_id)
    assert data["status"] == JobStatus.PENDING.value
    assert data["url"] == "https://example.com/careers"


@pytest.mark.unit
def test_set_result(fake_store):
    store, JobStatus = fake_store
    job_id = store.create("https://example.com/careers")
    store.set_result(job_id, [{"title": "Dev", "company": "Acme"}])
    data = store.get(job_id)
    assert data["status"] == JobStatus.COMPLETED.value
    assert len(data["result"]) == 1
