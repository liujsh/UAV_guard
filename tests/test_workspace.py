from pathlib import Path
import sqlite3
import tempfile
import unittest

from guard_workbench.scene import Obstacle, SceneConfig
from guard_workbench.workspace import ProjectSummary, Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "data" / "workspace.sqlite3"
        self.workspace = Workspace(self.path)

    def test_local_account_persists_and_prevents_unsigned_access(self):
        self.assertFalse(self.workspace.has_account())
        self.assertFalse(self.workspace.sign_in("实验员", "example-password"))
        with self.assertRaises(ValueError):
            self.workspace.set_up_account("实验员", "short")
        self.workspace.set_up_account("实验员", "example-password")
        self.assertEqual(self.workspace.username, "实验员")
        self.workspace.sign_out()
        with self.assertRaises(PermissionError):
            self.workspace.create_project("遮挡实验")

        reopened = Workspace(self.path)
        self.assertTrue(reopened.has_account())
        self.assertIsNone(reopened.username)
        self.assertFalse(reopened.sign_in("实验员", "wrong-password"))
        with self.assertRaises(PermissionError):
            reopened.list_projects()
        with self.assertRaises(ValueError):
            reopened.set_up_account("其他人", "another-password")
        self.assertTrue(reopened.sign_in("实验员", "example-password"))
        self.assertNotIn(b"example-password", self.path.read_bytes())

    def test_projects_and_independent_scenes_survive_restart(self):
        self.workspace.set_up_account("实验员", "example-password")
        baseline = self.workspace.create_project("基准")
        changed = self.workspace.create_project("悬停")
        scene = SceneConfig(
            target_speed_mps=1.1,
            pedestrian_count=2,
            obstacles=(Obstacle(5, 3, 2, 1, 2),),
        )
        self.workspace.rename_project(changed.project_id, "短时遮挡")
        self.workspace.save_project_scene(changed.project_id, scene)

        reopened = Workspace(self.path)
        self.assertTrue(reopened.sign_in("实验员", "example-password"))
        self.assertCountEqual(
            reopened.list_projects(),
            [
                ProjectSummary(baseline.project_id, "基准"),
                ProjectSummary(changed.project_id, "短时遮挡"),
            ],
        )
        self.assertEqual(reopened.open_project(baseline.project_id), baseline)
        self.assertEqual(reopened.open_project(changed.project_id).scene, scene)

    def test_invalid_project_operations_preserve_existing_data(self):
        self.workspace.set_up_account("实验员", "example-password")
        project = self.workspace.create_project("遮挡实验")
        with self.assertRaises(ValueError):
            self.workspace.rename_project(project.project_id, "  ")
        with self.assertRaises(KeyError):
            self.workspace.save_project_scene("missing", SceneConfig())
        with self.assertRaises(KeyError):
            self.workspace.open_project("missing")
        self.assertEqual(self.workspace.open_project(project.project_id), project)

    def test_future_database_version_is_rejected(self):
        with sqlite3.connect(self.path) as database:
            database.execute("PRAGMA user_version = 999")
        database.close()
        with self.assertRaisesRegex(ValueError, "数据库版本"):
            Workspace(self.path)

    def test_run_snapshot_survives_edit_and_schema_upgrade(self):
        self.workspace.set_up_account("实验员", "example-password")
        project = self.workspace.create_project("雨天观测", SceneConfig(seed=17))
        with sqlite3.connect(self.path) as database:
            database.execute("DROP TABLE simulation_run")
            database.execute("PRAGMA user_version = 1")
        database.close()

        upgraded = Workspace(self.path)
        self.assertTrue(upgraded.sign_in("实验员", "example-password"))
        run = upgraded.start_run(project.project_id, "offline-cv-v1")
        upgraded.save_project_scene(project.project_id, SceneConfig(seed=18))
        upgraded.finish_run(run.info.run_id, "completed", result={"samples": [1]})
        upgraded.append_run_log(run.info.run_id, "固定测试日志")
        self.assertIn("固定测试日志", upgraded.read_run_log(run.info.run_id))
        with self.assertRaises(ValueError):
            upgraded.run_log_path("../outside")
        with self.assertRaises(ValueError):
            upgraded.finish_run(run.info.run_id, "cancelled")

        reopened = Workspace(self.path)
        with self.assertRaises(PermissionError):
            reopened.open_run(run.info.run_id)
        self.assertTrue(reopened.sign_in("实验员", "example-password"))
        self.assertEqual(reopened.open_project(project.project_id).scene.seed, 18)
        stored = reopened.open_run(run.info.run_id)
        self.assertEqual(stored.scene.seed, 17)
        self.assertEqual(stored.result, {"samples": [1]})
        self.assertEqual(reopened.list_runs(project.project_id)[0].status, "completed")
