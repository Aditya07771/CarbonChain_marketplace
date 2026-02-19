from algopy import (
    ARC4Contract,
    Asset,
    GlobalState,
    LocalState,
    UInt64,
    Bytes,
    Account,
    Txn,
    Global,
    arc4,
    itxn,
    op,
)


class CreditIssuanceRegistry(ARC4Contract):
    """
    Contract 1 — Credit Issuance Registry (with Expiry Dates)

    Carbon credits now have expiry dates.
    - Standard credits expire 5 years after vintage year
    - NGO can set custom expiry (max 10 years)
    - Expired credits cannot be listed or sold
    - Anyone can check if a credit is expired
    """

    def __init__(self) -> None:
        self.admin                = GlobalState(Account)
        self.total_credits_issued = GlobalState(UInt64)

        self.issuer_verified      = LocalState(UInt64)
        self.issuer_credits       = LocalState(UInt64)
        self.issuer_name          = LocalState(Bytes)
        self.issuer_standard      = LocalState(Bytes)


    # ─────────────────────────────────────────
    #  DEPLOY
    # ─────────────────────────────────────────

    @arc4.abimethod(allow_actions=["NoOp"], create="require")
    def create_registry(self) -> None:
        """Deploy the registry. Caller becomes admin."""
        self.admin.value                = Txn.sender
        self.total_credits_issued.value = UInt64(0)


    # ─────────────────────────────────────────
    #  NGO REGISTRATION
    # ─────────────────────────────────────────

    @arc4.abimethod
    def register_issuer(
        self,
        name:                  arc4.String,
        country:               arc4.String,
        verification_standard: arc4.String,
    ) -> None:
        """NGO registers. Starts as unverified until admin approves."""
        self.issuer_name[Txn.sender]     = name.bytes
        self.issuer_standard[Txn.sender] = verification_standard.bytes
        self.issuer_verified[Txn.sender] = UInt64(0)
        self.issuer_credits[Txn.sender]  = UInt64(0)


    @arc4.abimethod
    def verify_issuer(self, issuer: arc4.Address) -> None:
        """Admin approves an NGO."""
        assert Txn.sender == self.admin.value, "Admin only"
        self.issuer_verified[issuer.native] = UInt64(1)


    # ─────────────────────────────────────────
    #  MINT CARBON CREDIT NFT (with expiry)
    # ─────────────────────────────────────────

    @arc4.abimethod
    def mint_carbon_credit(
        self,
        project_id:      arc4.String,
        project_name:    arc4.String,
        location:        arc4.String,
        co2_tonnes:      arc4.UInt64,
        vintage_year:    arc4.UInt64,
        project_type:    arc4.String,
        ipfs_hash:       arc4.String,
        years_valid:     arc4.UInt64,   # ← NEW: how many years before expiry (1-10)
    ) -> arc4.UInt64:
        """
        Verified NGO mints a carbon credit NFT with an expiry date.

        years_valid: how long the credit is valid for (1 to 10 years)
        Expiry is stored as a Unix timestamp on-chain.

        Example:
          vintage_year = 2024, years_valid = 5
          → credit expires on Jan 1, 2029 (Unix: 1861920000)

        Returns: ASA ID of the new NFT
        """
        assert self.issuer_verified[Txn.sender] == UInt64(1), "Issuer not verified"
        assert co2_tonnes.native > UInt64(0),                  "Must represent CO2"
        assert vintage_year.native >= UInt64(2000),            "Invalid vintage year"
        assert years_valid.native >= UInt64(1),                "Min 1 year validity"
        assert years_valid.native <= UInt64(10),               "Max 10 years validity"

        # Reject duplicate project IDs
        box_value, box_exists = op.Box.get(project_id.bytes)
        assert not box_exists, "Project ID already exists"

        # Calculate expiry timestamp
        # Unix timestamp for Jan 1 of (vintage_year + years_valid)
        # 1 year ≈ 31,536,000 seconds
        # Base: Jan 1 2000 = 946684800
        SECONDS_PER_YEAR = UInt64(31_536_000)
        BASE_2000_UNIX   = UInt64(946_684_800)

        years_since_2000  = vintage_year.native - UInt64(2000)
        vintage_timestamp = BASE_2000_UNIX + (years_since_2000 * SECONDS_PER_YEAR)
        expiry_timestamp  = vintage_timestamp + (years_valid.native * SECONDS_PER_YEAR)

        # Create the NFT
        asset_txn = itxn.AssetConfig(
            total          = 1,
            decimals       = 0,
            unit_name      = b"CCT",
            asset_name     = project_name.bytes,
            url            = b"ipfs://" + ipfs_hash.bytes,
            manager        = Global.current_application_address,
            reserve        = Txn.sender,
            freeze         = Global.current_application_address,
            clawback       = Global.current_application_address,
            default_frozen = False,
            fee            = Global.min_txn_fee,
        ).submit()

        asset_id = asset_txn.created_asset.id

        # Store metadata in box
        # Layout: asset_id(8) | co2(8) | vintage(8) | mint_time(8) | expiry(8)
        # Total: 40 bytes
        op.Box.put(
            project_id.bytes,
            op.itob(asset_id)                 +   # offset 0  — 8 bytes
            op.itob(co2_tonnes.native)         +   # offset 8  — 8 bytes
            op.itob(vintage_year.native)       +   # offset 16 — 8 bytes
            op.itob(Global.latest_timestamp)   +   # offset 24 — 8 bytes (mint time)
            op.itob(expiry_timestamp),             # offset 32 — 8 bytes (expiry ← NEW)
        )

        self.issuer_credits[Txn.sender]  = self.issuer_credits[Txn.sender] + UInt64(1)
        self.total_credits_issued.value  = self.total_credits_issued.value  + UInt64(1)

        return arc4.UInt64(asset_id)


    # ─────────────────────────────────────────
    #  CHECK EXPIRY (NEW)
    # ─────────────────────────────────────────

    @arc4.abimethod(readonly=True)
    def is_credit_expired(self, project_id: arc4.String) -> arc4.Bool:
        """
        Check if a carbon credit has expired.
        Returns True if expired, False if still valid.

        Compares expiry timestamp against current blockchain time.
        This is the source of truth — blockchain time cannot be faked.
        """
        box_value, box_exists = op.Box.get(project_id.bytes)
        assert box_exists, "Project not found"

        expiry_timestamp = op.btoi(op.extract(box_value, 32, 8))

        # Compare expiry against current blockchain timestamp
        return arc4.Bool(Global.latest_timestamp > expiry_timestamp)


    @arc4.abimethod(readonly=True)
    def get_credit_expiry(self, project_id: arc4.String) -> arc4.UInt64:
        """
        Returns the expiry Unix timestamp of a credit.
        Frontend can convert this to a human-readable date.
        """
        box_value, box_exists = op.Box.get(project_id.bytes)
        assert box_exists, "Project not found"
        return arc4.UInt64(op.btoi(op.extract(box_value, 32, 8)))


    @arc4.abimethod(readonly=True)
    def get_credit_asset_id(self, project_id: arc4.String) -> arc4.UInt64:
        """Returns the ASA ID for a given project ID."""
        box_value, box_exists = op.Box.get(project_id.bytes)
        assert box_exists, "Project not found"
        return arc4.UInt64(op.btoi(op.extract(box_value, 0, 8)))


    @arc4.abimethod(readonly=True)
    def get_issuer_stats(
        self,
        issuer: arc4.Address,
    ) -> tuple[arc4.UInt64, arc4.UInt64]:
        """Returns (is_verified, credits_issued)."""
        return (
            arc4.UInt64(self.issuer_verified[issuer.native]),
            arc4.UInt64(self.issuer_credits[issuer.native]),
        )


    @arc4.abimethod(readonly=True)
    def get_total_issued(self) -> arc4.UInt64:
        """Returns total credits ever minted."""
        return arc4.UInt64(self.total_credits_issued.value)