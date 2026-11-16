"""Record canonical identity and catalog decisions from the frozen Python Oracle."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.cli_config import _tool_contract
from nsl.ir import canonical_json
from nsl.tools import PostWriteVerification, ToolContractCatalog


ROOT = Path(sys.argv[2]).resolve()
READ = "compatibility/v0_2_1/contracts/read-tool-contract"


def load(path, tool_id):
    document = json.loads((ROOT / path).read_bytes())
    items = document if isinstance(document, list) else document["tools"]
    contracts = (entry.get("contract", entry) for entry in items)
    item, = (contract for contract in contracts if contract["tool_id"] == tool_id)
    return _tool_contract(item, "contract")


def emit(label, contract):
    canonical = canonical_json(contract.to_canonical_data())
    assert contract.contract_hash == "sha256:" + sha256(canonical).hexdigest()
    reloaded = _tool_contract(json.loads(canonical), "contract")
    assert reloaded.contract_hash == contract.contract_hash
    print("contract", label, contract.contract_hash, canonical.decode("utf-8"), sep="\t")


def reject(label, action):
    try:
        action()
    except (KeyError, ValueError) as error:
        print("catalog_error", label, type(error).__name__, sep="\t")
    else:
        raise AssertionError(label)


def main():
    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    create = load("compatibility/v0_2_1/write/add-many-mock-platform/tools.json", "PROJECT.MEMBERSHIP.ADD_MANY")
    draft = load("examples/voucher_draft_validation/tools_write.json", "FINANCE.CREATE_VOUCHER_DRAFT")
    update = load("compatibility/v0_2_1/write/update-mock-platform/tools.json", "PROJECT.PROJECT.DEACTIVATE")
    read = load(f"{READ}/tools.json", "PROJECT.LIST_PARENT_PROJECTS")
    assert canonical_json(read.to_canonical_data()) == (ROOT / f"{READ}/contract.json").read_bytes()

    for contract in (create, draft, update):
        emit(contract.operation_kind.value, contract)
    emit("READ", read)
    emit("create_without_verification", replace(
        create, write_semantics=replace(create.write_semantics, verification=None),
    ))
    emit("draft_with_reference_only", replace(
        draft,
        write_semantics=replace(
            draft.write_semantics, verification=PostWriteVerification(reference_schema=draft.output_type),
        ),
    ))
    emit("update_with_hint_only", replace(
        update,
        write_semantics=replace(
            update.write_semantics, verification=PostWriteVerification(consistency_hint=" 지연 허용 "),
        ),
    ))
    emit("legacy_unicode", replace(
        read, tool_id="TOOL.조회😀", capability=" Legacy.조회 ", input_types=(),
        required_scope=' scope:/한😀\u2028\0\v\b\f\n\r\t"\\ ', empty_is_valid=False,
        timeout_ms=2147483647, risk="CUSTOM", risk_level="R0",
    ))

    catalog = ToolContractCatalog((create, draft, update))
    reject("unknown", lambda: catalog.resolve("PROJECT.UNKNOWN", "1.0.0"))
    reject("wrong_version", lambda: catalog.resolve(update.tool_id, "1.0.1"))
    altered = replace(create, required_scope="test:other")
    assert altered.contract_hash != create.contract_hash
    reject("duplicate_identity", lambda: ToolContractCatalog((create, draft, update, altered)))


if __name__ == "__main__":
    main()
