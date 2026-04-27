from collections import OrderedDict
import json
import os
import re
import shutil
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRect, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QImageReader, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mediasorter_core import (
    AutoProcessThread,
    CATEGORIES_FILE,
    CORRECTION_FILE,
    IMAGE_EXT,
    LEGAL_INFO_URL,
    ModelLoadThread,
    PRIVACY_URL,
    REFUND_URL,
    ProviderInstallThread,
    SUPPORT_URL,
    TERMS_URL,
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
    log_product_event,
    pil_to_qpixmap,
)
import mediasorter_core as core
from mediasorter_widgets import PeopleReviewDialog


UI_SETTINGS_FILE = Path(core.DATA_DIR) / "ui_settings.json"


class ReviewHistoryDelegate(QStyledItemDelegate):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 92)

    def paint(self, painter, option, index):
        entry = index.data(Qt.UserRole)
        if not isinstance(entry, dict):
            super().paint(painter, option, index)
            return

        file_name, category_text, summary = self.host._review_text_parts(entry)
        rect = option.rect.adjusted(4, 3, -4, -3)
        selected = bool(option.state & QStyle.State_Selected)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#e8f2ff") if selected else QColor("#ffffff"))
        painter.drawRoundedRect(rect, 14, 14)

        thumb_rect = QRect(rect.left() + 10, rect.top() + 10, 64, 64)
        pixmap = self.host._review_thumbnail_for_entry(entry, thumb_rect.size())
        if pixmap is not None and not pixmap.isNull():
            x = thumb_rect.left() + max(0, (thumb_rect.width() - pixmap.width()) // 2)
            y = thumb_rect.top() + max(0, (thumb_rect.height() - pixmap.height()) // 2)
            painter.drawPixmap(x, y, pixmap)

        text_rect = rect.adjusted(86, 9, -12, -9)
        title_rect = QRect(text_rect.left(), text_rect.top(), text_rect.width(), 22)
        meta_rect = QRect(text_rect.left(), text_rect.top() + 24, text_rect.width(), 18)
        summary_rect = QRect(text_rect.left(), text_rect.top() + 44, text_rect.width(), 28)

        title_font = painter.font()
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#111827"))
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, option.fontMetrics.elidedText(file_name, Qt.ElideRight, title_rect.width()))

        meta_font = painter.font()
        meta_font.setBold(False)
        painter.setFont(meta_font)
        painter.setPen(QColor("#2563eb") if selected else QColor("#475467"))
        painter.drawText(meta_rect, Qt.AlignLeft | Qt.AlignVCenter, option.fontMetrics.elidedText(category_text, Qt.ElideRight, meta_rect.width()))

        painter.setPen(QColor("#334155"))
        painter.drawText(summary_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, summary)
        painter.restore()
class MediaSorter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setUpdatesEnabled(False)
        self.setObjectName("AppRoot")
        self.setWindowTitle("MediaSorter")
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#f4f5f7"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8f9fb"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#111827"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#111827"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#111827"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#0a84ff"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.input_folder = ""
        self.output_folder = ""
        self.files = []
        self.index = 0
        self.interactive_mode = False
        self.current_path = ""
        self.current_embedding = None
        self.current_img = None
        self._current_preview_pixmap = None
        self._heic_warning_shown = False
        self._trial_active = False
        self._trial_total_discovered = 0
        self.trial_limit = max(1, int(os.environ.get("MEDIASORTER_TRIAL_LIMIT", "200") or 200))
        self._run_started_monotonic = None
        self._interactive_failed = 0
        self._onboarding_marker = Path(core.DATA_DIR) / ".onboarding_complete"
        self._onboarding_ran = False
        self._run_active = False
        self._run_phase = "Preparing AI runtime"
        self._review_thumbnail_cache = OrderedDict()
        self._review_thumbnail_cache_limit = 96
        self._suspend_ui_settings = True
        self._pending_model_action = None

        # Folder structure presets (user can also type a custom pattern).
        self._structure_presets = [
            ("By category", "{category}"),
            ("By category, then year", "{category}/{year}"),
            ("By category, year, then month", "{category}/{year}/{month}"),
            ("By category, then year-month", "{category}/{yearmonth}"),
            ("By year, then category", "{year}/{category}"),
            ("By year, month, then category", "{year}/{month}/{category}"),
            ("By year-month, then category", "{yearmonth}/{category}"),
            ("By location, then category", "{location}/{category}"),
            ("By category, then location", "{category}/{location}"),
            ("By year, location, then category", "{year}/{location}/{category}"),
            ("By year, month, location, then category", "{year}/{month}/{location}/{category}"),
            ("Custom folder layout", ""),  # enables text box
        ]

        # Folder selectors
        self.label_input = QLineEdit("Not selected yet")
        self.label_output = QLineEdit("Not selected yet")
        self.label_input.setReadOnly(True)
        self.label_output.setReadOnly(True)
        self.label_input.setObjectName("SelectionDisplay")
        self.label_output.setObjectName("SelectionDisplay")
        btn_input = QPushButton("Choose Source Folder")
        btn_output = QPushButton("Choose Destination Folder")
        btn_input.setObjectName("SecondaryAction")
        btn_output.setObjectName("SecondaryAction")
        btn_input.clicked.connect(self.select_input)
        btn_output.clicked.connect(self.select_output)

        folder_hint = QLabel(
            "Pick the photos and videos you want to organize, then choose where MediaSorter should build the organized copy."
        )
        folder_hint.setObjectName("HintText")
        folder_note = QLabel("MediaSorter copies into the destination folder. Your originals stay in the source folder.")
        folder_note.setObjectName("HintText")
        folder_note.setWordWrap(True)
        folder_grid = QGridLayout()
        folder_grid.setColumnStretch(1, 1)
        folder_grid.addWidget(QLabel("Source library"), 0, 0)
        folder_grid.addWidget(self.label_input, 0, 1)
        folder_grid.addWidget(btn_input, 0, 2)
        folder_grid.addWidget(QLabel("Destination"), 1, 0)
        folder_grid.addWidget(self.label_output, 1, 1)
        folder_grid.addWidget(btn_output, 1, 2)
        folder_box = QGroupBox("1. Choose Your Folders")
        folder_box.setObjectName("StepCard")
        folder_layout = QVBoxLayout()
        folder_layout.addWidget(folder_hint)
        folder_layout.addLayout(folder_grid)
        folder_layout.addWidget(folder_note)
        folder_box.setLayout(folder_layout)
        self.folder_box = folder_box

        # Options
        self.chk_convert_videos = QCheckBox("Convert videos to MP4 with HandBrake")
        # Default off: video conversion can add hours/days; copying videos is the default behavior.
        self.chk_convert_videos.setChecked(False)
        self.chk_interactive = QCheckBox("Review files during the run when you want more control")
        self.chk_interactive.setChecked(False)
        self.chk_interactive.setVisible(False)
        self.chk_interactive.setEnabled(False)
        self.chk_people = QCheckBox("Group people after sorting finishes")
        self.chk_people.setChecked(False)
        self.chk_trial = QCheckBox(f"Try Before You Buy: process the first {self.trial_limit} items")
        self.chk_trial.setChecked(True)
        self.chk_trial.setVisible(True)
        self.chk_trial.setEnabled(True)
        self.btn_people_scan_now = QPushButton("Scan Existing Output For People")
        self.btn_people_scan_now.clicked.connect(self.run_people_scan_now)
        self.lbl_face_hint = QLabel(
            "Turn this on if you want person-grouping after the current sort, or use the button below to scan an output folder later."
        )
        self.lbl_face_hint.setWordWrap(True)
        self.lbl_face_hint.setObjectName("HintText")
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
        self.btn_install_ai_provider = QPushButton("Install Selected AI Runtime")
        self.btn_install_ai_provider.clicked.connect(self.install_selected_ai_provider)
        self.cmb_ai_provider.currentIndexChanged.connect(self.on_ai_provider_changed)
        self.cmb_ai_model.currentIndexChanged.connect(self.on_ai_model_changed)
        self.cmb_structure = QComboBox()
        self.cmb_structure.addItems([name for (name, _) in self._structure_presets])
        self.cmb_structure.setCurrentIndex(0)
        self.lbl_structure_picker = QLabel("Choose a folder layout")
        self.lbl_structure_picker.setObjectName("SectionLead")
        self.edit_structure = QLineEdit()
        self.edit_structure.setPlaceholderText("Custom layout, e.g. {category}/{yearmo}/{location}")
        self.edit_structure.setEnabled(False)
        self.lbl_custom_structure = QLabel("Custom folder layout")
        self.lbl_custom_structure.setObjectName("SectionLead")
        self.lbl_structure_selected = QLabel("Selected layout: By category")
        self.lbl_structure_selected.setObjectName("SectionLead")
        self.lbl_structure_preview = QLabel("Example folder result: (not set)")
        self.lbl_structure_preview.setObjectName("HintText")
        self.lbl_structure_help = QLabel(
            "Choose a template, then check the example below. Custom layouts can use: "
            "{category} {year} {month} {yearmonth} {yearmo} {location}"
        )
        self.lbl_structure_help.setObjectName("HintText")
        self.lbl_structure_help.setWordWrap(True)
        self.custom_structure_box = QWidget()
        custom_structure_layout = QVBoxLayout()
        custom_structure_layout.setContentsMargins(0, 0, 0, 0)
        custom_structure_layout.setSpacing(6)
        custom_structure_layout.addWidget(self.lbl_custom_structure)
        custom_structure_layout.addWidget(self.edit_structure)
        self.custom_structure_box.setLayout(custom_structure_layout)
        self.custom_structure_box.setVisible(False)

        btn_manage_categories = QPushButton("Manage Categories")
        btn_manage_categories.setObjectName("SecondaryAction")
        btn_manage_categories.clicked.connect(self.open_category_manager)
        btn_forget = QPushButton("Clear Learned Choices")
        btn_forget.setObjectName("SecondaryAction")
        btn_forget.clicked.connect(self.forget_last_classifications)
        btn_open_decision_log = QPushButton("Open Decision Log")
        btn_open_decision_log.setObjectName("SecondaryAction")
        btn_open_decision_log.clicked.connect(self.open_decision_log)
        btn_open_data_folder = QPushButton("Open MediaSorter Data")
        btn_open_data_folder.setObjectName("SecondaryAction")
        btn_open_data_folder.clicked.connect(self.open_app_data_folder)
        btn_drive_space = QPushButton("Check Drive Space")
        btn_drive_space.setObjectName("SecondaryAction")
        btn_drive_space.clicked.connect(self.open_sequoiaview)

        self.chk_show_advanced = QCheckBox("Show Power-User Tools")
        show_adv_default = (os.environ.get("MEDIASORTER_SHOW_ADVANCED") or "").strip().lower() in ("1", "true", "yes", "y")
        self.chk_show_advanced.setChecked(bool(show_adv_default))
        self.chk_show_advanced.setVisible(False)

        run_hint = QLabel(
            "Choose how you want organized folders to look. Pick a template first, then confirm the example folder path below."
        )
        run_hint.setObjectName("HintText")
        run_hint.setWordWrap(True)

        organization_box = QGroupBox("2. Choose How MediaSorter Organizes Files")
        organization_box.setObjectName("StepCard")
        organization_layout = QVBoxLayout()
        organization_layout.addWidget(run_hint)
        organization_layout.addWidget(self.lbl_structure_picker)
        organization_layout.addWidget(self.cmb_structure)
        organization_layout.addWidget(self.custom_structure_box)
        organization_layout.addWidget(self.lbl_structure_selected)
        organization_layout.addWidget(self.lbl_structure_preview)
        organization_layout.addWidget(self.lbl_structure_help)
        organization_box.setLayout(organization_layout)
        self.organization_box = organization_box

        face_box = QGroupBox("3. Optional People Grouping")
        face_box.setObjectName("StepCard")
        face_layout = QVBoxLayout()
        face_layout.addWidget(self.chk_people)
        face_layout.addWidget(self.btn_people_scan_now)
        face_layout.addWidget(self.lbl_face_hint)
        face_box.setLayout(face_layout)
        self.face_box = face_box

        power_options_box = QGroupBox("Power-user options")
        power_options_box.setObjectName("InnerCard")
        power_options_layout = QVBoxLayout()
        power_options_layout.addWidget(self.chk_convert_videos)
        power_options_box.setLayout(power_options_layout)

        ai_box = QGroupBox("AI Runtime")
        ai_box.setObjectName("InnerCard")
        ai_layout = QFormLayout()
        ai_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        ai_layout.addRow("Provider:", self.cmb_ai_provider)
        ai_layout.addRow("Model:", self.cmb_ai_model)
        ai_layout.addRow("", self.lbl_ai_provider)
        ai_layout.addRow("", self.lbl_ai_model)
        ai_layout.addRow("", self.btn_install_ai_provider)
        ai_box.setLayout(ai_layout)

        maintenance_box = QGroupBox("Maintenance Tools")
        maintenance_box.setObjectName("InnerCard")
        maintenance_layout = QGridLayout()
        maintenance_layout.addWidget(btn_manage_categories, 0, 0)
        maintenance_layout.addWidget(btn_forget, 0, 1)
        maintenance_layout.addWidget(btn_open_decision_log, 1, 0)
        maintenance_layout.addWidget(btn_open_data_folder, 1, 1)
        maintenance_layout.addWidget(btn_drive_space, 2, 0, 1, 2)
        maintenance_box.setLayout(maintenance_layout)

        advanced_layout = QVBoxLayout()
        advanced_layout.addWidget(power_options_box)
        advanced_layout.addWidget(ai_box)
        advanced_layout.addWidget(maintenance_box)
        self.advanced_box = QGroupBox("Power-User Tools")
        self.advanced_box.setLayout(advanced_layout)
        self.advanced_box.setVisible(bool(self.chk_show_advanced.isChecked()))
        self.chk_show_advanced.stateChanged.connect(lambda _: self._on_advanced_toggle())

        self.btn_toggle_advanced = QToolButton()
        self.btn_toggle_advanced.setObjectName("LinkToggle")
        self.btn_toggle_advanced.setCheckable(True)
        self.btn_toggle_advanced.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_toggle_advanced.toggled.connect(lambda on: self.chk_show_advanced.setChecked(bool(on)))

        # Current file preview
        self.image_label = QLabel("Start a run to see the photo or video currently being processed.")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setObjectName("MediaPreview")
        self.image_label.setWordWrap(True)
        self.image_label.setMinimumSize(420, 320)
        self.image_filename_label = QLabel("No file is being processed yet.")
        self.image_filename_label.setObjectName("SectionLead")
        self.image_filename_label.setWordWrap(True)
        self.image_explanation_label = QLabel(
            "MediaSorter will show the current file here and explain the folder category it is using."
        )
        self.image_explanation_label.setObjectName("HintText")
        self.image_explanation_label.setWordWrap(True)
        self.image_explanation_state_label = QLabel("Explanation type: Waiting for classification details")
        self.image_explanation_state_label.setObjectName("HintText")
        self.image_explanation_state_label.setWordWrap(True)
        self.review_history = QListWidget()
        self.review_history.setObjectName("ReviewHistory")
        self.review_history.setMinimumHeight(260)
        self.review_history.setWordWrap(True)
        self.review_history.setSpacing(6)
        self.review_history.setItemDelegate(ReviewHistoryDelegate(self, self.review_history))
        self.review_history.itemSelectionChanged.connect(self.on_review_history_selection_changed)
        self.review_status_label = QLabel(
            "Recent automatic categorizations will appear here while MediaSorter is sorting."
        )
        self.review_status_label.setObjectName("HintText")
        self.review_status_label.setWordWrap(True)
        self.review_detail_label = QLabel(
            "Select a sorted item below if you want to review or correct its category."
        )
        self.review_detail_label.setObjectName("HintText")
        self.review_detail_label.setWordWrap(True)
        self.review_category_combo = QComboBox()
        self.review_category_combo.addItems(core.CATEGORIES)
        self.review_category_combo.setEnabled(False)
        self.edit_new_review_category = QLineEdit()
        self.edit_new_review_category.setPlaceholderText("Create a new category while sorting")
        self.edit_new_review_category.returnPressed.connect(self.add_live_category_from_review_panel)
        self.btn_add_review_category = QPushButton("New Category")
        self.btn_add_review_category.setObjectName("SecondaryAction")
        self.btn_add_review_category.clicked.connect(self.add_live_category_from_review_panel)
        self.btn_apply_review_category = QPushButton("Apply Selected Category")
        self.btn_apply_review_category.setObjectName("SecondaryAction")
        self.btn_apply_review_category.setEnabled(False)
        self.btn_apply_review_category.clicked.connect(self.apply_selected_review_override)
        self.combo_category = QComboBox()
        self.combo_category.addItems(core.CATEGORIES)
        self.btn_confirm = QPushButton("Confirm Category")
        self.btn_confirm.clicked.connect(self.confirm_category)
        self.btn_skip = QPushButton("Let MediaSorter Decide")
        self.btn_skip.clicked.connect(self.dismiss_interactive)
        self.combo_category.setEnabled(False)
        self.btn_confirm.setEnabled(False)
        self.btn_skip.setEnabled(False)

        review_section_title = QLabel("Recent Automatic Categorizations")
        review_section_title.setObjectName("SectionLead")
        current_section_title = QLabel("Current File")
        current_section_title.setObjectName("SectionLead")

        image_layout = QVBoxLayout()
        image_layout.addSpacing(10)
        image_layout.addWidget(review_section_title)
        image_layout.addWidget(self.review_status_label)
        image_layout.addWidget(self.review_history, 1)
        image_layout.addWidget(self.review_detail_label)
        self.image_box = QGroupBox("Live Review")
        self.image_box.setObjectName("PreviewCard")
        review_add_row = QHBoxLayout()
        review_add_row.addWidget(self.edit_new_review_category, 1)
        review_add_row.addWidget(self.btn_add_review_category)

        review_controls = QHBoxLayout()
        review_controls.addWidget(self.review_category_combo, 1)
        review_controls.addWidget(self.btn_apply_review_category)

        image_layout.addLayout(review_add_row)
        image_layout.addLayout(review_controls)
        image_layout.addSpacing(10)
        image_layout.addWidget(current_section_title)
        image_layout.addWidget(self.image_label, 1)
        image_layout.addWidget(self.image_filename_label)
        image_layout.addWidget(self.image_explanation_label)
        image_layout.addWidget(self.image_explanation_state_label)
        self.image_box.setLayout(image_layout)
        self.image_box.setVisible(True)
        self.chk_interactive.stateChanged.connect(lambda _: self._sync_interactive_review_visibility())

        # Progress
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("Idle")
        self.status_label = QLabel("Status: Ready to configure your sort")
        self.status_label.setWordWrap(True)
        self.status_hint = QLabel("MediaSorter will show what it is doing here while it loads, sorts, reviews, and finishes.")
        self.status_hint.setObjectName("HintText")
        self.status_hint.setWordWrap(True)
        status_box = QGroupBox("Current Activity")
        status_box.setObjectName("ActivityCard")
        status_layout = QVBoxLayout()
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.status_hint)
        status_layout.addWidget(self.progress)
        status_box.setLayout(status_layout)
        self.status_box = status_box

        # Sidebar summary and help
        self.lbl_summary_state = QLabel("")
        self.lbl_summary_state.setObjectName("SectionLead")
        self.lbl_summary_state.setWordWrap(True)
        self.lbl_summary_checklist = QLabel("")
        self.lbl_summary_checklist.setWordWrap(True)
        self.lbl_summary_checklist.setObjectName("HintText")
        self.lbl_summary_paths = QLabel("")
        self.lbl_summary_paths.setWordWrap(True)
        self.lbl_summary_structure = QLabel("")
        self.lbl_summary_structure.setWordWrap(True)
        self.lbl_summary_features = QLabel("")
        self.lbl_summary_features.setWordWrap(True)
        self.lbl_summary_ai = QLabel("")
        self.lbl_summary_ai.setWordWrap(True)

        summary_box = QGroupBox("Run Summary")
        summary_box.setObjectName("SidebarCard")
        summary_layout = QVBoxLayout()
        summary_layout.addWidget(self.lbl_summary_state)
        summary_layout.addWidget(self.lbl_summary_checklist)
        summary_layout.addWidget(QLabel("Folders"))
        summary_layout.addWidget(self.lbl_summary_paths)
        summary_layout.addWidget(QLabel("Organization"))
        summary_layout.addWidget(self.lbl_summary_structure)
        summary_layout.addWidget(QLabel("Enabled Options"))
        summary_layout.addWidget(self.lbl_summary_features)
        summary_layout.addWidget(QLabel("AI"))
        summary_layout.addWidget(self.lbl_summary_ai)
        summary_box.setLayout(summary_layout)
        self.summary_box = summary_box

        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText('Search "dog", "beach", "document", or leave blank for recent files')
        self.edit_search.returnPressed.connect(self.run_library_search)
        self.btn_search = QPushButton("Search")
        self.btn_search.setObjectName("SecondaryAction")
        self.btn_search.clicked.connect(self.run_library_search)
        self.btn_refresh_search = QPushButton("Refresh Index")
        self.btn_refresh_search.setObjectName("SecondaryAction")
        self.btn_refresh_search.clicked.connect(self.refresh_search_index)
        self.lbl_search_status = QLabel(
            "Search uses the local SQLite index built from MediaSorter's AI explanations and categories."
        )
        self.lbl_search_status.setObjectName("HintText")
        self.lbl_search_status.setWordWrap(True)
        self.search_results = QListWidget()
        self.search_results.setMinimumHeight(220)
        self.search_results.itemDoubleClicked.connect(lambda _item: self.open_selected_search_result())
        self.search_results.itemSelectionChanged.connect(self._update_search_action_state)
        self.btn_open_search_result = QPushButton("Open Selected")
        self.btn_open_search_result.setObjectName("SecondaryAction")
        self.btn_open_search_result.clicked.connect(self.open_selected_search_result)
        self.btn_open_search_folder = QPushButton("Show Folder")
        self.btn_open_search_folder.setObjectName("SecondaryAction")
        self.btn_open_search_folder.clicked.connect(self.open_selected_search_folder)

        search_action_row = QHBoxLayout()
        search_action_row.addWidget(self.edit_search, 1)
        search_action_row.addWidget(self.btn_search)
        search_action_row.addWidget(self.btn_refresh_search)

        search_result_actions = QHBoxLayout()
        search_result_actions.addWidget(self.btn_open_search_result)
        search_result_actions.addWidget(self.btn_open_search_folder)

        search_box = QGroupBox("Search Your Indexed Library")
        search_box.setObjectName("SidebarCard")
        search_layout = QVBoxLayout()
        search_layout.addWidget(self.lbl_search_status)
        search_layout.addLayout(search_action_row)
        search_layout.addWidget(self.search_results)
        search_layout.addLayout(search_result_actions)
        search_box.setLayout(search_layout)
        self.search_box = search_box

        btn_help = QPushButton("Help")
        btn_help.setObjectName("SecondaryAction")
        btn_help.clicked.connect(self.show_help)
        btn_about = QPushButton("About")
        btn_about.setObjectName("SecondaryAction")
        btn_about.clicked.connect(self.show_about)
        btn_support = QPushButton("Support / Buy")
        btn_support.setObjectName("AccentAction")
        btn_support.clicked.connect(self.open_support)

        support_hint = QLabel(
            "Use these links for support, licensing, privacy, refund, and release information."
        )
        support_hint.setObjectName("HintText")
        support_hint.setWordWrap(True)
        help_box = QGroupBox("Need Help Or Ready To Buy?")
        help_box.setObjectName("SupportCard")
        help_layout = QVBoxLayout()
        help_layout.addWidget(support_hint)
        help_layout.addWidget(btn_help)
        help_layout.addWidget(btn_about)
        help_layout.addWidget(btn_support)
        help_box.setLayout(help_layout)
        self.help_box = help_box

        self.advanced_box.setObjectName("SidebarCard")
        summary_box.setMinimumWidth(320)
        search_box.setMinimumWidth(320)
        help_box.setMinimumWidth(320)
        self.advanced_box.setMinimumWidth(320)

        # Bottom buttons
        self.btn_start = QPushButton("Start Sorting")
        self.btn_start.setObjectName("PrimaryAction")
        self.btn_start.clicked.connect(self.start_processing)
        self.btn_start.setEnabled(False)
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.btn_start)

        # Main layout
        self.setStyleSheet(
            """
            QMainWindow#AppRoot, QWidget#AppRoot {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #f6f7f9,
                    stop: 0.55 #f2f4f7,
                    stop: 1 #eef2f7
                );
                color: #111827;
                font-family: "Segoe UI Variable Text", "Aptos", "Segoe UI";
            }
            QScrollArea#ContentScroll {
                border: none;
                background: transparent;
            }
            QScrollArea#ContentScroll > QWidget > QWidget {
                background: transparent;
            }
            QGroupBox {
                margin-top: 14px;
                border: 1px solid #dde3ea;
                border-radius: 20px;
                padding: 16px;
                background: rgba(255, 255, 255, 0.94);
            }
            QGroupBox#SidebarCard {
                background: rgba(252, 253, 255, 0.97);
                border-color: #dbe1e8;
            }
            QGroupBox#StepCard {
                background: rgba(255, 255, 255, 0.96);
                border-color: #e0e5eb;
            }
            QGroupBox#ActivityCard {
                background: rgba(248, 251, 255, 0.97);
                border-color: #d7e1ed;
            }
            QGroupBox#PreviewCard {
                background: rgba(253, 254, 255, 0.98);
                border-color: #d8dee7;
            }
            QGroupBox#SupportCard {
                background: rgba(250, 252, 255, 0.97);
                border-color: #dce3ec;
            }
            QGroupBox#InnerCard {
                background: rgba(255, 255, 255, 0.9);
                border-color: #e2e7ee;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px;
                color: #1f2937;
            }
            QPushButton {
                min-height: 34px;
                padding: 7px 16px;
                border-radius: 14px;
                border: 1px solid #d8dee6;
                background: rgba(255, 255, 255, 0.96);
                color: #111827;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #ffffff;
                border-color: #c5d0dd;
            }
            QPushButton:pressed {
                background: #f2f5f9;
            }
            QPushButton:disabled {
                background: rgba(255, 255, 255, 0.72);
                color: #98a2b3;
                border-color: #e2e7ee;
            }
            QPushButton#PrimaryAction {
                min-height: 42px;
                border: none;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #0a84ff,
                    stop: 1 #4aa8ff
                );
                color: white;
                font-size: 15px;
                font-weight: 700;
                padding: 8px 22px;
            }
            QPushButton#PrimaryAction:hover {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #0077ed,
                    stop: 1 #379dff
                );
            }
            QPushButton#PrimaryAction:disabled {
                background: #cfd9e6;
                color: #f8fafc;
            }
            QPushButton#SecondaryAction {
                background: rgba(255, 255, 255, 0.97);
            }
            QPushButton#AccentAction {
                background: rgba(246, 249, 253, 0.98);
                border-color: #d1d9e5;
                color: #0a84ff;
            }
            QLabel#HintText {
                color: #667085;
            }
            QLabel#LeadText {
                font-size: 30px;
                font-weight: 700;
                color: #111827;
            }
            QLabel#KickerText {
                color: #64748b;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
                text-transform: uppercase;
            }
            QLabel#HeroHint {
                color: #667085;
                font-size: 14px;
            }
            QLabel#SectionLead {
                font-size: 15px;
                font-weight: 700;
                color: #1f2937;
            }
            QLabel#MediaPreview {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #fbfcfe,
                    stop: 1 #f4f7fb
                );
                border: 1px solid #d9e0e8;
                border-radius: 22px;
                padding: 16px;
                color: #6b7280;
            }
            QListWidget#ReviewHistory {
                border-radius: 16px;
                border: 1px solid #d9e0e8;
                background: rgba(255, 255, 255, 0.98);
                padding: 4px;
                color: #111827;
            }
            QListWidget#ReviewHistory::item {
                padding: 8px 10px;
                border-bottom: 1px solid #edf2f7;
            }
            QListWidget#ReviewHistory::item:selected {
                background: #e8f2ff;
                color: #111827;
            }
            QLineEdit, QComboBox, QListWidget {
                min-height: 34px;
                border-radius: 14px;
                border: 1px solid #d8dee6;
                padding: 6px 10px;
                background: rgba(255, 255, 255, 0.98);
                selection-background-color: #d7ebff;
            }
            QLineEdit:focus, QComboBox:focus, QListWidget:focus {
                border-color: #0a84ff;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QProgressBar {
                min-height: 24px;
                border-radius: 12px;
                border: 1px solid #d5dee8;
                background: rgba(255, 255, 255, 0.92);
                text-align: center;
                padding: 2px;
                color: #475467;
            }
            QProgressBar::chunk {
                border-radius: 10px;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #56a8ff,
                    stop: 1 #7fc1ff
                );
            }
            QCheckBox {
                spacing: 8px;
                color: #344054;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 6px;
                border: 1px solid #ccd4df;
                background: rgba(255, 255, 255, 0.98);
            }
            QCheckBox::indicator:checked {
                background: #0a84ff;
                border-color: #0a84ff;
            }
            QToolButton#LinkToggle {
                text-align: left;
                min-height: 32px;
                font-weight: 600;
                border: none;
                background: transparent;
                color: #667085;
                padding: 2px 0;
            }
            QToolButton#LinkToggle:hover {
                color: #0a84ff;
            }
            QLineEdit#SelectionDisplay {
                background: #f8fafc;
                border-color: #cfd8e3;
                color: #111827;
                font-weight: 600;
            }
            QMenuBar#AppMenuBar {
                background: rgba(255, 255, 255, 0.94);
                border: 1px solid #dde3ea;
                border-radius: 12px;
                padding: 4px 8px;
            }
            QMenuBar#AppMenuBar::item {
                padding: 6px 12px;
                border-radius: 8px;
                background: transparent;
                color: #334155;
            }
            QMenuBar#AppMenuBar::item:selected {
                background: #e8f2ff;
                color: #0a84ff;
            }
            QMenuBar#AppMenuBar::item:pressed {
                background: #dcecff;
                color: #0a84ff;
            }
            QMenu {
                background: #ffffff;
                border: 1px solid #d9e1ea;
                padding: 6px;
                color: #111827;
            }
            QMenu::item {
                padding: 6px 22px 6px 12px;
                border-radius: 6px;
                background: transparent;
                color: #111827;
            }
            QMenu::item:selected {
                background: #e8f2ff;
                color: #0a84ff;
            }
            """
        )

        self.setMinimumSize(1100, 760)
        self.resize(1360, 900)
        hero_kicker = QLabel("Local-first Windows app")
        hero_kicker.setObjectName("KickerText")
        header = QLabel("Organize your photo library")
        header.setObjectName("LeadText")
        header.setWordWrap(True)
        header_hint = QLabel(
            "Choose your folders and layout, then watch the live preview as MediaSorter files each item into a cleaner structure."
        )
        header_hint.setObjectName("HeroHint")
        header_hint.setWordWrap(True)

        self._active_focus_view = "setup"
        self._focus_actions = {}
        menu_bar = QMenuBar(self)
        menu_bar.setNativeMenuBar(False)
        menu_bar.setObjectName("AppMenuBar")
        view_menu = menu_bar.addMenu("View")
        self._focus_action_group = QActionGroup(self)
        self._focus_action_group.setExclusive(True)
        for key, label in (
            ("setup", "Setup"),
            ("review", "Review"),
            ("search", "Search"),
            ("tools", "Tools"),
            ("all", "Show All"),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, name=key: self._set_focus_view(name))
            self._focus_action_group.addAction(action)
            view_menu.addAction(action)
            self._focus_actions[key] = action
        tools_menu = menu_bar.addMenu("Tools")
        tools_menu.setObjectName("AppToolsMenu")
        action_manage_categories = QAction("Manage Categories", self)
        action_manage_categories.triggered.connect(self.open_category_manager)
        tools_menu.addAction(action_manage_categories)
        action_open_decision_log = QAction("Open Decision Log", self)
        action_open_decision_log.triggered.connect(self.open_decision_log)
        tools_menu.addAction(action_open_decision_log)
        action_open_data_folder = QAction("Open MediaSorter Data", self)
        action_open_data_folder.triggered.connect(self.open_app_data_folder)
        tools_menu.addAction(action_open_data_folder)
        self.menu_bar = menu_bar

        content_host = QWidget()
        content_host.setObjectName("ContentHost")
        content_layout = QHBoxLayout()
        content_layout.setSpacing(18)

        setup_column = QVBoxLayout()
        setup_column.addWidget(folder_box)
        setup_column.addWidget(organization_box)
        setup_column.addWidget(face_box)
        setup_column.addStretch(1)

        preview_column = QVBoxLayout()
        preview_column.addWidget(status_box)
        preview_column.addWidget(self.image_box, 1)
        preview_column.addStretch(1)

        sidebar_column = QVBoxLayout()
        sidebar_column.addWidget(summary_box)
        sidebar_column.addWidget(search_box)
        sidebar_column.addWidget(help_box)
        sidebar_column.addWidget(self.btn_toggle_advanced)
        sidebar_column.addWidget(self.advanced_box)
        sidebar_column.addStretch(1)

        content_layout.addLayout(setup_column, 4)
        content_layout.addLayout(preview_column, 4)
        content_layout.addLayout(sidebar_column, 3)
        content_host.setLayout(content_layout)

        scroll = QScrollArea()
        scroll.setObjectName("ContentScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(content_host)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(12)
        main_layout.addWidget(hero_kicker)
        main_layout.addWidget(header)
        main_layout.addWidget(header_hint)
        main_layout.addWidget(scroll, 1)
        main_layout.addLayout(bottom_layout)
        central_widget = QWidget()
        central_widget.setObjectName("AppRoot")
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)
        self.setMenuBar(self.menu_bar)

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
            self.chk_convert_videos.setChecked(bool(self.autorun_convert_videos))

        def _sync_structure_ui():
            try:
                is_custom = (self.cmb_structure.currentIndex() == (len(self._structure_presets) - 1))
                self.edit_structure.setEnabled(bool(is_custom))
                self.custom_structure_box.setVisible(bool(is_custom))
            except Exception:
                is_custom = False
            self._update_structure_preview()

        self.cmb_structure.currentIndexChanged.connect(_sync_structure_ui)
        self.edit_structure.textChanged.connect(lambda _: self._update_structure_preview())
        for checkbox in (
            self.chk_convert_videos,
            self.chk_interactive,
            self.chk_people,
            self.chk_trial,
        ):
            checkbox.stateChanged.connect(lambda _=None: self._update_run_summary())
        _sync_structure_ui()
        self._on_advanced_toggle()
        self._sync_interactive_review_visibility()
        self._reset_review_history()
        self._update_search_action_state()
        self._restore_ui_settings()
        self._update_folder_labels()
        self.update_ai_provider_ui()
        self._update_run_summary()
        self._suspend_ui_settings = False
        self._set_focus_view(self._load_ui_settings().get("focus_view") or "setup")
        if self.autorun_enabled:
            QTimer.singleShot(0, self.start_model_load)
        else:
            self._set_idle_progress("Status: Ready to configure your sort")
        if not self.autorun_enabled:
            QTimer.singleShot(500, self._maybe_run_onboarding)
        self.setUpdatesEnabled(True)

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

    def _load_ui_settings(self) -> dict:
        try:
            if UI_SETTINGS_FILE.exists():
                raw = json.loads(UI_SETTINGS_FILE.read_text(encoding="utf-8"))
                return raw if isinstance(raw, dict) else {}
        except Exception:
            pass
        return {}

    def _save_ui_settings(self) -> None:
        if bool(getattr(self, "_suspend_ui_settings", False)):
            return
        settings = {
            "input_folder": str(self.input_folder or ""),
            "output_folder": str(self.output_folder or ""),
            "structure_index": int(self.cmb_structure.currentIndex()),
            "custom_structure": str(self.edit_structure.text() or ""),
            "focus_view": str(getattr(self, "_active_focus_view", "setup") or "setup"),
            "show_advanced": bool(self.chk_show_advanced.isChecked()),
            "convert_videos": bool(self.chk_convert_videos.isChecked()),
            "people_grouping": bool(self.chk_people.isChecked()),
            "ai_provider": str(self._selected_ai_provider_id() or ""),
            "ai_model": str(self._selected_ai_model_id() or ""),
        }
        try:
            _atomic_write_json(UI_SETTINGS_FILE, settings)
        except Exception:
            pass

    def _restore_ui_settings(self) -> None:
        settings = self._load_ui_settings()
        self.input_folder = str(settings.get("input_folder") or self.input_folder or "")
        self.output_folder = str(settings.get("output_folder") or self.output_folder or "")
        self.chk_show_advanced.setChecked(bool(settings.get("show_advanced", self.chk_show_advanced.isChecked())))
        self.chk_convert_videos.setChecked(bool(settings.get("convert_videos", self.chk_convert_videos.isChecked())))
        self.chk_people.setChecked(bool(settings.get("people_grouping", self.chk_people.isChecked())))

        structure_index = settings.get("structure_index")
        try:
            if structure_index is not None:
                idx = max(0, min(int(structure_index), len(self._structure_presets) - 1))
                self.cmb_structure.setCurrentIndex(idx)
        except Exception:
            pass
        self.edit_structure.setText(str(settings.get("custom_structure") or self.edit_structure.text() or ""))

        saved_provider = str(settings.get("ai_provider") or "").strip()
        if saved_provider:
            idx = self.cmb_ai_provider.findData(saved_provider)
            if idx >= 0:
                self.cmb_ai_provider.blockSignals(True)
                self.cmb_ai_provider.setCurrentIndex(idx)
                self.cmb_ai_provider.blockSignals(False)
                try:
                    core.set_ai_provider(saved_provider)
                except Exception:
                    pass

        saved_model = str(settings.get("ai_model") or "").strip()
        if saved_model and self._selected_ai_provider_id() == core.AI_PROVIDER_CLIP_LOCAL:
            idx = self.cmb_ai_model.findData(saved_model)
            if idx >= 0:
                self.cmb_ai_model.blockSignals(True)
                self.cmb_ai_model.setCurrentIndex(idx)
                self.cmb_ai_model.blockSignals(False)
                try:
                    core.set_ai_model_profile(saved_model)
                except Exception:
                    pass

    def _update_folder_labels(self) -> None:
        source = self.input_folder or "Not selected yet"
        dest = self.output_folder or "Not selected yet"
        self.label_input.setText(source)
        self.label_output.setText(dest)
        self._update_run_summary()

    def _enabled_option_labels(self):
        options = []
        if self.chk_people.isChecked():
            options.append("people grouping after sort")
        if self.chk_convert_videos.isChecked():
            options.append("video conversion")
        return options

    def _set_focus_view(self, view_name: str) -> None:
        active = str(view_name or "setup").strip().lower()
        if active not in {"setup", "review", "search", "tools", "all"}:
            active = "setup"
        self._active_focus_view = active

        visible_by_view = {
            "setup": {"folder_box", "organization_box", "face_box", "status_box", "summary_box"},
            "review": {"status_box", "image_box", "summary_box"},
            "search": {"search_box", "summary_box"},
            "tools": {"help_box", "advanced_box", "summary_box"},
            "all": {
                "folder_box",
                "organization_box",
                "face_box",
                "status_box",
                "image_box",
                "summary_box",
                "search_box",
                "help_box",
                "advanced_box",
            },
        }
        visible_names = visible_by_view.get(active, visible_by_view["setup"])

        for attr in (
            "folder_box",
            "organization_box",
            "face_box",
            "status_box",
            "image_box",
            "summary_box",
            "search_box",
            "help_box",
            "advanced_box",
        ):
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            if attr == "advanced_box":
                should_show = attr in visible_names and bool(self.chk_show_advanced.isChecked())
            else:
                should_show = attr in visible_names
            widget.setVisible(bool(should_show))

        for key, action in self._focus_actions.items():
            action.blockSignals(True)
            action.setChecked(key == active)
            action.blockSignals(False)
        try:
            self.btn_toggle_advanced.setVisible(active in {"tools", "all"})
        except Exception:
            pass
        self._save_ui_settings()

    def _selected_search_result(self) -> dict | None:
        item = self.search_results.currentItem()
        if item is None:
            return None
        data = item.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def _update_search_action_state(self) -> None:
        has_selection = self._selected_search_result() is not None
        self.btn_open_search_result.setEnabled(has_selection)
        self.btn_open_search_folder.setEnabled(has_selection)

    def _refresh_category_selectors(self, preferred: str | None = None) -> None:
        current_review = preferred or str(self.review_category_combo.currentText() or "").strip()
        current_hidden = preferred or str(self.combo_category.currentText() or "").strip()

        self.review_category_combo.blockSignals(True)
        self.review_category_combo.clear()
        self.review_category_combo.addItems(core.CATEGORIES)
        review_idx = self.review_category_combo.findText(current_review, Qt.MatchFixedString | Qt.MatchCaseSensitive)
        if review_idx >= 0:
            self.review_category_combo.setCurrentIndex(review_idx)
        self.review_category_combo.blockSignals(False)

        self.combo_category.blockSignals(True)
        self.combo_category.clear()
        self.combo_category.addItems(core.CATEGORIES)
        hidden_idx = self.combo_category.findText(current_hidden, Qt.MatchFixedString | Qt.MatchCaseSensitive)
        if hidden_idx >= 0:
            self.combo_category.setCurrentIndex(hidden_idx)
        self.combo_category.blockSignals(False)

    def _apply_category_catalog(self, new_categories: list[str], renames: dict | None = None) -> list[str]:
        seen = set()
        cleaned = []
        for raw in list(new_categories or []):
            cat = str(raw or "").strip()
            key = cat.casefold()
            if not cat or key in seen:
                continue
            seen.add(key)
            cleaned.append(cat)

        if not cleaned:
            cleaned = list(core.DEFAULT_CATEGORIES[:])

        rename_map = dict(renames or {})
        for old, new in rename_map.items():
            if old == new:
                continue
            try:
                if old in core.PROTOTYPES and new not in core.PROTOTYPES:
                    core.PROTOTYPES[new] = core.PROTOTYPES.pop(old)
                elif old in core.PROTOTYPES and new in core.PROTOTYPES:
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

        valid = set(cleaned)
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

        core.CATEGORIES = cleaned
        _atomic_write_text(CATEGORIES_FILE, "\n".join(core.CATEGORIES) + "\n")
        self._refresh_category_selectors()

        try:
            _atomic_write_json(CORRECTION_FILE, core.CORRECTIONS)
        except Exception:
            pass
        _save_prototypes()

        if core._MODEL_READY:
            lock = getattr(core, "_INFER_LOCK", None)
            if lock is not None:
                with lock:
                    _refresh_text_features()
                    _refresh_proto_features()
            else:
                _refresh_text_features()
                _refresh_proto_features()

        return cleaned

    def add_live_category_from_review_panel(self) -> None:
        text = str(self.edit_new_review_category.text() or "").strip()
        if not text:
            return

        existing = {str(cat).casefold(): str(cat) for cat in core.CATEGORIES}
        key = text.casefold()
        if key in existing:
            canonical = existing[key]
            self._refresh_category_selectors(preferred=canonical)
            self.edit_new_review_category.clear()
            self.review_status_label.setText(
                f'"{canonical}" already exists. You can apply it immediately to the selected file.'
            )
            self.on_review_history_selection_changed()
            return

        updated = self._apply_category_catalog(list(core.CATEGORIES) + [text])
        canonical = next((cat for cat in updated if cat.casefold() == key), text)
        self._refresh_category_selectors(preferred=canonical)
        self.edit_new_review_category.clear()
        self.review_status_label.setText(
            f'Added new category "{canonical}". New files can be classified into it right away.'
        )
        self.on_review_history_selection_changed()
        idx = self.review_category_combo.findText(canonical, Qt.MatchFixedString | Qt.MatchCaseSensitive)
        if idx >= 0:
            self.review_category_combo.setCurrentIndex(idx)

    def _review_summary_text(self, entry: dict) -> str:
        explanation = re.sub(r"\s+", " ", str((entry or {}).get("explanation") or "")).strip()
        summary = ""
        m = re.search(r"The clearest visual cues were (.+?)\.", explanation, re.IGNORECASE)
        if m:
            summary = f"AI sees: {m.group(1).strip()}"
        else:
            m = re.search(
                r"visual cues it matched most were consistent with (.+?)\.",
                explanation,
                re.IGNORECASE,
            )
            if m:
                summary = f"AI sees: {m.group(1).strip()}"
            else:
                m = re.search(r"It matched cues that looked most like (.+?)\.", explanation, re.IGNORECASE)
                if m:
                    summary = f"AI basis: cues most aligned with {m.group(1).strip()}."
                else:
                    m = re.search(r"The closest visual ideas in that bucket were (.+?)\.", explanation, re.IGNORECASE)
                    if m:
                        summary = f"AI basis: category prompt cues included {m.group(1).strip()}."
                    else:
                        m = re.search(r"The AI's strongest read was '([^']+)'", explanation)
                        if m:
                            summary = f"AI basis: strongest category match was {m.group(1).strip()}."

        if not summary:
            current_category = str((entry or {}).get("current_category") or (entry or {}).get("auto_category") or "").strip()
            if current_category:
                summary = f"AI basis: quick category summary for {current_category.lower()}."

        if len(summary) > 110:
            summary = summary[:107].rstrip() + "..."
        return summary or "AI basis: classification recorded."

    def _review_text_parts(self, entry: dict) -> tuple[str, str, str]:
        file_name = os.path.basename(str((entry or {}).get("source_path") or "")) or "Unnamed file"
        auto_category = str((entry or {}).get("auto_category") or "Uncategorized")
        current_category = str((entry or {}).get("current_category") or auto_category)
        if current_category != auto_category:
            category_text = f"Auto: {auto_category} -> Final: {current_category}"
        else:
            category_text = f"Auto: {auto_category}"
        return file_name, category_text, self._review_summary_text(entry)

    def _format_review_history_text(self, entry: dict) -> str:
        file_name, category_text, summary = self._review_text_parts(entry)
        return f"{file_name}\n{category_text}\n{summary}"

    def _review_thumbnail_for_entry(self, entry: dict, size: QSize) -> QPixmap:
        width = max(1, int(size.width() or 64))
        height = max(1, int(size.height() or 64))
        path = str((entry or {}).get("source_path") or "").strip()
        is_video = bool((entry or {}).get("is_video"))

        try:
            mtime = int(os.path.getmtime(path)) if path and os.path.exists(path) else 0
        except Exception:
            mtime = 0
        key = (path, is_video, width, height, mtime)
        cached = self._review_thumbnail_cache.get(key)
        if cached is not None:
            self._review_thumbnail_cache.move_to_end(key)
            return cached

        if is_video or not path or not os.path.exists(path):
            pixmap = QPixmap(width, height)
            pixmap.fill(QColor("#eef2f7"))
            painter = QPainter(pixmap)
            painter.setPen(QColor("#64748b"))
            painter.drawText(pixmap.rect(), Qt.AlignCenter, "VIDEO")
            painter.end()
        else:
            pixmap = QPixmap()
            try:
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                src_size = reader.size()
                if src_size.isValid() and src_size.width() > 0 and src_size.height() > 0:
                    scale = min(width / float(src_size.width()), height / float(src_size.height()))
                    reader.setScaledSize(
                        QSize(
                            max(1, int(src_size.width() * scale)),
                            max(1, int(src_size.height() * scale)),
                        )
                    )
                image = reader.read()
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
            except Exception:
                pixmap = QPixmap()

            if pixmap.isNull():
                try:
                    pixmap = QPixmap(path)
                except Exception:
                    pixmap = QPixmap()
            if pixmap.isNull():
                pixmap = QPixmap(width, height)
                pixmap.fill(QColor("#f4f7fb"))
            else:
                pixmap = pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        self._review_thumbnail_cache[key] = pixmap
        while len(self._review_thumbnail_cache) > self._review_thumbnail_cache_limit:
            self._review_thumbnail_cache.popitem(last=False)
        return pixmap

    def _reset_review_history(self) -> None:
        self.review_history.clear()
        self.review_status_label.setText(
            "Recent automatic categorizations will appear here while MediaSorter is sorting."
        )
        self.review_detail_label.setText(
            "Select a sorted item below if you want to review or correct its category."
        )
        self.review_category_combo.setEnabled(False)
        self.btn_apply_review_category.setEnabled(False)

    def _explanation_source_label(self, source: str) -> str:
        value = str(source or "").strip().lower()
        mapping = {
            "user_override": "Explanation type: User-confirmed change",
            "system_fallback": "Explanation type: System fallback",
            "rule_based_override": "Explanation type: Rule-based override",
            "rule_based_video": "Explanation type: Rule-based video handling",
            "category_template": "Explanation type: Quick category summary",
            "unknown": "Explanation type: Unknown",
        }
        return mapping.get(value, f"Explanation type: {value or 'Unknown'}")

    def _append_review_history_entry(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        self._set_focus_view("review")

        scrollbar = self.review_history.verticalScrollBar()
        should_auto_scroll = scrollbar.value() >= max(0, scrollbar.maximum() - 4)
        had_selection = self.review_history.currentItem() is not None

        entry = {
            "source_path": str(payload.get("source_path") or ""),
            "dest_path": str(payload.get("dest_path") or ""),
            "auto_category": str(payload.get("category") or "Uncategorized"),
            "current_category": str(payload.get("category") or "Uncategorized"),
            "is_video": bool(payload.get("is_video")),
            "explanation": str(payload.get("explanation") or ""),
            "explanation_source": str(payload.get("explanation_source") or ""),
        }

        item = QListWidgetItem(self._format_review_history_text(entry))
        item.setData(Qt.UserRole, entry)
        item.setSizeHint(QSize(0, 92))
        self.review_history.addItem(item)

        count = self.review_history.count()
        self.review_status_label.setText(
            f"{count} item{'s' if count != 1 else ''} reviewed automatically so far. Scroll to revisit and correct any category."
        )

        if should_auto_scroll and count > 0:
            self.review_history.setCurrentRow(count - 1)
        elif not had_selection and count > 0:
            self.review_history.setCurrentRow(count - 1)

        if should_auto_scroll:
            self.review_history.scrollToBottom()

    def _selected_review_history_entry(self) -> tuple[QListWidgetItem | None, dict | None]:
        item = self.review_history.currentItem()
        if item is None:
            return None, None
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict):
            return item, None
        return item, data

    def on_review_history_selection_changed(self) -> None:
        _item, entry = self._selected_review_history_entry()
        if not entry:
            self.review_detail_label.setText(
                "Select a sorted item below if you want to review or correct its category."
            )
            self.image_explanation_state_label.setText("Explanation type: Waiting for classification details")
            self.review_category_combo.setEnabled(False)
            self.btn_apply_review_category.setEnabled(False)
            return

        category = str(entry.get("current_category") or entry.get("auto_category") or "Uncategorized")
        try:
            self._show_current_media(
                str(entry.get("source_path") or ""),
                category,
                bool(entry.get("is_video")),
            )
            if str(entry.get("explanation") or "").strip():
                self.image_explanation_label.setText(str(entry.get("explanation") or "").strip())
            self.image_explanation_state_label.setText(
                self._explanation_source_label(str(entry.get("explanation_source") or "unknown"))
            )
        except Exception:
            pass
        idx = self.review_category_combo.findText(category, Qt.MatchFixedString | Qt.MatchCaseSensitive)
        if idx >= 0:
            self.review_category_combo.setCurrentIndex(idx)

        if bool(entry.get("is_video")):
            self.review_detail_label.setText(
                "Videos stay in the Videos destination in this live review panel."
            )
            self.review_category_combo.setEnabled(False)
            self.btn_apply_review_category.setEnabled(False)
            return

        self.review_detail_label.setText(
            str(entry.get("explanation") or "Choose a different category if MediaSorter got this one wrong.")
        )
        self.review_category_combo.setEnabled(True)
        self.btn_apply_review_category.setEnabled(True)

    def apply_selected_review_override(self) -> None:
        item, entry = self._selected_review_history_entry()
        if item is None or not entry:
            return
        if bool(entry.get("is_video")):
            return

        new_category = str(self.review_category_combo.currentText() or "").strip()
        current_category = str(entry.get("current_category") or entry.get("auto_category") or "")
        if not new_category or new_category == current_category:
            return

        self.btn_apply_review_category.setEnabled(False)
        try:
            result = core.apply_live_category_override(
                source_path=str(entry.get("source_path") or ""),
                current_dest_path=str(entry.get("dest_path") or ""),
                new_category=new_category,
                output_folder=self.output_folder,
                structure_pattern=self._get_structure_pattern(),
                previous_category=current_category,
            )
        except Exception as e:
            QMessageBox.warning(self, "Category Override Failed", str(e))
            self.btn_apply_review_category.setEnabled(True)
            return

        entry["current_category"] = str(result.get("category") or new_category)
        entry["dest_path"] = str(result.get("dest_path") or entry.get("dest_path") or "")
        entry["explanation"] = str(result.get("explanation") or entry.get("explanation") or "")
        item.setData(Qt.UserRole, entry)
        item.setText(self._format_review_history_text(entry))
        self.review_detail_label.setText(str(entry.get("explanation") or "Category updated."))
        self.review_status_label.setText(
            f'Updated "{os.path.basename(str(entry.get("source_path") or ""))}" to "{entry.get("current_category")}". Sorting can continue while you review older items.'
        )
        self.btn_apply_review_category.setEnabled(True)

    def _set_current_media_placeholder(self, title: str, explanation: str, preview_text: str | None = None) -> None:
        self._current_preview_pixmap = None
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText(preview_text or "Current file preview will appear here.")
        self.image_filename_label.setText(title)
        self.image_explanation_label.setText(explanation)
        self.image_explanation_state_label.setText("Explanation type: Waiting for classification details")

    def _apply_current_preview_pixmap(self) -> None:
        pixmap = self._current_preview_pixmap
        if pixmap is None or pixmap.isNull():
            return

        target_size = self.image_label.size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            target_size = pixmap.size()
        scaled = pixmap.scaled(target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.image_label.setText("")
        self.image_label.setPixmap(scaled)

    def _show_current_media(self, file_path: str, category: str, is_video: bool) -> None:
        self._set_focus_view("review")
        file_name = os.path.basename(file_path) or "Current file"
        safe_category = str(category or "Uncategorized")
        self.current_path = file_path or ""

        if is_video:
            self.current_img = None
            self._current_preview_pixmap = None
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("Video preview unavailable.\nMediaSorter is processing this video now.")
            self.image_filename_label.setText(file_name)
            self.image_explanation_label.setText(
                f'MediaSorter is treating "{file_name}" as a video and placing it in the "{safe_category}" folder.'
            )
            self.image_explanation_state_label.setText("Explanation type: Rule-based video handling")
            return

        self.current_img = None
        pixmap = QPixmap()
        try:
            img = load_image_for_ai(file_path)
            if img is not None:
                self.current_img = img
                pixmap = pil_to_qpixmap(img)
        except Exception:
            pixmap = QPixmap()

        if pixmap.isNull():
            try:
                pixmap = QPixmap(file_path)
            except Exception:
                pixmap = QPixmap()

        self._current_preview_pixmap = pixmap if not pixmap.isNull() else None
        if self._current_preview_pixmap is not None:
            self._apply_current_preview_pixmap()
        else:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("Preview unavailable for this file.")

        self.image_filename_label.setText(file_name)
        self.image_explanation_label.setText(
            f'MediaSorter is categorizing "{file_name}" as "{safe_category}" based on the current AI prediction and your selected folder layout.'
        )
        self.image_explanation_state_label.setText("Explanation type: Waiting for classification details")

    def _update_run_summary(self) -> None:
        if not hasattr(self, "lbl_summary_state"):
            return

        ready_items = []
        missing_items = []

        if self.input_folder:
            ready_items.append("source folder selected")
        else:
            missing_items.append("choose a source folder")

        if self.output_folder:
            ready_items.append("destination folder selected")
        else:
            missing_items.append("choose a destination folder")

        provider_installed = core.is_ai_provider_installed(self._selected_ai_provider_id())
        provider_id = self._selected_ai_provider_id()

        if core._MODEL_LOAD_ERROR:
            missing_items.append("fix the AI runtime error")
        elif core._MODEL_READY:
            ready_items.append("AI runtime ready")
        elif provider_id == core.AI_PROVIDER_NONE:
            ready_items.append("heuristics-only mode ready")
        elif not provider_installed:
            missing_items.append("install the selected AI runtime")
        else:
            ready_items.append("AI runtime will load when sorting starts")

        if self._run_active:
            self.lbl_summary_state.setText("Run in progress")
            self.lbl_summary_checklist.setText(
                "MediaSorter is currently working.\n\n"
                f"Current phase: {self._run_phase}"
            )
        elif missing_items:
            self.lbl_summary_state.setText("Finish setup before starting")
            self.lbl_summary_checklist.setText(
                "Ready:\n- "
                + ("\n- ".join(ready_items) if ready_items else "nothing yet")
                + "\n\nStill needed:\n- "
                + "\n- ".join(missing_items)
            )
        else:
            self.lbl_summary_state.setText("Ready to sort")
            self.lbl_summary_checklist.setText(
                "Everything needed for a run is ready.\n\n"
                "When you start, MediaSorter will copy your organized results into the destination folder."
            )

        pattern = self._get_structure_pattern()
        self.lbl_summary_paths.setText(
            f"From: {self.input_folder or 'Not selected yet'}\n"
            f"Into: {self.output_folder or 'Not selected yet'}"
        )
        self.lbl_summary_structure.setText(
            f"Layout pattern: {pattern}\n"
            f"{self.lbl_structure_preview.text()}"
        )
        options = self._enabled_option_labels()
        self.lbl_summary_features.setText(", ".join(options) if options else "Standard batch sort only")

        provider_name = core.get_ai_provider_display_name(self._selected_ai_provider_id())
        model_name = core.get_ai_model_display_name(self._selected_ai_model_id())
        if core._MODEL_LOAD_ERROR:
            ai_state = f"Problem loading {provider_name}: {core._MODEL_LOAD_ERROR}"
        elif core._MODEL_READY:
            ai_state = f"{provider_name} ready"
        elif provider_id == core.AI_PROVIDER_NONE:
            ai_state = f"{provider_name} ready"
        elif not provider_installed:
            ai_state = f"{provider_name} not installed"
        else:
            ai_state = f"{provider_name} will load when needed"

        if self._selected_ai_provider_id() == core.AI_PROVIDER_CLIP_LOCAL:
            ai_state = f"{ai_state}\nModel: {model_name}"
        self.lbl_summary_ai.setText(ai_state)

        can_start = (
            bool(self.input_folder)
            and bool(self.output_folder)
            and bool(provider_installed)
            and not bool(core._MODEL_LOAD_ERROR)
            and not bool(self._run_active)
        )
        self.btn_start.setEnabled(can_start)

    def _on_advanced_toggle(self) -> None:
        on = bool(self.chk_show_advanced.isChecked())
        try:
            self._set_focus_view(getattr(self, "_active_focus_view", "setup"))
        except Exception:
            pass
        try:
            self.btn_toggle_advanced.blockSignals(True)
            self.btn_toggle_advanced.setChecked(on)
            self.btn_toggle_advanced.blockSignals(False)
            self.btn_toggle_advanced.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
            self.btn_toggle_advanced.setText("Hide Power-User Tools" if on else "Show Power-User Tools")
        except Exception:
            pass

    def _sync_interactive_review_visibility(self) -> None:
        try:
            self._set_focus_view(getattr(self, "_active_focus_view", "setup"))
        except Exception:
            pass

    def _update_structure_preview(self) -> None:
        # Use the currently loaded image if available; otherwise, a representative example.
        try:
            preset_label = str(self.cmb_structure.currentText() or "By category").strip() or "By category"
            self.lbl_structure_selected.setText(f"Selected layout: {preset_label}")
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
            self.lbl_structure_preview.setText(f"Example folder result: {preview}")
        except Exception:
            pass
        self._save_ui_settings()
        self._update_run_summary()

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
            self.btn_install_ai_provider.setText("Install Selected AI Runtime")
            self.btn_install_ai_provider.setEnabled(not installed)
        self._update_run_summary()

    def _queue_model_load(self, action: str | None = None) -> None:
        if action:
            self._pending_model_action = str(action)
        self.start_model_load()

    def _set_idle_progress(self, status_text: str | None = None) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Idle")
        if status_text:
            self.status_label.setText(str(status_text))

    def start_model_load(self) -> None:
        try:
            if self.model_thread is not None and self.model_thread.isRunning():
                return
        except Exception:
            pass
        self._run_phase = "Preparing AI runtime"
        self.progress.setRange(0, 0)
        self.progress.setFormat("Loading AI runtime...")
        self.status_label.setText("Status: Loading AI runtime...")
        self._update_run_summary()
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
        self._save_ui_settings()
        self._set_idle_progress("Status: AI runtime will load when you start sorting.")
        self._run_phase = "AI runtime deferred until needed"
        self._update_run_summary()

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
        self._save_ui_settings()
        self._set_idle_progress("Status: AI runtime will load when you start sorting.")
        self._run_phase = "AI runtime deferred until needed"
        self._update_run_summary()

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
        self._run_phase = "Installing AI runtime"
        self.progress.setRange(0, 0)
        self.progress.setFormat("Installing AI runtime...")
        self.status_label.setText("Status: Installing AI runtime...")
        self._update_run_summary()

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
        self._update_run_summary()

    def on_model_status(self, message: str):
        try:
            self.status_label.setText(f"Status: {message}")
        except Exception:
            pass
        self._run_phase = str(message or self._run_phase)
        self._update_run_summary()

    def on_model_loaded(self, ok: bool, message: str):
        self.update_ai_provider_ui()
        if ok:
            self._set_idle_progress(f"Status: Ready - {message}")
            self._run_phase = "Ready"
            pending = str(self._pending_model_action or "").strip().lower()
            self._pending_model_action = None
            if pending == "start_processing":
                QTimer.singleShot(0, self.start_processing)
                self._update_run_summary()
                return
            if self.autorun_enabled:
                self.status_label.setText(f"Status: {message} (autorun)")
                QTimer.singleShot(0, self.start_processing)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("AI model failed to load")
            self.status_label.setText("Status: AI model failed to load")
            self._run_phase = "AI runtime failed to load"
            self._pending_model_action = None
            QMessageBox.critical(self, "AI Model Error", message)
        self._update_run_summary()

    # ---------------------------
    # Folder selection
    # ---------------------------
    def select_input(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder")
        if folder:
            self.input_folder = folder
            self._update_folder_labels()
            self._save_ui_settings()

    def select_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_folder = folder
            self._update_folder_labels()
            self._save_ui_settings()

    def open_app_data_folder(self):
        try:
            path = str(core.DATA_DIR)
            os.makedirs(path, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                QMessageBox.warning(self, "App Data Folder", f"Could not open:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "App Data Folder", f"Could not open app data folder.\n\n{e}")

    # ---------------------------
    # Help / About / Support
    # ---------------------------
    def _save_onboarding_marker(self) -> None:
        try:
            self._onboarding_marker.parent.mkdir(parents=True, exist_ok=True)
            self._onboarding_marker.write_text("ok\n", encoding="utf-8")
        except Exception:
            pass

    def _maybe_run_onboarding(self) -> None:
        if self._onboarding_ran:
            return
        self._onboarding_ran = True
        if self.autorun_enabled:
            return
        try:
            if self._onboarding_marker.exists():
                return
        except Exception:
            pass

        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Information)
        prompt.setWindowTitle("Welcome To MediaSorter")
        prompt.setText(
            "Set up your first sort in under a minute.\n\n"
            "MediaSorter will guide you through input folder, output folder, and basic options."
        )
        prompt.addButton("Start Setup", QMessageBox.AcceptRole)
        skip_btn = prompt.addButton("Skip", QMessageBox.RejectRole)
        prompt.exec()
        if prompt.clickedButton() is skip_btn:
            log_product_event("onboarding_skipped")
            self._save_onboarding_marker()
            return

        if not self.input_folder:
            self.select_input()
        if not self.output_folder:
            self.select_output()

        preset_names = [name for (name, _) in self._structure_presets if name != "Custom..."]
        current_idx = max(0, min(self.cmb_structure.currentIndex(), len(preset_names) - 1))
        choice, ok = QInputDialog.getItem(
            self,
            "Folder Structure",
            "Choose a folder layout:",
            preset_names,
            current_idx,
            False,
        )
        if ok and choice:
            idx = self.cmb_structure.findText(choice, Qt.MatchFixedString)
            if idx >= 0:
                self.cmb_structure.setCurrentIndex(idx)

        log_product_event(
            "onboarding_completed",
            {
                "has_input": bool(self.input_folder),
                "has_output": bool(self.output_folder),
                "trial_enabled": False,
            },
        )
        self._save_onboarding_marker()

    def show_help(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Help")
        box.setText(
            "MediaSorter Help:\n\n"
            "1. Choose your source library (where your originals live).\n"
            "2. Choose a destination folder for the organized copy.\n"
            "3. Pick a folder layout.\n"
            "4. If you want person-grouping:\n"
            "   - turn on 'Group people after sorting finishes' for this run\n"
            "   - or use 'Scan Existing Output For People' later\n"
            "5. Click Start Sorting.\n"
            "6. Watch the Live Review panel to see what MediaSorter is placing and why.\n\n"
            f"Decision log: {core.get_decision_log_path()}"
        )
        btn_support = box.addButton("Support / Buy", QMessageBox.ActionRole)
        btn_privacy = box.addButton("Privacy", QMessageBox.ActionRole)
        btn_terms = box.addButton("Terms", QMessageBox.ActionRole)
        btn_refund = box.addButton("Refund Policy", QMessageBox.ActionRole)
        btn_legal = box.addButton("Legal + Marketing", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()

        clicked = box.clickedButton()
        if clicked is btn_support:
            self.open_support()
        elif clicked is btn_privacy:
            self._open_external_link(PRIVACY_URL, "Privacy Policy")
        elif clicked is btn_terms:
            self._open_external_link(TERMS_URL, "Terms of Use")
        elif clicked is btn_refund:
            self._open_external_link(REFUND_URL, "Refund Policy")
        elif clicked is btn_legal:
            self._open_external_link(LEGAL_INFO_URL, "Legal + Marketing Guide")

    def _open_external_link(self, url: str, label: str) -> None:
        target = str(url or "").strip()
        if not target:
            QMessageBox.warning(self, f"{label} Link Missing", f"No URL configured for {label}.")
            return
        if not QDesktopServices.openUrl(QUrl(target)):
            QMessageBox.warning(self, f"{label} Link Error", f"Could not open:\n{target}")

    def show_about(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("About MediaSorter")
        box.setText(
            "MediaSorter\n"
            "Local-first media organization for Windows.\n\n"
            "Organizes photos and videos into a cleaner folder structure without uploading your library."
        )
        btn_support = box.addButton("Support / Buy", QMessageBox.ActionRole)
        btn_privacy = box.addButton("Privacy", QMessageBox.ActionRole)
        btn_terms = box.addButton("Terms", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_support:
            self.open_support()
        elif clicked is btn_privacy:
            self._open_external_link(PRIVACY_URL, "Privacy Policy")
        elif clicked is btn_terms:
            self._open_external_link(TERMS_URL, "Terms of Use")

    def open_support(self):
        log_product_event("support_clicked", {"trial_active": bool(self._trial_active)})
        self._open_external_link(SUPPORT_URL, "Support / Buy")

    def refresh_search_index(self):
        stats = core.rebuild_search_index_from_decision_log()
        indexed = int((stats or {}).get("indexed") or 0)
        seen = int((stats or {}).get("seen_sort_records") or 0)
        self.lbl_search_status.setText(
            f"Indexed {indexed} files from {seen} sort records. Leave the search box blank to browse recent matches."
        )
        if (self.edit_search.text() or "").strip() or indexed > 0:
            self.run_library_search()

    def run_library_search(self):
        self._set_focus_view("search")
        query = str(self.edit_search.text() or "").strip()
        results = core.search_media_index(query, limit=50)
        self.search_results.clear()

        if not results:
            if query:
                self.lbl_search_status.setText(
                    f'No indexed files matched "{query}". Try a broader term or refresh the index.'
                )
            else:
                self.lbl_search_status.setText(
                    "No indexed files yet. Run a sort or refresh the index from the decision log."
                )
            self._update_search_action_state()
            return

        for result in results:
            file_name = str(result.get("file_name") or "Unnamed file")
            category = str(result.get("category") or "Uncategorized")
            explanation = re.sub(r"\s+", " ", str(result.get("explanation") or "")).strip()
            if len(explanation) > 120:
                explanation = explanation[:117].rstrip() + "..."
            item = QListWidgetItem(f"{file_name}\n{category} - {explanation}")
            item.setData(Qt.UserRole, dict(result))
            self.search_results.addItem(item)

        if query:
            self.lbl_search_status.setText(
                f'Found {len(results)} indexed matches for "{query}". Double-click a result to open it.'
            )
        else:
            self.lbl_search_status.setText(
                f"Showing {len(results)} recent indexed files. Enter a search term to filter."
            )

        if self.search_results.count() > 0:
            self.search_results.setCurrentRow(0)
        self._update_search_action_state()

    def open_selected_search_result(self):
        result = self._selected_search_result()
        if not result:
            return
        path = str(result.get("path") or "").strip()
        if not path:
            QMessageBox.warning(self, "Search Result", "This result does not have a file path.")
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, "Search Result Missing", f"File not found:\n{path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(self, "Search Result", f"Could not open:\n{path}")

    def open_selected_search_folder(self):
        result = self._selected_search_result()
        if not result:
            return
        path = str(result.get("path") or "").strip()
        folder = os.path.dirname(path) if path else ""
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "Search Result Folder", f"Folder not found:\n{folder or path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            QMessageBox.warning(self, "Search Result Folder", f"Could not open:\n{folder}")

    def open_sequoiaview(self):
        ok, _result = core.launch_sequoiaview()
        if ok:
            self.status_label.setText("Status: Opened SequoiaView")
            return

        searched = core.get_sequoiaview_search_paths()
        preview = "\n".join([f"- {p}" for p in searched[:6]]) if searched else "- (no search paths available)"
        msg = (
            "SequoiaView was not found on this computer.\n\n"
            "Checked:\n"
            f"{preview}\n\n"
            "Open the official SequoiaView website to install it?"
        )
        q = QMessageBox.question(
            self,
            "SequoiaView Not Found",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if q == QMessageBox.Yes:
            self._open_external_link(core.SEQUOIAVIEW_URL, "SequoiaView")

    def _run_elapsed_label(self) -> str:
        if self._run_started_monotonic is None:
            return "unknown duration"
        try:
            sec = max(0, int(time.monotonic() - float(self._run_started_monotonic)))
        except Exception:
            return "unknown duration"
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        if h > 0:
            return f"{h:d}:{m:02d}:{s:02d}"
        return f"{m:d}:{s:02d}"

    def _show_completion_dialog(self, *, images: int, videos: int, failed: int) -> None:
        processed = int(images or 0) + int(videos or 0)
        elapsed = self._run_elapsed_label()
        log_product_event(
            "run_completed",
            {
                "images": int(images or 0),
                "videos": int(videos or 0),
                "failed": int(failed or 0),
                "processed": int(processed),
                "elapsed": str(elapsed),
                "trial_active": bool(self._trial_active),
                "trial_total_discovered": int(self._trial_total_discovered or 0),
                "trial_processed": int(len(self.files) if self._trial_active else processed),
            },
        )
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Sorting Complete")
        text = (
            f"Sorted {processed} items in {elapsed}.\n\n"
            f"Images: {int(images or 0)}\n"
            f"Videos: {int(videos or 0)}\n"
            f"Failed: {int(failed or 0)}"
        )
        if self._trial_active:
            text += (
                f"\n\nTrial processed {len(self.files)} of {self._trial_total_discovered} discovered items."
                "\nUse Support / Buy to run your full library."
            )
        msg.setText(text)
        btn_open = msg.addButton("Open Output Folder", QMessageBox.ActionRole)
        btn_buy = None
        if self._trial_active:
            btn_buy = msg.addButton("Unlock Full Run", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Ok)
        msg.exec()

        if msg.clickedButton() is btn_open:
            try:
                QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_folder))
            except Exception:
                pass
        elif btn_buy is not None and msg.clickedButton() is btn_buy:
            self.open_support()

    # Backward-compatible alias for existing signal hookups/custom scripts.
    def open_paypal(self):
        self.open_support()

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
            self.status_label.setText("Status: Loading AI runtime before sorting...")
            self._run_phase = "Loading AI runtime before sorting"
            self._update_run_summary()
            self._queue_model_load("start_processing")
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
        self._trial_total_discovered = len(self.files)
        self._trial_active = bool(self.chk_trial.isChecked())
        if self._trial_active and len(self.files) > self.trial_limit:
            self.files = self.files[: self.trial_limit]

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
        if self._trial_active and self._trial_total_discovered > len(self.files):
            self.status_label.setText(
                f"Status: Found {self._trial_total_discovered} media files (trial will process first {len(self.files)})"
            )
            self._run_phase = f"Trial ready: processing {len(self.files)} of {self._trial_total_discovered} items"
        else:
            self.status_label.setText(f"Status: Found {len(self.files)} media files")
            self._run_phase = f"Ready to process {len(self.files)} items"
        self.interactive_mode = False
        self._sync_interactive_review_visibility()
        if self.chk_people.isChecked():
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
        self.combo_category.setEnabled(False)
        self.btn_confirm.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.progress.setFormat("%v/%m ETA: estimating...")

        if len(self.files) == 0:
            QMessageBox.information(self, "Empty Folder", "No media files found.")
            return
        self._interactive_failed = 0
        self._run_started_monotonic = time.monotonic()
        self._run_active = True
        self._reset_review_history()
        self._set_focus_view("review")
        self._set_current_media_placeholder(
            "Waiting for the first file",
            "MediaSorter will show each item here and explain the category it is assigning during the run.",
            "Current file preview will appear here as soon as sorting begins.",
        )
        self._update_run_summary()
        log_product_event(
            "run_started",
            {
                "interactive": bool(self.interactive_mode),
                "trial_enabled": bool(self.chk_trial.isChecked()),
                "trial_active": bool(self._trial_active),
                "files_discovered": int(self._trial_total_discovered or len(self.files)),
                "files_to_process": int(len(self.files)),
            },
        )
        self._sync_interactive_review_visibility()
        self.start_auto_thread()

    # ---------------------------
    # Interactive mode
    # ---------------------------
    def process_next_interactive(self):
        if self.index >= len(self.files):
            self.status_label.setText("Status: Complete")
            self._run_phase = "Run complete"
            self._run_active = False
            self.interactive_mode = False
            self._sync_interactive_review_visibility()
            self.combo_category.setEnabled(False)
            self.btn_confirm.setEnabled(False)
            self.btn_skip.setEnabled(False)
            self._update_run_summary()
            images = sum(1 for f in self.files if str(f).lower().endswith(IMAGE_EXT))
            videos = sum(1 for f in self.files if str(f).lower().endswith(VIDEO_EXT))
            self._show_completion_dialog(images=images, videos=videos, failed=self._interactive_failed)
            if self.chk_people.isChecked():
                ask = QMessageBox.question(
                    self,
                    "Face Identification",
                    "Run face identification now on the output folder?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if ask == QMessageBox.Yes:
                    self.run_people_scan_now()
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
                    self._run_phase = f"Converting video {self.index + 1} of {total}"
                    base = os.path.splitext(file)[0]
                    mp4_path = _unique_dest_path(out_dir, base + ".mp4")
                    convert_video(path, mp4_path)
                else:
                    self.status_label.setText(f"Status: Interactive {self.index+1}/{total}: Copying video {file}")
                    self._run_phase = f"Copying video {self.index + 1} of {total}"
                    dest = _unique_dest_path(out_dir, file)
                    shutil.copy2(path, dest)
            except Exception as e:
                print("Video failed:", file, e)
                self._interactive_failed += 1
            self.index +=1
            self.progress.setValue(self.index)
            QApplication.processEvents()
            self.process_next_interactive()
            return

        if file.lower().endswith(IMAGE_EXT):
            self.status_label.setText(f"Status: Interactive {self.index+1}/{total}: Predicting category for {file}")
            self._run_phase = f"Reviewing file {self.index + 1} of {total}"
            self.current_embedding = None
            try:
                img = load_image_for_ai(path)
                if img:
                    pixmap = pil_to_qpixmap(img)
                else:
                    pixmap = QPixmap()
                self.image_label.setPixmap(pixmap)
            except (OSError, RuntimeError, ValueError):
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
            self._update_run_summary()
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
            self._run_active = False
            self._run_phase = "Interactive review not active"
            self._sync_interactive_review_visibility()
            QMessageBox.information(self, "Interactive Mode", "No active interactive item to dismiss.")
            self.combo_category.setEnabled(False)
            self.btn_confirm.setEnabled(False)
            self.btn_skip.setEnabled(False)
            self._update_run_summary()
            return
        self.interactive_mode=False
        self._sync_interactive_review_visibility()
        self.combo_category.setEnabled(False)
        self.btn_confirm.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.status_label.setText("Status: Continuing auto-sort")
        self._run_phase = "Continuing auto-sort"
        self._update_run_summary()
        self.start_auto_thread(start_index=self.index)

    # ---------------------------
    # Auto mode with thread
    # ---------------------------
    def start_auto_thread(self, start_index=0):
        if int(start_index) >= len(self.files):
            self.status_label.setText("Status: Complete")
            self._set_current_media_placeholder(
                "No remaining files",
                "There are no more files left to process in this run.",
            )
            QMessageBox.information(self, "Done", "No remaining files to process.")
            return
        self._run_active = True
        self._run_phase = "Starting batch sort"
        self._update_run_summary()
        self.thread = AutoProcessThread(
            self.files,
            self.input_folder,
            self.output_folder,
            convert_videos=self.chk_convert_videos.isChecked(),
            start_index=start_index,
            structure_pattern=self._get_structure_pattern(),
            enable_people=(self.chk_people.isChecked() and (not self.interactive_mode)),
        )
        self.thread.progress_signal.connect(self.progress.setValue)
        self.thread.status_signal.connect(self.on_auto_status)
        self.thread.current_item_signal.connect(self.on_current_item_event)
        self.thread.visual_signal.connect(self.on_visual_event)
        self.thread.done_signal.connect(self.auto_done)
        self.thread.start()

    def on_auto_status(self, s: str):
        try:
            self.status_label.setText(f"Status: {s}")
        except Exception:
            pass
        self._run_phase = str(s or self._run_phase)
        self._update_run_summary()
        try:
            m = re.search(r"\\bETA\\b.*$", s or "")
            if m:
                eta = m.group(0).strip()
                self.progress.setFormat(f"%v/%m {eta}")
            else:
                self.progress.setFormat("%v/%m")
        except Exception:
            pass

    def on_visual_event(self, payload: dict):
        try:
            self._append_review_history_entry(payload or {})
        except Exception:
            pass

    def on_current_item_event(self, payload: dict):
        try:
            entry = payload or {}
            source_path = str(entry.get("source_path") or "")
            category = str(entry.get("category") or "Uncategorized")
            is_video = bool(entry.get("is_video"))
            explanation = str(entry.get("explanation") or "")
            self._show_current_media(source_path, category, is_video)
            if explanation:
                self.image_explanation_label.setText(explanation)
            self.image_explanation_state_label.setText("Explanation type: In progress")
        except Exception:
            pass

    def resizeEvent(self, event):
        try:
            self._apply_current_preview_pixmap()
        except Exception:
            pass
        try:
            super().resizeEvent(event)
        except Exception:
            pass

    def closeEvent(self, event):
        # Prevent "QThread: Destroyed while thread is still running" on shutdown.
        try:
            self._save_ui_settings()
        except Exception:
            pass
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
                QMessageBox.information(self, "Face Identification", "Another processing task is currently running.")
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
        self._run_active = True
        self._run_phase = "Running people scan"
        self.progress.setRange(0, 0)
        self.progress.setFormat("Running face identification...")
        self.status_label.setText("Status: Running face identification on output folder...")
        self._set_current_media_placeholder(
            "Scanning existing output",
            "MediaSorter is grouping faces in the output folder. Current-file preview is paused during this step.",
            "Face grouping is running.",
        )
        self._update_run_summary()

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
        self._run_active = False
        self._run_phase = "People scan complete"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("%v/%m")
        self.status_label.setText("Status: Face identification complete")
        self.image_explanation_label.setText("Face identification is complete. Start another sort to resume current-file preview.")
        self._update_run_summary()

        shown = self._show_people_review_for_thread(getattr(self, "thread", None))
        try:
            all_clusters = list(getattr(self.thread, "people_clusters", []) or [])
        except Exception:
            all_clusters = []
        unknown = [cl for cl in all_clusters if not cl.get("name")]
        QMessageBox.information(
            self,
            "Face Identification Complete",
            f"Clusters found: {len(all_clusters)}\n"
            f"Unknown clusters: {len(unknown)}\n"
            f"Review dialog shown: {'Yes' if shown else 'No'}",
        )

        try:
            self.btn_start.setEnabled(bool(core._MODEL_READY and not core._MODEL_LOAD_ERROR))
        except Exception:
            pass

    def auto_done(self, counts):
        self._run_active = False
        self._run_phase = "Run complete"
        self.combo_category.setEnabled(False)
        self.btn_confirm.setEnabled(False)
        self.btn_skip.setEnabled(False)
        self.status_label.setText("Status: Complete")
        self.image_explanation_label.setText("Run complete. The last processed item is shown above.")
        try:
            self.progress.setFormat("%v/%m Done")
        except Exception:
            pass
        self._update_run_summary()

        # Post-run people identification flow (batch mode only).
        try:
            if getattr(self, "thread", None) is not None and bool(self.chk_people.isChecked()):
                self._show_people_review_for_thread(self.thread)
        except Exception:
            pass

        self._show_completion_dialog(
            images=int(counts.get("images") or 0),
            videos=int(counts.get("videos") or 0),
            failed=int(counts.get("failed") or 0),
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
            proposed = []
            for i in range(list_widget.count()):
                proposed.append((list_widget.item(i).text() or "").strip())

            self._apply_category_catalog(proposed, renames=renames)
            self.on_review_history_selection_changed()
            dialog.accept()

        btn_add.clicked.connect(add_category)
        btn_rename.clicked.connect(rename_category)
        btn_remove.clicked.connect(remove_category)
        btn_save.clicked.connect(save_categories)

        dialog.exec()
