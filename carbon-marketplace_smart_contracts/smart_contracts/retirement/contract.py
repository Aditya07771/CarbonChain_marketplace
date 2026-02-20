from algopy import (
    ARC4Contract,
    Asset,
    GlobalState,
    UInt64,
    Account,
    Txn,
    Global,
    arc4,
    itxn,
    op,
)


class RetirementRegistry(ARC4Contract):
    """
    Contract 3 — Retirement Registry
    Companies permanently burn carbon credits as proof of offset.
    Every retirement is recorded forever on-chain — cannot be undone.
    Anyone can verify a company's offset claims publicly.
    """

    def __init__(self) -> None:
        self.admin                = GlobalState(Account)
        self.total_tonnes_retired = GlobalState(UInt64)
        self.total_retirements    = GlobalState(UInt64)


    @arc4.abimethod(allow_actions=["NoOp"], create="require")
    def create_registry(self) -> None:
        """Deploy the retirement registry. Caller becomes admin."""
        self.admin.value                = Txn.sender
        self.total_tonnes_retired.value = UInt64(0)
        self.total_retirements.value    = UInt64(0)


    @arc4.abimethod
    def retire_credit(
        self,
        asset_id:         arc4.UInt64,
        company_name:     arc4.String,
        co2_tonnes:       arc4.UInt64,
        ipfs_certificate: arc4.String,
    ) -> arc4.UInt64:
        """
        Permanently retire (burn) a carbon credit NFT.

        Steps:
        1. Clawback NFT from the company's wallet → contract
        2. Destroy the ASA forever (cannot be reversed)
        3. Write retirement certificate to box storage

        Returns: retirement timestamp (use as certificate reference ID)
        """
        assert co2_tonnes.native > UInt64(0), "Invalid tonnes"

        retirement_time = Global.latest_timestamp

        # Step 1 — Clawback NFT from company wallet back to contract
        itxn.AssetTransfer(
            xfer_asset     = Asset(asset_id.native),
            asset_sender   = Txn.sender,
            asset_receiver = Global.current_application_address,
            asset_amount   = 1,
            fee            = Global.min_txn_fee,
        ).submit()

        # Step 2 — Destroy the ASA permanently
        # Calling AssetConfig with no fields = destroy
        itxn.AssetConfig(
            config_asset = Asset(asset_id.native),
            fee          = Global.min_txn_fee,
        ).submit()

        # Step 3 — Write retirement record to box storage
        # Box key   = asset_id (8 bytes) — unique per credit
        # Box value = asset_id(8) | company_address(32) | co2_tonnes(8) | timestamp(8) | txn_id(32)
        # Total     = 88 bytes
        op.Box.put(
            op.itob(asset_id.native),
            op.itob(asset_id.native)   +   # offset 0  — 8 bytes
            Txn.sender.bytes           +   # offset 8  — 32 bytes  (company wallet)
            op.itob(co2_tonnes.native) +   # offset 40 — 8 bytes
            op.itob(retirement_time)   +   # offset 48 — 8 bytes
            Txn.tx_id,                     # offset 56 — 32 bytes  (transaction proof)
        )

        # Update global stats
        self.total_tonnes_retired.value = (
            self.total_tonnes_retired.value + co2_tonnes.native
        )
        self.total_retirements.value = self.total_retirements.value + UInt64(1)

        return arc4.UInt64(retirement_time)


    @arc4.abimethod(readonly=True)
    def verify_retirement(
        self,
        asset_id: arc4.UInt64,
    ) -> tuple[arc4.Address, arc4.UInt64, arc4.UInt64]:
        """
        Publicly verify a retirement by asset ID.
        Regulators and investors can call this to check offset claims.

        Returns: (company_address, co2_tonnes, retirement_date_unix)
        """
        box_value, box_exists = op.Box.get(op.itob(asset_id.native))
        assert box_exists, "Retirement certificate not found"

        return (
            arc4.Address(op.extract(box_value, 8,  32)),
            arc4.UInt64(op.btoi(op.extract(box_value, 40, 8))),
            arc4.UInt64(op.btoi(op.extract(box_value, 48, 8))),
        )


    @arc4.abimethod(readonly=True)
    def get_global_stats(self) -> tuple[arc4.UInt64, arc4.UInt64]:
        """Returns (total_tonnes_retired, total_retirements)."""
        return (
            arc4.UInt64(self.total_tonnes_retired.value),
            arc4.UInt64(self.total_retirements.value),
        )