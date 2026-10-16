/**
 * ComfyAudit: a warning before "Export (API Format)" on a tracked shot.
 *
 * The API format is ComfyUI's prompt format: nodes and inputs only, with no
 * room for `extra`, where the shot history lives. Exporting a tracked shot
 * that way silently leaves its history behind, so ask first and point to the
 * export that keeps it.
 */
import { app } from "../../scripts/app.js";

const EXTRA_KEY = "comfyaudit_history";

async function confirmApiExport() {
  const entries = app.graph?.extra?.[EXTRA_KEY]?.entries?.length || 0;
  if (!entries) return true;
  const message = "The API format has no room for this shot's history "
    + `(${entries} recorded ${entries === 1 ? "entry" : "entries"}), so the exported file `
    + "will not carry it. Use Export Workflow, or Save, to keep the history with the file.\n\n"
    + "Export in API format anyway?";
  const dialog = app.extensionManager?.dialog;
  if (typeof dialog?.confirm === "function") {
    const ok = await dialog.confirm({ title: "This export leaves the shot history behind", message, type: "default" });
    return ok === true;
  }
  return window.confirm(message);
}

app.registerExtension({
  name: "comfyaudit.exportguard",
  setup() {
    const command = (app.extensionManager?.command?.commands || []).find((c) => c.id === "Comfy.ExportWorkflowAPI");
    if (!command || typeof command.function !== "function" || command.__comfyaudit) return;
    const original = command.function;
    command.function = async (...args) => {
      if (await confirmApiExport()) return original.apply(command, args);
    };
    command.__comfyaudit = true;
  },
});
