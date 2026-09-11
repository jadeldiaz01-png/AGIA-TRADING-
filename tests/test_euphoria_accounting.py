from eth_hash.auto import keccak

from agia_trading.euphoria_data.accounting import (
    BALANCE_UPDATE_TOPIC,
    PROXY,
    REDSTONE_MARKER,
    TRANSFER_TOPIC,
    USDM,
    parse_receipt_logs,
    parse_redstone_payload,
)


def _topic_address(address: str) -> str:
    return "0x" + ("00" * 12) + address.removeprefix("0x")


def test_parse_redstone_payload_separates_business_calldata() -> None:
    business = bytes.fromhex("21d5c9bb") + (b"\x11" * 32)
    feed = b"ETH" + (b"\x00" * 29)
    value = (243_850_000_000).to_bytes(8, "big")
    timestamp = (1_789_043_885_000).to_bytes(6, "big")
    value_size = (8).to_bytes(4, "big")
    points_count = (1).to_bytes(3, "big")
    signature = b"\x22" * 65
    package = feed + value + timestamp + value_size + points_count + signature
    payload = package + (1).to_bytes(2, "big") + (0).to_bytes(3, "big") + REDSTONE_MARKER

    parsed = parse_redstone_payload("0x" + (business + payload).hex())

    assert parsed is not None
    assert parsed["business_calldata"] == "0x" + business.hex()
    assert parsed["package_count"] == 1
    assert parsed["timestamps_equal"] is True
    assert parsed["packages"][0]["timestamp_ms"] == 1_789_043_885_000
    assert parsed["packages"][0]["data_points"][0]["feed_id"] == "ETH"
    assert parsed["packages"][0]["data_points"][0]["value_raw"] == 243_850_000_000
    assert parsed["packages"][0]["signature_bytes"] == 65
    signed = feed + value + timestamp + value_size + points_count
    assert parsed["packages"][0]["signed_message_keccak256"] == "0x" + keccak(signed).hex()


def test_parse_receipt_logs_reconciles_usdm_cashflow_without_calling_it_pnl() -> None:
    wallet = "0x1111111111111111111111111111111111111111"
    beneficiary = "0x2222222222222222222222222222222222222222"
    logs = [
        {
            "address": USDM,
            "topics": [TRANSFER_TOPIC, _topic_address(wallet), _topic_address(PROXY)],
            "data": "0x" + (100).to_bytes(32, "big").hex(),
        },
        {
            "address": USDM,
            "topics": [TRANSFER_TOPIC, _topic_address(PROXY), _topic_address(beneficiary)],
            "data": "0x" + (140).to_bytes(32, "big").hex(),
        },
        {
            "address": PROXY,
            "topics": [BALANCE_UPDATE_TOPIC, _topic_address(wallet)],
            "data": "0x" + (900).to_bytes(32, "big").hex(),
        },
    ]

    parsed = parse_receipt_logs(logs)

    assert parsed["usdm_in_raw"] == 100
    assert parsed["usdm_out_raw"] == 140
    assert parsed["proxy_net_inflow_raw"] == -40
    assert parsed["balance_updates"][0]["account"] == wallet
    assert parsed["balance_updates"][0]["economic_role_verified"] is False
