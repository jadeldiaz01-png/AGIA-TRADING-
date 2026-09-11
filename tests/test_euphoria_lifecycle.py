from agia_trading.euphoria_data.lifecycle import derive_sample_lifecycle_pairs, enrich

KEY = "0x" + ("ab" * 32)
IN_EVENT = "0x9f039a0ca58d6157d7b6914e2d60cedacf65fea21a365e93d708a5e5c25454f3"
OUT_EVENT = "0x90cacdb710d0777a8e5fb6387c414be82e2ffe362a703077117a2a995fce12c1"


def test_pairs_same_raw_key_amount_and_later_redstone_leg() -> None:
    transactions = [
        {
            "tx_hash": "0xin",
            "block_number": 100,
            "selector": "0x93ca625d",
            "usdm_in_raw": 25,
            "unknown_events": [{"topic0": IN_EVENT, "indexed_topics": [KEY]}],
            "redstone": None,
        },
        {
            "tx_hash": "0xout",
            "block_number": 115,
            "selector": "0x21d5c9bb",
            "usdm_out_raw": 25,
            "unknown_events": [{"topic0": OUT_EVENT, "indexed_topics": [KEY]}],
            "redstone": {"marker_verified": True},
        },
    ]

    pairs = derive_sample_lifecycle_pairs(transactions)
    assert len(pairs) == 1
    assert pairs[0]["block_delta"] == 15
    assert pairs[0]["amounts_equal"] is True
    assert pairs[0]["economic_role_verified"] is False


def test_enrichment_replaces_sample_linkage_with_historical_completeness_blocker() -> None:
    evidence = {
        "transactions": [
            {
                "tx_hash": "0xin",
                "block_number": 100,
                "selector": "0x93ca625d",
                "usdm_in_raw": 25,
                "unknown_events": [{"topic0": IN_EVENT, "indexed_topics": [KEY]}],
                "redstone": None,
            },
            {
                "tx_hash": "0xout",
                "block_number": 115,
                "selector": "0x21d5c9bb",
                "usdm_out_raw": 25,
                "unknown_events": [{"topic0": OUT_EVENT, "indexed_topics": [KEY]}],
                "redstone": {"marker_verified": True},
            },
        ],
        "unresolved": ["settlement_lifecycle_linkage", "gross_payout_formula"],
    }

    enriched = enrich(evidence)
    assert enriched["sample_lifecycle_linkage_verified"] is True
    assert enriched["sample_lifecycle_block_deltas"] == [15]
    assert "settlement_lifecycle_linkage" not in enriched["unresolved"]
    assert "historical_lifecycle_linkage_completeness" in enriched["unresolved"]
    assert "gross_payout_formula" in enriched["unresolved"]
