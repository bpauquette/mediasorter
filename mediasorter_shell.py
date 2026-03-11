import ctypes
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mediasorter_ntfs import probe_ntfs_enumerator
from mediasorter_treemap import TreemapDialog
from mediasorter_window import MediaSorter as LegacyMediaSorter

SHELL_SETTINGS_FILE = Path(__file__).resolve().parent / "mediasorter_shell_settings.json"


class MediaSorter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("AppRoot")
        self.setWindowTitle("MediaSorter")
        self.setMinimumSize(1100, 760)
        self.resize(1280, 880)

        self.input_folder = ""
        self.output_folder = ""
        self._legacy_window = None
        self._legacy_widget = None
        self._pages = {}
        self._nav_buttons = {}

        self._build_menu_bar()
        self._build_ui()
        self._apply_styles()
        self._restore_window_settings()
        self._update_shell_state()
        self._show_page("welcome")

    def _build_menu_bar(self):
        menu_bar = QMenuBar(self)
        menu_bar.setNativeMenuBar(False)
        self.setMenuBar(menu_bar)

        files_menu = menu_bar.addMenu("Files")
        action_input = QAction("Input Folder...", self)
        action_input.triggered.connect(self.select_input_folder)
        files_menu.addAction(action_input)
        action_output = QAction("Output Folder...", self)
        action_output.triggered.connect(self.select_output_folder)
        files_menu.addAction(action_output)
        files_menu.addSeparator()
        action_disk = QAction("Check Disk Space", self)
        action_disk.triggered.connect(self.check_disk_space)
        files_menu.addAction(action_disk)
        files_menu.addSeparator()
        action_exit = QAction("Exit", self)
        action_exit.triggered.connect(self.close)
        files_menu.addAction(action_exit)

        edit_menu = menu_bar.addMenu("Edit")
        action_categories = QAction("Categories...", self)
        action_categories.triggered.connect(self.open_categories)
        edit_menu.addAction(action_categories)
        action_preferences = QAction("User Preferences...", self)
        action_preferences.triggered.connect(lambda: self._show_page("options"))
        edit_menu.addAction(action_preferences)

        view_menu = menu_bar.addMenu("View")
        for label, message in (
            ("Classification Log", "Classification log is available in the full workspace."),
            ("Current Item", "Current-item preview is available in the full workspace."),
            ("Statistics", "Statistics and summaries are available in the full workspace."),
        ):
            action = QAction(label, self)
            action.triggered.connect(lambda _=False, text=message: self._open_legacy_workspace(text))
            view_menu.addAction(action)

        run_menu = menu_bar.addMenu("Run")
        action_start = QAction("Start", self)
        action_start.triggered.connect(self.start_processing)
        run_menu.addAction(action_start)
        action_stop = QAction("Stop", self)
        action_stop.triggered.connect(self.stop_processing)
        run_menu.addAction(action_stop)

        tools_menu = menu_bar.addMenu("Tools")
        action_people = QAction("Scan Existing Output For People", self)
        action_people.triggered.connect(self.run_people_scan_now)
        tools_menu.addAction(action_people)
        action_workspace = QAction("Open Full Workspace", self)
        action_workspace.triggered.connect(lambda: self._open_legacy_workspace("Full workspace loaded."))
        tools_menu.addAction(action_workspace)

        help_menu = menu_bar.addMenu("Help")
        action_welcome = QAction("Welcome", self)
        action_welcome.triggered.connect(lambda: self._show_page("welcome"))
        help_menu.addAction(action_welcome)

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("ShellCentral")
        self.setCentralWidget(central)

        title = QLabel("MediaSorter")
        title.setObjectName("LeadText")
        subtitle = QLabel("Step-driven shell with the full sorting workspace behind it")
        subtitle.setObjectName("SubheadText")
        self.status_label = QLabel("Welcome")
        self.status_label.setObjectName("HintText")
        self.status_label.setWordWrap(True)

        nav = QHBoxLayout()
        for key, label in (
            ("welcome", "Welcome"),
            ("folders", "Folders"),
            ("options", "Options"),
            ("run", "Run"),
            ("tools", "Tools"),
            ("workspace", "Workspace"),
        ):
            button = QPushButton(label)
            button.setProperty("navButton", True)
            button.clicked.connect(lambda _=False, name=key: self._show_page(name))
            self._nav_buttons[key] = button
            nav.addWidget(button)
        nav.addStretch(1)

        self.page_stack = QStackedWidget()
        self._pages["welcome"] = self.page_stack.addWidget(self._build_welcome_page())
        self._pages["folders"] = self.page_stack.addWidget(self._build_folders_page())
        self._pages["options"] = self.page_stack.addWidget(self._build_options_page())
        self._pages["run"] = self.page_stack.addWidget(self._build_run_page())
        self._pages["tools"] = self.page_stack.addWidget(self._build_tools_page())
        self._pages["workspace"] = self.page_stack.addWidget(self._build_workspace_page())

        layout = QVBoxLayout()
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(nav)
        layout.addWidget(self.status_label)
        layout.addWidget(self.page_stack, 1)
        central.setLayout(layout)

    def _build_welcome_page(self):
        page = self._page()
        layout = QVBoxLayout(page)
        layout.addWidget(self._title("How MediaSorter Flows"))
        layout.addWidget(self._hint("Choose folders, review options, then start the run. The original full workspace stays available as a page in this shell."))
        row = QHBoxLayout()
        for label, action in (
            ("Choose Folders", lambda: self._show_page("folders")),
            ("Review Options", lambda: self._show_page("options")),
            ("Open Full Workspace", lambda: self._open_legacy_workspace("Full workspace loaded.")),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(action)
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _build_folders_page(self):
        page = self._page()
        layout = QVBoxLayout(page)
        layout.addWidget(self._title("1. Choose Your Folders"))
        self.label_input = QLabel("Not selected yet")
        self.label_input.setObjectName("SelectionValue")
        self.label_input.setWordWrap(True)
        self.label_output = QLabel("Not selected yet")
        self.label_output.setObjectName("SelectionValue")
        self.label_output.setWordWrap(True)
        grid = QGridLayout()
        btn_input = QPushButton("Choose Source Folder")
        btn_input.clicked.connect(self.select_input_folder)
        btn_output = QPushButton("Choose Destination Folder")
        btn_output.clicked.connect(self.select_output_folder)
        grid.addWidget(QLabel("Source library"), 0, 0)
        grid.addWidget(self.label_input, 0, 1)
        grid.addWidget(btn_input, 0, 2)
        grid.addWidget(QLabel("Destination"), 1, 0)
        grid.addWidget(self.label_output, 1, 1)
        grid.addWidget(btn_output, 1, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        row = QHBoxLayout()
        btn_next = QPushButton("Next: Options")
        btn_next.clicked.connect(lambda: self._show_page("options"))
        btn_workspace = QPushButton("Open Full Workspace")
        btn_workspace.clicked.connect(lambda: self._open_legacy_workspace("Full workspace loaded with the selected folders."))
        row.addWidget(btn_next)
        row.addWidget(btn_workspace)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _build_options_page(self):
        page = self._page()
        layout = QVBoxLayout(page)
        layout.addWidget(self._title("2. Choose Sorting Options"))
        box = QGroupBox("Run Options")
        box_layout = QVBoxLayout()
        self.chk_convert_videos = QCheckBox("Convert videos to MP4 with HandBrake")
        self.chk_people = QCheckBox("Group people after sorting finishes")
        for checkbox in (self.chk_convert_videos, self.chk_people):
            checkbox.setEnabled(True)
            checkbox.setStyleSheet(
                """
                QCheckBox {
                    color: #10213e;
                    background: transparent;
                    font-size: 14px;
                    spacing: 8px;
                }
                QCheckBox:disabled {
                    color: #10213e;
                }
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                    border-radius: 4px;
                    border: 1px solid #9fb2cf;
                    background: #ffffff;
                }
                QCheckBox::indicator:checked {
                    background: #0b63ce;
                    border-color: #0b63ce;
                }
                """
            )
        box_layout.addWidget(self.chk_convert_videos)
        box_layout.addWidget(self.chk_people)
        box.setLayout(box_layout)
        layout.addWidget(box)
        row = QHBoxLayout()
        btn_back = QPushButton("Back")
        btn_back.clicked.connect(lambda: self._show_page("folders"))
        btn_next = QPushButton("Next: Run")
        btn_next.clicked.connect(lambda: self._show_page("run"))
        btn_workspace = QPushButton("Open Full Workspace")
        btn_workspace.clicked.connect(lambda: self._open_legacy_workspace("Full workspace loaded with the selected options."))
        row.addWidget(btn_back)
        row.addWidget(btn_next)
        row.addWidget(btn_workspace)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _build_run_page(self):
        page = self._page()
        layout = QVBoxLayout(page)
        layout.addWidget(self._title("3. Run MediaSorter"))
        self.run_summary_label = QLabel("")
        self.run_summary_label.setObjectName("WelcomeText")
        self.run_summary_label.setWordWrap(True)
        layout.addWidget(self.run_summary_label)
        row = QHBoxLayout()
        btn_start = QPushButton("Start Sorting")
        btn_start.setObjectName("PrimaryAction")
        btn_start.clicked.connect(self.start_processing)
        btn_stop = QPushButton("Stop")
        btn_stop.clicked.connect(self.stop_processing)
        btn_people = QPushButton("Scan Existing Output For People")
        btn_people.clicked.connect(self.run_people_scan_now)
        btn_workspace = QPushButton("Open Full Workspace")
        btn_workspace.clicked.connect(lambda: self._open_legacy_workspace("Full workspace loaded."))
        for btn in (btn_start, btn_stop, btn_people, btn_workspace):
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _build_tools_page(self):
        page = self._page()
        layout = QVBoxLayout(page)
        layout.addWidget(self._title("Tools"))
        grid = QGridLayout()
        btn_categories = QPushButton("Manage Categories")
        btn_categories.clicked.connect(self.open_categories)
        btn_people = QPushButton("People Scan")
        btn_people.clicked.connect(self.run_people_scan_now)
        btn_workspace = QPushButton("Open Full Workspace")
        btn_workspace.clicked.connect(lambda: self._open_legacy_workspace("Full workspace loaded."))
        btn_disk = QPushButton("Check Disk Space")
        btn_disk.clicked.connect(self.check_disk_space)
        grid.addWidget(btn_categories, 0, 0)
        grid.addWidget(btn_people, 0, 1)
        grid.addWidget(btn_workspace, 1, 0)
        grid.addWidget(btn_disk, 1, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        return page

    def _build_workspace_page(self):
        page = self._page()
        layout = QVBoxLayout(page)
        layout.addWidget(self._title("Full Workspace"))
        self.workspace_status_label = self._hint("The original full workspace will load here when needed.")
        layout.addWidget(self.workspace_status_label)
        self.legacy_host = QFrame()
        self.legacy_host.setObjectName("LegacyHost")
        host_layout = QVBoxLayout()
        host_layout.setContentsMargins(0, 0, 0, 0)
        self.legacy_host.setLayout(host_layout)
        layout.addWidget(self.legacy_host, 1)
        return page

    def _page(self):
        page = QWidget()
        page.setObjectName("StepPage")
        return page

    def _title(self, text):
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def _hint(self, text):
        label = QLabel(text)
        label.setObjectName("HintText")
        label.setWordWrap(True)
        return label

    def _apply_styles(self):
        self.setStyleSheet(
            """
            QMainWindow#AppRoot, QWidget#ShellCentral { background: #f5f7fb; }
            QLabel#LeadText { font-size: 30px; font-weight: 700; color: #10213e; }
            QLabel#SubheadText { font-size: 16px; font-weight: 600; color: #3d5a80; }
            QLabel#SectionTitle { font-size: 20px; font-weight: 700; color: #10213e; }
            QLabel#HintText { color: #5b6f8f; font-size: 14px; }
            QLabel#WelcomeText { color: #24324a; font-size: 15px; }
            QLabel#SelectionValue { background: #ffffff; border: 1px solid #d6deea; border-radius: 10px; padding: 10px 12px; color: #10213e; font-weight: 600; }
            QWidget#StepPage { background: #ffffff; border: 1px solid #d8e0ec; border-radius: 18px; }
            QFrame#LegacyHost { background: #ffffff; border: 1px solid #d8e0ec; border-radius: 14px; }
            QPushButton { min-height: 36px; padding: 7px 16px; border-radius: 10px; border: 1px solid #cad5e6; background: #ffffff; color: #10213e; font-weight: 600; }
            QPushButton:hover { background: #f7faff; border-color: #b8c7df; }
            QPushButton[currentPage=\"true\"] { background: #e7f0ff; border-color: #8cb9ff; color: #0b63ce; }
            QPushButton#PrimaryAction { background: #0b63ce; border-color: #0b63ce; color: #ffffff; }
            QGroupBox { border: 1px solid #dde3ea; border-radius: 14px; margin-top: 12px; padding: 16px; background: #fbfcfe; color: #24324a; }
            QGroupBox::title { left: 12px; padding: 0 6px; color: #24324a; }
            QCheckBox { color: #24324a; spacing: 8px; background: transparent; }
            QCheckBox:disabled { color: #24324a; }
            QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px; border: 1px solid #b8c7df; background: #ffffff; }
            QCheckBox::indicator:checked { background: #0b63ce; border-color: #0b63ce; }
            QMenuBar { background: #ffffff; border-bottom: 1px solid #d6deea; padding: 4px 8px; }
            QMenuBar::item { color: #24324a; padding: 6px 12px; }
            QMenuBar::item:selected, QMenu::item:selected { background: #e7f0ff; color: #0b63ce; }
            QMenu { background: #ffffff; border: 1px solid #d6deea; color: #162033; }
            """
        )

    def _show_page(self, page_name):
        if page_name == "workspace":
            self._ensure_legacy_workspace()
        index = self._pages.get(page_name)
        if index is None:
            return
        self.page_stack.setCurrentIndex(index)
        for key, button in self._nav_buttons.items():
            button.setProperty("currentPage", key == page_name)
            button.style().unpolish(button)
            button.style().polish(button)
        self._sync_from_legacy()
        self._update_shell_state()

    def _ensure_legacy_workspace(self):
        if self._legacy_window is not None and self._legacy_widget is not None:
            self._sync_shell_to_legacy()
            return
        legacy = LegacyMediaSorter()
        legacy_widget = legacy.takeCentralWidget()
        if legacy_widget is None:
            raise RuntimeError("Legacy MediaSorter workspace failed to initialize.")
        legacy_widget.setParent(self.legacy_host)
        self.legacy_host.layout().addWidget(legacy_widget)
        self._legacy_window = legacy
        self._legacy_widget = legacy_widget
        self._sync_shell_to_legacy()
        self.workspace_status_label.setText("The full legacy workspace is loaded below.")

    def _sync_shell_to_legacy(self):
        if self._legacy_window is None:
            return
        legacy = self._legacy_window
        legacy.input_folder = self.input_folder
        legacy.output_folder = self.output_folder
        legacy.label_input.setText(self.input_folder or "Not selected yet")
        legacy.label_output.setText(self.output_folder or "Not selected yet")
        legacy.chk_convert_videos.setChecked(bool(self.chk_convert_videos.isChecked()))
        legacy.chk_people.setChecked(bool(self.chk_people.isChecked()))

    def _sync_from_legacy(self):
        if self._legacy_window is None:
            return
        legacy = self._legacy_window
        self.input_folder = str(legacy.input_folder or self.input_folder or "")
        self.output_folder = str(legacy.output_folder or self.output_folder or "")
        self.chk_convert_videos.setChecked(bool(legacy.chk_convert_videos.isChecked()))
        self.chk_people.setChecked(bool(legacy.chk_people.isChecked()))

    def _open_legacy_workspace(self, status):
        self._ensure_legacy_workspace()
        self._show_page("workspace")
        self.status_label.setText(status)

    def _update_shell_state(self):
        if hasattr(self, "label_input"):
            self.label_input.setText(self.input_folder or "Not selected yet")
        if hasattr(self, "label_output"):
            self.label_output.setText(self.output_folder or "Not selected yet")
        if hasattr(self, "run_summary_label"):
            source = self.input_folder or "no source selected"
            dest = self.output_folder or "no destination selected"
            options = []
            if self.chk_convert_videos.isChecked():
                options.append("convert videos")
            if self.chk_people.isChecked():
                options.append("group people")
            self.run_summary_label.setText(
                f"Source: {source}\nDestination: {dest}\nOptions: {', '.join(options) if options else 'default options'}"
            )

    def select_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder", self.input_folder or str(Path.home()))
        if folder:
            self.input_folder = folder
            self._update_shell_state()
            self._sync_shell_to_legacy()
            self.status_label.setText("Source folder updated.")
            if self.page_stack.currentIndex() == self._pages.get("welcome"):
                self._show_page("folders")

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_folder or str(Path.home()))
        if folder:
            self.output_folder = folder
            self._update_shell_state()
            self._sync_shell_to_legacy()
            self.status_label.setText("Destination folder updated.")
            if self.page_stack.currentIndex() == self._pages.get("welcome"):
                self._show_page("folders")

    def open_categories(self):
        self._ensure_legacy_workspace()
        self._show_page("workspace")
        self._legacy_window.open_category_manager()
        self.status_label.setText("Category manager opened in the full workspace.")

    def start_processing(self):
        if not self.input_folder:
            self._show_page("folders")
            QMessageBox.information(self, "Choose Source Folder", "Choose the source folder before starting MediaSorter.")
            return
        if not self.output_folder:
            self._show_page("folders")
            QMessageBox.information(self, "Choose Destination Folder", "Choose the destination folder before starting MediaSorter.")
            return
        self._ensure_legacy_workspace()
        self._sync_shell_to_legacy()
        self._show_page("workspace")
        try:
            self._legacy_window._set_focus_view("review")
        except Exception:
            pass
        self.status_label.setText("Starting MediaSorter in the full workspace.")
        self._legacy_window.start_processing()

    def stop_processing(self):
        if self._legacy_window is None:
            self.status_label.setText("Nothing is running.")
            return
        stopped = False
        for attr in ("thread", "model_thread", "provider_install_thread"):
            worker = getattr(self._legacy_window, attr, None)
            if worker is not None and hasattr(worker, "requestInterruption"):
                try:
                    worker.requestInterruption()
                    stopped = True
                except Exception:
                    pass
        self.status_label.setText("Stop requested." if stopped else "No active worker was available to stop.")
        self._show_page("workspace")

    def run_people_scan_now(self):
        if not self.output_folder and self._legacy_window is None:
            self._show_page("folders")
            QMessageBox.information(self, "Choose Destination Folder", "Choose the destination folder before running people scan.")
            return
        self._ensure_legacy_workspace()
        self._sync_shell_to_legacy()
        self._show_page("workspace")
        self.status_label.setText("Running people scan in the full workspace.")
        self._legacy_window.run_people_scan_now()

    def check_disk_space(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Disk Space")
        dialog.resize(760, 520)
        intro = QLabel("Review free space across all available drives. Open the treemap for a drive to inspect it visually.")
        intro.setWordWrap(True)
        intro.setObjectName("HintText")
        content = QWidget()
        content_layout = QVBoxLayout()
        for drive in self._available_drives():
            content_layout.addWidget(self._build_drive_row(drive))
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

    def _available_drives(self):
        drives = []
        letters = [f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{letter}:\\")] if os.name == "nt" else ["/"]
        for root in letters:
            try:
                total, used, free = shutil.disk_usage(root)
            except Exception:
                continue
            drives.append({"path": root, "total": int(total), "used": int(used), "free": int(free)})
        return drives

    def _build_drive_row(self, drive):
        path = str(drive.get("path") or "")
        total = int(drive.get("total") or 0)
        used = int(drive.get("used") or 0)
        free = int(drive.get("free") or 0)
        percent_used = int(round((used / total) * 100)) if total > 0 else 0
        gib = 1024 ** 3
        card = QFrame()
        layout = QVBoxLayout(card)
        title_row = QHBoxLayout()
        title = QLabel(path)
        title.setObjectName("SectionTitle")
        summary = QLabel(f"Free {free / gib:.1f} GiB of {total / gib:.1f} GiB total")
        summary.setObjectName("HintText")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(summary)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(percent_used)
        bar.setFormat(f"{percent_used}% used")
        btn_treemap = QPushButton("Open Treemap")
        btn_treemap.clicked.connect(lambda _=False, p=path: self._open_drive_treemap(p))
        layout.addLayout(title_row)
        layout.addWidget(bar)
        layout.addWidget(btn_treemap, alignment=Qt.AlignLeft)
        return card

    def _open_drive_treemap(self, drive_path, allow_uac_prompt=True):
        if allow_uac_prompt and self._maybe_prompt_for_elevated_ntfs_scan(drive_path):
            return
        TreemapDialog(drive_path, self).exec()

    def _maybe_prompt_for_elevated_ntfs_scan(self, drive_path):
        if os.name != "nt":
            return False
        probe = probe_ntfs_enumerator(drive_path)
        if probe.filesystem.upper() != "NTFS" or probe.enum_usn_ok or int(probe.open_error or 0) != 5:
            return False
        answer = QMessageBox.question(
            self,
            "Administrator Access Needed",
            f"MediaSorter needs Administrator access to use the fast NTFS scan backend for {drive_path}.\n\nDo you want to relaunch this treemap with a UAC prompt?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return answer == QMessageBox.Yes and self._launch_elevated_treemap(drive_path)

    def _launch_elevated_treemap(self, drive_path):
        cli_path = Path(__file__).resolve().with_name("mediasorter_cli.py")
        params = subprocess.list2cmdline([str(cli_path), "--open-treemap", str(drive_path), "--skip-uac-prompt"])
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, str(cli_path.parent), 1)
        if int(result or 0) <= 32:
            QMessageBox.warning(self, "Elevation Failed", f"Windows could not relaunch MediaSorter with Administrator access for {drive_path}.")
            return False
        return True

    def _load_shell_settings(self):
        try:
            if SHELL_SETTINGS_FILE.exists():
                data = json.loads(SHELL_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_shell_settings(self):
        payload = {
            "x": int(self.x()),
            "y": int(self.y()),
            "width": int(self.width()),
            "height": int(self.height()),
            "input_folder": self.input_folder,
            "output_folder": self.output_folder,
        }
        try:
            SHELL_SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _restore_window_settings(self):
        settings = self._load_shell_settings()
        self.input_folder = str(settings.get("input_folder") or "")
        self.output_folder = str(settings.get("output_folder") or "")
        try:
            self.resize(max(int(settings.get("width") or 1280), self.minimumWidth()), max(int(settings.get("height") or 880), self.minimumHeight()))
        except Exception:
            pass
        try:
            if "x" in settings and "y" in settings:
                self.move(int(settings.get("x") or 0), int(settings.get("y") or 0))
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_shell_settings()
        super().closeEvent(event)
