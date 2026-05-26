from __future__ import annotations

import unittest
from pathlib import Path


class ResearchPackLoaderTest(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def test_discovers_pack_and_reads_assets(self) -> None:
        from app.research_packs import list_packs, load_pack, load_pack_template

        packs = list_packs()
        self.assertIn("gaa_vertical", {pack["pack_id"] for pack in packs})

        pack = load_pack("gaa_vertical")
        self.assertEqual(pack["manifest"]["pack_id"], "gaa_vertical")
        self.assertIn("vertical_nanosheet", pack["ontology"]["architecture_class"])
        self.assertIn("low_difficulty_first_step_proposal", pack["templates"])
        template = load_pack_template("gaa_vertical", "low_difficulty_first_step_proposal")
        self.assertIn("Low-Difficulty First-Step Proposal", template)
        self.assertIn("later-stage only", template)

    def test_pack_errors_do_not_block_core_database_startup(self) -> None:
        from app.db import init_db
        from app.research_packs import load_pack

        with self.assertRaises(FileNotFoundError):
            load_pack("missing_pack")
        init_db()

    def test_domain_terms_stay_out_of_app_core_logic(self) -> None:
        forbidden = [
            "GAA",
            "gate-all-around",
            "nanosheet",
            "nanowire",
            "nanotube",
            "Ge/Si",
            "GeSn",
            "DRIE",
            "ALD",
            "ESR",
            "Raman",
            "Bosch",
            "contact printing",
        ]
        app_text = "\n".join(path.read_text(encoding="utf-8") for path in (self.root / "app").glob("*.py"))
        for term in forbidden:
            self.assertNotIn(term, app_text)


if __name__ == "__main__":
    unittest.main()
