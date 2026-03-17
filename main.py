import os
import sys
import difflib
import shlex
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import shutil

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

MAX_GENERATED_TABS = 8
FILTER_DEBOUNCE_MS = 300
DEFAULT_FONT_SIZE = 10
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 24


def _scale_generated_image(label: QLabel) -> None:
    pix = label.property("original_pixmap")
    if pix and not pix.isNull():
        parent = label.parentWidget()
        if parent:
            available_height = parent.height() - 100
            available_width = parent.width() - 20
            target_size = QSize(max(100, available_width), max(100, available_height))
        else:
            target_size = label.size()

        if target_size.width() > 50 and target_size.height() > 50:
            scaled_pix = pix.scaled(
                target_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            label.setPixmap(scaled_pix)
        else:
            label.setPixmap(pix)


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

        if any(dest_folder.iterdir()):
            timestamp = datetime.now().strftime("%y%m%d%H%M%S")
            target_dir = dest_folder / f"generated.{timestamp}"
            try:
                target_dir.mkdir(parents=True, exist_ok=False)
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Import Error",
                    f"Failed to create timestamped directory:\n{e}"
                )
                return

            self.statusBar().showMessage(f"Destination not empty. Created: {target_dir.name}", 3000)
        else:
            target_dir = dest_folder

        self.import_generated_files(self.generated_root, target_dir)

        self.generated_root = target_dir
        self.lbl_gen.setText(f"Generated: {self.generated_root}")
        self.statusBar().showMessage("Import complete. Click 'Scan/Match' to scan.", 3000)

    def import_generated_files(self, source_dir: Path, target_dir: Path) -> None:
        self.statusBar().showMessage("Importing generated files...")
        QApplication.processEvents()

        source_items: List[GeneratedItem] = []
        for dirpath, _, filenames in os.walk(source_dir):
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext in GENERATED_IMG_EXTS:
                    p = Path(dirpath) / fname
                    prompt = self.scanner.load_image_metadata_prompt_png(p)
                    source_items.append(GeneratedItem(image_path=p, prompt_text=prompt))

        if not source_items:
            QMessageBox.information(self, "Import", "No generated images found in selected folder.")
            return

        orig_by_stem: Dict[str, OriginalItem] = {}
        for orig in self.original_items:
            stem = orig.image_path.stem.lower()
            orig_by_stem[stem] = orig

        orig_norm_map: Dict[Path, str] = {}
        for orig in self.original_items:
            norm = self.scanner._normalize_prompt(orig.prompt_text)
            if norm:
                orig_norm_map[orig.image_path] = norm

        imported_count = 0
        skipped_count = 0
        unmatched_count = 0

        for gen_item in source_items:
            gen_stem = gen_item.image_path.stem.lower()
            target_name = None
            matched_orig: Optional[OriginalItem] = None

            # Try to match by filename stem first
            if gen_stem in orig_by_stem:
                matched_orig = orig_by_stem[gen_stem]
                target_name = matched_orig.image_path.stem + gen_item.image_path.suffix

            # If no filename match, try to match by prompt metadata
            if not matched_orig and gen_item.prompt_text:
                gen_norm = self.scanner._normalize_prompt(gen_item.prompt_text)
                if gen_norm:
                    best_match: Optional[OriginalItem] = None
                    best_score = 0.0

                    for orig in self.original_items:
                        orig_norm = orig_norm_map.get(orig.image_path)
                        if not orig_norm:
                            continue

                        if gen_norm == orig_norm or gen_norm in orig_norm or orig_norm in gen_norm:
                            best_match = orig
                            best_score = 1.0
                            break

                        len_diff = abs(len(orig_norm) - len(gen_norm)) / max(len(orig_norm), len(gen_norm), 1)
                        if len_diff > 0.5:
                            continue

                        o_words = set(orig_norm.split())
                        g_words = set(gen_norm.split())
                        if not o_words or not g_words:
                            continue

                        word_overlap = len(o_words & g_words) / len(o_words | g_words)
                        if word_overlap < 0.3:
                            continue

                        score = difflib.SequenceMatcher(None, orig_norm, gen_norm).ratio()
                        if score >= 0.85 and score > best_score:
                            best_score = score
                            best_match = orig

                    if best_match:
                        matched_orig = best_match
                        target_name = matched_orig.image_path.stem + gen_item.image_path.suffix

            # Skip if no match found
            if not matched_orig or not target_name:
                unmatched_count += 1
                continue

            target_path = target_dir / target_name

            if target_path.exists():
                if target_path.resolve() == gen_item.image_path.resolve():
                    skipped_count += 1
                    continue
                base_stem = Path(target_name).stem
                ext = Path(target_name).suffix
                counter = 1
                while target_path.exists():
                    target_name = f"{base_stem}_{counter}{ext}"
                    target_path = target_dir / target_name
                    counter += 1

            try:
                shutil.copy2(gen_item.image_path, target_path)
                imported_count += 1
            except Exception as e:
                error_msg = f"Failed to copy {gen_item.image_path.name}: {e}"
                print(error_msg)
                self.statusBar().showMessage(error_msg, 5000)

        self.statusBar().showMessage(
            f"Import complete: {imported_count} imported, {skipped_count} skipped, {unmatched_count} unmatched",
            5000
        )

        if imported_count > 0 or unmatched_count > 0:
            message = f"Successfully imported {imported_count} file(s) to:\n{target_dir}\n\n"
            message += f"Skipped: {skipped_count} (already exist)\n"
            message += f"Unmatched: {unmatched_count} (no corresponding original dataset item found)"

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

    def _parse_filter_text(self, filter_text: str) -> Tuple[List[str], List[str], List[str]]:
        include_words = []
        exclude_words = []
        phrases = []

        phrase_pattern = r'"([^"]+)"'
        for match in re.finditer(phrase_pattern, filter_text):
            phrases.append(match.group(1).lower())

        remaining_text = re.sub(phrase_pattern, '', filter_text)

        try:
            tokens = shlex.split(remaining_text.lower())
        except ValueError:
            tokens = remaining_text.lower().split()

        for token in tokens:
            token = token.strip()
            if not token:
                continue

            if token.startswith('-'):
                exclude_word = token[1:].strip()
                if exclude_word:
                    exclude_words.append(exclude_word)
            else:
                include_words.append(token)

        return include_words, exclude_words, phrases

    def populate_original_list(self) -> None:
        if self._updating_selection:
            return

        self._updating_selection = True
        try:
            current_item = self.current_original_item
            current_row = self.list_originals.currentRow()

            self.list_originals.clear()
            self.displayed_items.clear()

            include_words, exclude_words, phrases = self._parse_filter_text(self.filter_text)
            has_filter = include_words or exclude_words or phrases

            exclude_include_words, exclude_exclude_words, exclude_phrases = self._parse_filter_text(self.exclude_text)
            has_exclude_filter = exclude_include_words or exclude_exclude_words or exclude_phrases

            displayed_count = 0
            for item in self.original_items:
                prompt_lower = (item.prompt_text or "").lower()

                if has_filter:
                    if any(word in prompt_lower for word in exclude_words):
                        continue

                    if include_words and not all(word in prompt_lower for word in include_words):
                        continue

                    if phrases and not all(phrase in prompt_lower for phrase in phrases):
                        continue

                if has_exclude_filter:
                    if exclude_include_words and any(word in prompt_lower for word in exclude_include_words):
                        continue

                    if exclude_phrases and any(phrase in prompt_lower for phrase in exclude_phrases):
                        continue

                match_count = len(self.matches.get(item.image_path, []))
                lw = QListWidgetItem(self._format_match_count(item.image_path.name, match_count))
                self.list_originals.addItem(lw)
                self.displayed_items.append(item)
                displayed_count += 1

            total_count = len(self.original_items)
            if has_filter or has_exclude_filter:
                self.lbl_list_count.setText(f"{displayed_count} of {total_count} items (filtered)")
            else:
                self.lbl_list_count.setText(f"{displayed_count} items")

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
                self.lbl_orig_image.clear()
                self.lbl_orig_image.setText("Original image will appear here")
                self.txt_prompt.clear()
                self.populate_generated_tabs([])
                self.current_original_item = None
                return

            selected_rows = sorted([self.list_originals.row(item) for item in selected_items])
            first_row = selected_rows[0]

            if 0 <= first_row < len(self.displayed_items):
                item = self.displayed_items[first_row]
                self.current_original_item = item
                self.lbl_orig_image.clear()
                self._display_original_image(item)
                self.txt_prompt.setPlainText(item.prompt_text or "")
                self.populate_generated_tabs(self.matches.get(item.image_path, []))
            else:
                self.lbl_orig_image.clear()
                self.lbl_orig_image.setText("Original image will appear here")
                self.txt_prompt.clear()
                self.populate_generated_tabs([])
                self.current_original_item = None

        finally:
            self._updating_selection = False

    def _display_original_image(self, item: OriginalItem) -> None:
        pix = self.image_loader.load_for_display(item.image_path, (2000, 2000))
        if pix and not pix.isNull():
            label_size = self.lbl_orig_image.size()
            if label_size.width() > 50 and label_size.height() > 50:
                scaled_pix = pix.scaled(
                    label_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.lbl_orig_image.setPixmap(scaled_pix)
            else:
                self.lbl_orig_image.setPixmap(pix)
            self.lbl_orig_image.setText("")
        else:
            self.lbl_orig_image.setText(f"Failed to load: {item.image_path.name}")

    def _on_tab_changed(self, index: int) -> None:
        if index >= 0:
            tab_widget = self.tabs_generated.widget(index)
            if tab_widget:
                for img_label in tab_widget.findChildren(QLabel):
                    if img_label.property("original_pixmap"):
                        _scale_generated_image(img_label)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        if self.current_original_item:
            self._display_original_image(self.current_original_item)

        current_tab_idx = self.tabs_generated.currentIndex()
        if current_tab_idx >= 0:
            tab_widget = self.tabs_generated.widget(current_tab_idx)
            if tab_widget:
                for img_label in tab_widget.findChildren(QLabel):
                    if img_label.property("original_pixmap"):
                        _scale_generated_image(img_label)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.current_original_item:
            self._display_original_image(self.current_original_item)

        current_tab_idx = self.tabs_generated.currentIndex()
        if current_tab_idx >= 0:
            tab_widget = self.tabs_generated.widget(current_tab_idx)
            if tab_widget:
                for img_label in tab_widget.findChildren(QLabel):
                    if img_label.property("original_pixmap"):
                        _scale_generated_image(img_label)

    def populate_generated_tabs(self, items: List[GeneratedItem]) -> None:
        while self.tabs_generated.count() > 0:
            widget = self.tabs_generated.widget(0)
            self.tabs_generated.removeTab(0)
            if widget:
                for child in widget.findChildren(QLabel):
                    child.clear()
                widget.deleteLater()

        if not items:
            placeholder = QWidget()
            layout = QVBoxLayout(placeholder)
            no_match_label = QLabel("No matching generated images found.")
            no_match_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_match_label)
            self.tabs_generated.addTab(placeholder, "None")
            return

        max_tabs = min(len(items), MAX_GENERATED_TABS)
        items_to_show = items[:max_tabs]

        for i, g in enumerate(items_to_show):
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.setContentsMargins(5, 5, 5, 5)

            img_label = QLabel()
            img_label.setAlignment(Qt.AlignCenter)
            img_label.setScaledContents(False)
            img_label.setMinimumSize(100, 100)
            img_label.setSizePolicy(img_label.sizePolicy().horizontalPolicy(), img_label.sizePolicy().verticalPolicy())

            pix = self.image_loader.load_for_display(g.image_path, (2000, 2000))
            if pix and not pix.isNull():
                img_label.setProperty("original_pixmap", pix)
                _scale_generated_image(img_label)
            else:
                img_label.setText(f"Failed to load:\n{g.image_path.name}")

            layout.addWidget(img_label, 1)

            file_info = QLabel(f"File: {g.image_path.name}")
            file_info.setTextFormat(Qt.PlainText)
            layout.addWidget(file_info)

            prompt_text = (g.prompt_text or "<no prompt in metadata>").strip()

            prompt_display = QPlainTextEdit()
            prompt_display.setPlainText(prompt_text)
            prompt_display.setReadOnly(True)
            prompt_display.setMaximumHeight(80)
            prompt_display.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            prompt_display.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            layout.addWidget(prompt_display, 0)

            tab_name = g.image_path.stem
            if len(tab_name) > 12:
                tab_name = tab_name[:9] + "..."
            self.tabs_generated.addTab(tab, tab_name)

        if len(items) > max_tabs:
            info_tab = QWidget()
            info_layout = QVBoxLayout(info_tab)
            info_label = QLabel(
                f"Showing {max_tabs} of {len(items)} matches.\nScroll through original list to see others.")
            info_label.setAlignment(Qt.AlignCenter)
            info_label.setWordWrap(True)
            info_layout.addWidget(info_label)
            self.tabs_generated.addTab(info_tab, f"+{len(items) - max_tabs}")

    def save_prompt(self) -> None:
        if not self.current_original_item:
            return

        item = self.current_original_item
        display_row = next((idx for idx, disp_item in enumerate(self.displayed_items)
                           if disp_item.image_path == item.image_path), -1)

        if display_row < 0:
            return
        old_text = item.prompt_text
        new_text = self.txt_prompt.toPlainText().strip()

        if old_text == new_text:
            self.statusBar().showMessage("No changes to save", 2000)
            return

        try:
            self.prompt_manager.save_prompt(item, new_text)
            self._update_original_item(item, new_text)

            # Find original index for rematching
            orig_idx = next((idx for idx, orig_item in enumerate(self.original_items)
                           if orig_item.image_path == item.image_path), None)

            if orig_idx is not None:
                self.rematch_single_item(orig_idx, display_row)

            self._has_unsaved_changes = False
            self.btn_save_prompt.setStyleSheet("")

            self.statusBar().showMessage(f"Saved: {item.prompt_path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save prompt to {item.prompt_path}:\n{e}")

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
        if not self.list_originals.selectedItems():
            return

        menu = QMenu(self)

        select_all_action = menu.addAction("Select All")
        select_all_action.triggered.connect(self.select_all_items)

        copy_prompts_action = menu.addAction("Copy prompt(s)")
        copy_prompts_action.triggered.connect(self.copy_selected_prompts)

        open_image_action = menu.addAction("Open image with default application")
        open_image_action.triggered.connect(self.open_image_with_default_app)

        menu.exec(self.list_originals.viewport().mapToGlobal(position))

    def select_all_items(self) -> None:
        self.list_originals.selectAll()
        self.statusBar().showMessage(f"Selected {self.list_originals.count()} item(s)", 2000)

    def copy_selected_prompts(self) -> None:
        selected_rows = sorted([self.list_originals.row(item) for item in self.list_originals.selectedItems()])

        if not selected_rows:
            self.statusBar().showMessage("No items selected", 2000)
            return

        prompts = []
        for row in selected_rows:
            if 0 <= row < len(self.displayed_items):
                item = self.displayed_items[row]
                if item.prompt_text:
                    cleaned_prompt = item.prompt_text.replace("\r\n", "").replace("\r", "").replace("\n", "")
                    prompts.append(cleaned_prompt)

        if prompts:
            final_text = "\n\n".join(prompts)

            clipboard = QApplication.clipboard()
            clipboard.setText(final_text)

            self.statusBar().showMessage(f"Copied {len(prompts)} prompt(s) to clipboard", 3000)
        else:
            self.statusBar().showMessage("No prompts found in selected items", 2000)

    def open_image_with_default_app(self) -> None:
        selected_rows = sorted([self.list_originals.row(item) for item in self.list_originals.selectedItems()])

        if not selected_rows:
            self.statusBar().showMessage("No items selected", 2000)
            return

        first_row = selected_rows[0]
        if 0 <= first_row < len(self.displayed_items):
            item = self.displayed_items[first_row]
            image_path = item.image_path

            if not image_path.exists():
                QMessageBox.warning(self, "File Not Found", f"Image file not found:\n{image_path}")
                return

            try:
                if sys.platform == "win32":
                    os.startfile(str(image_path))
                elif sys.platform == "darwin":
                    subprocess.run(["open", str(image_path)], check=True)
                else:
                    subprocess.run(["xdg-open", str(image_path)], check=True)

                self.statusBar().showMessage(f"Opened: {image_path.name}", 2000)
            except Exception as e:
                QMessageBox.critical(self, "Open Error", f"Failed to open image:\n{e}")

    @staticmethod
    def _format_match_count(filename: str, match_count: int) -> str:
        plural = "es" if match_count != 1 else ""
        return f"{filename}  ({match_count} match{plural})"

    def _update_original_item(self, item: OriginalItem, new_text: str) -> None:
        for idx, orig_item in enumerate(self.original_items):
            if orig_item.image_path == item.image_path:
                self.original_items[idx] = OriginalItem(
                    image_path=item.image_path,
                    prompt_path=item.prompt_path,
                    prompt_text=new_text
                )
                break

        for idx, disp_item in enumerate(self.displayed_items):
            if disp_item.image_path == item.image_path:
                self.displayed_items[idx] = OriginalItem(
                    image_path=item.image_path,
                    prompt_path=item.prompt_path,
                    prompt_text=new_text
                )
                break

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
                self.populate_generated_tabs(result)

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
        display_row = self.list_originals.currentRow()
        if display_row < 0 or display_row >= len(self.displayed_items):
            self.statusBar().showMessage("No prompt selected", 2000)
            return

        item = self.displayed_items[display_row]
        old_prompt = item.prompt_text or ""

        if case_sensitive:
            new_prompt = old_prompt.replace(search_text, replace_text)
        else:
            new_prompt = self.prompt_manager.case_insensitive_replace(old_prompt, search_text, replace_text)

        if new_prompt == old_prompt:
            self.statusBar().showMessage(f"'{search_text}' not found in current prompt", 3000)
            return

        self.prompt_manager.add_to_history(item.prompt_path, old_prompt)
        self.txt_prompt.setPlainText(new_prompt)
        self._save_prompt_internal(item, new_prompt, display_row)

        count = old_prompt.count(search_text) if case_sensitive else old_prompt.lower().count(search_text.lower())
        self.statusBar().showMessage(f"Replaced {count} occurrence(s) in current prompt", 3000)

    def on_replace_all_requested(self, search_text: str, replace_text: str, case_sensitive: bool) -> None:
        if not self.displayed_items:
            self.statusBar().showMessage("No items to replace", 2000)
            return

        replaced_count = 0
        total_occurrences = 0

        for display_row, item in enumerate(self.displayed_items):
            old_prompt = item.prompt_text or ""

            if case_sensitive:
                new_prompt = old_prompt.replace(search_text, replace_text)
                count = old_prompt.count(search_text)
            else:
                new_prompt = self.prompt_manager.case_insensitive_replace(old_prompt, search_text, replace_text)
                count = old_prompt.lower().count(search_text.lower())

            if new_prompt != old_prompt:
                self.prompt_manager.add_to_history(item.prompt_path, old_prompt)
                self._save_prompt_internal(item, new_prompt, display_row)
                replaced_count += 1
                total_occurrences += count

        current_row = self.list_originals.currentRow()
        if 0 <= current_row < len(self.displayed_items):
            self.txt_prompt.setPlainText(self.displayed_items[current_row].prompt_text or "")

        self.statusBar().showMessage(
            f"Replaced {total_occurrences} occurrence(s) in {replaced_count} prompt(s)",
            5000
        )

    def _save_prompt_internal(self, item: OriginalItem, new_text: str, display_row: int) -> None:
        try:
            self.prompt_manager.save_prompt(item, new_text)
            self._update_original_item(item, new_text)

            orig_idx = next((idx for idx, orig_item in enumerate(self.original_items)
                           if orig_item.image_path == item.image_path), None)

            if orig_idx is not None:
                self.rematch_single_item(orig_idx, display_row)

        except Exception as e:
            print(f"Failed to save prompt to {item.prompt_path}: {e}")

    def undo_prompt_change(self) -> None:
        if not self.prompt_manager.has_history():
            self.statusBar().showMessage("No changes to undo", 2000)
            return

        result = self.prompt_manager.undo()
        if not result:
            return

        prompt_path, old_text = result

        try:
            prompt_path.write_text(old_text, encoding="utf-8")

            for idx, orig_item in enumerate(self.original_items):
                if orig_item.prompt_path == prompt_path:
                    self._update_original_item(orig_item, old_text)

                    for display_row, disp_item in enumerate(self.displayed_items):
                        if disp_item.prompt_path == prompt_path:
                            if self.list_originals.currentRow() == display_row:
                                self.txt_prompt.setPlainText(old_text)
                            self.rematch_single_item(idx, display_row)
                            break
                    break

            self.statusBar().showMessage(f"Undone: {prompt_path.name}", 3000)

        except Exception as e:
            QMessageBox.critical(self, "Undo Error", f"Failed to undo changes to {prompt_path}:\n{e}")

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            self.lbl_orig_image.clear()

            while self.tabs_generated.count() > 0:
                widget = self.tabs_generated.widget(0)
                self.tabs_generated.removeTab(0)
                if widget:
                    for child in widget.findChildren(QLabel):
                        child.clear()
                    widget.deleteLater()

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
