from shared.queue.factory import QueuePublisherFactory
from shared.queue.noop_publisher import NoOpQueuePublisher
from shared.queue.redis_stream_publisher import RedisStreamPublisher
from shared.queue.sns_publisher import SNSPublisher


def test_async_publisher_uses_noop_when_transport_disabled(monkeypatch) -> None:
    from shared.config.settings import settings

    monkeypatch.setattr(settings, "async_transport", "disabled")
    publisher = QueuePublisherFactory.get_async_publisher()
    assert isinstance(publisher, NoOpQueuePublisher)


def test_async_publisher_uses_sns_when_transport_is_aws(monkeypatch) -> None:
    from shared.config.settings import settings

    monkeypatch.setattr(settings, "async_transport", "aws")
    monkeypatch.setattr(settings, "aws_account_id", "123456789012")
    publisher = QueuePublisherFactory.get_async_publisher()
    assert isinstance(publisher, SNSPublisher)


def test_async_publisher_uses_redis_streams_when_transport_is_redis(monkeypatch) -> None:
    from shared.config.settings import settings

    monkeypatch.setattr(settings, "async_transport", "redis")
    publisher = QueuePublisherFactory.get_async_publisher()
    assert isinstance(publisher, RedisStreamPublisher)
