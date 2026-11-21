# VPS deployment state

The deploy workflow installs files, creates an isolated service account and root-owned environment file, installs the systemd unit, and runs compile checks. It deliberately does not enable or start the unit. Trading remains disabled, the signer key remains unset, and the emergency kill switch is created. Live operation requires verified chain/RPC/factory/DEX ABI and router allowlists plus a reviewed chain-specific execution adapter.
