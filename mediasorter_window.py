import os
import re
import shutil

import numpy as np
from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mediasorter_core import (
    AutoProcessThread,
    CATEGORIES_FILE,
    CORRECTION_FILE,
    IMAGE_EXT,
    ModelLoadThread,
    ProviderInstallThread,
    PAYPAL_LINK,
    VIDEO_EXT,
    _atomic_write_json,
    _atomic_write_text,
    _log_line,
    _predict_category_internal,
    _refresh_proto_features,
    _refresh_text_features,
    _render_structure,
    _save_prototypes,
    _structure_tokens,
    _unique_dest_path,
    _update_prototype,
    convert_video,
    hash_image,
    load_image_for_ai,
    pil_to_qpixmap,
)
import mediasorter_core as core
from mediasorter_widgets import PeopleReviewDialog, SortingStacksView
class MediaSorter(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MediaSorter Hybrid")
        self.resize(700,600)
        self.input_folder = ""
        self.output_folder = ""
        self.files = []
        self.index = 0
        self.interactive_mode = False
        self.current_path = ""
        self.current_embedding = None
        self.current_img = None
        self._heic_warning_shown = False

        # Folder structure presets (user can also type a custom pattern).
        self._structure_presets = [
            ("Category", "{category}"),
            ("Category/Year", "{category}/{year}"),
            ("Category/Year/Month", "{category}/{year}/{month}"),
            ("Category/YearMonth", "{category}/{yearmonth}"),
            ("Year/Category", "{year}/{category}"),
            ("Year/Month/Category", "{year}/{month}/{category}"),
            ("YearMonth/Category", "{yearmonth}/{category}"),
            ("Location/Category", "{location}/{category}"),
            ("Category/Location", "{category}/{location}"),
            ("Year/Location/Category", "{year}/{location}/{category}"),
            ("Year/Month/Location/Category", "{year}/{month}/{location}/{category}"),
            ("Custom...", ""),  # enables text box
        ]

        # Folder selectors
        self.label_input = QLabel("Input Folder: Not selected")
        self.label_output = QLabel("Output Folder: Not selected")
        btn_input = QPushButton("Select Input Folder")
        btn_output = QPushButton("Select Output Folder")
        btn_input.clicked.connect(self.select_input)
        btn_output.clicked.connect(self.select_output)

        folder_layout = QVBoxLayout()
        folder_layout.addWidget(self.label_input)
        folder_layout.addWidget(btn_input)
        folder_layout.addWidget(self.label_output)
        folder_layout.addWidget(btn_output)
        folder_box = QGroupBox("Folders")
        folder_box.setLayout(folder_layout)

        # Options
        self.chk_convert_videos = QCheckBox("Convert Videos to MP4 using HandBrake")
        # Default off: video conversion can add hours/days; copying videos is the default behavior.
        self.chk_convert_videos.setChecked(False)
        self.chk_interactive = QCheckBox("Interactive Mode")
        self.chk_interactive.setChecked(False)
        self.chk_people = QCheckBox("Identify People After Run (faces)")
        self.chk_people.setChecked(False)
        self.btn_people_scan_now = QPushButton("Run Face Scan On Output")
        self.btn_people_scan_now.clicked.connect(self.run_people_scan_now)
        self.chk_sortviz = QCheckBox("Show Stacks Animation (Batch Mode)")
        self.chk_sortviz.setChecked(True)
        self.cmb_ai_provider = QComboBox()
        for opt in core.get_ai_provider_options():
            self.cmb_ai_provider.addItem(str(opt.get("label") or opt.get("id")), str(opt.get("id")))
        ai_idx = self.cmb_ai_provider.findData(core.get_ai_provider_id())
        if ai_idx >= 0:
            self.cmb_ai_provider.setCurrentIndex(ai_idx)
        self.cmb_ai_model = QComboBox()
        for opt in core.get_ai_model_options(provider_id=core.AI_PROVIDER_CLIP_LOCAL):
            self.cmb_ai_model.addItem(str(opt.get("label") or opt.get("id")), str(opt.get("id")))
        model_idx = self.cmb_ai_model.findData(core.get_ai_model_id())
        if model_idx >= 0:
            self.cmb_ai_model.setCurrentIndex(model_idx)
        self.lbl_ai_provider = QLabel("")
        self.lbl_ai_model = QLabel("")
        self.btn_install_ai_provider = QPushButton("Install Selected AI Provider")
        self.btn_install_ai_provider.clicked.connect(self.install_selected_ai_provider)
        self.cmb_ai_provider.currentIndexChanged.connect(self.on_ai_provider_changed)
        self.cmb_ai_model.currentIndexChanged.connect(self.on_ai_model_changed)
        self.cmb_structure = QComboBox()
        self.cmb_structure.addItems([name for (name, _) in self._structure_presets])
        self.cmb_structure.setCurrentIndex(0)
        self.edit_structure = QLineEdit()
        self.edit_structure.setPlaceholderText("Custom structure, e.g. {category}/{yearmo}/{location}")
        self.edit_structure.setEnabled(False)
        self.lbl_structure_preview = QLabel("Structure preview: (not set)")
        self.lbl_structure_help = QLabel("Tokens: {category} {year} {month} {yearmonth} {yearmo} {location}")

        btn_manage_categories = QPushButton("Manage Categories")
        btn_manage_categories.clicked.connect(self.open_category_manager)
        btn_forget = QPushButton("Forget My Last Classifications")
        btn_forget.clicked.connect(self.forget_last_classifications)
        btn_open_decision_log = QPushButton("Open Decision Log")
        btn_open_decision_log.clicked.connect(self.open_decision_log)

        options_layout = QVBoxLayout()
        options_layout.addWidget(self.chk_convert_videos)
        options_layout.addWidget(self.chk_interactive)
        options_layout.addWidget(self.chk_people)
        options_layout.addWidget(self.btn_people_scan_now)
        options_layout.addWidget(self.chk_sortviz)
        options_layout.addWidget(QLabel("AI Provider:"))
        options_layout.addWidget(self.cmb_ai_provider)
        options_layout.addWidget(QLabel("AI Model:"))
        options_layout.addWidget(self.cmb_ai_model)
        options_layout.addWidget(self.lbl_ai_provider)
        options_layout.addWidget(self.lbl_ai_model)
        options_layout.addWidget(self.btn_install_ai_provider)
        options_layout.addWidget(QLabel("Folder Structure:"))
        options_layout.addWidget(self.cmb_structure)
        options_layout.addWidget(self.edit_structure)
        options_layout.addWidget(self.lbl_structure_preview)
        options_layout.addWidget(self.lbl_structure_help)
        options_layout.addWidget(btn_manage_categories)
        options_layout.addWidget(btn_forget)
        options_layout.addWidget(btn_open_decision_log)
        options_box = QGroupBox("Options")
        options_box.setLayout(options_layout)

        # Sorting visualization (prototype)
        self.sortviz = SortingStacksView()
        self.sortviz_box = QGroupBox("Stacks")
        sortviz_layout = QVBoxLayout()
        sortviz_layout.addWidget(self.sortviz)
        self.sortviz_box.setLayout(sortviz_layout)
        self.sortviz_box.setVisible(bool(self.chk_sortviz.isChecked()))
        def _toggle_sortviz():
            on = bool(self.chk_sortviz.isChecked())
            self.sortviz_box.setVisible(on)
            try:
                # Only run animation during processing.
                if hasattr(self, "thread") and getattr(self, "thread", None) is not None and self.thread.isRunning():
                    self.sortviz.set_running(on)
                elif not on:
                    self.sortviz.set_running(False)
            except Exception:
                pass
        self.chk_sortviz.stateChanged.connect(lambda _: _toggle_sortviz())

        # Image preview + category selection
        self.image_label = QLabel("Image preview will appear here")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.combo_category = QComboBox()
        self.combo_category.addItems(core.CATEGORIES)
        self.btn_confirm = QPushButton("Confirm")
        self.btn_confirm.clicked.connect(self.confirm_category)
        self.btn_skip = QPushButton("Dismiss and auto-sort")
        self.btn_skip.clicked.connect(self.dismiss_interactive)
        self.combo_category.setEnabled(False)
        self.btn_confirm.setEnabled(False)
        self.btn_skip.setEnabled(False)

        image_layout = QVBoxLayout()
        image_layout.addWidget(self.image_label)
        image_layout.addWidget(QLabel("Select Category:"))
        image_layout.addWidget(self.combo_category)
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.btn_confirm)
        btn_layout.addWidget(self.btn_skip)
        image_layout.addLayout(btn_layout)
        image_box = QGroupBox("Interactive Categorization")
        image_box.setLayout(image_layout)

        # Progress
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate while the AI model loads
        self.progress.setTextVisible(True)
        self.progress.setFormat("Loading AI model...")
        self.status_label = QLabel("Status: Loading AI model...")

        # Bottom buttons
        self.btn_start = QPushButton("Start Processing")
        self.btn_start.clicked.connect(self.start_processing)
        self.btn_start.setEnabled(False)
        btn_help = QPushButton("Help")
        btn_help.clicked.connect(self.show_help)
        btn_donate = QPushButton("Donate ❤️")
        btn_donate.clicked.connect(self.open_paypal)
        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(self.btn_start)
        bottom_layout.addWidget(btn_help)
        bottom_layout.addWidget(btn_donate)

        # Main layout
        main_layout = QVBoxLayout()
        main_layout.addWidget(folder_box)
        main_layout.addWidget(options_box)
        main_layout.addWidget(self.sortviz_box)
        main_layout.addWidget(image_box)
        main_layout.addWidget(self.status_label)
        main_layout.addWidget(self.progress)
        main_layout.addLayout(bottom_layout)
        self.setLayout(main_layout)

        self.model_thread = None
        self.provider_install_thread = None
        self._people_scan_only = False

        # Optional: autorun without user clicks (useful for long-running jobs).
        self.autorun_input = os.environ.get("MEDIASORTER_AUTORUN_INPUT")
        self.autorun_output = os.environ.get("MEDIASORTER_AUTORUN_OUTPUT")
        self.autorun_interactive = (os.environ.get("MEDIASORTER_AUTORUN_INTERACTIVE") or "").strip().lower() in ("1", "true", "yes", "y")
        self.autorun_convert_videos = (os.environ.get("MEDIASORTER_AUTORUN_CONVERT_VIDEOS") or "").strip().lower() in ("1", "true", "yes", "y")
        self.autorun_enabled = bool(self.autorun_input and self.autorun_output)

        if self.autorun_enabled:
            self.input_folder = self.autorun_input
            self.output_folder = self.autorun_output
            self.label_input.setText(f"Input Folder: {self.input_folder}")
            self.label_output.setText(f"Output Folder: {self.output_folder}")
            self.chk_interactive.setChecked(bool(self.autorun_interactive))
            self.chk_convert_videos.setChecked(bool(self.autorun_convert_videos))

        def _sync_structure_ui():
            try:
                is_custom = (self.cmb_structure.currentIndex() == (len(self._structure_presets) - 1))
                self.edit_structure.setEnabled(bool(is_custom))
            except Exception:
                is_custom = False
            self._update_structure_preview()

        self.cmb_structure.currentIndexChanged.connect(_sync_structure_ui)
        self.edit_structure.textChanged.connect(lambda _: self._update_structure_preview())
        _sync_structure_ui()
        self.update_ai_provider_ui()
        self.start_model_load()

    def _get_structure_pattern(self) -> str:
        try:
            is_custom = (self.cmb_structure.currentIndex() == (len(self._structure_presets) - 1))
        except Exception:
            is_custom = False

        if is_custom:
            pat = (self.edit_structure.text() or "").strip()
            return pat or "{category}"

        try:
            _, pat = self._structure_presets[int(self.cmb_structure.currentIndex())]
            return (pat or "{category}").strip() or "{category}"
        except Exception:
            return "{category}"

    def _update_structure_preview(self) -> None:
        # Use the currently loaded image if available; otherwise, a representative example.
        try:
            category = "ExampleCategory"
            img_path = self.current_path or ""
            img = self.current_img
            toks = _structure_tokens(category, img_path, img) if (img_path and img is not None) else {
                "category": category,
                "year": "2024",
                "month": "09",
                "yearmonth": "2024-09",
                "yearmo": "202409",
                "location": "GPS_37.77_-122.42",
            }
            pat = self._get_structure_pattern()
            preview = _render_structure("OUTPUT", pat, toks)
            self.lbl_structure_preview.setText(f"Structure preview: {preview}")
        except Exception:
            pass

    def _selected_ai_provider_id(self) -> str:
        pid = self.cmb_ai_provider.currentData()
        return str(pid or core.get_ai_provider_id())

    def _selected_ai_model_id(self) -> str:
        mid = self.cmb_ai_model.currentData()
        return str(mid or core.get_ai_model_id())

    def update_ai_provider_ui(self) -> None:
        pid = self._selected_ai_provider_id()
        name = core.get_ai_provider_display_name(pid)
        installed = core.is_ai_provider_installed(pid)
        state = "installed" if installed else "not installed"
        self.lbl_ai_provider.setText(f"Provider status: {name} ({state})")
        if pid == core.AI_PROVIDER_CLIP_LOCAL:
            self.cmb_ai_model.setEnabled(True)
            mid = core.get_ai_model_id()
            idx = self.cmb_ai_model.findData(mid)
            if idx >= 0 and idx != self.cmb_ai_model.currentIndex():
                self.cmb_ai_model.blockSignals(True)
                self.cmb_ai_model.setCurrentIndex(idx)
                self.cmb_ai_model.blockSignals(False)
            self.lbl_ai_model.setText(f"Model: {core.get_ai_model_display_name(mid)}")
        else:
            self.cmb_ai_model.setEnabled(False)
            self.lbl_ai_model.setText("Model: n/a for selected provider")
        if pid == core.AI_PROVIDER_NONE:
            self.btn_install_ai_provider.setText("No Installation Needed")
            self.btn_install_ai_provider.setEnabled(False)
        else:
            self.btn_install_ai_provider.setText("Install Selected AI Provider")
            self.btn_install_ai_provider.setEnabled(not installed)

    def start_model_load(self) -> None:
        try:
            if self.model_thread is not None and self.model_thread.isRunning():
                return
        except Exception:
            pass
        self.progress.setRange(0, 0)
        self.progress.setFormat("Loading AI provider...")
        self.status_label.setText("Status: Loading AI provider...")
        self.btn_start.setEnabled(False)
        self.model_thread = ModelLoadThread()
        self.model_thread.status_signal.connect(self.on_model_status)
        self.model_thread.done_signal.connect(self.on_model_loaded)
        self.model_thread.start()

    def on_ai_provider_changed(self):
        pid = self._selected_ai_provider_id()
        try:
            core.set_ai_provider(pid)
        except Exception as e:
            QMessageBox.critical(self, "AI Provider Error", str(e))
            return
        self.update_ai_provider_ui()
        self.start_model_load()

    def on_ai_model_changed(self):
        if self._selected_ai_provider_id() != core.AI_PROVIDER_CLIP_LOCAL:
            return
        mid = self._selected_ai_model_id()
        try:
            core.set_ai_model_profile(mid)
        except Exception as e:
            QMessageBox.critical(self, "AI Model Error", str(e))
            return
        self.update_ai_provider_ui()
        self.start_model_load()

    def install_selected_ai_provider(self):
        pid = self._selected_ai_provider_id()
        if pid == core.AI_PROVIDER_NONE:
            QMessageBox.information(self, "AI Provider", "No installation is needed for this provider.")
            return

        try:
            if self.provider_install_thread is not None and self.provider_install_thread.isRunning():
                return
        except Exception:
            pass

        self.cmb_ai_provider.setEnabled(False)
        self.cmb_ai_model.setEnabled(False)
        self.btn_install_ai_provider.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Installing AI provider...")
        self.status_label.setText("Status: Installing AI provider...")

        self.provider_install_thread = ProviderInstallThread(pid)
        self.provider_install_thread.status_signal.connect(self.on_model_status)
        self.provider_install_thread.done_signal.connect(self.on_ai_provider_installed)
        self.provider_install_thread.start()

    def on_ai_provider_installed(self, ok: bool, message: str):
        self.cmb_ai_provider.setEnabled(True)
        self.update_ai_provider_ui()
        if ok:
            QMessageBox.information(self, "AI Provider", message)
            self.start_model_load()
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("AI provider install failed")
            QMessageBox.critical(self, "AI Provider Install Error", message)

    def on_model_status(self, message: str):
        try:
            self.status_label.setText(f"Status: {message}")
        except Exception:
            pass

    def on_model_loaded(self, ok: bool, message: str):
        self.update_ai_provider_ui()
        if ok:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("%v/%m")
            self.status_label.setText(f"Status: Ready - {message}")
            self.btn_start.setEnabled(True)
            if self.autorun_enabled:
                self.status_label.setText(f"Status: {message} (autorun)")
                QTimer.singleShot(0, self.start_processing)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("AI model failed to load")
            self.status_label.setText("Status: AI model failed to load")
            QMessageBox.critical(self, "AI Model Error", message)

    # ---------------------------
    # Folder selection
    # ---------------------------
    def select_input(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder")
        if folder:
            self.input_folder = folder
            self.label_input.setText(f"Input Folder: {folder}")

    def select_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_folder = folder
            self.label_output.setText(f"Output Folder: {folder}")

    # ---------------------------
    # Help & Donate
    # ---------------------------
    def show_help(self):
        QMessageBox.information(
            self,
            "Help",
            "MediaSorter Help:\n\n"
            "1. Export photos/videos from iPhone or other sources.\n"
            "2. Select Input and Output folders.\n"
            "3. Choose options:\n"
            "   - AI Provider (none or local CLIP)\n"
            "   - Convert videos to MP4\n"
            "   - Interactive Mode\n"
            "   - Folder Structure (choose order of Category/Date/Location)\n"
            "4. Click Start Processing.\n"
            "5. Interactive Mode allows confirming/changing AI categories.\n"
            "6. You can dismiss Interactive Mode to continue auto-sorting.\n"
            f"7. Decision log: {core.get_decision_log_path()}"
        )

    def open_paypal(self):
        QDesktopServices.openUrl(QUrl(PAYPAL_LINK))

    def open_decision_log(self):
        log_path = core.get_decision_log_path()
        parent = ""
        try:
            parent = os.path.dirname(log_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if not os.path.exists(log_path):
                with open(log_path, "a", encoding="utf-8"):
                    pass
        except Exception as e:
            QMessageBox.critical(self, "Decision Log Error", f"Unable to prepare decision log:\n{e}")
            return

        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(log_path))
        if opened:
            return

        # Fallback: open containing folder if direct file open fails.
        if parent and QDesktopServices.openUrl(QUrl.fromLocalFile(parent)):
            QMessageBox.information(
                self,
                "Decision Log",
                f"Opened log folder. Decision log file:\n{log_path}",
            )
            return

        QMessageBox.warning(
            self,
            "Decision Log",
            f"Could not open the log automatically.\n\nPath:\n{log_path}",
        )

    def forget_last_classifications(self):
        resp = QMessageBox.question(
            self,
            "Forget Classifications",
            "This will permanently forget your saved per-photo classifications.\n\n"
            "It deletes user_corrections.json so future runs won't force those categories.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        core.CORRECTIONS = {}
        try:
            if CORRECTION_FILE.exists():
                CORRECTION_FILE.unlink()
        except Exception:
            # If deletion fails, fall back to overwriting with an empty object.
            try:
                _atomic_write_json(CORRECTION_FILE, {})
            except Exception:
                pass

        _log_line("[ui] user requested to forget classifications (cleared user_corrections.json)")
        QMessageBox.information(self, "Forgotten", "Saved classifications have been cleared.")

    # ---------------------------
    # Start processing
    # ---------------------------
    def start_processing(self):
        if core._MODEL_LOAD_ERROR:
            QMessageBox.critical(self, "AI Model Error", core._MODEL_LOAD_ERROR)
            return
        if not core._MODEL_READY:
            QMessageBox.information(self, "AI Loading", "AI provider is still loading. Please wait.")
            return

        # Require output folder: prompt the user to pick one instead of just failing.
        if not self.output_folder:
            QMessageBox.information(self, "Output Folder Required", "Please choose an output folder to copy files into.")
            self.select_output()
            if not self.output_folder:
                return

        if not self.input_folder:
            QMessageBox.information(self, "Input Folder Required", "Please choose an input folder to process.")
            self.select_input()
            if not self.input_folder:
                return

        try:
            self.files = [f for f in os.listdir(self.input_folder) if f.lower().endswith(IMAGE_EXT + VIDEO_EXT)]
        except Exception as e:
            QMessageBox.critical(self, "Input Folder Error", f"Unable to scan input folder:\n{e}")
            self.status_label.setText("Status: Input scan failed")
            return

        try:
            has_heic_inputs = any(f.lower().endswith((".heic", ".heif")) for f in self.files)
            if has_heic_inputs and (not self._heic_warning_shown):
                heic = core.get_heic_support_status()
                if not bool(heic.get("supported")):
                    self._heic_warning_shown = True
                    QMessageBox.warning(
                        self,
                        "HEIC Support Missing",
                        "HEIC/HEIF files were detected, but decoding is not available in this runtime.\n\n"
                        f"{heic.get('detail')}",
                    )
        except Exception:
            pass
        self.index = 0
        self.progress.setRange(0, len(self.files))
        self.progress.setValue(0)
        self.status_label.setText(f"Status: Found {len(self.files)} media files")
        self.interactive_mode = self.chk_interactive.isChecked()
        if self.chk_people.isChecked() and (not self.interactive_mode):
            try:
                face_status = core.get_face_support_status()
                if not bool(face_status.get("supported")):
                    self.chk_people.setChecked(False)
                    QMessageBox.warning(
                        self,
                        "Face Identification Unavailable",
                        "Identify People After Run is enabled, but face support is unavailable in this runtime.\n\n"
                        f"{face_status.get('detail')}\n\n"
                        "Face review will be skipped for this run.",
                    )
            except Exception:
                pass
        self.combo_category.setEnabled(bool(self.interactive_mode))
        self.btn_confirm.setEnabled(bool(self.interactive_mode))
        self.btn_skip.setEnabled(bool(self.interactive_mode))
        if self.interactive_mode:
            self.progress.setFormat("%v/%m")
        else:
            self.progress.setFormat("%v/%m ETA: estimating...")

        if len(self.files) == 0:
            QMessageBox.information(self, "Empty Folder", "No media files found.")
            return

        if self.interactive_mode:
            self.process_next_interactive()
        else:
            self.start_auto_thread()

    # ---------------------------
    # Interactive mode
    # ---------------------------
    def process_next_interactive(self):
        if self.index >= len(self.files):
            self.status_label.setText("Status: Complete")
            self.combo_category.setEnabled(False)
            self.btn_confirm.setEnabled(False)
            self.btn_skip.setEnabled(False)
            QMessageBox.information(self, "Done", "All files processed.")
            return

        file = self.files[self.index]
        path = os.path.join(self.input_folder, file)
        self.current_path = path

        total = max(1, len(self.files))
        # Interactive mode: no ETA, just show 1/N style progress.
        try:
            self.progress.setFormat("%v/%m")
            self.progress.setValue(self.index)
        except Exception:
            pass

        if file.lower().endswith(VIDEO_EXT):
            try:
                out_dir = os.path.join(self.output_folder, "Videos")
                os.makedirs(out_dir, exist_ok=True)
                if self.chk_convert_videos.isChecked():
                    self.status_label.setText(f"Status: Interactive {self.index+1}/{total}: Converting video {file}")
                    base = os.path.splitext(file)[0]
                    mp4_path = _unique_dest_path(out_dir, base + ".mp4")
                    convert_video(path, mp4_path)
                else:
                    self.status_label.setText(f"Status: Interactive {self.index+1}/{total}: Copying video {file}")
                    dest = _unique_dest_path(out_dir, file)
                    shutil.copy2(path, dest)
            except Exception as e:
                print("Video failed:", file, e)
            self.index +=1
            self.progress.setValue(self.index)
            QApplication.processEvents()
            self.process_next_interactive()
            return

        if file.lower().endswith(IMAGE_EXT):
            self.status_label.setText(f"Status: Interactive {self.index+1}/{total}: Predicting category for {file}")
            self.current_embedding = None
            try:
                img = load_image_for_ai(path)
                if img:
                    pixmap = pil_to_qpixmap(img)
                else:
                    pixmap = QPixmap()
                self.image_label.setPixmap(pixmap)
            except:
                self.image_label.setPixmap(QPixmap())
                img = None

            self.current_img = img
            try:
                self._update_structure_preview()
            except Exception:
                pass

            predicted, score, emb = _predict_category_internal(path, pil_img=img)
            self.current_embedding = emb

            try:
                self.status_label.setText(f"Status: Interactive {self.index+1}/{total}: {file} -> {predicted} ({score:.2f})")
            except Exception:
                pass

            # QComboBox.setCurrentText() can be a no-op if the text doesn't match exactly or the box isn't editable.
            # Make selection deterministic by setting the index directly (and adding if missing).
            try:
                before = self.combo_category.currentText()
            except Exception:
                before = ""

            idx = self.combo_category.findText(predicted, Qt.MatchFixedString | Qt.MatchCaseSensitive)
            if idx < 0:
                self.combo_category.addItem(predicted)
                idx = self.combo_category.findText(predicted, Qt.MatchFixedString | Qt.MatchCaseSensitive)
            if idx >= 0:
                self.combo_category.setCurrentIndex(idx)

            try:
                after = self.combo_category.currentText()
            except Exception:
                after = ""
            _log_line(f"[interactive] {file} predicted={predicted!r} score={score:.4f} combo_before={before!r} combo_after={after!r}")
            self.combo_category.setEnabled(True)
            self.btn_confirm.setEnabled(True)
            self.btn_skip.setEnabled(True)
            return

    def confirm_category(self):
        chosen = self.combo_category.currentText()
        file = os.path.basename(self.current_path)

        img = self.current_img
        if img is None:
            img = load_image_for_ai(self.current_path)

        toks = _structure_tokens(chosen, self.current_path, img)
        dest_dir = _render_structure(self.output_folder, self._get_structure_pattern(), toks)
        dest = _unique_dest_path(dest_dir, file)
        shutil.copy2(self.current_path, dest)
        try:
            core._log_sort_destination_decision(
                source_path=self.current_path,
                category=chosen,
                structure_pattern=self._get_structure_pattern(),
                tokens=toks,
                dest_dir=dest_dir,
                dest_path=dest,
                flow="interactive_image",
            )
        except Exception:
            pass
        img_hash = hash_image(self.current_path)
        if img_hash:
            core.CORRECTIONS[img_hash] = chosen
            try:
                _atomic_write_json(CORRECTION_FILE, core.CORRECTIONS)
            except Exception:
                pass

        try:
            _update_prototype(chosen, self.current_embedding)
        except Exception:
            pass

        self.current_embedding = None
        self.current_img = None
        self.index +=1
        self.progress.setValue(self.index)
        QApplication.processEvents()
        self.process_next_interactive()

    def dismiss_interactive(self):
        if not self.interactive_mode or self.index >= len(self.files):
            self.interactive_mode = False
            QMessageBox.information(self, "Interactive Mode", "No active interactive item to dismiss.")
            self.combo_category.setEnabled(False)
            self.btn_confirm.setEnabled(False)
            self.btn_skip.setEnabled(False)
            return
        self.interactive_mode=False
        self.combo_category.setEnabled(False)
        self.btn_confirm.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.status_label.setText("Status: Continuing auto-sort")
        self.start_auto_thread(start_index=self.index)

    # ---------------------------
    # Auto mode with thread
    # ---------------------------
    def start_auto_thread(self, start_index=0):
        if int(start_index) >= len(self.files):
            try:
                self.sortviz.set_running(False)
            except Exception:
                pass
            self.status_label.setText("Status: Complete")
            QMessageBox.information(self, "Done", "No remaining files to process.")
            return
        try:
            self.sortviz.set_running(bool(self.chk_sortviz.isChecked()))
        except Exception:
            pass
        self.thread = AutoProcessThread(
            self.files,
            self.input_folder,
            self.output_folder,
            convert_videos=self.chk_convert_videos.isChecked(),
            start_index=start_index,
            structure_pattern=self._get_structure_pattern(),
            enable_people=(self.chk_people.isChecked() and (not self.chk_interactive.isChecked())),
        )
        self.thread.progress_signal.connect(self.progress.setValue)
        self.thread.status_signal.connect(self.on_auto_status)
        self.thread.visual_signal.connect(self.on_sortviz_event)
        self.thread.done_signal.connect(self.auto_done)
        self.thread.start()

    def on_auto_status(self, s: str):
        try:
            self.status_label.setText(f"Status: {s}")
        except Exception:
            pass
        try:
            m = re.search(r"\\bETA\\b.*$", s or "")
            if m:
                eta = m.group(0).strip()
                self.progress.setFormat(f"%v/%m {eta}")
            else:
                self.progress.setFormat("%v/%m")
        except Exception:
            pass

    def on_sortviz_event(self, file_path: str, category: str, is_video: bool):
        try:
            if not self.chk_sortviz.isChecked():
                return
            self.sortviz.enqueue(file_path, category, bool(is_video))
        except Exception:
            pass

    def closeEvent(self, event):
        # Prevent "QThread: Destroyed while thread is still running" on shutdown.
        try:
            t = getattr(self, "thread", None)
            if t is not None and t.isRunning():
                try:
                    t.requestInterruption()
                except Exception:
                    pass
                try:
                    t.wait(3000)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            t = getattr(self, "provider_install_thread", None)
            if t is not None and t.isRunning():
                try:
                    t.requestInterruption()
                except Exception:
                    pass
                try:
                    t.wait(3000)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            super().closeEvent(event)
        except Exception:
            pass

    def _show_people_review_for_thread(self, th) -> bool:
        try:
            if th is None:
                return False
            clusters = []
            for cl in (getattr(th, "people_clusters", []) or []):
                # Only prompt for unknown clusters of non-trivial size.
                if cl.get("name"):
                    continue
                if int(cl.get("count") or 0) < 4:
                    continue
                clusters.append(cl)
            clusters.sort(key=lambda c: int(c.get("count") or 0), reverse=True)
            if clusters:
                dlg = PeopleReviewDialog(self, clusters, getattr(th, "people_output_map", {}), self.output_folder)
                dlg.exec()
                return True
        except Exception:
            pass
        return False

    def run_people_scan_now(self):
        try:
            t = getattr(self, "thread", None)
            if t is not None and t.isRunning():
                QMessageBox.information(self, "Face Scan", "Another processing task is currently running.")
                return
        except Exception:
            pass

        if not self.output_folder:
            QMessageBox.information(self, "Output Folder Required", "Please choose an output folder first.")
            self.select_output()
            if not self.output_folder:
                return

        face_status = core.get_face_support_status()
        if not bool(face_status.get("supported")):
            QMessageBox.warning(self, "Face Identification Unavailable", str(face_status.get("detail") or "Unavailable"))
            return

        self._people_scan_only = True
        self.btn_people_scan_now.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Scanning faces in output...")
        self.status_label.setText("Status: Running face scan on output folder...")

        self.thread = AutoProcessThread(
            files=[],
            input_folder=self.output_folder,
            output_folder=self.output_folder,
            convert_videos=False,
            start_index=0,
            structure_pattern=self._get_structure_pattern(),
            enable_people=True,
        )
        self.thread.status_signal.connect(self.on_auto_status)
        self.thread.done_signal.connect(self.people_scan_done)
        self.thread.start()

    def people_scan_done(self, _counts):
        self._people_scan_only = False
        self.btn_people_scan_now.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("%v/%m")
        self.status_label.setText("Status: Face scan complete")

        shown = self._show_people_review_for_thread(getattr(self, "thread", None))
        try:
            all_clusters = list(getattr(self.thread, "people_clusters", []) or [])
        except Exception:
            all_clusters = []
        unknown = [cl for cl in all_clusters if not cl.get("name")]
        QMessageBox.information(
            self,
            "Face Scan Complete",
            f"Clusters found: {len(all_clusters)}\n"
            f"Unknown clusters: {len(unknown)}\n"
            f"Review dialog shown: {'Yes' if shown else 'No'}",
        )

        try:
            self.btn_start.setEnabled(bool(core._MODEL_READY and not core._MODEL_LOAD_ERROR))
        except Exception:
            pass

    def auto_done(self, counts):
        try:
            self.sortviz.set_running(False)
        except Exception:
            pass
        self.combo_category.setEnabled(False)
        self.btn_confirm.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.status_label.setText("Status: Complete")
        try:
            self.progress.setFormat("%v/%m Done")
        except Exception:
            pass

        # Post-run people identification flow (batch mode only).
        try:
            if getattr(self, "thread", None) is not None and bool(self.chk_people.isChecked()) and (not self.chk_interactive.isChecked()):
                self._show_people_review_for_thread(self.thread)
        except Exception:
            pass

        QMessageBox.information(
            self,
            "Processing Complete",
            f"Images categorized: {counts['images']}\n"
            f"Videos handled: {counts['videos']}\n"
            f"Failed items: {counts['failed']}"
        )

    # ---------------------------
    # Category Manager
    # ---------------------------
    def open_category_manager(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Manage Categories")
        dialog.resize(400, 300)

        renames = {}  # old -> new

        list_widget = QListWidget()
        list_widget.addItems(core.CATEGORIES)

        btn_add = QPushButton("Add")
        btn_rename = QPushButton("Rename")
        btn_remove = QPushButton("Remove")
        btn_save = QPushButton("Save & Close")

        layout = QVBoxLayout()
        layout.addWidget(list_widget)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(btn_rename)
        btn_layout.addWidget(btn_remove)
        layout.addLayout(btn_layout)
        layout.addWidget(btn_save)

        dialog.setLayout(layout)

        def add_category():
            text, ok = QInputDialog.getText(dialog, "Add Category", "Category name:")
            if ok and text.strip():
                list_widget.addItem(text.strip())

        def rename_category():
            current = list_widget.currentItem()
            if current:
                old = current.text()
                text, ok = QInputDialog.getText(dialog, "Rename Category", "New name:", text=current.text())
                if ok and text.strip():
                    new = text.strip()
                    if new and new != old:
                        current.setText(new)
                        renames[old] = new

        def remove_category():
            current = list_widget.currentItem()
            if current:
                list_widget.takeItem(list_widget.row(current))

        def save_categories():
            # Build cleaned category list (no empties, no dups)
            seen = set()
            new_categories = []
            for i in range(list_widget.count()):
                c = (list_widget.item(i).text() or "").strip()
                if not c or c in seen:
                    continue
                seen.add(c)
                new_categories.append(c)

            if not new_categories:
                new_categories = core.DEFAULT_CATEGORIES[:]

            # Apply renames to prototypes and corrections so user feedback isn't lost.
            for old, new in renames.items():
                if old == new:
                    continue
                try:
                    if old in core.PROTOTYPES and new not in core.PROTOTYPES:
                        core.PROTOTYPES[new] = core.PROTOTYPES.pop(old)
                    elif old in core.PROTOTYPES and new in core.PROTOTYPES:
                        # Merge: simple weighted average of centroids when both exist.
                        a = core.PROTOTYPES.get(old, {})
                        b = core.PROTOTYPES.get(new, {})
                        try:
                            a_cnt = int(a.get("count") or 1)
                            b_cnt = int(b.get("count") or 1)
                            a_vec = np.array([float(x) for x in a.get("embedding") or []], dtype=np.float32)
                            b_vec = np.array([float(x) for x in b.get("embedding") or []], dtype=np.float32)
                            if a_vec.size and b_vec.size and a_vec.size == b_vec.size:
                                merged = (a_vec * a_cnt + b_vec * b_cnt) / float(a_cnt + b_cnt)
                                n = float(np.linalg.norm(merged))
                                if n > 0:
                                    merged = merged / n
                                core.PROTOTYPES[new] = {"count": a_cnt + b_cnt, "embedding": merged.tolist()}
                                core.PROTOTYPES.pop(old, None)
                        except Exception:
                            pass
                except Exception:
                    pass

                try:
                    for h, cat in list(core.CORRECTIONS.items()):
                        if cat == old:
                            core.CORRECTIONS[h] = new
                except Exception:
                    pass

            valid = set(new_categories)
            try:
                for k in list(core.PROTOTYPES.keys()):
                    if k not in valid:
                        core.PROTOTYPES.pop(k, None)
            except Exception:
                pass
            try:
                for h, cat in list(core.CORRECTIONS.items()):
                    if cat not in valid:
                        core.CORRECTIONS.pop(h, None)
            except Exception:
                pass

            core.CATEGORIES = new_categories
            _atomic_write_text(CATEGORIES_FILE, "\n".join(core.CATEGORIES) + "\n")
            self.combo_category.clear()
            self.combo_category.addItems(core.CATEGORIES)

            try:
                _atomic_write_json(CORRECTION_FILE, core.CORRECTIONS)
            except Exception:
                pass
            _save_prototypes()

            if core._MODEL_READY:
                _refresh_text_features()
                _refresh_proto_features()
            dialog.accept()

        btn_add.clicked.connect(add_category)
        btn_rename.clicked.connect(rename_category)
        btn_remove.clicked.connect(remove_category)
        btn_save.clicked.connect(save_categories)

        dialog.exec()
