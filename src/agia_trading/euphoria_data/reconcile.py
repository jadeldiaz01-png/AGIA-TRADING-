from __future__ import annotations

from collections import Counter

from .models import CanonicalEvent, RawChainRecord, ReconciliationIssue, ReconciliationReport


def reconcile(raw: list[RawChainRecord], canonical: list[CanonicalEvent]) -> ReconciliationReport:
    issues: list[ReconciliationIssue] = []

    tx_log_keys = [(r.tx_hash, r.log_index) for r in raw if r.log_index is not None]
    duplicates = [key for key, n in Counter(tx_log_keys).items() if n > 1]
    for tx_hash, log_index in duplicates:
        issues.append(ReconciliationIssue(
            code="DUPLICATE_LOG_KEY",
            key=f"{tx_hash}:{log_index}",
            detail="same tx_hash/log_index observed more than once",
        ))

    for r in raw:
        if r.status is None:
            issues.append(ReconciliationIssue(
                code="MISSING_RECEIPT_STATUS",
                key=r.tx_hash,
                detail="receipt status is required before certification",
            ))
        if not r.evm_block_hash or not r.tx_hash:
            issues.append(ReconciliationIssue(
                code="MISSING_CHAIN_IDENTITY",
                key=r.tx_hash or "UNKNOWN",
                detail="block hash and transaction hash are mandatory",
            ))

    canonical_keys = {(e.tx_hash, e.log_index) for e in canonical}
    raw_keys = {(r.tx_hash, r.log_index) for r in raw if r.log_index is not None}
    for key in sorted(raw_keys - canonical_keys):
        issues.append(ReconciliationIssue(
            code="UNMAPPED_REQUIRED_LOG",
            key=f"{key[0]}:{key[1]}",
            detail="raw log has no canonical representation",
        ))

    for event in canonical:
        if event.semantic_confidence != "VERIFIED":
            issues.append(ReconciliationIssue(
                code="UNVERIFIED_SEMANTICS",
                key=f"{event.tx_hash}:{event.log_index}",
                detail="event semantics are not VERIFIED",
            ))
        if not event.abi_sha256 or not event.bytecode_sha256:
            issues.append(ReconciliationIssue(
                code="MISSING_CONTRACT_PROVENANCE",
                key=f"{event.tx_hash}:{event.log_index}",
                detail="ABI and bytecode hashes are required for verified semantics",
            ))

    blocking = [issue for issue in issues if issue.blocking]
    return ReconciliationReport(
        total_raw_records=len(raw),
        total_canonical_events=len(canonical),
        issues=issues,
        unresolved_count=len(blocking),
    )
