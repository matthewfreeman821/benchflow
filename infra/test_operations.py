"""Isolated host tests; no normal/demo data and no network listeners."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import unittest
import uuid

from config.errors import ConfigurationError
from config.settings import Settings, load_settings
from infra import operations as ops


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="benchflow-infra-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.settings = Settings(data_root=self.base / "data",
                                 local_app_data_root=self.base)

    def test_acl_and_launcher_lock(self):
        ops.private_directory(self.settings.data_root)
        with ops.launcher_lock(self.settings.data_root):
            with self.assertRaisesRegex(ops.Refused, "DATA_ROOT_BUSY"):
                with ops.launcher_lock(self.settings.data_root):
                    self.fail("second lock acquired")

    def test_process_identity(self):
        first = ops.process_identity(os.getpid())
        self.assertEqual(first, ops.process_identity(os.getpid()))
        self.assertEqual(first["pid"], os.getpid())
        self.assertTrue(first["image"].lower().endswith("python.exe"))

    def test_integrity_checkpoint_and_hash_bundle(self):
        from backend.database import initialize_database
        with ops.offline(self.settings):
            initialize_database(self.settings)
            bid = ops.snapshot(self.settings)
        data, checked = ops.validate_bundle(self.settings, bid)
        ops.integrity(data)
        ops.checkpoint(data)
        manifest = checked.model_dump(mode="json")
        manifest["sha256"] = "0" * 64
        (data.parent / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ops.Refused, "BACKUP_MANIFEST_MISMATCH"):
            ops.validate_bundle(self.settings, bid)

    def test_bad_integrity_refused(self):
        path = self.base / "corrupt.sqlite3"
        path.write_bytes(b"fictional corrupt database")
        with self.assertRaises(sqlite3.DatabaseError):
            ops.integrity(path)

    def test_restore_requires_exact_confirmation_before_backend_import(self):
        with self.assertRaisesRegex(ops.Refused, "CONFIRMATION_REQUIRED"):
            ops.restore(self.settings, str(uuid.uuid4()), "")

    def test_configuration_refuses_remote_bind_without_data_creation(self):
        with self.assertRaises(ConfigurationError):
            load_settings({"LOCALAPPDATA": str(self.base), "BENCHFLOW_HOST": "0.0.0.0"})
        self.assertFalse(self.settings.data_root.exists())

    def test_staging_settings_are_separate_same_volume(self):
        stage = replace(self.settings, data_root=self.settings.data_root / ".restore-fixture")
        self.assertEqual(stage.database_path.name, "benchflow.sqlite3")
        self.assertEqual(stage.data_root.drive, self.settings.data_root.drive)
        self.assertNotEqual(stage.database_path, self.settings.database_path)

    def test_unc_refused(self):
        with self.assertRaisesRegex(ops.Refused, "LOCAL_PATH_REQUIRED"):
            ops.no_reparse(Path(r"\\fictional\share"))


if __name__ == "__main__":
    unittest.main()
