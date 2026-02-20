import logging
from algokit_utils import (
    AlgorandClient,
    AlgoAmount,
)

logger = logging.getLogger(__name__)


def deploy() -> None:
    algorand = AlgorandClient.testnet()
    deployer = algorand.account.from_environment("DEPLOYER")

    from smart_contracts.artifacts.marketplace.carbon_marketplace_client import (
        CarbonMarketplaceFactory,
    )

    factory = CarbonMarketplaceFactory(
        algorand,
        default_sender=deployer.address,
        default_signer=deployer.signer,
    )

    # 2.5% platform fee (250 basis points)
    PLATFORM_FEE_BPS = 250

    result, is_new = factory.deploy(
        on_schema_break="replace",
        on_update="update",
        create_args={"args": (PLATFORM_FEE_BPS,)},
    )

    app_id = result.app_id
    app_address = result.app_address

    logger.info(f"✅ CarbonMarketplace deployed!")
    logger.info(f"   App ID      : {app_id}")
    logger.info(f"   App Address : {app_address}")
    logger.info(f"   Fee         : {PLATFORM_FEE_BPS} bps (2.5%)")
    logger.info(f"   Explorer    : https://testnet.explorer.perawallet.app/application/{app_id}/")

    # Fund the contract for inner transactions
    algorand.send.payment(
        sender=deployer.address,
        signer=deployer.signer,
        receiver=app_address,
        amount=AlgoAmount.from_algo(2),  # marketplace needs more for swaps
    )
    logger.info("   Funded with 2 ALGO for inner transactions")