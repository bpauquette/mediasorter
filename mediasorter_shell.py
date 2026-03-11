import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import ctypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mediasorter_ntfs import probe_ntfs_enumerator
from mediasorter_treemap import TreemapDialog

SHELL_SETTINGS_FILE = Path(__file__).resolve().parent / "mediasorter_shell_settings.json"


class MediaSorter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("AppRoot")
        self.setWindowTitle("MediaSorter")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 820)

        self.input_folder = ""
        self.output_folder = ""

        self._build_menu_bar()
        self._build_ui()
        self._apply_styles()
        self._restore_window_settings()
        self._update_folder_labels()

    def _build_menu_bar(self) -> None:
        menu_bar = QMenuBar(self)
        menu_bar.setNativeMenuBar(False)
        self.setMenuBar(menu_bar)

        self.files_menu = menu_bar.addMenu("Files")
        self.action_input_folder = QAction("Input Folder...", self)
        self.action_input_folder.triggered.connect(self.select_input_folder)
        self.files_menu.addAction(self.action_input_folder)

        self.action_output_folder = QAction("Output Folder...", self)
        self.action_output_folder.triggered.connect(self.select_output_folder)
        self.files_menu.addAction(self.action_output_folder)

        self.files_menu.addSeparator()

        self.action_check_disk_space = QAction("Check Disk Space", self)
        self.action_check_disk_space.triggered.connect(self.check_disk_space)
        self.files_menu.addAction(self.action_check_disk_space)

        self.files_menu.addSeparator()

        self.action_exit = QAction("Exit", self)
        self.action_exit.triggered.connect(self.close)
        self.files_menu.addAction(self.action_exit)

        self.edit_menu = menu_bar.addMenu("Edit")
        self.action_categories = QAction("Categories...", self)
        self.action_categories.triggered.connect(
            lambda: self._show_placeholder("Categories", "Category editing will be added in the new app shell.")
        )
        self.edit_menu.addAction(self.action_categories)

        self.action_user_preferences = QAction("User Preferences...", self)
        self.action_user_preferences.triggered.connect(
            lambda: self._show_placeholder("User Preferences", "User preferences will be added in the new app shell.")
        )
        self.edit_menu.addAction(self.action_user_preferences)

        self.view_menu = menu_bar.addMenu("View")
        self.action_classification_log = QAction("Classification Log", self)
        self.action_classification_log.triggered.connect(
            lambda: self._show_placeholder("Classification Log", "The classification log panel will be added next.")
        )
        self.view_menu.addAction(self.action_classification_log)

        self.action_current_item = QAction("Current Item", self)
        self.action_current_item.triggered.connect(
            lambda: self._show_placeholder("Current Item", "The current item panel will be added next.")
        )
        self.view_menu.addAction(self.action_current_item)

        self.action_statistics = QAction("Statistics", self)
        self.action_statistics.triggered.connect(
            lambda: self._show_placeholder("Statistics", "The statistics panel will be added next.")
        )
        self.view_menu.addAction(self.action_statistics)

        self.run_menu = menu_bar.addMenu("Run")
        self.action_start = QAction("Start", self)
        self.action_start.triggered.connect(lambda: self._show_placeholder("Run", "Start will be wired after the shell is approved."))
        self.run_menu.addAction(self.action_start)

        self.action_stop = QAction("Stop", self)
        self.action_stop.triggered.connect(lambda: self._show_placeholder("Run", "Stop will be wired after the shell is approved."))
        self.run_menu.addAction(self.action_stop)

        self.help_menu = menu_bar.addMenu("Help")
        self.action_about = QAction("About", self)
        self.action_about.triggered.connect(
            lambda: self._show_placeholder("About", "MediaSorter new shell prototype.")
        )
        self.help_menu.addAction(self.action_about)

        self.action_welcome = QAction("Welcome", self)
        self.action_welcome.triggered.connect(self.show_welcome_message)
        self.help_menu.addAction(self.action_welcome)

        self.action_check_updates = QAction("Check for Updates", self)
        self.action_check_updates.triggered.connect(
            lambda: self._show_placeholder("Check for Updates", "Update checking will be added next.")
        )
        self.help_menu.addAction(self.action_check_updates)

        self.action_privacy = QAction("Privacy", self)
        self.action_privacy.triggered.connect(
            lambda: self._show_placeholder("Privacy", "Privacy information will be added next.")
        )
        self.help_menu.addAction(self.action_privacy)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("ShellCentral")
        self.setCentralWidget(central)

        title = QLabel("MediaSorter")
        title.setObjectName("LeadText")
        self.title_label = title

        subtitle = QLabel("Organize your photo library")
        subtitle.setObjectName("SubheadText")
        self.subtitle_label = subtitle

        intro = QLabel(
            "MediaSorter helps you organize a photo library by selecting an input folder, choosing an output folder, and then running the sorter to place files into a cleaner structure."
        )
        intro.setObjectName("HintText")
        intro.setWordWrap(True)
        self.intro_label = intro

        self.welcome_label = QLabel(
            "Use the Files menu to choose your input and output folders. Use Edit to manage categories and preferences. When you are ready, use Run to start organizing your library. View will later provide the current item, classification log, and statistics."
        )
        self.welcome_label.setObjectName("WelcomeText")
        self.welcome_label.setWordWrap(True)

        self.label_input = QLabel("Not selected yet")
        self.label_input.setObjectName("SelectionValue")
        self.label_input.setWordWrap(True)

        self.label_output = QLabel("Not selected yet")
        self.label_output.setObjectName("SelectionValue")
        self.label_output.setWordWrap(True)

        btn_input = QPushButton("Choose Input Folder")
        btn_input.clicked.connect(self.select_input_folder)
        btn_output = QPushButton("Choose Output Folder")
        btn_output.clicked.connect(self.select_output_folder)

        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(16)
        info_grid.setVerticalSpacing(14)
        info_grid.addWidget(QLabel("Input Folder"), 0, 0)
        info_grid.addWidget(self.label_input, 0, 1)
        info_grid.addWidget(btn_input, 0, 2)
        info_grid.addWidget(QLabel("Output Folder"), 1, 0)
        info_grid.addWidget(self.label_output, 1, 1)
        info_grid.addWidget(btn_output, 1, 2)
        info_grid.setColumnStretch(1, 1)

        self.status_label = QLabel("Welcome")
        self.status_label.setObjectName("HintText")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        shell_layout = QVBoxLayout()
        shell_layout.setContentsMargins(28, 28, 28, 28)
        shell_layout.setSpacing(18)
        shell_layout.addWidget(title)
        shell_layout.addWidget(subtitle)
        shell_layout.addWidget(intro)
        shell_layout.addWidget(self.welcome_label)
        shell_layout.addLayout(info_grid)
        shell_layout.addWidget(self.status_label)
        shell_layout.addStretch(1)
        central.setLayout(shell_layout)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow#AppRoot {
                background: #f5f7fb;
            }
            QWidget#ShellCentral {
                background: #f5f7fb;
            }
            QMenuBar {
                background: #ffffff;
                border-bottom: 1px solid #d6deea;
                padding: 4px 8px;
            }
            QMenuBar::item {
                color: #24324a;
                padding: 6px 12px;
                background: transparent;
            }
            QMenuBar::item:selected {
                background: #e7f0ff;
                color: #0b63ce;
            }
            QMenu {
                background: #ffffff;
                border: 1px solid #d6deea;
                color: #162033;
            }
            QMenu::item {
                padding: 6px 24px 6px 12px;
            }
            QMenu::item:selected {
                background: #e7f0ff;
                color: #0b63ce;
            }
            QLabel#LeadText {
                font-size: 28px;
                font-weight: 700;
                color: #10213e;
            }
            QLabel#SubheadText {
                font-size: 16px;
                font-weight: 600;
                color: #3d5a80;
            }
            QLabel#HintText {
                color: #5b6f8f;
                font-size: 14px;
            }
            QLabel#WelcomeText {
                color: #24324a;
                font-size: 15px;
                line-height: 1.4;
            }
            QLabel#SelectionValue {
                background: #ffffff;
                border: 1px solid #d6deea;
                border-radius: 10px;
                padding: 10px 12px;
                color: #10213e;
                font-weight: 600;
            }
            QPushButton {
                min-height: 34px;
                padding: 7px 14px;
                border-radius: 10px;
                border: 1px solid #cad5e6;
                background: #ffffff;
                color: #10213e;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f7faff;
                border-color: #b8c7df;
            }
            """
        )

    def _update_folder_labels(self) -> None:
        self.label_input.setText(self.input_folder or "Not selected yet")
        self.label_output.setText(self.output_folder or "Not selected yet")

    def _show_placeholder(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)

    def show_welcome_message(self) -> None:
        self.status_label.setText("Welcome")
        self.centralWidget().setFocus()

    def select_input_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder", self.input_folder or str(Path.home()))
        if folder:
            self.input_folder = folder
            self._update_folder_labels()

    def select_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_folder or str(Path.home()))
        if folder:
            self.output_folder = folder
            self._update_folder_labels()

    def check_disk_space(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Disk Space")
        dialog.resize(760, 520)

        intro = QLabel(
            "Review free space across all available drives. Open the treemap for a drive to inspect it visually and launch items in Explorer for cleanup."
        )
        intro.setWordWrap(True)
        intro.setObjectName("HintText")

        content = QWidget()
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        drives = self._available_drives()
        if not drives:
            empty = QLabel("No drives were detected.")
            empty.setObjectName("HintText")
            content_layout.addWidget(empty)
        else:
            for drive in drives:
                content_layout.addWidget(self._build_drive_row(dialog, drive))
        content_layout.addStretch(1)
        content.setLayout(content_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)

        layout = QVBoxLayout()
        layout.addWidget(intro)
        layout.addWidget(scroll, 1)
        layout.addWidget(close_button, alignment=Qt.AlignRight)
        dialog.setLayout(layout)
        dialog.exec()

    def _available_drives(self) -> list[dict]:
        drives = []
        seen = set()
        letters = []
        if os.name == "nt":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                root = f"{letter}:\\"
                if os.path.exists(root):
                    letters.append(root)
        else:
            letters.append("/")

        for root in letters:
            try:
                total, used, free = shutil.disk_usage(root)
            except Exception:
                continue
            key = os.path.normcase(root)
            if key in seen:
                continue
            seen.add(key)
            drives.append(
                {
                    "path": root,
                    "total": int(total),
                    "used": int(used),
                    "free": int(free),
                }
            )
        return drives

    def _build_drive_row(self, dialog: QDialog, drive: dict) -> QWidget:
        path = str(drive.get("path") or "")
        total = int(drive.get("total") or 0)
        used = int(drive.get("used") or 0)
        free = int(drive.get("free") or 0)
        percent_used = int(round((used / total) * 100)) if total > 0 else 0
        gib = 1024 ** 3

        card = QFrame()
        card.setObjectName("DriveCard")
        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel(path)
        title.setObjectName("DriveTitle")
        summary = QLabel(
            f"Free {free / gib:.1f} GiB of {total / gib:.1f} GiB total"
        )
        summary.setObjectName("HintText")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(summary)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(percent_used)
        bar.setFormat(f"{percent_used}% used")

        detail = QLabel(
            f"Used: {used / gib:.1f} GiB    Free: {free / gib:.1f} GiB"
        )
        detail.setObjectName("HintText")

        button_row = QHBoxLayout()
        btn_treemap = QPushButton("Open Treemap")
        btn_treemap.clicked.connect(lambda _=False, p=path: self._open_drive_treemap(p))
        button_row.addWidget(btn_treemap)
        button_row.addStretch(1)

        layout.addLayout(title_row)
        layout.addWidget(bar)
        layout.addWidget(detail)
        layout.addLayout(button_row)
        card.setLayout(layout)
        return card

    def _open_drive_treemap(self, drive_path: str, *, allow_uac_prompt: bool = True) -> None:
        if allow_uac_prompt and self._maybe_prompt_for_elevated_ntfs_scan(drive_path):
            return
        dialog = TreemapDialog(drive_path, self)
        dialog.exec()

    def _maybe_prompt_for_elevated_ntfs_scan(self, drive_path: str) -> bool:
        if os.name != "nt":
            return False
        probe = probe_ntfs_enumerator(drive_path)
        if probe.filesystem.upper() != "NTFS":
            return False
        if probe.enum_usn_ok:
            return False
        if int(probe.open_error or 0) != 5:
            return False

        answer = QMessageBox.question(
            self,
            "Administrator Access Needed",
            (
                f"MediaSorter needs Administrator access to use the fast NTFS scan backend for {drive_path}.\n\n"
                "Do you want to relaunch this treemap with a UAC prompt?"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return False
        return self._launch_elevated_treemap(drive_path)

    def _launch_elevated_treemap(self, drive_path: str) -> bool:
        if os.name != "nt":
            return False

        cli_path = Path(__file__).resolve().with_name("mediasorter_cli.py")
        params = subprocess.list2cmdline(
            [
                str(cli_path),
                "--open-treemap",
                str(drive_path),
                "--skip-uac-prompt",
            ]
        )
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            params,
            str(cli_path.parent),
            1,
        )
        if int(result or 0) <= 32:
            QMessageBox.warning(
                self,
                "Elevation Failed",
                f"Windows could not relaunch MediaSorter with Administrator access for {drive_path}.",
            )
            return False
        return True

    def _load_shell_settings(self) -> dict:
        try:
            if SHELL_SETTINGS_FILE.exists():
                data = json.loads(SHELL_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_shell_settings(self) -> None:
        payload = {
            "x": int(self.x()),
            "y": int(self.y()),
            "width": int(self.width()),
            "height": int(self.height()),
        }
        try:
            SHELL_SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _restore_window_settings(self) -> None:
        settings = self._load_shell_settings()
        try:
            width = max(int(settings.get("width") or 1200), self.minimumWidth())
            height = max(int(settings.get("height") or 820), self.minimumHeight())
            self.resize(width, height)
        except Exception:
            pass
        try:
            if "x" in settings and "y" in settings:
                self.move(int(settings.get("x") or 0), int(settings.get("y") or 0))
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._save_shell_settings()
        super().closeEvent(event)
