from __future__ import annotations

import re
import unittest
from pathlib import Path


class StaticUiContractTest(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def test_workflow_nav_uses_branch_tools(self) -> None:
        css = (Path(__file__).resolve().parents[1] / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".workflow-strip {\n  display: grid;\n  grid-template-columns: 1fr;", css)
        self.assertIn(".branch-nav {\n  grid-column: 1 / -1;", css)
        self.assertIn(".branch-nav[hidden] {\n  display: none;", css)
        self.assertIn(".branch-tools.active {\n  display: flex;", css)
        self.assertNotIn("grid-template-columns: minmax(220px, 0.26fr) minmax(0, 1fr);", css)
        self.assertNotIn(".utility-nav", css)

    def test_tool_entries_are_not_repeated_across_button_rows(self) -> None:
        html = (self.root / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("workbench-lanes", html)
        self.assertNotIn("utility-nav", html)
        self.assertNotIn("action-card", html)
        for page in (
            "sources",
            "processing",
            "query",
            "audits",
            "pdf",
            "compare",
            "outputs",
            "maintenance",
            "settings",
        ):
            self.assertEqual(len(re.findall(fr'data-page="{page}"', html)), 1, page)

    def test_collect_entry_groups_ingest_discovery_and_doi(self) -> None:
        html = (self.root / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (self.root / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('<button class="nav-item branch-item" data-page="upload">收集资料</button>', html)
        self.assertNotIn('class="nav-item branch-item" data-page="literature"', html)
        self.assertNotIn('class="nav-item branch-item" data-page="doi"', html)
        self.assertEqual(len(re.findall(r'class="nav-item mode-tab[^"]*" data-page="upload"', html)), 3)
        self.assertEqual(len(re.findall(r'class="nav-item mode-tab[^"]*" data-page="literature"', html)), 3)
        self.assertEqual(len(re.findall(r'class="nav-item mode-tab[^"]*" data-page="doi"', html)), 3)
        self.assertIn('const collectIntakePages = new Set(["upload", "literature", "doi"]);', app_js)

    def test_runtime_boundary_is_not_a_highlighted_choice(self) -> None:
        html = (self.root / "static" / "index.html").read_text(encoding="utf-8")
        css = (self.root / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('class="runtime-boundary"', html)
        self.assertNotIn('class="header-actions"', html)
        self.assertNotIn('class="status-chip ok"', html)
        self.assertIn(".runtime-boundary span {", css)

    def test_known_visible_english_copy_was_localized(self) -> None:
        ui_text = "\n".join(
            [
                (self.root / "static" / "index.html").read_text(encoding="utf-8"),
                (self.root / "static" / "app.js").read_text(encoding="utf-8"),
                (self.root / "static" / "pdf_reader.html").read_text(encoding="utf-8"),
            ]
        )
        for phrase in (
            "Index Coverage",
            "Backend Status",
            "Recent Ingestions",
            "Failed Ingestions",
            "Ingest PDF",
            "Run Query",
            "Allow private API",
            "Audit Detail / Repair",
            "Build Local Vector",
            "Backup DB",
            "Evaluation Result",
            "Focused evidence:",
            "No chunks",
            "No evidence",
                "Private scope: API is blocked",
            ):
            self.assertNotIn(phrase, ui_text)

    def test_file_inputs_use_integrated_picker_shell(self) -> None:
        html = (self.root / "static" / "index.html").read_text(encoding="utf-8")
        css = (self.root / "static" / "styles.css").read_text(encoding="utf-8")
        app_js = (self.root / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('class="file-field span-2"', html)
        self.assertIn('class="file-picker"', html)
        self.assertIn('class="file-picker-input" type="file"', html)
        self.assertIn('class="file-picker-action">选择 PDF</span>', html)
        self.assertIn('class="file-picker-name" data-empty="未选择文件"', html)
        self.assertIn(".file-picker {\n  position: relative;", css)
        self.assertIn(".file-picker:focus-within {", css)
        self.assertIn(".file-picker-input {\n  position: absolute;\n  inset: 0;", css)
        self.assertIn("  border: 0;\n  border-radius: 0;\n  background: transparent;\n  padding: 0;", css)
        self.assertIn("function initFilePickers()", app_js)
        self.assertIn('document.querySelectorAll(".file-picker")', app_js)

    def test_local_import_uses_single_source_switch(self) -> None:
        html = (self.root / "static" / "index.html").read_text(encoding="utf-8")
        css = (self.root / "static" / "styles.css").read_text(encoding="utf-8")
        app_js = (self.root / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="importForm"', html)
        self.assertNotIn('id="uploadForm"', html)
        self.assertNotIn('id="folderForm"', html)
        self.assertIn('name="source_kind" value="file"', html)
        self.assertIn('name="source_kind" value="folder"', html)
        self.assertIn('data-import-source="file"', html)
        self.assertIn('data-import-source="folder"', html)
        self.assertIn("PDF 文件夹", html)
        self.assertIn("[hidden] {\n  display: none !important;\n}", css)
        self.assertIn(".segmented-control {", css)
        self.assertIn("function updateImportForm()", app_js)
        self.assertIn('/api/ingest/upload', app_js)
        self.assertIn('/api/ingest/folder', app_js)

    def test_import_metadata_explains_default_domain_and_sensitivity_policy(self) -> None:
        html = (self.root / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (self.root / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("默认领域", html)
        self.assertIn('id="importClassificationHint"', html)
        self.assertIn('id="importSensitivityHint"', html)
        self.assertIn("当前批量导入使用同一默认领域", html)
        self.assertIn("const sensitivityHint = {", app_js)
        self.assertIn("默认只走本地检索", app_js)
        self.assertIn("默认阻止 API 索引、API 检索和 API LLM 分析", app_js)

    def test_primary_flow_uses_virtual_branches(self) -> None:
        html = (self.root / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (self.root / "static" / "app.js").read_text(encoding="utf-8")
        for branch, landing_page in {
            "collect": "upload",
            "retrieve": "query",
            "publish": "outputs",
            "maintain": "maintenance",
        }.items():
            self.assertIn(f'data-page="{branch}" data-branch="{branch}"', html)
            self.assertIn(f'{branch}: "{landing_page}"', app_js)
            self.assertIn(f'data-branch="{branch}"', html)

    def test_active_page_uses_full_available_width(self) -> None:
        css = (self.root / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".page {\n  display: none;\n  width: 100%;\n  max-width: none;", css)
        self.assertNotIn("max-width: 1360px", css)


if __name__ == "__main__":
    unittest.main()
