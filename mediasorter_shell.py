import ctypes
import itertools
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
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

import mediasorter_core as core
from mediasorter_ntfs import probe_ntfs_enumerator
from mediasorter_treemap import TreemapDialog
from mediasorter_window import MediaSorter as MediaSorterController

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
        self.files = []
        self._controller = None
        self.thread = None
        self.model_thread = None
        self._pending_start_after_model = False
        self._display_hold_active = False
        self._pending_current_item = None
        self._history_items = {}
        self._history_item_seq = 0
        self._pending_history_entries = []
        self._current_preview_request_id = 0
        self._current_preview_pixmap = QPixmap()
        self._media_load_requests = queue.PriorityQueue()
        self._media_load_results = queue.Queue()
        self._media_request_seq = itertools.count()
        self._media_loader_stop = threading.Event()
        self._media_loader_thread = threading.Thread(target=self._media_loader_loop, daemon=True)
        self._media_loader_thread.start()
        self._display_hold_timer = QTimer(self)
        self._display_hold_timer.setSingleShot(True)
        self._display_hold_timer.timeout.connect(self._release_display_hold)
        self._history_flush_timer = QTimer(self)
        self._history_flush_timer.setInterval(75)
        self._history_flush_timer.timeout.connect(self._flush_history_entries)
        self._media_result_timer = QTimer(self)
        self._media_result_timer.timeout.connect(self._drain_media_load_results)
        self._media_result_timer.start(50)
        self._starting_run = False
        self._pages = {}
        self._nav_buttons = {}

        self._build_menu_bar()
        self._build_ui()
        self._apply_styles()
        self._restore_window_settings()
        self._update_shell_state()
        self._show_page("welcome")
        QTimer.singleShot(200, self._warm_ai_runtime)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_current_preview_pixmap()

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
            ("Classification Log", "Live review shows recent automatic categorizations during a run."),
            ("Current Item", "Live review shows the current file preview during a run."),
            ("Statistics", "Run summary is visible in the live review page during a run."),
        ):
            action = QAction(label, self)
            action.triggered.connect(lambda _=False, text=message: self._open_live_review(text))
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
            ("live", "Live Review"),
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
        self._pages["live"] = self.page_stack.addWidget(self._build_live_review_page())

        layout = QVBoxLayout()
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addLayout(nav)
        layout.addWidget(self.status_label)
        layout.addWidget(self.page_stack, 1)
        central.setLayout(layout)

    def _build_welcome_page(self):
        page = self._page()
        layout = QVBoxLayout(page)
        layout.addWidget(self._title("How MediaSorter Flows"))
        layout.addWidget(self._hint("Choose folders, review options, then start the run. The shell will switch into live review as soon as sorting begins."))
        row = QHBoxLayout()
        for label, action in (
            ("Choose Folders", lambda: self._show_page("folders")),
            ("Review Options", lambda: self._show_page("options")),
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
        row.addWidget(btn_next)
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

        ai_box = QGroupBox("AI Model")
        ai_layout = QGridLayout()
        self.cmb_ai_provider = QComboBox()
        for opt in core.get_ai_provider_options():
            self.cmb_ai_provider.addItem(str(opt.get("label") or opt.get("id")), str(opt.get("id")))
        provider_idx = self.cmb_ai_provider.findData(core.get_ai_provider_id())
        if provider_idx >= 0:
            self.cmb_ai_provider.setCurrentIndex(provider_idx)

        self.cmb_ai_model = QComboBox()
        self.lbl_ai_status = self._hint("")
        self.cmb_ai_provider.currentIndexChanged.connect(self._on_ai_provider_changed)
        self.cmb_ai_model.currentIndexChanged.connect(self._on_ai_model_changed)
        self._refresh_ai_model_options()
        self._update_ai_status_label()

        ai_layout.addWidget(QLabel("Provider"), 0, 0)
        ai_layout.addWidget(self.cmb_ai_provider, 0, 1)
        ai_layout.addWidget(QLabel("Model"), 1, 0)
        ai_layout.addWidget(self.cmb_ai_model, 1, 1)
        ai_layout.addWidget(self.lbl_ai_status, 2, 0, 1, 2)
        ai_box.setLayout(ai_layout)
        layout.addWidget(ai_box)

        row = QHBoxLayout()
        btn_back = QPushButton("Back")
        btn_back.clicked.connect(lambda: self._show_page("folders"))
        btn_next = QPushButton("Next: Run")
        btn_next.clicked.connect(lambda: self._show_page("run"))
        row.addWidget(btn_back)
        row.addWidget(btn_next)
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
        self.btn_start_run = QPushButton("Start Sorting")
        self.btn_start_run.setObjectName("PrimaryAction")
        self.btn_start_run.clicked.connect(self.start_processing)
        self.btn_stop_run = QPushButton("Stop")
        self.btn_stop_run.clicked.connect(self.stop_processing)
        for btn in (self.btn_start_run, self.btn_stop_run):
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
        btn_disk = QPushButton("Check Disk Space")
        btn_disk.clicked.connect(self.check_disk_space)
        grid.addWidget(btn_categories, 0, 0)
        grid.addWidget(btn_people, 0, 1)
        grid.addWidget(btn_disk, 1, 0)
        layout.addLayout(grid)
        layout.addStretch(1)
        return page

    def _build_live_review_page(self):
        page = self._page()
        layout = QVBoxLayout(page)
        layout.addWidget(self._title("Live Review"))
        self.live_panel_status_label = self._hint("Current activity, current file, and recent automatic categorizations appear here while MediaSorter runs.")
        layout.addWidget(self.live_panel_status_label)

        self.live_status_label = QLabel("Status: Ready")
        self.live_status_label.setObjectName("SelectionValue")
        self.live_now_processing_label = QLabel("Now processing: Waiting")
        self.live_now_processing_label.setObjectName("WelcomeText")
        self.live_now_processing_label.setWordWrap(True)
        self.live_progress = QProgressBar()
        self.live_progress.setRange(0, 1)
        self.live_progress.setValue(0)
        self.live_progress.setFormat("Idle")
        activity_box = QGroupBox("Current Activity")
        activity_layout = QVBoxLayout()
        activity_layout.addWidget(self.live_status_label)
        activity_layout.addWidget(self.live_now_processing_label)
        activity_layout.addWidget(self.live_progress)
        activity_box.setLayout(activity_layout)

        self.live_review_status_label = self._hint("Recent automatic categorizations will appear here while MediaSorter is sorting.")
        self.live_review_history = QListWidget()
        self.live_review_history.setMinimumHeight(260)
        self.live_review_history.setIconSize(QSize(56, 56))
        self.live_review_history.setSpacing(6)
        self.live_review_history.itemClicked.connect(self._open_history_item_dialog)
        history_box = QGroupBox("Recent Automatic Categorizations")
        history_layout = QVBoxLayout()
        history_layout.addWidget(self.live_review_status_label)
        history_layout.addWidget(self.live_review_history, 1)
        history_box.setLayout(history_layout)

        self.live_image_label = QLabel("Last completed preview will appear here.")
        self.live_image_label.setAlignment(Qt.AlignCenter)
        self.live_image_label.setWordWrap(True)
        self.live_image_label.setMinimumSize(420, 260)
        self.live_image_filename_label = QLabel("No completed item yet.")
        self.live_image_filename_label.setObjectName("SectionTitle")
        self.live_image_category_label = QLabel("Category: Waiting")
        self.live_image_category_label.setObjectName("WelcomeText")
        self.live_image_explanation_label = self._hint("MediaSorter will hold the last completed item here briefly while the next file continues processing.")
        preview_box = QGroupBox("Last Completed")
        preview_layout = QVBoxLayout()
        preview_layout.setSpacing(6)
        preview_layout.setContentsMargins(12, 12, 12, 12)
        preview_layout.addWidget(self.live_image_label, 1)
        preview_layout.addWidget(self.live_image_filename_label)
        preview_layout.addWidget(self.live_image_category_label)
        preview_layout.addWidget(self.live_image_explanation_label)
        preview_box.setLayout(preview_layout)

        self.live_summary_label = QLabel("")
        self.live_summary_label.setObjectName("WelcomeText")
        self.live_summary_label.setWordWrap(True)
        summary_box = QGroupBox("Run Summary")
        summary_layout = QVBoxLayout()
        summary_layout.addWidget(self.live_summary_label)
        summary_box.setLayout(summary_layout)

        columns = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(activity_box)
        left.addWidget(history_box, 1)
        middle = QVBoxLayout()
        middle.addWidget(preview_box, 1)
        right = QVBoxLayout()
        right.addWidget(summary_box)
        right.addStretch(1)
        columns.addLayout(left, 4)
        columns.addLayout(middle, 4)
        columns.addLayout(right, 3)
        layout.addLayout(columns, 1)
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
        index = self._pages.get(page_name)
        if index is None:
            return
        self.page_stack.setCurrentIndex(index)
        for key, button in self._nav_buttons.items():
            button.setProperty("currentPage", key == page_name)
            button.style().unpolish(button)
            button.style().polish(button)
        self._update_shell_state()

    def _ensure_backend_controller(self):
        if self._controller is not None:
            self._sync_shell_to_controller()
            return
        controller = MediaSorterController()
        controller.hide()
        self._install_controller_hooks(controller)
        self._controller = controller
        self._sync_shell_to_controller()
        self.live_panel_status_label.setText("Live review ready.")

    def _install_controller_hooks(self, controller):
        original_on_model_status = controller.on_model_status
        original_on_model_loaded = controller.on_model_loaded
        original_on_auto_status = controller.on_auto_status
        original_on_current_item_event = controller.on_current_item_event
        original_on_visual_event = controller.on_visual_event
        original_auto_done = controller.auto_done
        original_start_auto_thread = controller.start_auto_thread

        def shell_on_model_status(message):
            original_on_model_status(message)
            status = f"Status: {message}"
            self.status_label.setText(status)
            self.live_status_label.setText(status)
            self.live_panel_status_label.setText("Preparing AI runtime...")

        def shell_on_model_loaded(ok, message):
            original_on_model_loaded(ok, message)
            if ok:
                status = f"Status: Ready - {message}"
                self.status_label.setText(status)
                self.live_status_label.setText(status)
            else:
                status = "Status: AI model failed to load"
                self.status_label.setText(status)
                self.live_status_label.setText(status)

        def shell_on_auto_status(status_text):
            original_on_auto_status(status_text)
            self._on_backend_status(status_text)

        def shell_on_current_item_event(payload):
            original_on_current_item_event(payload)
            self._on_backend_current_item(payload)
            self._sync_live_review_from_backend()

        def shell_on_visual_event(payload):
            original_on_visual_event(payload)
            self._on_backend_visual(payload)
            self._sync_live_review_from_backend()

        def shell_auto_done(counts):
            original_auto_done(counts)
            self._on_backend_done(counts)
            self._sync_live_review_from_backend()

        def shell_start_auto_thread(*args, **kwargs):
            original_start_auto_thread(*args, **kwargs)
            thread = getattr(controller, "thread", None)
            if thread is not None:
                try:
                    thread.progress_signal.connect(self.live_progress.setValue)
                except Exception:
                    pass
            self._sync_live_review_from_backend()

        controller.on_model_status = shell_on_model_status
        controller.on_model_loaded = shell_on_model_loaded
        controller.on_auto_status = shell_on_auto_status
        controller.on_current_item_event = shell_on_current_item_event
        controller.on_visual_event = shell_on_visual_event
        controller.auto_done = shell_auto_done
        controller.start_auto_thread = shell_start_auto_thread

    def _sync_shell_to_controller(self):
        if self._controller is None:
            return
        controller = self._controller
        controller.input_folder = self.input_folder
        controller.output_folder = self.output_folder
        controller.label_input.setText(self.input_folder or "Not selected yet")
        controller.label_output.setText(self.output_folder or "Not selected yet")
        controller.chk_convert_videos.setChecked(bool(self.chk_convert_videos.isChecked()))
        controller.chk_people.setChecked(bool(self.chk_people.isChecked()))
        try:
            provider_id = self._selected_ai_provider_id()
            idx = controller.cmb_ai_provider.findData(provider_id)
            if idx >= 0:
                controller.cmb_ai_provider.setCurrentIndex(idx)
        except Exception:
            pass
        try:
            model_id = self._selected_ai_model_id()
            idx = controller.cmb_ai_model.findData(model_id)
            if idx >= 0:
                controller.cmb_ai_model.setCurrentIndex(idx)
        except Exception:
            pass

    def _open_live_review(self, status):
        self._show_page("live")
        self.status_label.setText(status)

    def _update_shell_state(self):
        options = []
        if hasattr(self, "chk_convert_videos") and self.chk_convert_videos.isChecked():
            options.append("convert videos")
        if hasattr(self, "chk_people") and self.chk_people.isChecked():
            options.append("group people")
        provider_name = core.get_ai_provider_display_name(self._selected_ai_provider_id() if hasattr(self, "cmb_ai_provider") else None)
        model_name = core.get_ai_model_display_name(self._selected_ai_model_id() if hasattr(self, "cmb_ai_model") else None)
        if hasattr(self, "label_input"):
            self.label_input.setText(self.input_folder or "Not selected yet")
        if hasattr(self, "label_output"):
            self.label_output.setText(self.output_folder or "Not selected yet")
        if hasattr(self, "run_summary_label"):
            source = self.input_folder or "no source selected"
            dest = self.output_folder or "no destination selected"
            self.run_summary_label.setText(
                f"Source: {source}\n"
                f"Destination: {dest}\n"
                f"Options: {', '.join(options) if options else 'default options'}\n"
                f"AI Provider: {provider_name}\n"
                f"AI Model: {model_name}"
            )
        if hasattr(self, "live_summary_label"):
            self.live_summary_label.setText(
                f"Source: {self.input_folder or 'no source selected'}\n"
                f"Destination: {self.output_folder or 'no destination selected'}\n"
                f"Options: {', '.join(options) if options else 'default options'}\n"
                f"AI Provider: {provider_name}\n"
                f"AI Model: {model_name}"
            )
        if hasattr(self, "lbl_ai_status"):
            self._update_ai_status_label()

    def _selected_ai_provider_id(self) -> str:
        if not hasattr(self, "cmb_ai_provider"):
            return core.get_ai_provider_id()
        pid = self.cmb_ai_provider.currentData()
        return str(pid or core.get_ai_provider_id())

    def _selected_ai_model_id(self) -> str:
        if not hasattr(self, "cmb_ai_model"):
            return core.get_ai_model_id()
        mid = self.cmb_ai_model.currentData()
        return str(mid or core.get_ai_model_id())

    def _refresh_ai_model_options(self) -> None:
        if not hasattr(self, "cmb_ai_model"):
            return
        provider_id = self._selected_ai_provider_id()
        self.cmb_ai_model.blockSignals(True)
        self.cmb_ai_model.clear()
        for opt in core.get_ai_model_options(provider_id=provider_id):
            self.cmb_ai_model.addItem(str(opt.get("label") or opt.get("id")), str(opt.get("id")))
        if self.cmb_ai_model.count() == 0:
            self.cmb_ai_model.addItem("n/a for selected provider", "")
            self.cmb_ai_model.setEnabled(False)
        else:
            self.cmb_ai_model.setEnabled(True)
            idx = self.cmb_ai_model.findData(core.get_ai_model_id())
            if idx >= 0:
                self.cmb_ai_model.setCurrentIndex(idx)
        self.cmb_ai_model.blockSignals(False)

    def _update_ai_status_label(self) -> None:
        if not hasattr(self, "lbl_ai_status"):
            return
        provider_id = self._selected_ai_provider_id()
        provider_name = core.get_ai_provider_display_name(provider_id)
        installed = core.is_ai_provider_installed(provider_id)
        model_name = core.get_ai_model_display_name(self._selected_ai_model_id()) if provider_id == core.AI_PROVIDER_CLIP_LOCAL else "n/a"
        state = "installed" if installed else "not installed"
        self.lbl_ai_status.setText(f"Provider: {provider_name} ({state}) | Model: {model_name}")

    def _on_ai_provider_changed(self) -> None:
        provider_id = self._selected_ai_provider_id()
        try:
            core.set_ai_provider(provider_id)
        except Exception as exc:
            QMessageBox.warning(self, "AI Provider", str(exc))
            return
        self._refresh_ai_model_options()
        self._update_shell_state()
        self._sync_shell_to_controller()
        self._save_shell_settings()

    def _on_ai_model_changed(self) -> None:
        provider_id = self._selected_ai_provider_id()
        if provider_id != core.AI_PROVIDER_CLIP_LOCAL:
            self._update_shell_state()
            return
        model_id = self._selected_ai_model_id()
        if not model_id:
            self._update_shell_state()
            return
        try:
            core.set_ai_model_profile(model_id)
        except Exception as exc:
            QMessageBox.warning(self, "AI Model", str(exc))
            return
        self._update_shell_state()
        self._sync_shell_to_controller()
        self._save_shell_settings()

    def select_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder", self.input_folder or str(Path.home()))
        if folder:
            self.input_folder = folder
            self._update_shell_state()
            self._sync_shell_to_controller()
            self.status_label.setText("Source folder updated.")
            if self.page_stack.currentIndex() == self._pages.get("welcome"):
                self._show_page("folders")

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.output_folder or str(Path.home()))
        if folder:
            self.output_folder = folder
            self._update_shell_state()
            self._sync_shell_to_controller()
            self.status_label.setText("Destination folder updated.")
            if self.page_stack.currentIndex() == self._pages.get("welcome"):
                self._show_page("folders")

    def open_categories(self):
        self._ensure_backend_controller()
        self._show_page("live")
        self._controller.open_category_manager()
        self.status_label.setText("Category manager opened.")

    def start_processing(self):
        if self._starting_run:
            return
        if not self.input_folder:
            self._show_page("folders")
            QMessageBox.information(self, "Choose Source Folder", "Choose the source folder before starting MediaSorter.")
            return
        if not self.output_folder:
            self._show_page("folders")
            QMessageBox.information(self, "Choose Destination Folder", "Choose the destination folder before starting MediaSorter.")
            return
        self._starting_run = True
        if hasattr(self, "btn_start_run"):
            self.btn_start_run.setEnabled(False)
        self._show_page("live")
        self._reset_live_review()
        self.live_panel_status_label.setText("Live review active.")
        self.status_label.setText("Starting MediaSorter.")
        QTimer.singleShot(0, self._begin_start_processing)

    def _begin_start_processing(self):
        try:
            if core._MODEL_LOAD_ERROR:
                QMessageBox.critical(self, "AI Model Error", core._MODEL_LOAD_ERROR)
                return

            if not core._MODEL_READY:
                self._pending_start_after_model = True
                self.live_status_label.setText("Status: Loading AI runtime")
                self.live_panel_status_label.setText("Preparing AI runtime...")
                self.status_label.setText("Loading AI runtime before sorting.")
                self._start_model_load()
                return

            self._begin_sort_run()
        finally:
            self._starting_run = False
            if hasattr(self, "btn_start_run"):
                self.btn_start_run.setEnabled(True)

    def _start_model_load(self):
        try:
            if self.model_thread is not None and self.model_thread.isRunning():
                return
        except RuntimeError:
            self.model_thread = None
        self.model_thread = core.ModelLoadThread()
        self.model_thread.status_signal.connect(self._on_backend_status)
        self.model_thread.done_signal.connect(self._on_model_loaded)
        self.model_thread.start()

    def _on_model_loaded(self, ok, message):
        if ok:
            self.status_label.setText(f"Status: Ready - {message}")
            self.live_status_label.setText(f"Status: Ready - {message}")
            self.live_panel_status_label.setText("AI runtime ready.")
            if self._pending_start_after_model:
                self._pending_start_after_model = False
                QTimer.singleShot(0, self._begin_sort_run)
            return
        self._pending_start_after_model = False
        self.status_label.setText("Status: AI model failed to load")
        self.live_status_label.setText("Status: AI model failed to load")
        self.live_panel_status_label.setText("AI runtime failed to load.")
        QMessageBox.critical(self, "AI Model Error", str(message or "Unknown AI model error."))

    def _warm_ai_runtime(self):
        if core._MODEL_READY or core._MODEL_LOAD_ERROR:
            return
        try:
            if self.model_thread is not None and self.model_thread.isRunning():
                return
        except RuntimeError:
            self.model_thread = None
        self.status_label.setText("Preparing AI runtime in the background.")
        self.live_status_label.setText("Status: Preparing AI runtime")
        self.live_panel_status_label.setText("Preparing AI runtime...")
        self._start_model_load()

    def _begin_sort_run(self):
        try:
            self.files = [
                name
                for name in os.listdir(self.input_folder)
                if name.lower().endswith(core.IMAGE_EXT + core.VIDEO_EXT)
            ]
        except Exception as exc:
            QMessageBox.critical(self, "Input Folder Error", f"Unable to scan input folder:\n{exc}")
            self.status_label.setText("Status: Input scan failed")
            self.live_status_label.setText("Status: Input scan failed")
            return

        if not self.files:
            QMessageBox.information(self, "Empty Folder", "No media files found.")
            self.status_label.setText("Status: No media files found")
            self.live_status_label.setText("Status: No media files found")
            return

        self._reset_live_review()
        total = len(self.files)
        self.live_progress.setRange(0, total)
        self.live_progress.setValue(0)
        self.live_progress.setFormat("%v/%m")
        self.status_label.setText(f"Status: Found {total} media files")
        self.live_status_label.setText(f"Status: Found {total} media files")
        self.live_panel_status_label.setText("Live review active.")

        self.thread = core.AutoProcessThread(
            self.files,
            self.input_folder,
            self.output_folder,
            convert_videos=self.chk_convert_videos.isChecked(),
            start_index=0,
            structure_pattern="{category}",
            enable_people=self.chk_people.isChecked(),
        )
        self.thread.progress_signal.connect(self.live_progress.setValue)
        self.thread.status_signal.connect(self._on_backend_status)
        self.thread.current_item_signal.connect(self._on_backend_current_item)
        self.thread.visual_signal.connect(self._on_backend_visual)
        self.thread.done_signal.connect(self._on_backend_done)
        self.thread.start()

    def stop_processing(self):
        if self._controller is None and self.thread is None and self.model_thread is None:
            self.status_label.setText("Nothing is running.")
            return
        stopped = False
        for worker in (self.thread, self.model_thread):
            if worker is not None and hasattr(worker, "requestInterruption"):
                try:
                    worker.requestInterruption()
                    stopped = True
                except Exception:
                    pass
        controller = self._controller
        for attr in ("thread", "model_thread", "provider_install_thread"):
            worker = getattr(controller, attr, None) if controller is not None else None
            if worker is not None and hasattr(worker, "requestInterruption"):
                try:
                    worker.requestInterruption()
                    stopped = True
                except Exception:
                    pass
        self.status_label.setText("Stop requested." if stopped else "No active worker was available to stop.")
        self._show_page("live")

    def run_people_scan_now(self):
        if not self.output_folder and self._controller is None:
            self._show_page("folders")
            QMessageBox.information(self, "Choose Destination Folder", "Choose the destination folder before running people scan.")
            return
        self._ensure_backend_controller()
        self._sync_shell_to_controller()
        self._show_page("live")
        self.status_label.setText("Running people scan.")
        self._controller.run_people_scan_now()

    def _reset_live_review(self):
        self.live_progress.setRange(0, 1)
        self.live_progress.setValue(0)
        self.live_progress.setFormat("Idle")
        self.live_status_label.setText("Status: Preparing run")
        self.live_now_processing_label.setText("Now processing: Waiting")
        self.live_review_history.clear()
        self._history_items.clear()
        self._pending_history_entries.clear()
        self._history_flush_timer.stop()
        self.live_review_status_label.setText("Recent automatic categorizations will appear here while MediaSorter is sorting.")
        self._display_hold_active = False
        self._pending_current_item = None
        self._display_hold_timer.stop()
        self._current_preview_pixmap = QPixmap()
        self.live_image_label.setPixmap(QPixmap())
        self.live_image_label.setText("Last completed preview will appear here.")
        self.live_image_filename_label.setText("Waiting for the first completed item")
        self.live_image_category_label.setText("Category: Waiting")
        self.live_image_explanation_label.setText("MediaSorter will hold the last completed item here briefly while the next file continues processing.")

    def _sync_live_review_from_backend(self):
        controller = self._controller
        if controller is None:
            return
        try:
            self.live_status_label.setText(str(controller.status_label.text() or self.live_status_label.text() or ""))
        except Exception:
            pass
        try:
            self.live_progress.setRange(int(controller.progress.minimum()), int(controller.progress.maximum()))
            self.live_progress.setValue(int(controller.progress.value()))
            self.live_progress.setFormat(str(controller.progress.format() or "%v/%m"))
        except Exception:
            pass
        try:
            self.live_review_status_label.setText(str(controller.review_status_label.text() or self.live_review_status_label.text() or ""))
        except Exception:
            pass
        try:
            self.live_image_filename_label.setText(str(controller.image_filename_label.text() or self.live_image_filename_label.text() or ""))
            self.live_image_explanation_label.setText(str(controller.image_explanation_label.text() or self.live_image_explanation_label.text() or ""))
        except Exception:
            pass
        try:
            pixmap = getattr(controller, "_current_preview_pixmap", None)
            if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.live_image_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                self.live_image_label.setText("")
                self.live_image_label.setPixmap(scaled)
            else:
                text = str(controller.image_label.text() or "")
                self.live_image_label.setPixmap(QPixmap())
                self.live_image_label.setText(text or "Current file preview will appear here.")
        except Exception:
            pass

    def _on_backend_current_item(self, payload):
        entry = payload or {}
        if self._display_hold_active:
            self._pending_current_item = dict(entry)
            self._update_now_processing(entry)
            return
        phase = str(entry.get("phase") or "").strip().lower()
        self._update_now_processing(entry)
        if phase == "skipping":
            return

    def _update_now_processing(self, entry):
        source_path = str((entry or {}).get("source_path") or "")
        category = str((entry or {}).get("category") or "Uncategorized")
        explanation = str((entry or {}).get("explanation") or "")
        phase = str((entry or {}).get("phase") or "").strip().lower()
        name = os.path.basename(source_path) or source_path or "Unknown file"
        if phase == "skipping":
            self.live_now_processing_label.setText(f"Now processing: {name} (skip check)")
            return
        if phase == "starting":
            self.live_now_processing_label.setText(f"Now processing: {name}")
            return
        suffix = f" | {category}" if category and category != "Analyzing..." else ""
        if explanation:
            self.live_now_processing_label.setText(f"Now processing: {name}{suffix}")
        else:
            self.live_now_processing_label.setText(f"Now processing: {name}{suffix}")

    def _on_backend_status(self, text):
        status = f"Status: {text}"
        self.status_label.setText(status)
        self.live_status_label.setText(status)
        try:
            self.live_progress.setFormat("%v/%m")
        except Exception:
            pass

    def _on_backend_visual(self, payload):
        entry = payload or {}
        source_path = str(entry.get("source_path") or "")
        category = str(entry.get("category") or "Uncategorized")
        explanation = str(entry.get("explanation") or "")
        explanation_source = str(entry.get("explanation_source") or "")
        name = os.path.basename(source_path) or source_path or "Unknown file"
        history_entry = {
            "source_path": source_path,
            "dest_path": str(entry.get("dest_path") or ""),
            "auto_category": category,
            "current_category": category,
            "is_video": bool(entry.get("is_video")),
            "explanation": explanation,
            "explanation_source": explanation_source,
        }
        self._pending_history_entries.append((name, history_entry))
        if not self._history_flush_timer.isActive():
            self._history_flush_timer.start()
        try:
            if explanation_source == "previous_run_reuse":
                self.live_review_status_label.setText(
                    f"{self.live_review_history.count() + len(self._pending_history_entries)} items reviewed automatically so far."
                )
                return
            self.live_image_filename_label.setText(name)
            self.live_image_category_label.setText(f"Category: {category}")
            self.live_image_explanation_label.setText(explanation or f"Sorted into {category}.")
            if bool(entry.get("is_video")):
                self._current_preview_pixmap = QPixmap()
                self.live_image_label.setPixmap(QPixmap())
                self.live_image_label.setText("Video preview unavailable.\nMediaSorter is processing this video now.")
            else:
                self._current_preview_pixmap = QPixmap()
                self.live_image_label.setPixmap(QPixmap())
                self.live_image_label.setText("Loading preview...")
                self._queue_current_preview(source_path)
        except Exception:
            self._current_preview_pixmap = QPixmap()
            self.live_image_label.setPixmap(QPixmap())
            self.live_image_label.setText("Preview unavailable for this file.")
        self._display_hold_active = True
        self._pending_current_item = None
        self._display_hold_timer.start(5000)

    def _on_backend_done(self, counts):
        self.status_label.setText("Status: Complete")
        self.live_status_label.setText("Status: Complete")
        try:
            images = int((counts or {}).get("images") or 0)
            videos = int((counts or {}).get("videos") or 0)
            failed = int((counts or {}).get("failed") or 0)
            skipped = int((counts or {}).get("skipped") or 0)
            total = images + videos + failed + skipped
            self.live_progress.setRange(0, max(1, total))
            self.live_progress.setValue(max(0, total))
            self.live_progress.setFormat("%v/%m Done")
            self.live_summary_label.setText(
                f"Source: {self.input_folder or 'no source selected'}\n"
                f"Destination: {self.output_folder or 'no destination selected'}\n"
                f"Processed images: {images}\n"
                f"Processed videos: {videos}\n"
                f"Skipped existing: {skipped}\n"
                f"Failed: {failed}"
            )
        except Exception:
            pass
        self.live_panel_status_label.setText("Run complete.")

    def _release_display_hold(self):
        self._display_hold_active = False
        pending = self._pending_current_item
        self._pending_current_item = None
        if pending:
            self._on_backend_current_item(pending)

    def _flush_history_entries(self):
        if not self._pending_history_entries:
            self._history_flush_timer.stop()
            return
        batch_size = 24
        for _ in range(min(batch_size, len(self._pending_history_entries))):
            name, history_entry = self._pending_history_entries.pop(0)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, history_entry)
            item.setSizeHint(QSize(0, 86))
            self.live_review_history.addItem(item)

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(10)

            thumb = QLabel()
            thumb.setFixedSize(56, 56)
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setText("")

            text_box = QWidget()
            text_layout = QVBoxLayout(text_box)
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(2)

            title_label = QLabel(name)
            title_label.setStyleSheet("font-weight: 600; color: #ffffff;")
            category_label = QLabel(str(history_entry.get("current_category") or "Uncategorized"))
            category_label.setStyleSheet("color: #ffffff;")
            explanation_text = str(history_entry.get("explanation") or "").strip()
            if len(explanation_text) > 80:
                explanation_text = explanation_text[:77] + "..."
            explanation_label = QLabel(explanation_text)
            explanation_label.setWordWrap(True)
            explanation_label.setStyleSheet("color: #ffffff;")

            text_layout.addWidget(title_label)
            text_layout.addWidget(category_label)
            text_layout.addWidget(explanation_label)

            row_layout.addWidget(thumb)
            row_layout.addWidget(text_box, 1)

            self.live_review_history.setItemWidget(item, row_widget)
            history_id = self._history_item_seq
            self._history_item_seq += 1
            self._history_items[history_id] = {"item": item, "thumb": thumb}
            if (not bool(history_entry.get("is_video"))) and str(history_entry.get("source_path") or "").strip():
                self._queue_history_thumbnail(history_id, str(history_entry.get("source_path") or ""))

        self.live_review_history.scrollToBottom()
        count = self.live_review_history.count() + len(self._pending_history_entries)
        self.live_review_status_label.setText(
            f"{count} item{'s' if count != 1 else ''} reviewed automatically so far."
        )
        if not self._pending_history_entries:
            self._history_flush_timer.stop()

    def _history_thumbnail(self, entry):
        try:
            source_path = str((entry or {}).get("source_path") or "")
            if not source_path or bool((entry or {}).get("is_video")):
                return QPixmap()
            img = core.load_image_for_ai(source_path)
            if img is None:
                return QPixmap()
            pixmap = core.pil_to_qpixmap(img, max_size=(96, 96))
            return pixmap
        except Exception:
            return QPixmap()

    def _dialog_preview_pixmap(self, entry):
        try:
            source_path = str((entry or {}).get("source_path") or "")
            if not source_path or bool((entry or {}).get("is_video")):
                return QPixmap()
            img = core.load_image_for_ai(source_path)
            if img is None:
                return QPixmap()
            return core.pil_to_qpixmap(img, max_size=(1800, 1800))
        except Exception:
            return QPixmap()

    def _open_history_item_dialog(self, item):
        if item is None:
            return
        entry = item.data(Qt.UserRole)
        if not isinstance(entry, dict):
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Review Categorization")
        dialog.resize(760, 640)

        preview = QLabel()
        preview.setMinimumSize(520, 360)
        preview.setAlignment(Qt.AlignCenter)
        file_name = os.path.basename(str(entry.get("source_path") or "")) or "Unknown file"
        category = str(entry.get("current_category") or entry.get("auto_category") or "Uncategorized")
        title = QLabel(file_name)
        title.setObjectName("SectionTitle")
        category_label = QLabel(f"Category: {category}")
        category_label.setObjectName("WelcomeText")
        explanation = QLabel(str(entry.get("explanation") or "Choose a different category if needed."))
        explanation.setWordWrap(True)
        explanation.setObjectName("HintText")

        if bool(entry.get("is_video")):
            preview.setText("Video preview unavailable.")
        else:
            pixmap = self._dialog_preview_pixmap(entry)
            if pixmap is not None and not pixmap.isNull():
                preview.setPixmap(pixmap.scaled(preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                preview.setText("Preview unavailable.")

        combo = QComboBox()
        combo.addItems(core.CATEGORIES)
        idx = combo.findText(category, Qt.MatchFixedString | Qt.MatchCaseSensitive)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.setEnabled(not bool(entry.get("is_video")))

        btn_apply = QPushButton("Apply Category")
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dialog.accept)

        def apply_override():
            new_category = str(combo.currentText() or "").strip()
            current_category = str(entry.get("current_category") or entry.get("auto_category") or "")
            if not new_category or new_category == current_category or bool(entry.get("is_video")):
                dialog.accept()
                return
            try:
                result = core.apply_live_category_override(
                    source_path=str(entry.get("source_path") or ""),
                    current_dest_path=str(entry.get("dest_path") or ""),
                    new_category=new_category,
                    output_folder=self.output_folder,
                    structure_pattern="{category}",
                    previous_category=current_category,
                )
            except Exception as exc:
                QMessageBox.warning(dialog, "Category Override Failed", str(exc))
                return

            entry["current_category"] = str(result.get("category") or new_category)
            entry["dest_path"] = str(result.get("dest_path") or entry.get("dest_path") or "")
            entry["explanation"] = str(result.get("explanation") or entry.get("explanation") or "")
            item.setData(Qt.UserRole, entry)
            item.setText(
                f"{file_name}\n{entry.get('current_category')}\n{str(entry.get('explanation') or '').strip()}".strip()
            )
            self.live_review_status_label.setText(
                f'Updated "{file_name}" to "{entry.get("current_category")}".'
            )
            dialog.accept()

        btn_apply.clicked.connect(apply_override)

        button_row = QHBoxLayout()
        button_row.addWidget(combo, 1)
        button_row.addWidget(btn_apply)
        button_row.addWidget(btn_close)

        layout = QVBoxLayout(dialog)
        layout.addWidget(title)
        layout.addWidget(category_label)
        layout.addWidget(explanation)
        layout.addWidget(preview, 1)
        layout.addLayout(button_row)
        dialog.exec()

    def _queue_current_preview(self, source_path):
        path = str(source_path or "").strip()
        if not path:
            return
        self._current_preview_request_id += 1
        self._media_load_requests.put(
            (
                0,
                next(self._media_request_seq),
                {
                "kind": "current",
                "request_id": self._current_preview_request_id,
                "source_path": path,
                },
            )
        )

    def _queue_history_thumbnail(self, history_id, source_path):
        path = str(source_path or "").strip()
        if not path:
            return
        self._media_load_requests.put(
            (
                1,
                next(self._media_request_seq),
                {
                "kind": "history",
                "history_id": int(history_id),
                "source_path": path,
                },
            )
        )

    def _media_loader_loop(self):
        while not self._media_loader_stop.is_set():
            try:
                _, _, request = self._media_load_requests.get(timeout=0.1)
            except queue.Empty:
                continue
            path = str((request or {}).get("source_path") or "")
            result = dict(request or {})
            image = None
            if path:
                try:
                    image = core.load_image_for_ai(path)
                except Exception:
                    image = None
            result["image"] = image
            self._media_load_results.put(result)

    def _drain_media_load_results(self):
        while True:
            try:
                result = self._media_load_results.get_nowait()
            except queue.Empty:
                break

            kind = str((result or {}).get("kind") or "")
            image = result.get("image")

            if kind == "current":
                request_id = int((result or {}).get("request_id") or 0)
                if request_id != self._current_preview_request_id:
                    continue
                if image is None:
                    self._current_preview_pixmap = QPixmap()
                    self.live_image_label.setPixmap(QPixmap())
                    self.live_image_label.setText("Preview unavailable for this file.")
                    continue
                try:
                    pixmap = core.pil_to_qpixmap(image, max_size=(900, 900))
                    self._current_preview_pixmap = pixmap
                    self._apply_current_preview_pixmap()
                except Exception:
                    self._current_preview_pixmap = QPixmap()
                    self.live_image_label.setPixmap(QPixmap())
                    self.live_image_label.setText("Preview unavailable for this file.")
                continue

            if kind == "history":
                history_id = int((result or {}).get("history_id") or -1)
                row = self._history_items.get(history_id)
                if not isinstance(row, dict) or image is None:
                    continue
                try:
                    pixmap = core.pil_to_qpixmap(image, max_size=(56, 56))
                    if not pixmap.isNull():
                        thumb = row.get("thumb")
                        if isinstance(thumb, QLabel):
                            thumb.setPixmap(
                                pixmap.scaled(
                                    thumb.size(),
                                    Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation,
                                )
                            )
                except Exception:
                    continue

    def _apply_current_preview_pixmap(self):
        pixmap = self._current_preview_pixmap
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            return
        target_size = self.live_image_label.size()
        if target_size.width() < 32 or target_size.height() < 32:
            target_size = self.live_image_label.minimumSize()
        scaled = pixmap.scaled(
            target_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        if scaled.isNull():
            self.live_image_label.setPixmap(QPixmap())
            self.live_image_label.setText("Preview unavailable for this file.")
            return
        self.live_image_label.setText("")
        self.live_image_label.setPixmap(scaled)

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
            "ai_provider": self._selected_ai_provider_id() if hasattr(self, "cmb_ai_provider") else core.get_ai_provider_id(),
            "ai_model": self._selected_ai_model_id() if hasattr(self, "cmb_ai_model") else core.get_ai_model_id(),
        }
        try:
            SHELL_SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _restore_window_settings(self):
        settings = self._load_shell_settings()
        self.input_folder = str(settings.get("input_folder") or "")
        self.output_folder = str(settings.get("output_folder") or "")
        saved_provider = str(settings.get("ai_provider") or "").strip()
        if saved_provider:
            try:
                core.set_ai_provider(saved_provider)
            except Exception:
                pass
        saved_model = str(settings.get("ai_model") or "").strip()
        if saved_model:
            try:
                core.set_ai_model_profile(saved_model)
            except Exception:
                pass
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
