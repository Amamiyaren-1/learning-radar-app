"""Roles decouple workflow from providers; model changes require configuration only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from learnradar.cli import _roles_text
from learnradar.config import ConfigError, load_settings

REPO_ROOT = Path(__file__).resolve().parent.parent


class RoleConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_settings()

    def test_roles_resolve_to_provider_available_models(self) -> None:
        self.assertEqual(self.settings.roles["screening"], "flash")
        self.assertEqual(self.settings.roles["analysis"], "pro")
        self.assertEqual(self.settings.model_for("screening").model_id, "deepseek-flash")
        self.assertEqual(self.settings.model_for("analysis").model_id, "deepseek-v4-pro")

    def test_roles_text_shows_role_candidate_and_model_id(self) -> None:
        text = _roles_text(self.settings)
        self.assertIn("screening=flash(deepseek-flash)", text)
        self.assertIn("analysis=pro(deepseek-v4-pro)", text)

    def test_unknown_role_raises_config_error(self) -> None:
        with self.assertRaises(ConfigError):
            self.settings.model_for("nonexistent-role")

    def test_role_pointing_to_unknown_candidate_raises(self) -> None:
        settings = load_settings()
        settings.roles["screening"] = "no-such-candidate"
        with self.assertRaises(ConfigError):
            settings.model_for("screening")

    def test_replacing_the_analysis_model_needs_config_only(self) -> None:
        """Replacing analysis with an offline candidate requires no source changes."""
        original = (REPO_ROOT / "config" / "settings.toml").read_text(encoding="utf-8")
        swapped = original.replace('analysis = "pro"', 'analysis = "offline"')
        self.assertNotEqual(original, swapped)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            (root / "config" / "settings.toml").write_text(swapped, encoding="utf-8")
            settings = load_settings(root)
            self.assertTrue(settings.model_for("analysis").is_fixture)
            self.assertEqual(settings.model_for("analysis").model_id, "offline-fixture-v1")
            # The screening role is unaffected.
            self.assertEqual(settings.model_for("screening").model_id, "deepseek-flash")

    def test_missing_roles_section_is_a_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            (root / "config" / "settings.toml").write_text(
                '[[models.candidate]]\nname = "x"\nprovider = "offline_fixture"\nmodel_id = "x"\n',
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_settings(root)


if __name__ == "__main__":
    unittest.main()
