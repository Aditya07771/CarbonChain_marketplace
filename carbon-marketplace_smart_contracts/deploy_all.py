import os
import json
import base64
from dotenv import load_dotenv
from algosdk import mnemonic, account, transaction, abi
from algosdk.v2client import algod
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
    TransactionWithSigner,
)

load_dotenv()

# ── Connect to Testnet ─────────────────────────────────────────
ALGOD_ADDRESS = "https://testnet-api.algonode.cloud"
ALGOD_TOKEN   = ""
client = algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)

# ── Load wallet ────────────────────────────────────────────────
raw_mnemonic = os.environ["DEPLOYER_MNEMONIC"]
private_key  = mnemonic.to_private_key(raw_mnemonic)
address      = account.address_from_private_key(private_key)
signer       = AccountTransactionSigner(private_key)

print(f"Deployer : {address}")
info    = client.account_info(address)
balance = info["amount"] / 1_000_000
print(f"Balance  : {balance:.4f} ALGO")
print()

if balance < 1:
    print("Not enough ALGO! Go to https://bank.testnet.algorand.network/")
    exit()


# ── Helper: compile TEAL ───────────────────────────────────────
def compile_teal(path):
    with open(path) as f:
        result = client.compile(f.read())
    return base64.b64decode(result["result"])


# ── Helper: deploy one contract via ABI ───────────────────────
def deploy(name, approval_path, clear_path, arc56_path, global_schema, local_schema, method_name, method_args):
    print(f"Deploying {name}...")

    approval = compile_teal(approval_path)
    clear    = compile_teal(clear_path)

    # Load ABI from arc56 json
    with open(arc56_path) as f:
        arc56 = json.load(f)

    # Build ABI contract object
    abi_contract = abi.Contract.from_json(json.dumps({
        "name": arc56.get("name", name),
        "methods": arc56.get("methods", []),
    }))

    # Get the create method
    method = abi_contract.get_method_by_name(method_name)

    sp = client.suggested_params()

    # Create the app transaction
    create_txn = transaction.ApplicationCreateTxn(
        sender            = address,
        sp                = sp,
        on_complete       = transaction.OnComplete.NoOpOC,
        approval_program  = approval,
        clear_program     = clear,
        global_schema     = global_schema,
        local_schema      = local_schema,
        extra_pages       = 1,
    )

    # Use AtomicTransactionComposer for ABI method call
    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id          = 0,
        method          = method,
        sender          = address,
        sp              = sp,
        signer          = signer,
        method_args     = method_args,
        on_complete     = transaction.OnComplete.NoOpOC,
        approval_program = approval,
        clear_program   = clear,
        global_schema   = global_schema,
        local_schema    = local_schema,
        extra_pages     = 1,
    )

    result  = atc.execute(client, 4)
    tx_id   = result.tx_ids[0]

    confirmed = transaction.wait_for_confirmation(client, tx_id, 4)
    app_id    = confirmed["application-index"]

    print(f"✅  {name}")
    print(f"    App ID   : {app_id}")
    print(f"    Tx ID    : {tx_id}")
    print(f"    Explorer : https://testnet.explorer.perawallet.app/application/{app_id}/")
    print()
    return app_id


# ── Deploy Contract 1: CreditIssuanceRegistry ─────────────────
id1 = deploy(
    name          = "CreditIssuanceRegistry",
    approval_path = "smart_contracts/credit_issuance/CreditIssuanceRegistry.approval.teal",
    clear_path    = "smart_contracts/credit_issuance/CreditIssuanceRegistry.clear.teal",
    arc56_path    = "smart_contracts/credit_issuance/CreditIssuanceRegistry.arc56.json",
    global_schema = transaction.StateSchema(num_uints=2, num_byte_slices=1),
    local_schema  = transaction.StateSchema(num_uints=2, num_byte_slices=2),
    method_name   = "create_registry",
    method_args   = [],
)

# ── Deploy Contract 2: CarbonMarketplace ──────────────────────
id2 = deploy(
    name          = "CarbonMarketplace",
    approval_path = "smart_contracts/marketplace/CarbonMarketplace.approval.teal",
    clear_path    = "smart_contracts/marketplace/CarbonMarketplace.clear.teal",
    arc56_path    = "smart_contracts/marketplace/CarbonMarketplace.arc56.json",
    global_schema = transaction.StateSchema(num_uints=4, num_byte_slices=1),
    local_schema  = transaction.StateSchema(num_uints=0, num_byte_slices=0),
    method_name   = "create_marketplace",
    method_args   = [250],   # 250 bps = 2.5% fee
)

# ── Deploy Contract 3: RetirementRegistry ─────────────────────
id3 = deploy(
    name          = "RetirementRegistry",
    approval_path = "smart_contracts/retirement/RetirementRegistry.approval.teal",
    clear_path    = "smart_contracts/retirement/RetirementRegistry.clear.teal",
    arc56_path    = "smart_contracts/retirement/RetirementRegistry.arc56.json",
    global_schema = transaction.StateSchema(num_uints=3, num_byte_slices=1),
    local_schema  = transaction.StateSchema(num_uints=0, num_byte_slices=0),
    method_name   = "create_registry",
    method_args   = [],
)

# ── Save App IDs ───────────────────────────────────────────────
with open("app_ids.txt", "w") as f:
    f.write(f"CreditIssuanceRegistry : {id1}\n")
    f.write(f"CarbonMarketplace      : {id2}\n")
    f.write(f"RetirementRegistry     : {id3}\n")
    f.write(f"\nExplorer links:\n")
    f.write(f"https://testnet.explorer.perawallet.app/application/{id1}/\n")
    f.write(f"https://testnet.explorer.perawallet.app/application/{id2}/\n")
    f.write(f"https://testnet.explorer.perawallet.app/application/{id3}/\n")

print("=" * 50)
print("All 3 contracts deployed!")
print("App IDs saved to app_ids.txt")
print("Submit these App IDs to RIFT!")