# Euphoria accounting invariants

The backfill remains blocked until each lifecycle can be reconciled deterministically.

For every economically meaningful lifecycle, the certifier must be able to establish identities equivalent to:

`gross_payout = principal_return + gross_profit_component`

`net_payout = gross_payout - protocol_fee - execution_costs - gas_attribution`

`net_pnl = net_payout - stake_or_principal`

At wallet/ledger scope:

`opening_equity + deposits - withdrawals + realized_pnl + unrealized_pnl - explicit_costs = closing_equity`

No field may be inferred from a balance delta when a receipt/log/state transition can provide a stronger source. Any unresolved mapping, missing receipt, unknown selector/event semantics, inconsistent asset amount, or upgrade-era ambiguity is blocking and contributes to `unresolved_count`.
