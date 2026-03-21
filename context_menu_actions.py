"""Context menu actions for the original items list."""

import os
import sys
import subprocess
import shutil
from pathlib import Path
from typing import List

from PySide6.QtWidgets import QApplication, QMessageBox, QStatusBar, QListWidget, QFileDialog

from items import OriginalItem


class ContextMenuActions:
    """Handles right-click context menu actions for the original items list."""

    def __init__(self, list_widget: QListWidget, status_bar: QStatusBar):
        """
        Initialize context menu actions handler.

        Args:
            list_widget: The QListWidget displaying original items
            status_bar: The QStatusBar for displaying status messages
        """
        self.list_widget = list_widget
        self.status_bar = status_bar

    def select_all_items(self) -> None:
        """Select all items in the list."""
        self.list_widget.selectAll()
        self.status_bar.showMessage(f"Selected {self.list_widget.count()} item(s)", 2000)

    def copy_selected_prompts(self, displayed_items: List[OriginalItem]) -> None:
        """
        Copy selected prompts to clipboard.

        Args:
            displayed_items: List of currently displayed OriginalItem objects
        """
        selected_rows = self._get_sorted_selected_rows()

        if not selected_rows:
            self.status_bar.showMessage("No items selected", 2000)
            return

        prompts = []
        for row in selected_rows:
            if 0 <= row < len(displayed_items):
                item = displayed_items[row]
                if item.prompt_text:
                    cleaned_prompt = self._clean_prompt_text(item.prompt_text)
                    prompts.append(cleaned_prompt)

        if prompts:
            self._copy_to_clipboard(prompts)
            self.status_bar.showMessage(f"Copied {len(prompts)} prompt(s) to clipboard", 3000)
        else:
            self.status_bar.showMessage("No prompts found in selected items", 2000)

    def open_image_with_default_app(self, displayed_items: List[OriginalItem], parent_widget=None) -> None:
        """
        Open the first selected image with the system's default application.

        Args:
            displayed_items: List of currently displayed OriginalItem objects
            parent_widget: Parent widget for displaying error dialogs
        """
        selected_rows = self._get_sorted_selected_rows()

        if not selected_rows:
            self.status_bar.showMessage("No items selected", 2000)
            return

        first_row = selected_rows[0]
        if 0 <= first_row < len(displayed_items):
            item = displayed_items[first_row]
            self._open_file_with_default_app(item.image_path, parent_widget)

    def _get_sorted_selected_rows(self) -> List[int]:
        """Get sorted list of selected row indices."""
        return sorted([self.list_widget.row(item) for item in self.list_widget.selectedItems()])

    @staticmethod
    def _clean_prompt_text(prompt_text: str) -> str:
        """Remove line breaks from prompt text."""
        return prompt_text.replace("\r\n", "").replace("\r", "").replace("\n", "")

    @staticmethod
    def _copy_to_clipboard(prompts: List[str]) -> None:
        """Copy prompts to system clipboard."""
        final_text = "\n\n".join(prompts)
        clipboard = QApplication.clipboard()
        clipboard.setText(final_text)

    def _open_file_with_default_app(self, file_path: Path, parent_widget=None) -> None:
        """
        Open a file with the system's default application.

        Args:
            file_path: Path to the file to open
            parent_widget: Parent widget for displaying error dialogs
        """
        if not file_path.exists():
            if parent_widget:
                QMessageBox.warning(
                    parent_widget,
                    "File Not Found",
                    f"Image file not found:\n{file_path}"
                )
            return

        try:
            self._launch_default_app(file_path)
            self.status_bar.showMessage(f"Opened: {file_path.name}", 2000)
        except Exception as e:
            if parent_widget:
                QMessageBox.critical(
                    parent_widget,
                    "Open Error",
                    f"Failed to open image:\n{e}"
                )

    @staticmethod
    def _launch_default_app(file_path: Path) -> None:
        """Launch the system's default application for the file."""
        if sys.platform == "win32":
            os.startfile(str(file_path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(file_path)], check=True)
        else:
            subprocess.run(["xdg-open", str(file_path)], check=True)

    def copy_files_and_prompts(self, displayed_items: List[OriginalItem], parent_widget=None) -> None:
        """
        Copy selected files and their prompts to a destination folder.

        Args:
            displayed_items: List of currently displayed OriginalItem objects
            parent_widget: Parent widget for displaying dialogs
        """
        self._copy_or_move_files(displayed_items, parent_widget, move=False)

    def move_files_and_prompts(self, displayed_items: List[OriginalItem], parent_widget=None) -> None:
        """
        Move selected files and their prompts to a destination folder.

        Args:
            displayed_items: List of currently displayed OriginalItem objects
            parent_widget: Parent widget for displaying dialogs
        """
        self._copy_or_move_files(displayed_items, parent_widget, move=True)

    def _copy_or_move_files(self, displayed_items: List[OriginalItem], parent_widget, move: bool) -> None:
        """
        Copy or move selected files and their prompts to a destination folder.

        Args:
            displayed_items: List of currently displayed OriginalItem objects
            parent_widget: Parent widget for displaying dialogs
            move: If True, move files; if False, copy files
        """
        selected_rows = self._get_sorted_selected_rows()

        if not selected_rows:
            self.status_bar.showMessage("No items selected", 2000)
            return

        # Ask user for destination folder
        dest_path = QFileDialog.getExistingDirectory(
            parent_widget,
            f"Select Destination Folder to {'Move' if move else 'Copy'} Files"
        )
        if not dest_path:
            return

        dest_folder = Path(dest_path)
        if not dest_folder.exists():
            QMessageBox.warning(
                parent_widget,
                "Invalid Destination",
                f"Destination folder does not exist:\n{dest_folder}"
            )
            return

        # Collect items to process
        items_to_process = []
        for row in selected_rows:
            if 0 <= row < len(displayed_items):
                items_to_process.append(displayed_items[row])

        if not items_to_process:
            self.status_bar.showMessage("No valid items to process", 2000)
            return

        # Process files
        success_count = 0
        error_count = 0
        errors = []

        for item in items_to_process:
            try:
                # Copy/move image file
                dest_image = dest_folder / item.image_path.name
                if move:
                    shutil.move(str(item.image_path), str(dest_image))
                else:
                    shutil.copy2(str(item.image_path), str(dest_image))

                # Copy/move prompt file if it exists
                if item.prompt_path and item.prompt_path.exists():
                    dest_prompt = dest_folder / item.prompt_path.name
                    if move:
                        shutil.move(str(item.prompt_path), str(dest_prompt))
                    else:
                        shutil.copy2(str(item.prompt_path), str(dest_prompt))

                success_count += 1
            except Exception as e:
                error_count += 1
                errors.append(f"{item.image_path.name}: {str(e)}")

        # Show results
        action = "Moved" if move else "Copied"
        message = f"{action} {success_count} file(s) and prompt(s)"
        if error_count > 0:
            message += f"\nFailed: {error_count} file(s)"
            if errors:
                message += f"\n\nErrors:\n" + "\n".join(errors[:5])
                if len(errors) > 5:
                    message += f"\n... and {len(errors) - 5} more error(s)"

        self.status_bar.showMessage(f"{action} {success_count} file(s)", 3000)

        if parent_widget:
            msg_type = QMessageBox.Information if error_count == 0 else QMessageBox.Warning
            QMessageBox(
                msg_type,
                f"{action} Complete" if error_count == 0 else f"{action} Completed with Errors",
                message,
                QMessageBox.Ok,
                parent_widget
            ).exec()

    def copy_files_and_prompts(self, displayed_items: List[OriginalItem], parent_widget=None) -> None:
        """
        Copy selected files and their prompts to a destination folder.

        Args:
            displayed_items: List of currently displayed OriginalItem objects
            parent_widget: Parent widget for displaying dialogs
        """
        self._copy_or_move_files(displayed_items, parent_widget, move=False)

    def move_files_and_prompts(self, displayed_items: List[OriginalItem], parent_widget=None) -> None:
        """
        Move selected files and their prompts to a destination folder.

        Args:
            displayed_items: List of currently displayed OriginalItem objects
            parent_widget: Parent widget for displaying dialogs
        """
        self._copy_or_move_files(displayed_items, parent_widget, move=True)

    def _copy_or_move_files(self, displayed_items: List[OriginalItem], parent_widget, move: bool) -> None:
        """
        Copy or move selected files and their prompts to a destination folder.

        Args:
            displayed_items: List of currently displayed OriginalItem objects
            parent_widget: Parent widget for displaying dialogs
            move: If True, move files; if False, copy files
        """
        selected_rows = self._get_sorted_selected_rows()

        if not selected_rows:
            self.status_bar.showMessage("No items selected", 2000)
            return

        # Ask user for destination folder
        dest_path = QFileDialog.getExistingDirectory(
            parent_widget,
            f"Select Destination Folder to {'Move' if move else 'Copy'} Files"
        )
        if not dest_path:
            return

        dest_folder = Path(dest_path)
        if not dest_folder.exists():
            QMessageBox.warning(
                parent_widget,
                "Invalid Destination",
                f"Destination folder does not exist:\n{dest_folder}"
            )
            return

        # Collect items to process
        items_to_process = []
        for row in selected_rows:
            if 0 <= row < len(displayed_items):
                items_to_process.append(displayed_items[row])

        if not items_to_process:
            self.status_bar.showMessage("No valid items to process", 2000)
            return

        # Process files
        success_count = 0
        error_count = 0
        errors = []

        for item in items_to_process:
            try:
                # Copy/move image file
                dest_image = dest_folder / item.image_path.name
                if move:
                    shutil.move(str(item.image_path), str(dest_image))
                else:
                    shutil.copy2(str(item.image_path), str(dest_image))

                # Copy/move prompt file if it exists
                if item.prompt_path and item.prompt_path.exists():
                    dest_prompt = dest_folder / item.prompt_path.name
                    if move:
                        shutil.move(str(item.prompt_path), str(dest_prompt))
                    else:
                        shutil.copy2(str(item.prompt_path), str(dest_prompt))

                success_count += 1
            except Exception as e:
                error_count += 1
                errors.append(f"{item.image_path.name}: {str(e)}")

        # Show results
        action = "Moved" if move else "Copied"
        message = f"{action} {success_count} file(s) and prompt(s)"
        if error_count > 0:
            message += f"\nFailed: {error_count} file(s)"
            if errors:
                message += f"\n\nErrors:\n" + "\n".join(errors[:5])
                if len(errors) > 5:
                    message += f"\n... and {len(errors) - 5} more error(s)"

        self.status_bar.showMessage(f"{action} {success_count} file(s)", 3000)

        if parent_widget:
            msg_type = QMessageBox.Information if error_count == 0 else QMessageBox.Warning
            QMessageBox(
                msg_type,
                f"{action} Complete" if error_count == 0 else f"{action} Completed with Errors",
                message,
                QMessageBox.Ok,
                parent_widget
            ).exec()
