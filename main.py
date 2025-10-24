import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import difflib
import re

# Qt imports
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QImage, QKeySequence, QShortcut
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
    QCheckBox,
)

try:
    from PIL import Image
except Exception:
    raise SystemExit("Pillow is required. Please install with: pip install Pillow")

SUPPORTED_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
GENERATED_IMG_EXTS = {".png"}


@dataclass
class OriginalItem:
    image_path: Path
    prompt_path: Path
    prompt_text: str


@dataclass
class GeneratedItem:
    image_path: Path
    prompt_text: Optional[str]


class DatasetScanner:
    def __init__(self) -> None:
        pass

    @staticmethod
    def _normalize_prompt(text: Optional[str]) -> str:
        if not text:
            return ""
        s = text.strip()
        # Remove leading 'parameters ' noise sometimes present in metadata
        if s.lower().startswith("parameters "):
            s = s[len("parameters "):]
        # Drop anything inside angle brackets like <lora:...>
        s = re.sub(r"<[^>]+>", " ", s)
        # Normalize line breaks and collapse whitespace
        s = s.replace("\r", " \n ").replace("\n", " ")
        s = re.sub(r"\s+", " ", s)
        # Lowercase for case-insensitive matching
        s = s.lower().strip()
        return s

    @staticmethod
    def read_text_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:
            return path.read_text(errors="ignore").strip()
        except FileNotFoundError:
            return ""

    @staticmethod
    def load_image_metadata_prompt_png(path: Path) -> Optional[str]:
        try:
            with Image.open(path) as im:
                info = getattr(im, "info", {}) or {}
        except Exception:
            return None

        for key in ("prompt", "Prompt"):
            if key in info and isinstance(info[key], str) and info[key].strip():
                return info[key].strip()

        params = info.get("parameters") or info.get("Parameters")
        if isinstance(params, str) and params.strip():
            text = params.strip()
            neg_idx = text.find("\nNegative prompt:")
            if neg_idx != -1:
                text = text[:neg_idx]
            for marker in ["\nSteps:", "\nSampler:", "\nCFG scale:", "\nSeed:", "\nModel:"]:
                m = text.find(marker)
                if m != -1:
                    text = text[:m]
            return text.strip() if text.strip() else None
        return None

    def scan_original_dataset(self, root: Path) -> List[OriginalItem]:
        items: List[OriginalItem] = []
        if not root.exists():
            return items
        for dirpath, _, filenames in os.walk(root):
            images_by_stem: Dict[str, Path] = {}
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext in SUPPORTED_IMG_EXTS:
                    stem = Path(fname).stem
                    priority = {".png": 0, ".jpg": 1, ".jpeg": 2, ".webp": 3}.get(ext, 9)
                    prev = images_by_stem.get(stem)
                    if prev is None or {".png": 0, ".jpg": 1, ".jpeg": 2, ".webp": 3}.get(prev.suffix.lower(), 9) > priority:
                        images_by_stem[stem] = Path(dirpath) / fname
            for stem, img_path in images_by_stem.items():
                txt_path = Path(dirpath) / f"{stem}.txt"
                prompt = self.read_text_file(txt_path)
                items.append(OriginalItem(image_path=img_path, prompt_path=txt_path, prompt_text=prompt))
        items.sort(key=lambda it: str(it.image_path).lower())
        return items

    def scan_generated_dataset(self, root: Path) -> List[GeneratedItem]:
        items: List[GeneratedItem] = []
        if not root.exists():
            return items
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext in GENERATED_IMG_EXTS:
                    p = Path(dirpath) / fname
                    prompt = self.load_image_metadata_prompt_png(p)
                    items.append(GeneratedItem(image_path=p, prompt_text=prompt))
        items.sort(key=lambda it: str(it.image_path).lower())
        return items

    def match_generated_to_original(self, originals: List[OriginalItem], generated: List[GeneratedItem]) -> Dict[
        Path, List[GeneratedItem]]:
        # Build a lookup for generated items by stem for filename matching
        gen_by_stem: Dict[str, List[GeneratedItem]] = {}
        for g in generated:
            stem = g.image_path.stem.lower()
            if stem not in gen_by_stem:
                gen_by_stem[stem] = []
            gen_by_stem[stem].append(g)

        # Prepare normalized prompts for generated items
        gen_norm: List[Tuple[GeneratedItem, str]] = []
        for g in generated:
            norm = self._normalize_prompt(g.prompt_text)
            if norm:
                gen_norm.append((g, norm))

        mapping: Dict[Path, List[GeneratedItem]] = {}
        for o in originals:
            o_stem = o.image_path.stem.lower()

            # Priority 1: Filename match (preserves association even after prompt edits)
            filename_matches = gen_by_stem.get(o_stem, [])

            # Priority 2: Prompt-based matching
            o_norm = self._normalize_prompt(o.prompt_text)
            prompt_exact_matches: List[GeneratedItem] = []
            fuzzy_matches: List[Tuple[float, GeneratedItem]] = []

            if o_norm:
                for g, g_norm in gen_norm:
                    # Skip if already matched by filename
                    if g in filename_matches:
                        continue

                    # Quick exact match checks first
                    if o_norm == g_norm:
                        prompt_exact_matches.append(g)
                        continue

                    # Fast substring checks
                    if o_norm in g_norm or g_norm in o_norm:
                        prompt_exact_matches.append(g)
                        continue

                    # Only do expensive fuzzy matching if strings are similar length
                    # and share some common words (optimization)
                    len_diff = abs(len(o_norm) - len(g_norm)) / max(len(o_norm), len(g_norm), 1)
                    if len_diff > 0.5:  # Skip if length difference > 50%
                        continue

                    # Quick word overlap check
                    o_words = set(o_norm.split())
                    g_words = set(g_norm.split())
                    if not o_words or not g_words:
                        continue

                    word_overlap = len(o_words & g_words) / len(o_words | g_words)
                    if word_overlap < 0.3:  # Skip if word overlap < 30%
                        continue

                    # Only now do the expensive similarity calculation
                    score = difflib.SequenceMatcher(None, o_norm, g_norm).ratio()
                    if score >= 0.85:  # Lowered threshold since we pre-filter
                        fuzzy_matches.append((score, g))

            # Combine results: filename matches first, then prompt matches, then fuzzy
            result = filename_matches[:]
            result.extend(prompt_exact_matches)
            fuzzy_matches.sort(key=lambda x: (-x[0], x[1].image_path.name.lower()))
            result.extend([g for _, g in fuzzy_matches])

            mapping[o.image_path] = result

        return mapping


class ImageLoader:
    def __init__(self, max_size: Tuple[int, int] = (1200, 1200)):  # Increased max size
        self.max_size = max_size
        self.cache: Dict[Tuple[Path, Tuple[int, int]], QPixmap] = {}
        self.max_cache_size = 15  # Reduced cache size

    def clear_cache(self) -> None:
        self.cache.clear()

    def load_for_display(self, path: Path, target_size: Tuple[int, int]) -> Optional[QPixmap]:
        key = (path, target_size)
        if key in self.cache:
            return self.cache[key]

        # Clear cache if getting too large
        if len(self.cache) >= self.max_cache_size:
            self.cache.clear()

        try:
            with Image.open(path) as pil_image:
                # Handle transparency by compositing on white background
                if pil_image.mode in ('RGBA', 'LA'):
                    background = Image.new('RGB', pil_image.size, (255, 255, 255))
                    background.paste(pil_image, mask=pil_image.split()[-1])
                    pil_image = background
                elif pil_image.mode != 'RGB':
                    pil_image = pil_image.convert('RGB')

                # Copy and resize
                pil_image = pil_image.copy()
                pil_image.thumbnail(target_size, Image.LANCZOS)

                # Convert to QPixmap
                width, height = pil_image.size
                qimg = QImage(
                    pil_image.tobytes(),
                    width,
                    height,
                    width * 3,
                    QImage.Format_RGB888
                )
                pix = QPixmap.fromImage(qimg.copy())

                self.cache[key] = pix
                return pix

        except Exception as e:
            print(f"Error loading image {path}: {e}")
            placeholder = QPixmap(100, 100)
            placeholder.fill(Qt.lightGray)
            return placeholder

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Generated Dataset Comparator")
        self.resize(1200, 800)

        self.scanner = DatasetScanner()
        self.image_loader = ImageLoader()

        self.original_root: Optional[Path] = None
        self.generated_root: Optional[Path] = None

        self.original_items: List[OriginalItem] = []
        self.generated_items: List[GeneratedItem] = []
        self.matches: Dict[Path, List[GeneratedItem]] = {}
        self.filter_text: str = ""
        self.displayed_items: List[OriginalItem] = []  # Track which items are currently displayed
        self.current_original_item: Optional[OriginalItem] = None  # Track current item for resize

        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        # Toolbar-like row
        top_row = QHBoxLayout()
        self.btn_load_orig = QPushButton("Load Original Folder")
        self.lbl_orig = QLabel("Original: <not set>")
        self.btn_load_gen = QPushButton("Load Generated Folder")
        self.lbl_gen = QLabel("Generated: <not set>")
        self.btn_rescan = QPushButton("Rescan/Match")

        for w in (self.btn_load_orig, self.lbl_orig, self.btn_load_gen, self.lbl_gen, self.btn_rescan):
            top_row.addWidget(w)
        top_row.addStretch(1)
        root_layout.addLayout(top_row)

        # Import checkbox row (below generated folder button)
        import_row = QHBoxLayout()
        import_row.addWidget(QLabel(""))  # Spacer to align with generated folder section
        import_row.addWidget(self.lbl_orig)  # Align checkbox under generated button
        self.chk_import = QCheckBox("Import")
        self.chk_import.setToolTip("Create 'generated' subdirectory in original dataset folder and import files there")
        import_row.addWidget(self.chk_import)
        import_row.addStretch(1)
        root_layout.addLayout(import_row)

        self.btn_load_orig.clicked.connect(self.choose_original)
        self.btn_load_gen.clicked.connect(self.choose_generated)
        self.btn_rescan.clicked.connect(self.rescan_all)

        # Filter textbox row
        filter_row = QHBoxLayout()
        filter_label = QLabel("Filter by words:")
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Enter words separated by space to filter...")
        self.txt_filter.textChanged.connect(self.on_filter_changed)
        filter_row.addWidget(filter_label)
        filter_row.addWidget(self.txt_filter, 1)
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
        self.btn_rematch.clicked.connect(self.rematch_only)

        # Add Ctrl+S shortcut for saving prompts
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save_prompt)

        # Status bar
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    def choose_original(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Original Dataset Folder")
        if not path:
            return
        self.original_root = Path(path)
        self.lbl_orig.setText(f"Original: {self.original_root}")
        self.rescan_original()

    def choose_generated(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Generated Dataset Folder")
        if not path:
            return
        self.generated_root = Path(path)
        self.lbl_gen.setText(f"Generated: {self.generated_root}")
        self.rescan_generated()

    def rescan_original(self) -> None:
        if not self.original_root:
            return
        self.statusBar().showMessage("Scanning original dataset...")
        QApplication.processEvents()

        # Clear cache to prevent stale data
        self.image_loader.clear_cache()

        self.original_items = self.scanner.scan_original_dataset(self.original_root)
        self.statusBar().showMessage(f"Found {len(self.original_items)} original items")
        self.populate_original_list()
        self.rematch_only()

    def rescan_generated(self) -> None:
        if not self.generated_root:
            return
        self.statusBar().showMessage("Scanning generated dataset...")
        QApplication.processEvents()

        # Clear cache to prevent stale data
        self.image_loader.clear_cache()

        self.generated_items = self.scanner.scan_generated_dataset(self.generated_root)
        has_prompt = sum(1 for g in self.generated_items if g.prompt_text)
        self.statusBar().showMessage(f"Found {len(self.generated_items)} generated images ({has_prompt} with prompts)")
        self.rematch_only()

    def rescan_all(self) -> None:
        if self.original_root:
            self.rescan_original()
        if self.generated_root:
            self.rescan_generated()

    def populate_original_list(self) -> None:
        self.list_originals.clear()
        self.displayed_items.clear()

        # Get filter words
        filter_words = [w.lower().strip() for w in self.filter_text.split() if w.strip()]

        displayed_count = 0
        for item in self.original_items:
            # Apply filter if any words are specified
            if filter_words:
                prompt_lower = (item.prompt_text or "").lower()
                # Check if all filter words are in the prompt
                if not all(word in prompt_lower for word in filter_words):
                    continue  # Skip this item

            match_count = len(self.matches.get(item.image_path, []))
            lw = QListWidgetItem(f"{item.image_path.name}  ({match_count} match{'es' if match_count!=1 else ''})")
            self.list_originals.addItem(lw)
            self.displayed_items.append(item)  # Track displayed items
            displayed_count += 1

        # Update count label
        total_count = len(self.original_items)
        if filter_words:
            self.lbl_list_count.setText(f"{displayed_count} of {total_count} items (filtered)")
        else:
            self.lbl_list_count.setText(f"{displayed_count} items")

        if self.list_originals.count() > 0:
            self.list_originals.setCurrentRow(0)

    def rematch_only(self) -> None:
        # Show progress for long operations
        self.statusBar().showMessage("Matching images to prompts...")
        QApplication.processEvents()

        self.matches = self.scanner.match_generated_to_original(self.original_items, self.generated_items)

        # Refresh list counts text
        for row, item in enumerate(self.original_items):
            match_count = len(self.matches.get(item.image_path, []))
            lw_item = self.list_originals.item(row)
            if lw_item is not None:
                lw_item.setText(f"{item.image_path.name}  ({match_count} match{'es' if match_count != 1 else ''})")

        # Refresh right side for current selection
        self.on_select_original(self.list_originals.currentRow())

        self.statusBar().showMessage("Ready")

    def on_select_original(self, row: int) -> None:
        if row is None or row < 0 or row >= len(self.displayed_items):
            # Clear displays when no valid selection
            self.lbl_orig_image.clear()
            self.lbl_orig_image.setText("Original image will appear here")
            self.txt_prompt.clear()
            self.populate_generated_tabs([])
            return

        item = self.displayed_items[row]

        # Store the current item for resize handling
        self.current_original_item = item

        # Clear previous image first to prevent display issues
        self.lbl_orig_image.clear()

        # Load and scale original image properly
        self._display_original_image(item)

        # Set prompt text
        self.txt_prompt.setPlainText(item.prompt_text or "")

        # Load generated tabs
        self.populate_generated_tabs(self.matches.get(item.image_path, []))

    def _display_original_image(self, item: OriginalItem) -> None:
        """Display the original image scaled to fit the label"""
        # Load high-res image
        pix = self.image_loader.load_for_display(item.image_path, (2000, 2000))
        if pix and not pix.isNull():
            # Scale to fit the label size while preserving aspect ratio
            label_size = self.lbl_orig_image.size()
            if label_size.width() > 50 and label_size.height() > 50:  # Valid size
                scaled_pix = pix.scaled(
                    label_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.lbl_orig_image.setPixmap(scaled_pix)
            else:
                # Fallback for when label size isn't available yet
                self.lbl_orig_image.setPixmap(pix)
            self.lbl_orig_image.setText("")
        else:
            self.lbl_orig_image.setText(f"Failed to load: {item.image_path.name}")

    def _scale_generated_image(self, label: QLabel) -> None:
        """Scale a generated image label to fit its container"""
        pix = label.property("original_pixmap")
        if pix and not pix.isNull():
            # Get the parent widget size (the tab container)
            parent = label.parentWidget()
            if parent:
                # Account for the layout and snippet text below
                available_height = parent.height() - 100  # Reserve space for prompt text
                available_width = parent.width() - 20  # Account for margins
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
        """Handle tab change to ensure images are properly scaled"""
        if index >= 0:
            QApplication.processEvents()
            tab_widget = self.tabs_generated.widget(index)
            if tab_widget:
                for img_label in tab_widget.findChildren(QLabel):
                    if img_label.property("original_pixmap"):
                        self._scale_generated_image(img_label)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """Handle splitter movement to rescale images"""
        # Re-display current original image with new scaling
        if self.current_original_item:
            QApplication.processEvents()
            self._display_original_image(self.current_original_item)

        # Re-scale generated images in the current tab
        current_tab_idx = self.tabs_generated.currentIndex()
        if current_tab_idx >= 0:
            tab_widget = self.tabs_generated.widget(current_tab_idx)
            if tab_widget:
                for img_label in tab_widget.findChildren(QLabel):
                    if img_label.property("original_pixmap"):
                        self._scale_generated_image(img_label)

    def resizeEvent(self, event):
        """Handle window resize to update image scaling"""
        super().resizeEvent(event)
        # Re-display current original image with new scaling
        if self.current_original_item:
            QApplication.processEvents()
            self._display_original_image(self.current_original_item)

        # Re-scale generated images in the current tab only (for performance)
        current_tab_idx = self.tabs_generated.currentIndex()
        if current_tab_idx >= 0:
            tab_widget = self.tabs_generated.widget(current_tab_idx)
            if tab_widget:
                for img_label in tab_widget.findChildren(QLabel):
                    if img_label.property("original_pixmap"):
                        self._scale_generated_image(img_label)

    def populate_generated_tabs(self, items: List[GeneratedItem]) -> None:
        # Clear all tabs and their widgets properly
        while self.tabs_generated.count() > 0:
            widget = self.tabs_generated.widget(0)
            self.tabs_generated.removeTab(0)
            if widget:
                # Clear any pixmaps in the widget to free memory
                for child in widget.findChildren(QLabel):
                    child.clear()
                widget.deleteLater()

        # Force garbage collection of cleared widgets
        QApplication.processEvents()

        if not items:
            placeholder = QWidget()
            layout = QVBoxLayout(placeholder)
            no_match_label = QLabel("No matching generated images found.")
            no_match_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_match_label)
            self.tabs_generated.addTab(placeholder, "None")
            return

        # Limit tabs to prevent memory issues
        max_tabs = min(len(items), 8)
        items_to_show = items[:max_tabs]

        for i, g in enumerate(items_to_show):
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.setContentsMargins(5, 5, 5, 5)

            # Image label with proper scaling setup
            img_label = QLabel()
            img_label.setAlignment(Qt.AlignCenter)
            img_label.setScaledContents(False)  # Don't stretch, we'll scale manually
            img_label.setMinimumSize(100, 100)
            img_label.setSizePolicy(img_label.sizePolicy().horizontalPolicy(), img_label.sizePolicy().verticalPolicy())

            # Load image at high resolution
            pix = self.image_loader.load_for_display(g.image_path, (2000, 2000))
            if pix and not pix.isNull():
                # Store the original pixmap for resize handling
                img_label.setProperty("original_pixmap", pix)
                # Calculate initial size based on available space
                QApplication.processEvents()
                self._scale_generated_image(img_label)
            else:
                img_label.setText(f"Failed to load:\n{g.image_path.name}")

            layout.addWidget(img_label, 1)  # Give maximum stretch to image

            # Rename button and filename info
            info_layout = QHBoxLayout()
            file_info = QLabel(f"File: {g.image_path.name}")
            file_info.setTextFormat(Qt.PlainText)
            rename_btn = QPushButton("Rename to Match")
            rename_btn.setMaximumWidth(120)
            rename_btn.clicked.connect(lambda checked, gen_item=g: self.rename_generated_to_match(gen_item))
            info_layout.addWidget(file_info, 1)
            info_layout.addWidget(rename_btn)
            layout.addLayout(info_layout)

            # Prompt snippet
            snippet = (g.prompt_text or "<no prompt in metadata>").strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + "..."

            snippet_label = QLabel()
            snippet_label.setTextFormat(Qt.PlainText)
            snippet_label.setText(snippet)
            snippet_label.setWordWrap(True)
            snippet_label.setMaximumHeight(60)  # Reduced height for button space
            layout.addWidget(snippet_label, 0)  # No stretch for text

            # Short tab name with indicator if filename already matches
            tab_name = g.image_path.stem
            if len(tab_name) > 12:
                tab_name = tab_name[:9] + "..."
            self.tabs_generated.addTab(tab, tab_name)

        # Show info if we limited the number of tabs
        if len(items) > max_tabs:
            info_tab = QWidget()
            info_layout = QVBoxLayout(info_tab)
            info_label = QLabel(
                f"Showing {max_tabs} of {len(items)} matches.\nScroll through original list to see others.")
            info_label.setAlignment(Qt.AlignCenter)
            info_label.setWordWrap(True)
            info_layout.addWidget(info_label)
            self.tabs_generated.addTab(info_tab, f"+{len(items) - max_tabs}")

    def rename_generated_to_match(self, gen_item: GeneratedItem) -> None:
        """Rename a generated file to match the currently selected original item"""
        display_row = self.list_originals.currentRow()
        if display_row < 0 or display_row >= len(self.displayed_items):
            QMessageBox.warning(self, "No Selection", "Please select an original item first.")
            return

        orig_item = self.displayed_items[display_row]

        # Get the target filename (same stem as original, keep generated extension)
        target_stem = orig_item.image_path.stem
        current_ext = gen_item.image_path.suffix
        target_name = target_stem + current_ext
        target_path = gen_item.image_path.parent / target_name

        # Check if already has the correct name
        if gen_item.image_path.name.lower() == target_name.lower():
            self.statusBar().showMessage("File already has matching name", 2000)
            return

        # Check for conflicts
        if target_path.exists():
            reply = QMessageBox.question(
                self,
                "File Exists",
                f"A file named '{target_name}' already exists in the generated folder.\n\n"
                f"Do you want to replace it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        try:
            # Rename the file
            gen_item.image_path.rename(target_path)

            # Update the GeneratedItem in our list
            for idx, g in enumerate(self.generated_items):
                if g.image_path == gen_item.image_path:
                    self.generated_items[idx] = GeneratedItem(
                        image_path=target_path,
                        prompt_text=g.prompt_text
                    )
                    break

            # Clear cache to prevent stale image data
            self.image_loader.clear_cache()

            # Re-match and refresh display
            self.rematch_only()

            self.statusBar().showMessage(f"Renamed to: {target_name}", 3000)

        except Exception as e:
            QMessageBox.critical(self, "Rename Error", f"Failed to rename file:\n{e}")

    def save_prompt(self) -> None:
        display_row = self.list_originals.currentRow()
        if display_row < 0 or display_row >= len(self.displayed_items):
            return
        item = self.displayed_items[display_row]
        old_text = item.prompt_text
        new_text = self.txt_prompt.toPlainText().strip()

        # Only proceed if the text actually changed
        if old_text == new_text:
            self.statusBar().showMessage("No changes to save", 2000)
            return

        try:
            item.prompt_path.parent.mkdir(parents=True, exist_ok=True)
            item.prompt_path.write_text(new_text, encoding="utf-8")
            # update in-memory - find the item in original_items by image_path
            orig_idx = None
            for idx, orig_item in enumerate(self.original_items):
                if orig_item.image_path == item.image_path:
                    self.original_items[idx] = OriginalItem(image_path=item.image_path, prompt_path=item.prompt_path,
                                                            prompt_text=new_text)
                    orig_idx = idx
                    break

            # Update the displayed item as well
            self.displayed_items[display_row] = OriginalItem(image_path=item.image_path, prompt_path=item.prompt_path,
                                                             prompt_text=new_text)

            # Re-match and update display using the original items index and display row
            if orig_idx is not None:
                self.rematch_single_item(orig_idx, display_row)

            self.statusBar().showMessage(f"Saved: {item.prompt_path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save prompt to {item.prompt_path}:\n{e}")

    def on_filter_changed(self, text: str) -> None:
        """Handle filter text changes and refresh the list"""
        self.filter_text = text
        self.populate_original_list()

    def rematch_single_item(self, orig_idx: int, display_row: int = -1) -> None:
        """Re-match only a specific original item instead of all items

        Args:
            orig_idx: Index in self.original_items
            display_row: Optional index in displayed list for UI update
        """
        if orig_idx < 0 or orig_idx >= len(self.original_items):
            return

        item = self.original_items[orig_idx]
        o_stem = item.image_path.stem.lower()

        # Priority 1: Filename match
        filename_matches: List[GeneratedItem] = []
        for g in self.generated_items:
            if g.image_path.stem.lower() == o_stem:
                filename_matches.append(g)

        # Prepare normalized prompts for generated items (reuse existing logic)
        gen_norm: List[Tuple[GeneratedItem, str]] = []
        for g in self.generated_items:
            norm = self.scanner._normalize_prompt(g.prompt_text)
            if norm:
                gen_norm.append((g, norm))

        # Priority 2: Prompt-based matching
        o_norm = self.scanner._normalize_prompt(item.prompt_text)
        prompt_exact_matches: List[GeneratedItem] = []
        fuzzy_matches: List[Tuple[float, GeneratedItem]] = []

        if o_norm:
            for g, g_norm in gen_norm:
                # Skip if already matched by filename
                if g in filename_matches:
                    continue

                # Quick exact match checks first
                if o_norm == g_norm:
                    prompt_exact_matches.append(g)
                    continue

                # Fast substring checks
                if o_norm in g_norm or g_norm in o_norm:
                    prompt_exact_matches.append(g)
                    continue

                # Only do expensive fuzzy matching if strings are similar length
                # and share some common words (optimization)
                len_diff = abs(len(o_norm) - len(g_norm)) / max(len(o_norm), len(g_norm), 1)
                if len_diff > 0.5:  # Skip if length difference > 50%
                    continue

                # Quick word overlap check
                o_words = set(o_norm.split())
                g_words = set(g_norm.split())
                if not o_words or not g_words:
                    continue

                word_overlap = len(o_words & g_words) / len(o_words | g_words)
                if word_overlap < 0.3:  # Skip if word overlap < 30%
                    continue

                # Only now do the expensive similarity calculation
                import difflib
                score = difflib.SequenceMatcher(None, o_norm, g_norm).ratio()
                if score >= 0.85:  # Lowered threshold since we pre-filter
                    fuzzy_matches.append((score, g))

        # Combine results: filename matches first, then prompt matches, then fuzzy
        result = filename_matches[:]
        result.extend(prompt_exact_matches)
        fuzzy_matches.sort(key=lambda x: (-x[0], x[1].image_path.name.lower()))
        result.extend([g for _, g in fuzzy_matches])

        self.matches[item.image_path] = result

        # Update the display text if we have a valid display row
        match_count = len(self.matches.get(item.image_path, []))
        if display_row >= 0:
            lw_item = self.list_originals.item(display_row)
            if lw_item is not None:
                lw_item.setText(f"{item.image_path.name}  ({match_count} match{'es' if match_count != 1 else ''})")

            # Refresh the generated tabs for the current selection if it's the same item
            if self.list_originals.currentRow() == display_row:
                self.populate_generated_tabs(self.matches.get(item.image_path, []))

    def on_font_size_changed(self, value: int) -> None:
        """Update font size for the prompt text editor"""
        self.font_size_label.setText(f"{value}pt")
        font = self.txt_prompt.font()
        font.setPointSize(value)
        self.txt_prompt.setFont(font)

    def closeEvent(self, event):
        """Clean up resources when closing"""
        try:
            # Clear all images from labels
            self.lbl_orig_image.clear()

            # Clear all tab widgets
            while self.tabs_generated.count() > 0:
                widget = self.tabs_generated.widget(0)
                self.tabs_generated.removeTab(0)
                if widget:
                    for child in widget.findChildren(QLabel):
                        child.clear()
                    widget.deleteLater()

            # Clear image cache
            self.image_loader.clear_cache()

            # Process events to ensure cleanup
            QApplication.processEvents()
        except Exception:
            pass  # Ignore cleanup errors

        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    # Preload test resources if exist
    repo_root = Path(__file__).resolve().parent
    test_orig = repo_root / "resources" / "test" / "original_dataset"
    test_gen = repo_root / "resources" / "test" / "generated_dataset"
    if test_orig.exists():
        win.original_root = test_orig
        win.lbl_orig.setText(f"Original: {test_orig}")
        win.rescan_original()
    if test_gen.exists():
        win.generated_root = test_gen
        win.lbl_gen.setText(f"Generated: {test_gen}")
        win.rescan_generated()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
