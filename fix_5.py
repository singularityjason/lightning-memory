# tests/test_nostr_relay_sync.py
import pytest
from lightning_memory.nostr import NostrRelay, NostrEvent
from lightning_memory.sync import NostrSync

@pytest.fixture
def mock_relay():
    class MockRelay(NostrRelay):
        def __init__(self):
            self.published_events = []

        def publish(self, event):
            self.published_events.append(event)

        def subscribe(self, filter):
            # For simplicity, return all published events
            return self.published_events

    return MockRelay()

def test_publish_event(mock_relay):
    nostr_sync = NostrSync(relay=mock_relay)
    event = NostrEvent(content="Test event", kind=1)
    nostr_sync.publish(event)
    assert len(mock_relay.published_events) == 1
    assert mock_relay.published_events[0] == event

def test_bidirectional_sync(mock_relay):
    nostr_sync = NostrSync(relay=mock_relay)
    event1 = NostrEvent(content="First event", kind=1)
    event2 = NostrEvent(content="Second event", kind=1)
    nostr_sync.publish(event1)
    nostr_sync.publish(event2)
    subscribed_events = nostr_sync.subscribe()
    assert len(subscribed_events) == 2
    assert subscribed_events[0] == event1
    assert subscribed_events[1] == event2