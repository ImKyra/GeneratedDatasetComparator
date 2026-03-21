import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QResizeEvent, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFileDialog,
    QMessageBox,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QPushButton,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QPlainTextEdit,
    QStatusBar,
    QLineEdit,
    QSlider,
    QProgressDialog,
    QMenu,
    QAbstractItemView,
)

from items import OriginalItem, GeneratedItem
from dataset_scanner import DatasetScanner, GENERATED_IMG_EXTS
from image_loader import ImageLoader
from prompt_manager import PromptManager
from matching_engine import MatchingEngine
from search_replace_dialog import SearchReplaceDialog
from context_menu_actions import ContextMenuActions
from import_manager import ImportManager
from filter_manager import FilterManager
from ui_display_manager import UIDisplayManager
from prompt_editor import PromptEditor

FILTER_DEBOUNCE_MS = 300
DEFAULT_FONT_SIZE = 10
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 24


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Generated Dataset Comparator")
        self.resize(1200, 800)

        self.scanner = DatasetScanner()
        self.image_loader = ImageLoader()
        self.prompt_manager = PromptManager()
        self.matching_engine = MatchingEngine(self.scanner)

        self.original_root: Optional[Path] = None
        self.generated_root: Optional[Path] = None

        self.original_items: List[OriginalItem] = []
        self.generated_items: List[GeneratedItem] = []
        self.matches: Dict[Path, List[GeneratedItem]] = {}
        self.filter_text: str = ""
        self.exclude_text: str = ""
        self.displayed_items: List[OriginalItem] = []
        self.current_original_item: Optional[OriginalItem] = None
        self._updating_selection: bool = False
        self._has_unsaved_changes: bool = False

        self.search_replace_dialog: Optional[SearchReplaceDialog] = None

        self.filter_timer = QTimer()
        self.filter_timer.setSingleShot(True)
        self.filter_timer.timeout.connect(self._apply_filter)

        self._build_ui()

        # Initialize managers (must be after _build_ui since they reference UI elements)
        self.context_menu_actions = ContextMenuActions(self.list_originals, self.statusBar())
        self.import_manager = ImportManager(self.scanner, self.statusBar())
        self.filter_manager = FilterManager()
        self.ui_display = UIDisplayManager(
            self.image_loader,
            self.lbl_orig_image,
            self.tabs_generated
        )
        self.prompt_editor = PromptEditor(self.prompt_manager, self.statusBar())

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        top_row = QHBoxLayout()
        self.btn_load_orig = QPushButton("Dataset")
        self.lbl_orig = QLabel("Dataset: <not set>")
        self.btn_load_gen = QPushButton("Generated Pictures")
        self.lbl_gen = QLabel("Generated: <not set>")
        self.btn_rescan = QPushButton("Scan/Match")
        self.btn_import = QPushButton("Import Generated Pictures")

        for w in (self.btn_load_orig, self.lbl_orig, self.btn_load_gen, self.lbl_gen, self.btn_rescan, self.btn_import):
            top_row.addWidget(w)
        top_row.addStretch(1)
        root_layout.addLayout(top_row)

        self.btn_load_orig.clicked.connect(self.choose_original)
        self.btn_load_gen.clicked.connect(self.choose_generated)
        self.btn_rescan.clicked.connect(self.rescan_all)
        self.btn_import.clicked.connect(self.import_generated_dialog)

        filter_row = QHBoxLayout()
        filter_label = QLabel("Filter by words:")
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText('Enter words to filter. Use -word to exclude, "phrase" for exact phrases...')
        self.txt_filter.textChanged.connect(self.on_filter_changed)
        filter_row.addWidget(filter_label)
        filter_row.addWidget(self.txt_filter, 1)

        exclude_label = QLabel("Exclude by words:")
        self.txt_exclude = QLineEdit()
        self.txt_exclude.setPlaceholderText('Enter words to exclude...')
        self.txt_exclude.textChanged.connect(self.on_exclude_changed)
        filter_row.addWidget(exclude_label)
        filter_row.addWidget(self.txt_exclude, 1)

        self.btn_search_replace = QPushButton("Search/Replace")
        self.btn_search_replace.clicked.connect(self.open_search_replace_dialog)
        filter_row.addWidget(self.btn_search_replace)

        root_layout.addLayout(filter_row)

        main_split = QSplitter(Qt.Horizontal)
        main_split.splitterMoved.connect(self._on_splitter_moved)
        root_layout.addWidget(main_split, 1)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        self.list_originals = QListWidget()
        self.list_originals.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_originals.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_originals.customContextMenuRequested.connect(self.show_context_menu)
        self.list_originals.setAutoScroll(False)
        left_layout.addWidget(self.list_originals)

        self.lbl_list_count = QLabel("0 items")
        self.lbl_list_count.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(self.lbl_list_count)

        main_split.addWidget(left_widget)
        self.list_originals.itemSelectionChanged.connect(self.on_selection_changed)

        center_split = QSplitter(Qt.Horizontal)
        center_split.splitterMoved.connect(self._on_splitter_moved)
        main_split.addWidget(center_split)
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 3)

        self.main_split = main_split
        self.center_split = center_split

        gen_widget = QWidget()
        gen_layout = QVBoxLayout(gen_widget)
        self.tabs_generated = QTabWidget()
        self.tabs_generated.currentChanged.connect(self._on_tab_changed)
        gen_layout.addWidget(self.tabs_generated)
        center_split.addWidget(gen_widget)

        orig_widget = QWidget()
        orig_layout = QVBoxLayout(orig_widget)
        self.lbl_orig_image = QLabel("Original image will appear here")
        self.lbl_orig_image.setAlignment(Qt.AlignCenter)
        self.lbl_orig_image.setMinimumSize(QSize(200, 200))
        self.lbl_orig_image.setScaledContents(False)
        orig_layout.addWidget(self.lbl_orig_image, 1)
        center_split.addWidget(orig_widget)

        prompt_widget = QWidget()
        prompt_layout = QVBoxLayout(prompt_widget)
        self.txt_prompt = QPlainTextEdit()
        self.txt_prompt.setPlaceholderText("Edit prompt here...")
        self.txt_prompt.textChanged.connect(self.on_prompt_text_changed)
        prompt_layout.addWidget(self.txt_prompt, 1)
        btn_row = QHBoxLayout()
        self.btn_save_prompt = QPushButton("Save Prompt")
        self.btn_rematch = QPushButton("Re-match")
        btn_row.addWidget(self.btn_save_prompt)
        btn_row.addWidget(self.btn_rematch)
        btn_row.addStretch(1)

        font_size_label = QLabel("Font:")
        self.font_slider = QSlider(Qt.Horizontal)
        self.font_slider.setMinimum(MIN_FONT_SIZE)
        self.font_slider.setMaximum(MAX_FONT_SIZE)
        self.font_slider.setValue(DEFAULT_FONT_SIZE)
        self.font_slider.setMaximumWidth(100)
        self.font_slider.valueChanged.connect(self.on_font_size_changed)
        self.font_size_label = QLabel(f"{DEFAULT_FONT_SIZE}pt")
        self.font_size_label.setMinimumWidth(35)
        btn_row.addWidget(font_size_label)
        btn_row.addWidget(self.font_slider)
        btn_row.addWidget(self.font_size_label)

        prompt_layout.addLayout(btn_row)
        center_split.addWidget(prompt_widget)

        self.btn_save_prompt.clicked.connect(self.save_prompt)
        self.btn_rematch.clicked.connect(self.rematch_with_progress)

        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save_prompt)

        self.search_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.search_shortcut.activated.connect(self.open_search_replace_dialog)

        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self.undo_prompt_change)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    def on_prompt_text_changed(self) -> None:
        if self._updating_selection:
            return

        if not self.current_original_item:
            return

        current_text = self.txt_prompt.toPlainText().strip()
        original_text = (self.current_original_item.prompt_text or "").strip()

        if current_text != original_text:
            if not self._has_unsaved_changes:
                self._has_unsaved_changes = True
                self.btn_save_prompt.setStyleSheet("background-color: #ff4444; color: white; font-weight: bold;")
        else:
            if self._has_unsaved_changes:
                self._has_unsaved_changes = False
                self.btn_save_prompt.setStyleSheet("")

    def choose_original(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Original Dataset Folder")
        if not path:
            return
        self.original_root = Path(path)
        self.lbl_orig.setText(f"Dataset: {self.original_root}")

        self.statusBar().showMessage("Scanning original dataset...")
        QApplication.processEvents()
        self.image_loader.clear_cache()
        self.original_items = self.scanner.scan_original_dataset(self.original_root)
        self.statusBar().showMessage(f"Found {len(self.original_items)} original items")
        self.populate_original_list()

        generated_subdir = self.original_root / "generated"
        if generated_subdir.exists() and generated_subdir.is_dir():
            self.generated_root = generated_subdir
            self.lbl_gen.setText(f"Generated: {self.generated_root}")
            self.statusBar().showMessage("Scanning generated dataset...")
            QApplication.processEvents()
            self.generated_items = self.scanner.scan_generated_dataset(self.generated_root)
            has_prompt = sum(1 for g in self.generated_items if g.prompt_text)
            self.statusBar().showMessage(f"Auto-detected 'generated' subdirectory. Found {len(self.generated_items)} generated images ({has_prompt} with prompts). Click 'Scan/Match' to start matching.", 5000)

    def choose_generated(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Generated Dataset Folder")
        if not path:
            return

        self.generated_root = Path(path)
        self.lbl_gen.setText(f"Generated: {self.generated_root}")

        self.statusBar().showMessage("Scanning generated dataset...")
        QApplication.processEvents()
        self.image_loader.clear_cache()
        self.generated_items = self.scanner.scan_generated_dataset(self.generated_root)
        has_prompt = sum(1 for g in self.generated_items if g.prompt_text)
        self.statusBar().showMessage(f"Found {len(self.generated_items)} generated images ({has_prompt} with prompts). Click 'Scan/Match' to match.", 5000)

    def import_generated_dialog(self) -> None:
        """Open dialog to import generated images from external source."""
        if not self.generated_root:
            QMessageBox.warning(
                self,
                "No Generated Folder",
                "Please select a generated dataset folder first using 'Load Generated Folder'."
            )
            return

        dest_path = QFileDialog.getExistingDirectory(
            self,
            "Select Destination Folder for Import"
        )
        if not dest_path:
            return

        dest_folder = Path(dest_path)

        # Create timestamped directory if needed
        target_dir = self.import_manager.create_timestamped_directory(dest_folder)
        if target_dir is None:
            QMessageBox.critical(
                self,
                "Import Error",
                "Failed to create timestamped directory"
            )
            return

        # Perform the import
        imported, skipped, unmatched = self.import_manager.import_generated_files(
            self.generated_root,
            target_dir,
            self.original_items
        )

        # Update UI
        self.generated_root = target_dir
        self.lbl_gen.setText(f"Generated: {self.generated_root}")
        self.statusBar().showMessage("Import complete. Click 'Scan/Match' to scan.", 3000)

        # Show results
        if imported > 0 or unmatched > 0:
            message = f"Successfully imported {imported} file(s) to:\n{target_dir}\n\n"
            message += f"Skipped: {skipped} (already exist)\n"
            message += f"Unmatched: {unmatched} (no corresponding original dataset item found)"

            QMessageBox.information(
                self,
                "Import Complete",
                message
            )

    def rescan_original(self) -> None:
        if not self.original_root:
            return
        self.statusBar().showMessage("Scanning original dataset...")
        QApplication.processEvents()
        self.image_loader.clear_cache()
        self.original_items = self.scanner.scan_original_dataset(self.original_root)
        self.statusBar().showMessage(f"Found {len(self.original_items)} original items")
        self.populate_original_list()

    def rescan_generated(self) -> None:
        if not self.generated_root:
            return
        self.statusBar().showMessage("Scanning generated dataset...")
        QApplication.processEvents()
        self.image_loader.clear_cache()
        self.generated_items = self.scanner.scan_generated_dataset(self.generated_root)
        has_prompt = sum(1 for g in self.generated_items if g.prompt_text)
        self.statusBar().showMessage(f"Found {len(self.generated_items)} generated images ({has_prompt} with prompts)")

    def rescan_all(self) -> None:
        if self.original_root:
            self.rescan_original()
        if self.generated_root:
            self.rescan_generated()
        if self.original_root and self.generated_root:
            self.rematch_with_progress()

    def populate_original_list(self) -> None:
        """Populate the list widget with filtered original items."""
        if self._updating_selection:
            return

        self._updating_selection = True
        try:
            current_item = self.current_original_item
            current_row = self.list_originals.currentRow()

            self.list_originals.clear()
            self.displayed_items.clear()

            # Filter items using FilterManager
            filtered_items = self.filter_manager.filter_items(
                self.original_items,
                self.filter_text,
                self.exclude_text
            )

            # Populate list with filtered items
            for item in filtered_items:
                match_count = len(self.matches.get(item.image_path, []))
                lw = QListWidgetItem(self._format_match_count(item.image_path.name, match_count))
                self.list_originals.addItem(lw)
                self.displayed_items.append(item)

            # Update count label
            displayed_count = len(filtered_items)
            total_count = len(self.original_items)
            has_filters = self.filter_text or self.exclude_text

            if has_filters:
                self.lbl_list_count.setText(f"{displayed_count} of {total_count} items (filtered)")
            else:
                self.lbl_list_count.setText(f"{displayed_count} items")

            # Restore selection if possible
            if current_item and self.displayed_items:
                for idx, item in enumerate(self.displayed_items):
                    if item.image_path == current_item.image_path:
                        self.list_originals.setCurrentRow(idx)
                        return
                if current_row >= 0:
                    new_row = min(current_row, len(self.displayed_items) - 1)
                    self.list_originals.setCurrentRow(new_row)
                    return

            if self.list_originals.count() > 0 and current_row < 0:
                self.list_originals.setCurrentRow(0)

        finally:
            self._updating_selection = False

    def rematch_with_progress(self) -> None:
        if not self.original_items or not self.generated_items:
            return

        progress = QProgressDialog("Initializing matching...", "Cancel", 0, len(self.original_items), self)
        progress.setWindowTitle("Matching Images to Prompts")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.show()

        cancelled = False

        def progress_callback(current: int, total: int, message: str) -> bool:
            nonlocal cancelled

            progress.setValue(current)
            progress.setLabelText(message)

            QApplication.processEvents()

            if progress.wasCanceled():
                cancelled = True
                return False

            return True

        try:
            matches = self.matching_engine.match_all_items(
                self.original_items,
                self.generated_items,
                progress_callback
            )

            if cancelled or matches is None:
                self.statusBar().showMessage("Matching cancelled by user", 3000)
                return

            self.matches = matches

            for row in range(len(self.displayed_items)):
                item = self.displayed_items[row]
                match_count = len(self.matches.get(item.image_path, []))
                lw_item = self.list_originals.item(row)
                if lw_item is not None:
                    lw_item.setText(self._format_match_count(item.image_path.name, match_count))

            self.on_selection_changed()

            total_matches = sum(len(match_list) for match_list in self.matches.values())
            self.statusBar().showMessage(
                f"Matching complete! Found {total_matches} total matches across {len(self.matches)} items", 5000)

        except Exception as e:
            QMessageBox.critical(self, "Matching Error", f"An error occurred during matching:\n{str(e)}")
            self.statusBar().showMessage("Matching failed", 3000)
        finally:
            progress.close()

    def on_selection_changed(self) -> None:
        if self._updating_selection:
            return

        if self._has_unsaved_changes and self.current_original_item:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                f"Do you want to save your changes on {self.current_original_item.image_path.name}?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save
            )

            if reply == QMessageBox.Save:
                self.save_prompt()
            elif reply == QMessageBox.Cancel:
                self._updating_selection = True
                try:
                    for idx, item in enumerate(self.displayed_items):
                        if item.image_path == self.current_original_item.image_path:
                            self.list_originals.setCurrentRow(idx)
                            break
                finally:
                    self._updating_selection = False
                return

        self._updating_selection = True
        try:
            self._has_unsaved_changes = False
            self.btn_save_prompt.setStyleSheet("")

            selected_items = self.list_originals.selectedItems()

            if not selected_items:
                self.ui_display.clear_original_image()
                self.txt_prompt.clear()
                self.ui_display.populate_generated_tabs([])
                self.current_original_item = None
                return

            selected_rows = sorted([self.list_originals.row(item) for item in selected_items])
            first_row = selected_rows[0]

            if 0 <= first_row < len(self.displayed_items):
                item = self.displayed_items[first_row]
                self.current_original_item = item
                self.ui_display.display_original_image(item)
                self.txt_prompt.setPlainText(item.prompt_text or "")
                self.ui_display.populate_generated_tabs(self.matches.get(item.image_path, []))
            else:
                self.ui_display.clear_original_image()
                self.txt_prompt.clear()
                self.ui_display.populate_generated_tabs([])
                self.current_original_item = None

        finally:
            self._updating_selection = False

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab change events to rescale images."""
        self.ui_display.rescale_current_tab_images()

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """Handle splitter movement to rescale images."""
        self.ui_display.rescale_original_image(self.current_original_item)
        self.ui_display.rescale_current_tab_images()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Handle window resize events to rescale images."""
        super().resizeEvent(event)
        self.ui_display.rescale_original_image(self.current_original_item)
        self.ui_display.rescale_current_tab_images()

    def save_prompt(self) -> None:
        """Save the current prompt to file."""
        if not self.current_original_item:
            return

        item = self.current_original_item
        display_row = next((idx for idx, disp_item in enumerate(self.displayed_items)
                           if disp_item.image_path == item.image_path), -1)

        if display_row < 0:
            return

        new_text = self.txt_prompt.toPlainText().strip()

        # Save the prompt
        if self.prompt_editor.save_prompt(item, new_text, self):
            # Update item in all lists
            updated_item = self.prompt_editor.update_item_prompt(
                item,
                new_text,
                self.original_items,
                self.displayed_items
            )
            self.current_original_item = updated_item

            # Find original index for rematching
            orig_idx = next((idx for idx, orig_item in enumerate(self.original_items)
                           if orig_item.image_path == item.image_path), None)

            if orig_idx is not None:
                self.rematch_single_item(orig_idx, display_row)

            self._has_unsaved_changes = False
            self.btn_save_prompt.setStyleSheet("")

    def on_filter_changed(self, text: str) -> None:
        self.filter_text = text
        self.filter_timer.stop()
        self.filter_timer.start(FILTER_DEBOUNCE_MS)

    def on_exclude_changed(self, text: str) -> None:
        self.exclude_text = text
        self.filter_timer.stop()
        self.filter_timer.start(FILTER_DEBOUNCE_MS)

    def _apply_filter(self) -> None:
        self.populate_original_list()

    def show_context_menu(self, position) -> None:
        """Display context menu for the original items list."""
        if not self.list_originals.selectedItems():
            return

        menu = QMenu(self)

        select_all_action = menu.addAction("Select All")
        select_all_action.triggered.connect(self.context_menu_actions.select_all_items)

        copy_prompts_action = menu.addAction("Copy prompt(s)")
        copy_prompts_action.triggered.connect(
            lambda: self.context_menu_actions.copy_selected_prompts(self.displayed_items)
        )

        copy_files_action = menu.addAction("Copy file(s) and prompt(s) to...")
        copy_files_action.triggered.connect(
            lambda: self.context_menu_actions.copy_files_and_prompts(self.displayed_items, self)
        )

        move_files_action = menu.addAction("Move file(s) and prompt(s) to...")
        move_files_action.triggered.connect(
            lambda: self.context_menu_actions.move_files_and_prompts(self.displayed_items, self)
        )

        open_image_action = menu.addAction("Open image with default application")
        open_image_action.triggered.connect(
            lambda: self.context_menu_actions.open_image_with_default_app(self.displayed_items, self)
        )

        menu.exec(self.list_originals.viewport().mapToGlobal(position))

    @staticmethod
    def _format_match_count(filename: str, match_count: int) -> str:
        plural = "es" if match_count != 1 else ""
        return f"{filename}  ({match_count} match{plural})"

    def rematch_single_item(self, orig_idx: int, display_row: int = -1) -> None:
        if orig_idx < 0 or orig_idx >= len(self.original_items):
            return

        item = self.original_items[orig_idx]
        result = self.matching_engine.match_single_item(item, self.generated_items)
        self.matches[item.image_path] = result

        match_count = len(result)
        if display_row >= 0:
            lw_item = self.list_originals.item(display_row)
            if lw_item is not None:
                lw_item.setText(self._format_match_count(item.image_path.name, match_count))

            if self.list_originals.currentRow() == display_row:
                self.ui_display.populate_generated_tabs(result)

    def on_font_size_changed(self, value: int) -> None:
        self.font_size_label.setText(f"{value}pt")
        font = self.txt_prompt.font()
        font.setPointSize(value)
        self.txt_prompt.setFont(font)

    def open_search_replace_dialog(self) -> None:
        if self.search_replace_dialog is None:
            self.search_replace_dialog = SearchReplaceDialog(self)
            self.search_replace_dialog.search_requested.connect(self.on_search_requested)
            self.search_replace_dialog.replace_requested.connect(self.on_replace_requested)
            self.search_replace_dialog.replace_all_requested.connect(self.on_replace_all_requested)

        self.search_replace_dialog.show()
        self.search_replace_dialog.raise_()
        self.search_replace_dialog.activateWindow()

    def on_search_requested(self, search_text: str, case_sensitive: bool) -> None:
        self.txt_filter.setText(search_text)
        self.statusBar().showMessage(f"Filtered by: {search_text}", 3000)

    def on_replace_requested(self, search_text: str, replace_text: str, case_sensitive: bool) -> None:
        """Handle replace request from search/replace dialog."""
        display_row = self.list_originals.currentRow()
        if display_row < 0 or display_row >= len(self.displayed_items):
            self.statusBar().showMessage("No prompt selected", 2000)
            return

        item = self.displayed_items[display_row]

        # Perform the replace
        new_prompt, count = self.prompt_editor.perform_search_replace(
            item,
            search_text,
            replace_text,
            case_sensitive
        )

        if new_prompt == (item.prompt_text or ""):
            self.statusBar().showMessage(f"'{search_text}' not found in current prompt", 3000)
            return

        # Add to undo history and save
        self.prompt_editor.add_to_history(item.prompt_path, item.prompt_text or "")
        self.txt_prompt.setPlainText(new_prompt)
        self._save_prompt_internal(item, new_prompt, display_row)

        self.statusBar().showMessage(f"Replaced {count} occurrence(s) in current prompt", 3000)

    def on_replace_all_requested(self, search_text: str, replace_text: str, case_sensitive: bool) -> None:
        """Handle replace all request from search/replace dialog."""
        if not self.displayed_items:
            self.statusBar().showMessage("No items to replace", 2000)
            return

        replaced_count = 0
        total_occurrences = 0

        for display_row, item in enumerate(self.displayed_items):
            # Perform the replace
            new_prompt, count = self.prompt_editor.perform_search_replace(
                item,
                search_text,
                replace_text,
                case_sensitive
            )

            if new_prompt != (item.prompt_text or ""):
                self.prompt_editor.add_to_history(item.prompt_path, item.prompt_text or "")
                self._save_prompt_internal(item, new_prompt, display_row)
                replaced_count += 1
                total_occurrences += count

        # Update current display
        current_row = self.list_originals.currentRow()
        if 0 <= current_row < len(self.displayed_items):
            self.txt_prompt.setPlainText(self.displayed_items[current_row].prompt_text or "")

        self.statusBar().showMessage(
            f"Replaced {total_occurrences} occurrence(s) in {replaced_count} prompt(s)",
            5000
        )

    def _save_prompt_internal(self, item: OriginalItem, new_text: str, display_row: int) -> None:
        """Internal method to save prompt without user interaction."""
        if self.prompt_editor.save_prompt_internal(item, new_text):
            # Update item in all lists
            self.prompt_editor.update_item_prompt(
                item,
                new_text,
                self.original_items,
                self.displayed_items
            )

            # Rematch
            orig_idx = next((idx for idx, orig_item in enumerate(self.original_items)
                           if orig_item.image_path == item.image_path), None)

            if orig_idx is not None:
                self.rematch_single_item(orig_idx, display_row)

    def undo_prompt_change(self) -> None:
        """Undo the last prompt change."""
        result = self.prompt_editor.undo_last_change(self.original_items, self)
        if not result:
            return

        prompt_path, old_text, orig_item = result

        if orig_item:
            # Update item in all lists
            self.prompt_editor.update_item_prompt(
                orig_item,
                old_text,
                self.original_items,
                self.displayed_items
            )

            # Find and rematch
            for idx, item in enumerate(self.original_items):
                if item.prompt_path == prompt_path:
                    for display_row, disp_item in enumerate(self.displayed_items):
                        if disp_item.prompt_path == prompt_path:
                            if self.list_originals.currentRow() == display_row:
                                self.txt_prompt.setPlainText(old_text)
                            self.rematch_single_item(idx, display_row)
                            break
                    break

    def closeEvent(self, event: QCloseEvent) -> None:
        """Clean up resources when closing the window."""
        try:
            self.ui_display.clear_original_image()
            self.ui_display.populate_generated_tabs([])
            self.image_loader.clear_cache()
            QApplication.processEvents()
        except Exception:
            pass

        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    repo_root = Path(__file__).resolve().parent
    test_orig = repo_root / "resources" / "test" / "original_dataset"
    test_gen = repo_root / "resources" / "test" / "generated_dataset"
    if test_orig.exists():
        win.original_root = test_orig
        win.lbl_orig.setText(f"Original: {test_orig}")
    if test_gen.exists():
        win.generated_root = test_gen
        win.lbl_gen.setText(f"Generated: {test_gen}")
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
