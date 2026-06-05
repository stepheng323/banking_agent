from shared.queue.factory import CompositeQueuePublisher, QueuePublisherFactory
from shared.queue.redis_stream_publisher import RedisStreamPublisher


def test_async_publisher_uses_redis_streams(monkeypatch) -> None:
    monkeypatch.setattr("shared.queue.redis_stream_publisher.RedisClient.get_client", lambda: object())
    publisher = QueuePublisherFactory.get_async_publisher()
    assert isinstance(publisher, RedisStreamPublisher)


def test_composite_publisher_uses_redis_for_chat_and_async(monkeypatch) -> None:
    monkeypatch.setattr("shared.queue.redis_stream_publisher.RedisClient.get_client", lambda: object())
    publisher = QueuePublisherFactory.get_publisher()
    assert isinstance(publisher, CompositeQueuePublisher)
    assert isinstance(publisher.chat_publisher, RedisStreamPublisher)
    assert isinstance(publisher.async_publisher, RedisStreamPublisher)
