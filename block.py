"""Table Tools: an autonomous, side-effect-free tabular data block."""

from __future__ import annotations

import json

from bloxsmith_app.block_api import (
    APPLICATION_JSON, BlockDefinition, BlockRuntimeContext, BlockRuntimeOutput, BlockRuntimeResult,
)
from . import logic, ui


# FB1 - Normalize JSON/CSV rows with explicit types, limits and controlled file reads.
# FB2 - Select, compare and join deterministic unique keys without overwriting inputs.
# FB3 - Reject malformed, oversized, ambiguous or cancelled operations without outputs.
# FB4 - Publish JSON outputs in both runtimes and through managed/linked packages.
# FB5 - Own responsive translated forms with explicit draft/Apply and scoped assets.
class TableToolsBlock(BlockDefinition):
    """Transform bounded tables using only the public runtime and surface contracts."""

    kind = "table_tools"

    def ui_assets(self, surface="modal"):
        """Use the same statically declared assets in bundled and hosted surfaces."""
        return list(self.model.get("ui_assets", {}).get(surface, []))

    def execute_runtime(self, context: BlockRuntimeContext) -> BlockRuntimeResult:
        """Transform the current input batch; failures never publish partial results."""
        try:
            values = logic.process(context.input_value("tables", "1"),
                                   context.input_value("reference", "2", default=None),
                                   context.config, context.root_dir,
                                   context.services.get("cancel_requested", lambda: False))
            outputs = [BlockRuntimeOutput(port_id=int(port.id), port_name=port.name,
                       value=json.dumps(values[port.name], ensure_ascii=False, allow_nan=False),
                       content_type=APPLICATION_JSON) for port in context.output_ports if port.name in values]
            count = values["summary"]["rows"]
            return BlockRuntimeResult(status="success", outputs=outputs, content_type=APPLICATION_JSON,
                                      last_message=json.dumps(values["summary"]), worker_received=str(count),
                                      metadata={self.kind: values["summary"]}, logs=[f"[table-tools] Processed {count} rows."])
        except (ValueError, TypeError, OSError, RecursionError) as exc:
            detail = str(exc) if isinstance(exc, logic.TableError) else "Unable to process the table input."
            message = self.translate("block.table_tools.error", {"detail": detail}, fallback="Table operation failed: {detail}")
            return BlockRuntimeResult(status="failed", outputs=[], error=message, exit_code=1,
                                      logs=[f"[table-tools-error] {detail}"])

    def handle_ui_action(self, *, node, action, values, payload=None):
        """Validate proposed settings before the framework persists a generic form patch."""
        if action in {"save_settings", "modal_update_fields", "inspector_update_fields"}:
            patch = (values or {}).get("node_patch") or {}
            if "config" in patch:
                try:
                    logic.configuration({**self.default_config(), **(node.get("config") or {}), **patch["config"]})
                except (ValueError, TypeError) as exc:
                    return {"error": self.translate("block.table_tools.error", {"detail": str(exc)}, fallback="Table operation failed: {detail}")}
        result = super().handle_ui_action(node=node, action="modal_update_fields" if action == "save_settings" else action, values=values, payload=payload)
        return ui.replace_config(result, node)

    def render_modal(self, *, node, payload=None):
        """Render the package-owned modal with pending settings and reachable actions."""
        return ui.modal(self, node, payload)

    def render_inspector_panel(self, *, node, payload=None):
        """Render the same settings in the narrower inspector surface."""
        return ui.inspector(self, node, payload)

    def render_node_card(self, *, node, payload=None):
        """Show the chosen operation without replacing the framework-owned shell."""
        return ui.card(self, node, str((node.get("config") or {}).get("operation", "normalize")))
