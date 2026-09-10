from agia_trading.euphoria_data.adjudicate_semantics import (
    EVENT_TOPICS,
    FUNCTION_SELECTORS,
)


def test_target_selectors_are_exact() -> None:
    assert FUNCTION_SELECTORS == ["0x21d5c9bb", "0x93ca625d"]


def test_target_event_topics_are_exact() -> None:
    assert len(EVENT_TOPICS) == 3
    assert all(topic.startswith("0x") and len(topic) == 66 for topic in EVENT_TOPICS)
