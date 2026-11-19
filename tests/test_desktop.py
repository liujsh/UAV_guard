import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QInputDialog

from guard_workbench.desktop import MainWindow, _LoginDialog
from guard_workbench.scene import Obstacle, SceneConfig
from guard_workbench.scene_editor import SceneEditor, _ObstacleDialog
from guard_workbench.workspace import Workspace


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.application = QApplication.instance() or QApplication([])

    def test_editor_updates_preview_and_persists_project(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        database = Path(directory) / "workspace.sqlite3"
        workspace = Workspace(database)
        dialog = _LoginDialog(workspace)
        dialog.user.setText("实验员")
        dialog.password.setText("test-password")
        dialog.confirmation.setText("test-password")
        dialog._submit()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

        window = MainWindow(workspace)
        window.show()
        self.addCleanup(window.hide)
        with patch.object(QInputDialog, "getText", return_value=("城市实验", True)):
            window._create_project()
        project_id = window.project.project_id
        self.assertEqual(window.projects.count(), 1)
        window.editor.direction.setCurrentIndex(1)
        window.editor.seed.setText("4294967295")
        window.time_slider.setValue(128)
        self.assertTrue(window._pending_changes)
        self.assertEqual(window.time_label.text(), "12.8 s")
        self.assertIn("*", window.project_label.text())
        QTest.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
        self.assertFalse(window._pending_changes)

        reopened = Workspace(database)
        self.assertTrue(reopened.sign_in("实验员", "test-password"))
        self.assertEqual(reopened.open_project(project_id).scene,
                         SceneConfig(seed=4294967295, target_route_direction="reverse"))

    def test_obstacle_dialog_rejects_reversed_time_window(self):
        editor = SceneEditor()
        dialog = _ObstacleDialog(Obstacle(4, -2, 2, 1, 2, 5, 10), editor)
        dialog.fields["end_s"].setValue(4)
        confirm = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)
        confirm.click()
        self.assertIn("end_s", dialog.error.text())
        dialog.fields["end_s"].setValue(8)
        confirm.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.obstacle.end_s, 8)
