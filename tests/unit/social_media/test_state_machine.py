from src.social_media.publishing.state_machine import can_transition


def test_can_transition_allows_manual_confirmation():
    assert can_transition("ready_for_manual_publish", "manually_confirmed") is True


def test_can_transition_rejects_published_to_queued():
    assert can_transition("published", "queued") is False
