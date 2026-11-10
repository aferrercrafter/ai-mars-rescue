"""vanilla/ ships a copy of the engine so it can run without the harness.

The copy is made by hand, so this test fails when the two drift apart.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class VanillaCopyTest(unittest.TestCase):
    def test_vanilla_engine_matches_engine(self):
        main = (ROOT / "engine" / "engine.py").read_text(encoding="utf-8")
        copy = (ROOT / "vanilla" / "engine" / "engine.py").read_text(encoding="utf-8")
        self.assertEqual(
            main.replace("\r\n", "\n"),
            copy.replace("\r\n", "\n"),
            "vanilla/engine/engine.py differs from engine/engine.py; copy it over",
        )


if __name__ == "__main__":
    unittest.main()
