import logging
from algokit_utils import (
    AlgorandClient,
    AlgoAmount,
)

logger = logging.getLogger(__name__)


def deploy() -> None:
    algorand = AlgorandClient.testnet()
    deployer = algorand.account.from_environment("DEPLOYER")

    from smart_contracts.artifacts.retirement.retirement_registry_client import (
        RetirementRegistryFactory,
    )

    factory = RetirementRegistryFactory(
        algorand,
        default_sender=deployer.address,
        default_signer=deployer.signer,
    )

    result, is_new = factory.deploy(
        on_schema_break="replace",
        on_update="update",
        create_args={"args": ()},  # create_registry takes no args
    )

    app_id = result.app_id
    app_address = result.app_address

    logger.info(f"✅ RetirementRegistry deployed!")
    logger.info(f"   App ID      : {app_id}")
    logger.info(f"   App Address : {app_address}")
    logger.info(f"   Explorer    : https://testnet.explorer.perawallet.app/application/{app_id}/")

    # Fund for burn transactions
    algorand.send.payment(
        sender=deployer.address,
        signer=deployer.signer,
        receiver=app_address,
        amount=AlgoAmount.from_algo(1),
    )
    logger.info("   Funded with 1 ALGO for burn transactions")