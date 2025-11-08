import os
import sys
import difflib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
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
)

from items import OriginalItem, GeneratedItem
from dataset_scanner import DatasetScanner, GENERATED_IMG_EXTS
from image_loader import ImageLoader
from prompt_manager import PromptManager
from matching_engine import MatchingEngine
from search_replace_dialog import SearchReplaceDialog


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
        self.displayed_items: List[OriginalItem] = []
        self.current_original_item: Optional[OriginalItem] = None
        self._updating_selection: bool = False

        self.search_replace_dialog: Optional[SearchReplaceDialog] = None

        # Debounce timer for filter text input
        self.filter_timer = QTimer()
        self.filter_timer.setSingleShot(True)
        self.filter_timer.timeout.connect(self._apply_filter)

        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        # Toolbar-like row
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

        # Filter textbox row
        filter_row = QHBoxLayout()
        filter_label = QLabel("Filter by words:")
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText('Enter words to filter. Use -word to exclude, "phrase" for exact phrases...')
        self.txt_filter.textChanged.connect(self.on_filter_changed)
        filter_row.addWidget(filter_label)
        filter_row.addWidget(self.txt_filter, 1)

        # Search/Replace button
        self.btn_search_replace = QPushButton("Search/Replace")
        self.btn_search_replace.clicked.connect(self.open_search_replace_dialog)
        filter_row.addWidget(self.btn_search_replace)

        root_layout.addLayout(filter_row)

        # Main splitter: left list and center splitter
        main_split = QSplitter(Qt.Horizontal)
        main_split.splitterMoved.connect(self._on_splitter_moved)
        root_layout.addWidget(main_split, 1)

        # Left: list of originals
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        self.list_originals = QListWidget()
        left_layout.addWidget(self.list_originals)

        # Count label at bottom of list
        self.lbl_list_count = QLabel("0 items")
        self.lbl_list_count.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(self.lbl_list_count)

        main_split.addWidget(left_widget)
        self.list_originals.currentRowChanged.connect(self.on_select_original)

        # Center splitter: generated tabs | original image | prompt editor
        center_split = QSplitter(Qt.Horizontal)
        center_split.splitterMoved.connect(self._on_splitter_moved)
        main_split.addWidget(center_split)
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 3)

        # Store splitter references
        self.main_split = main_split
        self.center_split = center_split

        # Generated tabs
        gen_widget = QWidget()
        gen_layout = QVBoxLayout(gen_widget)
        self.tabs_generated = QTabWidget()
        self.tabs_generated.currentChanged.connect(self._on_tab_changed)
        gen_layout.addWidget(self.tabs_generated)
        center_split.addWidget(gen_widget)

        # Original image
        orig_widget = QWidget()
        orig_layout = QVBoxLayout(orig_widget)
        self.lbl_orig_image = QLabel("Original image will appear here")
        self.lbl_orig_image.setAlignment(Qt.AlignCenter)
        self.lbl_orig_image.setMinimumSize(QSize(200, 200))
        self.lbl_orig_image.setScaledContents(False)  # Don't stretch, we'll handle scaling manually
        orig_layout.addWidget(self.lbl_orig_image, 1)
        center_split.addWidget(orig_widget)

        # Prompt editor
        prompt_widget = QWidget()
        prompt_layout = QVBoxLayout(prompt_widget)
        self.txt_prompt = QPlainTextEdit()
        self.txt_prompt.setPlaceholderText("Edit prompt here...")
        prompt_layout.addWidget(self.txt_prompt, 1)
        # buttons
        btn_row = QHBoxLayout()
        self.btn_save_prompt = QPushButton("Save Prompt")
        self.btn_rematch = QPushButton("Re-match")
        btn_row.addWidget(self.btn_save_prompt)
        btn_row.addWidget(self.btn_rematch)
        btn_row.addStretch(1)

        # Font size slider
        font_size_label = QLabel("Font:")
        self.font_slider = QSlider(Qt.Horizontal)
        self.font_slider.setMinimum(8)
        self.font_slider.setMaximum(24)
        self.font_slider.setValue(10)
        self.font_slider.setMaximumWidth(100)
        self.font_slider.valueChanged.connect(self.on_font_size_changed)
        self.font_size_label = QLabel("10pt")
        self.font_size_label.setMinimumWidth(35)
        btn_row.addWidget(font_size_label)
        btn_row.addWidget(self.font_slider)
        btn_row.addWidget(self.font_size_label)

        prompt_layout.addLayout(btn_row)
        center_split.addWidget(prompt_widget)

        self.btn_save_prompt.clicked.connect(self.save_prompt)
        self.btn_rematch.clicked.connect(self.rematch_with_progress)

        # Add Ctrl+S shortcut for saving prompts
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save_prompt)

        # Add Ctrl+F shortcut for search/replace
        self.search_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.search_shortcut.activated.connect(self.open_search_replace_dialog)

        # Add Ctrl+Z shortcut for undo
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self.undo_prompt_change)

        # Status bar
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

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
        from datetime import datetime

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
        import shutil

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

        for gen_item in source_items:
            gen_stem = gen_item.image_path.stem.lower()
            target_name = None
            matched_orig: Optional[OriginalItem] = None

            if gen_stem in orig_by_stem:
                matched_orig = orig_by_stem[gen_stem]
                target_name = matched_orig.image_path.stem + gen_item.image_path.suffix

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

            if not target_name:
                target_name = gen_item.image_path.name

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
                print(f"Failed to copy {gen_item.image_path.name}: {e}")

        self.statusBar().showMessage(
            f"Import complete: {imported_count} files imported, {skipped_count} skipped",
            5000
        )

        if imported_count > 0:
            QMessageBox.information(
                self,
                "Import Complete",
                f"Successfully imported {imported_count} file(s) to:\n{target_dir}\n\n"
                f"Skipped: {skipped_count} (already exist)"
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

        import shlex
        import re

        # Extract quoted phrases first (before any splitting)
        phrase_pattern = r'"([^"]+)"'
        for match in re.finditer(phrase_pattern, filter_text):
            phrases.append(match.group(1).lower())

        # Remove quoted phrases from the text before tokenizing
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
            self.list_originals.clear()
            self.displayed_items.clear()

            include_words, exclude_words, phrases = self._parse_filter_text(self.filter_text)
            has_filter = include_words or exclude_words or phrases

            displayed_count = 0
            for item in self.original_items:
                if has_filter:
                    prompt_lower = (item.prompt_text or "").lower()

                    if any(word in prompt_lower for word in exclude_words):
                        continue

                    if include_words and not all(word in prompt_lower for word in include_words):
                        continue

                    if phrases and not all(phrase in prompt_lower for phrase in phrases):
                        continue

                match_count = len(self.matches.get(item.image_path, []))
                lw = QListWidgetItem(f"{item.image_path.name}  ({match_count} match{'es' if match_count != 1 else ''})")
                self.list_originals.addItem(lw)
                self.displayed_items.append(item)
                displayed_count += 1

            total_count = len(self.original_items)
            if has_filter:
                self.lbl_list_count.setText(f"{displayed_count} of {total_count} items (filtered)")
            else:
                self.lbl_list_count.setText(f"{displayed_count} items")

        finally:
            self._updating_selection = False

        if self.list_originals.count() > 0:
            self.list_originals.setCurrentRow(0)

    def rematch_with_progress(self) -> None:
        """Perform matching with progress dialog."""
        if not self.original_items or not self.generated_items:
            return

        # Create progress dialog
        progress = QProgressDialog("Initializing matching...", "Cancel", 0, len(self.original_items), self)
        progress.setWindowTitle("Matching Images to Prompts")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)  # Show immediately
        progress.show()

        # Keep track of cancellation
        cancelled = False

        def progress_callback(current: int, total: int, message: str) -> bool:
            nonlocal cancelled

            # Update progress dialog
            progress.setValue(current)
            progress.setLabelText(message)

            # Process events to keep UI responsive
            QApplication.processEvents()

            # Check if user cancelled
            if progress.wasCanceled():
                cancelled = True
                return False

            return True

        try:
            # Perform matching with progress reporting
            matches = self.matching_engine.match_all_items(
                self.original_items,
                self.generated_items,
                progress_callback
            )

            if cancelled or matches is None:
                self.statusBar().showMessage("Matching cancelled by user", 3000)
                return

            # Update matches and UI
            self.matches = matches

            # Update list items with match counts
            for row in range(len(self.displayed_items)):
                item = self.displayed_items[row]
                match_count = len(self.matches.get(item.image_path, []))
                lw_item = self.list_originals.item(row)
                if lw_item is not None:
                    lw_item.setText(f"{item.image_path.name}  ({match_count} match{'es' if match_count != 1 else ''})")

            # Refresh current selection
            self.on_select_original(self.list_originals.currentRow())

            total_matches = sum(len(match_list) for match_list in self.matches.values())
            self.statusBar().showMessage(
                f"Matching complete! Found {total_matches} total matches across {len(self.matches)} items", 5000)

        except Exception as e:
            QMessageBox.critical(self, "Matching Error", f"An error occurred during matching:\n{str(e)}")
            self.statusBar().showMessage("Matching failed", 3000)
        finally:
            progress.close()

    # def rematch_only(self) -> None:
    #     self.statusBar().showMessage("Matching images to prompts...")
    #     QApplication.processEvents()
    #
    #     self.matches = self.scanner.match_generated_to_original(self.original_items, self.generated_items)
    #
    #     for row, item in enumerate(self.original_items):
    #         match_count = len(self.matches.get(item.image_path, []))
    #         lw_item = self.list_originals.item(row)
    #         if lw_item is not None:
    #             lw_item.setText(f"{item.image_path.name}  ({match_count} match{'es' if match_count != 1 else ''})")
    #
    #     self.on_select_original(self.list_originals.currentRow())
    #     self.statusBar().showMessage("Ready")

    def on_select_original(self, row: int) -> None:
        if self._updating_selection:
            return

        self._updating_selection = True
        try:
            if row is None or row < 0 or row >= len(self.displayed_items):
                self.lbl_orig_image.clear()
                self.lbl_orig_image.setText("Original image will appear here")
                self.txt_prompt.clear()
                self.populate_generated_tabs([])
                return

            item = self.displayed_items[row]
            self.current_original_item = item
            self.lbl_orig_image.clear()
            self._display_original_image(item)
            self.txt_prompt.setPlainText(item.prompt_text or "")
            self.populate_generated_tabs(self.matches.get(item.image_path, []))
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

    def _scale_generated_image(self, label: QLabel) -> None:
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

    def _on_tab_changed(self, index: int) -> None:
        if index >= 0:
            QApplication.processEvents()
            tab_widget = self.tabs_generated.widget(index)
            if tab_widget:
                for img_label in tab_widget.findChildren(QLabel):
                    if img_label.property("original_pixmap"):
                        self._scale_generated_image(img_label)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        if self.current_original_item:
            QApplication.processEvents()
            self._display_original_image(self.current_original_item)

        current_tab_idx = self.tabs_generated.currentIndex()
        if current_tab_idx >= 0:
            tab_widget = self.tabs_generated.widget(current_tab_idx)
            if tab_widget:
                for img_label in tab_widget.findChildren(QLabel):
                    if img_label.property("original_pixmap"):
                        self._scale_generated_image(img_label)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_original_item:
            QApplication.processEvents()
            self._display_original_image(self.current_original_item)

        current_tab_idx = self.tabs_generated.currentIndex()
        if current_tab_idx >= 0:
            tab_widget = self.tabs_generated.widget(current_tab_idx)
            if tab_widget:
                for img_label in tab_widget.findChildren(QLabel):
                    if img_label.property("original_pixmap"):
                        self._scale_generated_image(img_label)

    def populate_generated_tabs(self, items: List[GeneratedItem]) -> None:
        while self.tabs_generated.count() > 0:
            widget = self.tabs_generated.widget(0)
            self.tabs_generated.removeTab(0)
            if widget:
                for child in widget.findChildren(QLabel):
                    child.clear()
                widget.deleteLater()

        QApplication.processEvents()

        if not items:
            placeholder = QWidget()
            layout = QVBoxLayout(placeholder)
            no_match_label = QLabel("No matching generated images found.")
            no_match_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_match_label)
            self.tabs_generated.addTab(placeholder, "None")
            return

        max_tabs = min(len(items), 8)
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
                QApplication.processEvents()
                self._scale_generated_image(img_label)
            else:
                img_label.setText(f"Failed to load:\n{g.image_path.name}")

            layout.addWidget(img_label, 1)

            file_info = QLabel(f"File: {g.image_path.name}")
            file_info.setTextFormat(Qt.PlainText)
            layout.addWidget(file_info)

            snippet = (g.prompt_text or "<no prompt in metadata>").strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + "..."

            snippet_label = QLabel()
            snippet_label.setTextFormat(Qt.PlainText)
            snippet_label.setText(snippet)
            snippet_label.setWordWrap(True)
            snippet_label.setMaximumHeight(60)
            layout.addWidget(snippet_label, 0)

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
        display_row = self.list_originals.currentRow()
        if display_row < 0 or display_row >= len(self.displayed_items):
            return
        item = self.displayed_items[display_row]
        old_text = item.prompt_text
        new_text = self.txt_prompt.toPlainText().strip()

        if old_text == new_text:
            self.statusBar().showMessage("No changes to save", 2000)
            return

        try:
            self.prompt_manager.save_prompt(item, new_text)

            orig_idx = None
            for idx, orig_item in enumerate(self.original_items):
                if orig_item.image_path == item.image_path:
                    self.original_items[idx] = OriginalItem(
                        image_path=item.image_path,
                        prompt_path=item.prompt_path,
                        prompt_text=new_text
                    )
                    orig_idx = idx
                    break

            self.displayed_items[display_row] = OriginalItem(
                image_path=item.image_path,
                prompt_path=item.prompt_path,
                prompt_text=new_text
            )

            if orig_idx is not None:
                self.rematch_single_item(orig_idx, display_row)

            self.statusBar().showMessage(f"Saved: {item.prompt_path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save prompt to {item.prompt_path}:\n{e}")

    def on_filter_changed(self, text: str) -> None:
        self.filter_text = text
        # Restart the debounce timer - only apply filter after typing stops for 300ms
        self.filter_timer.stop()
        self.filter_timer.start(300)

    def _apply_filter(self) -> None:
        """Called after debounce timer expires to actually apply the filter"""
        self.populate_original_list()

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
                lw_item.setText(f"{item.image_path.name}  ({match_count} match{'es' if match_count != 1 else ''})")

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

            orig_idx = None
            for idx, orig_item in enumerate(self.original_items):
                if orig_item.image_path == item.image_path:
                    self.original_items[idx] = OriginalItem(
                        image_path=item.image_path,
                        prompt_path=item.prompt_path,
                        prompt_text=new_text
                    )
                    orig_idx = idx
                    break

            self.displayed_items[display_row] = OriginalItem(
                image_path=item.image_path,
                prompt_path=item.prompt_path,
                prompt_text=new_text
            )

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
                    self.original_items[idx] = OriginalItem(
                        image_path=orig_item.image_path,
                        prompt_path=orig_item.prompt_path,
                        prompt_text=old_text
                    )

                    for display_row, disp_item in enumerate(self.displayed_items):
                        if disp_item.prompt_path == prompt_path:
                            self.displayed_items[display_row] = OriginalItem(
                                image_path=disp_item.image_path,
                                prompt_path=disp_item.prompt_path,
                                prompt_text=old_text
                            )

                            if self.list_originals.currentRow() == display_row:
                                self.txt_prompt.setPlainText(old_text)

                            self.rematch_single_item(idx, display_row)
                            break
                    break

            self.statusBar().showMessage(f"Undone: {prompt_path.name}", 3000)

        except Exception as e:
            QMessageBox.critical(self, "Undo Error", f"Failed to undo changes to {prompt_path}:\n{e}")

    def closeEvent(self, event):
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
