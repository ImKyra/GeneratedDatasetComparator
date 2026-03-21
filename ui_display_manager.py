"""Manages UI display components for images and generated tabs."""

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QLabel, QTabWidget, QWidget, QVBoxLayout, QPlainTextEdit, QListWidgetItem

from items import OriginalItem, GeneratedItem
from image_loader import ImageLoader

MAX_GENERATED_TABS = 8


def scale_image_to_label(label: QLabel) -> None:
    """
    Scale the image stored in a label's property to fit the label size.

    Args:
        label: QLabel containing an 'original_pixmap' property
    """
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


class UIDisplayManager:
    """Manages display of images and generated item tabs in the UI."""

    def __init__(
        self,
        image_loader: ImageLoader,
        original_image_label: QLabel,
        generated_tabs: QTabWidget
    ):
        """
        Initialize the display manager.

        Args:
            image_loader: ImageLoader instance for loading images
            original_image_label: QLabel for displaying original images
            generated_tabs: QTabWidget for displaying generated image tabs
        """
        self.image_loader = image_loader
        self.original_image_label = original_image_label
        self.generated_tabs = generated_tabs

    def display_original_image(self, item: OriginalItem) -> None:
        """
        Display an original image in the original image label.

        Args:
            item: OriginalItem to display
        """
        pix = self.image_loader.load_for_display(item.image_path, (2000, 2000))
        if pix and not pix.isNull():
            label_size = self.original_image_label.size()
            if label_size.width() > 50 and label_size.height() > 50:
                scaled_pix = pix.scaled(
                    label_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.original_image_label.setPixmap(scaled_pix)
            else:
                self.original_image_label.setPixmap(pix)
            self.original_image_label.setText("")
        else:
            self.original_image_label.setText(f"Failed to load: {item.image_path.name}")

    def clear_original_image(self) -> None:
        """Clear the original image display."""
        self.original_image_label.clear()
        self.original_image_label.setText("Original image will appear here")

    def populate_generated_tabs(self, items: List[GeneratedItem]) -> None:
        """
        Populate the generated tabs widget with generated items.

        Args:
            items: List of GeneratedItem objects to display
        """
        # Clear existing tabs
        self._clear_all_tabs()

        if not items:
            self._create_no_matches_tab()
            return

        # Show up to MAX_GENERATED_TABS items
        max_tabs = min(len(items), MAX_GENERATED_TABS)
        items_to_show = items[:max_tabs]

        for i, gen_item in enumerate(items_to_show):
            self._create_generated_item_tab(gen_item)

        # Add overflow info tab if needed
        if len(items) > max_tabs:
            self._create_overflow_tab(len(items), max_tabs)

    def rescale_current_tab_images(self) -> None:
        """Rescale images in the currently visible tab."""
        current_tab_idx = self.generated_tabs.currentIndex()
        if current_tab_idx >= 0:
            tab_widget = self.generated_tabs.widget(current_tab_idx)
            if tab_widget:
                self._rescale_tab_images(tab_widget)

    def rescale_original_image(self, item: Optional[OriginalItem]) -> None:
        """
        Rescale the original image display.

        Args:
            item: OriginalItem currently being displayed, or None
        """
        if item:
            self.display_original_image(item)

    def _clear_all_tabs(self) -> None:
        """Remove and clean up all tabs."""
        while self.generated_tabs.count() > 0:
            widget = self.generated_tabs.widget(0)
            self.generated_tabs.removeTab(0)
            if widget:
                for child in widget.findChildren(QLabel):
                    child.clear()
                widget.deleteLater()

    def _create_no_matches_tab(self) -> None:
        """Create a placeholder tab when no matches are found."""
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        no_match_label = QLabel("No matching generated images found.")
        no_match_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(no_match_label)
        self.generated_tabs.addTab(placeholder, "None")

    def _create_generated_item_tab(self, gen_item: GeneratedItem) -> None:
        """
        Create a tab for a single generated item.

        Args:
            gen_item: GeneratedItem to create tab for
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)

        # Image label
        img_label = QLabel()
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setScaledContents(False)
        img_label.setMinimumSize(100, 100)
        img_label.setSizePolicy(
            img_label.sizePolicy().horizontalPolicy(),
            img_label.sizePolicy().verticalPolicy()
        )

        # Load and display image
        pix = self.image_loader.load_for_display(gen_item.image_path, (2000, 2000))
        if pix and not pix.isNull():
            img_label.setProperty("original_pixmap", pix)
            scale_image_to_label(img_label)
        else:
            img_label.setText(f"Failed to load:\n{gen_item.image_path.name}")

        layout.addWidget(img_label, 1)

        # File info label
        file_info = QLabel(f"File: {gen_item.image_path.name}")
        file_info.setTextFormat(Qt.PlainText)
        layout.addWidget(file_info)

        # Prompt display
        prompt_text = (gen_item.prompt_text or "<no prompt in metadata>").strip()
        prompt_display = QPlainTextEdit()
        prompt_display.setPlainText(prompt_text)
        prompt_display.setReadOnly(True)
        prompt_display.setMaximumHeight(80)
        prompt_display.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        prompt_display.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        layout.addWidget(prompt_display, 0)

        # Tab name (truncated if needed)
        tab_name = gen_item.image_path.stem
        if len(tab_name) > 12:
            tab_name = tab_name[:9] + "..."

        self.generated_tabs.addTab(tab, tab_name)

    def _create_overflow_tab(self, total_items: int, shown_items: int) -> None:
        """
        Create an info tab showing that more items exist.

        Args:
            total_items: Total number of items
            shown_items: Number of items currently shown
        """
        info_tab = QWidget()
        info_layout = QVBoxLayout(info_tab)
        info_label = QLabel(
            f"Showing {shown_items} of {total_items} matches.\n"
            f"Scroll through original list to see others."
        )
        info_label.setAlignment(Qt.AlignCenter)
        info_label.setWordWrap(True)
        info_layout.addWidget(info_label)
        self.generated_tabs.addTab(info_tab, f"+{total_items - shown_items}")

    def _rescale_tab_images(self, tab_widget: QWidget) -> None:
        """
        Rescale all images in a tab widget.

        Args:
            tab_widget: The tab widget containing image labels
        """
        for img_label in tab_widget.findChildren(QLabel):
            if img_label.property("original_pixmap"):
                scale_image_to_label(img_label)
