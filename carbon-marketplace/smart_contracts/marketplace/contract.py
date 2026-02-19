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
    gtxn,
)


class CarbonMarketplace(ARC4Contract):
    """
    Contract 2 — Carbon Credit Marketplace (with Expiry Enforcement)

    New expiry features:
    - Expiry timestamp stored in every listing
    - buy_credit() rejects expired credits on-chain
    - get_listing() returns expiry so frontend can show countdown
    - Expired listings can be cleaned up by anyone
    """

    def __init__(self) -> None:
        self.admin                  = GlobalState(Account)
        self.platform_fee_bps       = GlobalState(UInt64)
        self.total_volume_microalgo = GlobalState(UInt64)
        self.total_trades           = GlobalState(UInt64)

        # Business local state
        self.business_verified     = LocalState(UInt64)
        self.business_name         = LocalState(Bytes)
        self.business_country      = LocalState(Bytes)
        self.total_credits_bought  = LocalState(UInt64)


    # ─────────────────────────────────────────
    #  DEPLOY
    # ─────────────────────────────────────────

    @arc4.abimethod(allow_actions=["NoOp"], create="require")
    def create_marketplace(self, fee_bps: arc4.UInt64) -> None:
        """Deploy marketplace. fee_bps: 250 = 2.5%"""
        self.admin.value                  = Txn.sender
        self.platform_fee_bps.value       = fee_bps.native
        self.total_volume_microalgo.value = UInt64(0)
        self.total_trades.value           = UInt64(0)


    # ─────────────────────────────────────────
    #  BUSINESS REGISTRATION
    # ─────────────────────────────────────────

    @arc4.abimethod
    def register_business(
        self,
        name:    arc4.String,
        country: arc4.String,
    ) -> None:
        """Company registers to buy credits. Starts pending."""
        self.business_name[Txn.sender]          = name.bytes
        self.business_country[Txn.sender]        = country.bytes
        self.business_verified[Txn.sender]       = UInt64(0)
        self.total_credits_bought[Txn.sender]    = UInt64(0)


    @arc4.abimethod
    def verify_business(self, business: arc4.Address) -> None:
        """Admin approves a business."""
        assert Txn.sender == self.admin.value, "Admin only"
        self.business_verified[business.native] = UInt64(1)


    @arc4.abimethod
    def reject_business(self, business: arc4.Address) -> None:
        """Admin rejects a business."""
        assert Txn.sender == self.admin.value, "Admin only"
        self.business_verified[business.native] = UInt64(2)


    # ─────────────────────────────────────────
    #  LIST CREDIT FOR SALE
    # ─────────────────────────────────────────

    @arc4.abimethod
    def list_credit(
        self,
        asset_id:              arc4.UInt64,
        price_microalgo:       arc4.UInt64,
        co2_tonnes:            arc4.UInt64,
        vintage_year:          arc4.UInt64,
        project_type:          arc4.String,
        verification_standard: arc4.String,
        min_purchase_qty:      arc4.UInt64,
        ipfs_metadata_hash:    arc4.String,
        expiry_timestamp:      arc4.UInt64,   # ← NEW: Unix expiry from issuance contract
    ) -> None:
        """
        NGO lists a carbon credit NFT for sale.

        expiry_timestamp: pass in the value from get_credit_expiry()
        Contract stores it so buy_credit() can enforce it on-chain.

        Call as atomic group:
            [0] AssetTransfer — seller sends NFT to contract
            [1] AppCall       — this method

        Box layout (total 96 bytes):
        asset_id(8) | seller(32) | price(8) | co2(8) |
        vintage(8)  | min_qty(8) | timestamp(8) | expiry(8) | active(8)
        """
        assert price_microalgo.native > UInt64(0),                    "Price must be > 0"
        assert min_purchase_qty.native > UInt64(0),                   "Min qty must be > 0"
        assert min_purchase_qty.native <= co2_tonnes.native,          "Min qty exceeds total"
        assert Txn.group_index > UInt64(0),                           "Must be in atomic group"

        # ── Check credit is not already expired ───────────────────
        assert Global.latest_timestamp < expiry_timestamp.native, "Cannot list an expired credit"

        # ── Verify NFT was sent to contract ───────────────────────
        prev = gtxn.AssetTransferTransaction(Txn.group_index - UInt64(1))
        assert prev.asset_receiver == Global.current_application_address, "NFT must go to contract"
        assert prev.xfer_asset.id  == asset_id.native,                    "Wrong asset ID"
        assert prev.asset_amount   == UInt64(1),                          "Must send exactly 1"
        assert prev.sender         == Txn.sender,                         "Sender mismatch"

        # Box layout:
        # offset 0  — asset_id        8 bytes
        # offset 8  — seller          32 bytes
        # offset 40 — price           8 bytes
        # offset 48 — co2_tonnes      8 bytes
        # offset 56 — vintage_year    8 bytes
        # offset 64 — min_purchase    8 bytes
        # offset 72 — listed_at       8 bytes
        # offset 80 — expiry          8 bytes  ← NEW
        # offset 88 — active          8 bytes
        # Total: 96 bytes
        op.Box.put(
            op.itob(asset_id.native),
            op.itob(asset_id.native)          +   # 0:8
            Txn.sender.bytes                  +   # 8:40
            op.itob(price_microalgo.native)   +   # 40:48
            op.itob(co2_tonnes.native)        +   # 48:56
            op.itob(vintage_year.native)      +   # 56:64
            op.itob(min_purchase_qty.native)  +   # 64:72
            op.itob(Global.latest_timestamp)  +   # 72:80
            op.itob(expiry_timestamp.native)  +   # 80:88  ← expiry
            op.itob(UInt64(1)),                   # 88:96  active=1
        )


    # ─────────────────────────────────────────
    #  BUY CREDIT (enforces expiry on-chain)
    # ─────────────────────────────────────────

    @arc4.abimethod
    def buy_credit(self, asset_id: arc4.UInt64) -> None:
        """
        Verified business buys a listed carbon credit.

        ✅ Checks:
        - Buyer is a verified business
        - Listing is active
        - Credit has NOT expired (enforced on-chain by blockchain time)
        - Payment matches listing price

        Call as atomic group:
            [0] Payment  — buyer pays exact price to contract
            [1] AppCall  — this method
        """
        assert Txn.group_index > UInt64(0),                              "Must be in atomic group"
        assert self.business_verified[Txn.sender] == UInt64(1),          "Business not verified"

        # Load listing
        box_value, box_exists = op.Box.get(op.itob(asset_id.native))
        assert box_exists, "Listing not found"

        seller          = Account(op.extract(box_value, 8,  32))
        price           = op.btoi(op.extract(box_value, 40, 8))
        expiry          = op.btoi(op.extract(box_value, 80, 8))   # ← NEW
        active          = op.btoi(op.extract(box_value, 88, 8))

        assert active == UInt64(1), "Listing is not active"

        # ── EXPIRY CHECK (enforced on-chain) ──────────────────────
        # Global.latest_timestamp = current block time (cannot be faked)
        assert Global.latest_timestamp < expiry, "This carbon credit has expired and cannot be sold"

        # Verify payment
        pay = gtxn.PaymentTransaction(Txn.group_index - UInt64(1))
        assert pay.sender   == Txn.sender,                           "Payment sender mismatch"
        assert pay.receiver == Global.current_application_address,   "Wrong receiver"
        assert pay.amount   == price,                                "Wrong payment amount"

        # Fee split
        platform_fee  = (price * self.platform_fee_bps.value) // UInt64(10000)
        seller_payout = price - platform_fee

        # Pay seller
        itxn.Payment(
            receiver = seller,
            amount   = seller_payout,
            fee      = Global.min_txn_fee,
        ).submit()

        # Pay platform fee
        if platform_fee > UInt64(0):
            itxn.Payment(
                receiver = self.admin.value,
                amount   = platform_fee,
                fee      = Global.min_txn_fee,
            ).submit()

        # Transfer NFT to buyer
        itxn.AssetTransfer(
            xfer_asset     = Asset(asset_id.native),
            asset_receiver = Txn.sender,
            asset_amount   = 1,
            fee            = Global.min_txn_fee,
        ).submit()

        # Mark listing as sold (active = 0)
        op.Box.put(
            op.itob(asset_id.native),
            op.extract(box_value, 0, 88) + op.itob(UInt64(0)),
        )

        self.total_credits_bought[Txn.sender]     = self.total_credits_bought[Txn.sender] + UInt64(1)
        self.total_volume_microalgo.value          = self.total_volume_microalgo.value + price
        self.total_trades.value                    = self.total_trades.value + UInt64(1)


    # ─────────────────────────────────────────
    #  CANCEL LISTING
    # ─────────────────────────────────────────

    @arc4.abimethod
    def cancel_listing(self, asset_id: arc4.UInt64) -> None:
        """Seller cancels listing and gets NFT back."""
        box_value, box_exists = op.Box.get(op.itob(asset_id.native))
        assert box_exists, "Listing not found"

        seller = Account(op.extract(box_value, 8,  32))
        active = op.btoi(op.extract(box_value, 88, 8))

        assert Txn.sender == seller, "Only seller can cancel"
        assert active == UInt64(1),  "Listing not active"

        itxn.AssetTransfer(
            xfer_asset     = Asset(asset_id.native),
            asset_receiver = seller,
            asset_amount   = 1,
            fee            = Global.min_txn_fee,
        ).submit()

        op.Box.put(
            op.itob(asset_id.native),
            op.extract(box_value, 0, 88) + op.itob(UInt64(0)),
        )


    # ─────────────────────────────────────────
    #  READ ONLY
    # ─────────────────────────────────────────

    @arc4.abimethod(readonly=True)
    def get_listing(
        self,
        asset_id: arc4.UInt64,
    ) -> tuple[arc4.Address, arc4.UInt64, arc4.UInt64, arc4.UInt64, arc4.UInt64, arc4.UInt64]:
        """
        Get full listing details.
        Returns: (seller, price_microalgo, co2_tonnes, min_purchase_qty, expiry_timestamp, active)

        Frontend: use expiry_timestamp to show countdown timer
        If current time > expiry_timestamp → show EXPIRED badge
        """
        box_value, box_exists = op.Box.get(op.itob(asset_id.native))
        assert box_exists, "Listing not found"

        return (
            arc4.Address(op.extract(box_value, 8,  32)),
            arc4.UInt64(op.btoi(op.extract(box_value, 40, 8))),
            arc4.UInt64(op.btoi(op.extract(box_value, 48, 8))),
            arc4.UInt64(op.btoi(op.extract(box_value, 64, 8))),
            arc4.UInt64(op.btoi(op.extract(box_value, 80, 8))),
            arc4.UInt64(op.btoi(op.extract(box_value, 88, 8))),
        )


    @arc4.abimethod(readonly=True)
    def is_listing_expired(self, asset_id: arc4.UInt64) -> arc4.Bool:
        """
        Check if a listing has expired.
        Returns True if expired, False if still valid.
        """
        box_value, box_exists = op.Box.get(op.itob(asset_id.native))
        assert box_exists, "Listing not found"

        expiry = op.btoi(op.extract(box_value, 80, 8))
        return arc4.Bool(Global.latest_timestamp > expiry)


    @arc4.abimethod(readonly=True)
    def get_business_status(
        self,
        business: arc4.Address,
    ) -> tuple[arc4.UInt64, arc4.UInt64]:
        """Returns (verified_status, total_credits_bought). Status: 0=pending, 1=verified, 2=rejected"""
        return (
            arc4.UInt64(self.business_verified[business.native]),
            arc4.UInt64(self.total_credits_bought[business.native]),
        )


    @arc4.abimethod(readonly=True)
    def get_stats(self) -> tuple[arc4.UInt64, arc4.UInt64]:
        """Returns (total_volume_algo, total_trades)."""
        return (
            arc4.UInt64(self.total_volume_microalgo.value // UInt64(1_000_000)),
            arc4.UInt64(self.total_trades.value),
        )