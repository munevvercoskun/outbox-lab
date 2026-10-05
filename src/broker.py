"""Two brokers behind one interface.

MemoryBroker  -- in process. Deterministic, no services to install, and it can
                 be told to misbehave on purpose. This is what the chaos
                 harness uses, so `make chaos` runs anywhere Postgres runs.

RabbitBroker  -- the real thing, used by the Docker Compose demo. Same methods,
                 so nothing above this file knows which one it has.

The behaviour that matters is the same in both, and it is the behaviour every
real broker has:

    a message handed to a consumer is NOT gone. It is "unacknowledged".
    If the consumer dies without acknowledging, it comes back.

That single rule is why duplicates are unavoidable, and why everything
downstream has to be safe to run twice.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field


@dataclass
class Message:
    tag: int
    event_id: str
    topic: str
    payload: dict
    deliveries: int = 0


class MemoryBroker:
    """An at-least-once broker you can break on demand."""

    def __init__(self, duplicate_every_message: bool = False) -> None:
        self.queue: list[Message] = []
        self.inflight: dict[int, Message] = {}
        self._next_tag = 1
        self.duplicate_every_message = duplicate_every_message
        # counters, for the report
        self.published = 0
        self.delivered = 0
        self.acked = 0

    # --- producer side ---------------------------------------------------

    def publish(self, topic: str, event_id: str, payload: dict) -> None:
        self.queue.append(Message(self._tag(), str(event_id), topic, payload))
        self.published += 1
        if self.duplicate_every_message:
            # Some brokers really do this under network partitions or when a
            # publisher retries a confirm it never received. Modelling it is
            # cheaper than arguing about whether it happens.
            self.queue.append(Message(self._tag(), str(event_id), topic, payload))
            self.published += 1

    def _tag(self) -> int:
        t = self._next_tag
        self._next_tag += 1
        return t

    # --- consumer side ---------------------------------------------------

    def take(self, topic: str) -> Message | None:
        """Hand one message to a consumer. It is now unacknowledged."""
        for i, m in enumerate(self.queue):
            if m.topic == topic:
                msg = self.queue.pop(i)
                msg.deliveries += 1
                self.inflight[msg.tag] = msg
                self.delivered += 1
                return msg
        return None

    def ack(self, tag: int) -> None:
        self.inflight.pop(tag, None)
        self.acked += 1

    def nack(self, tag: int, requeue: bool = True) -> None:
        msg = self.inflight.pop(tag, None)
        if msg is not None and requeue:
            self.queue.append(msg)

    # --- failure ---------------------------------------------------------

    def consumer_died(self) -> None:
        """Everything handed out but not acknowledged comes back.

        This is not a simulation of anything exotic. It is what every broker
        does when a consumer's connection drops.
        """
        for msg in self.inflight.values():
            self.queue.append(msg)
        self.inflight.clear()

    def pending(self, topic: str) -> int:
        return sum(1 for m in self.queue if m.topic == topic)


class RabbitBroker:
    """The same interface over RabbitMQ, for the Docker Compose demo.

    Deliberately thin. The lab's findings do not depend on which broker is
    underneath, and proving that is part of the point.
    """

    def __init__(self, url: str, queue: str = "order.created") -> None:
        import pika  # imported here so the memory path needs no dependency

        self.queue_name = queue
        self.conn = pika.BlockingConnection(pika.URLParameters(url))
        self.ch = self.conn.channel()
        self.ch.queue_declare(queue=queue, durable=True)
        self.ch.basic_qos(prefetch_count=1)
        self.published = self.delivered = self.acked = 0

    def publish(self, topic: str, event_id: str, payload: dict) -> None:
        import pika

        self.ch.basic_publish(
            exchange="",
            routing_key=self.queue_name,
            body=json.dumps({"event_id": str(event_id), "payload": payload}),
            properties=pika.BasicProperties(
                delivery_mode=2,          # persist to disk
                message_id=str(event_id),
            ),
        )
        self.published += 1

    def take(self, topic: str) -> Message | None:
        method, _props, body = self.ch.basic_get(self.queue_name, auto_ack=False)
        if method is None:
            return None
        data = json.loads(body)
        self.delivered += 1
        return Message(method.delivery_tag, data["event_id"], topic,
                       data["payload"])

    def ack(self, tag: int) -> None:
        self.ch.basic_ack(delivery_tag=tag)
        self.acked += 1

    def nack(self, tag: int, requeue: bool = True) -> None:
        self.ch.basic_nack(delivery_tag=tag, requeue=requeue)

    def consumer_died(self) -> None:
        """Close the channel without acking. RabbitMQ requeues on its own."""
        self.ch.close()
        self.ch = self.conn.channel()
        self.ch.basic_qos(prefetch_count=1)

    def pending(self, topic: str) -> int:
        q = self.ch.queue_declare(queue=self.queue_name, durable=True,
                                  passive=True)
        return q.method.message_count


def new_event_id() -> str:
    return str(uuid.uuid4())
