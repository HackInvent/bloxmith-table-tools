"""FB3/FB4/FB5: actual runtimes, package origins, stable outputs and owned UI contracts."""

from pathlib import Path
from types import SimpleNamespace
import json
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from blocs.table_tools.block import TableToolsBlock
from bloxsmith_app.block_api import BlockRuntimeContext
from block_test_packages import install_test_package, surface_payload
from ui_smoke_common import create_run_api, data_edge, display_node, graph_payload, isolated_server, text_node, wait_for_run_terminal


def main():
    """Run real graph routes through bundled, copied and linked implementations."""
    block = TableToolsBlock()
    failure = block.execute_runtime(BlockRuntimeContext(
        run_id="unit", node_id="tables", kind="table_tools", title="Tables", config={},
        inputs={"tables": "bad"}, input_content_types={}, input_message="bad", root_dir=ROOT,
        input_ports=(SimpleNamespace(id=1, name="tables"),), output_ports=(SimpleNamespace(id=1, name="rows"),)))
    assert failure.status == "failed" and not failure.outputs
    for mode in ("centralized", "zeromq_active"):
        for origin in (None, "managed", "linked"):
            with isolated_server() as server:
                node = block.build_node_payload(node_id="table-node", position={"x": 360, "y": 140})
                node["config"] = {**block.default_config(), "column_types": {"qty": "integer"}}
                if origin:
                    model = install_test_package(server, "table_tools", origin=origin)
                    node["block_version"] = model["version"]
                    for surface in ("modal", "inspector_panel", "node_card"):
                        payload = surface_payload(server, model, node, surface=surface)
                        html = payload["html"]
                        assert "{{" not in html and "__title__" not in html
                        if surface != "node_card":
                            assert 'data-block-config-field="operation"' in html
                            assert 'data-owned-apply' in html and 'owned-advanced' in html
                document = graph_payload("Table processing", [
                    text_node("source", "Source", '[{"id":"a","qty":"3"}]', 80, 140),
                    node, display_node("display", "Rows", 680, 140)], [
                    data_edge("source-table", "source", 1, "table-node", 1),
                    data_edge("table-display", "table-node", 1, "display", 1)])
                created = create_run_api(server, document, runtime_mode=mode)
                run = wait_for_run_terminal(server, created["run_id"], timeout_sec=30)
                assert run["status"] == "success", (mode, origin, run)
                assert json.loads(run["output_values"]["table-node:1"]["value"]) == [{"id": "a", "qty": 3}]
                assert json.loads(run["output_values"]["table-node:3"]["value"])["rows"] == 1
    print("[ok] Table Tools runtime and surface contracts")


if __name__ == "__main__":
    main()
