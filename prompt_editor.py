"""Manages prompt editing, saving, and undo operations."""

from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtWidgets import QMessageBox, QStatusBar

from items import OriginalItem
from prompt_manager import PromptManager


class PromptEditor:
    """Handles prompt editing operations including save and undo."""

    def __init__(self, prompt_manager: PromptManager, status_bar: QStatusBar):
        """
        Initialize the prompt editor.

        Args:
            prompt_manager: PromptManager instance for file operations
            status_bar: Status bar for displaying messages
        """
        self.prompt_manager = prompt_manager
        self.status_bar = status_bar

    def save_prompt(
        self,
        item: OriginalItem,
        new_text: str,
        parent_widget=None
    ) -> bool:
        """
        Save a prompt to file.

        Args:
            item: OriginalItem to save prompt for
            new_text: New prompt text to save
            parent_widget: Parent widget for error dialogs

        Returns:
            True if saved successfully, False otherwise
        """
        old_text = item.prompt_text

        if old_text == new_text:
            self.status_bar.showMessage("No changes to save", 2000)
            return False

        try:
            self.prompt_manager.save_prompt(item, new_text)
            self.status_bar.showMessage(f"Saved: {item.prompt_path}", 3000)
            return True
        except Exception as e:
            if parent_widget:
                QMessageBox.critical(
                    parent_widget,
                    "Save Error",
                    f"Failed to save prompt to {item.prompt_path}:\n{e}"
                )
            return False

    def save_prompt_internal(
        self,
        item: OriginalItem,
        new_text: str
    ) -> bool:
        """
        Save a prompt internally (without user feedback).

        Used for batch operations like replace all.

        Args:
            item: OriginalItem to save prompt for
            new_text: New prompt text to save

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            self.prompt_manager.save_prompt(item, new_text)
            return True
        except Exception as e:
            print(f"Failed to save prompt to {item.prompt_path}: {e}")
            return False

    def undo_last_change(
        self,
        original_items: List[OriginalItem],
        parent_widget=None
    ) -> Optional[Tuple[Path, str, OriginalItem]]:
        """
        Undo the last prompt change.

        Args:
            original_items: List of all original items
            parent_widget: Parent widget for error dialogs

        Returns:
            Tuple of (prompt_path, old_text, affected_item) if successful, None otherwise
        """
        if not self.prompt_manager.has_history():
            self.status_bar.showMessage("No changes to undo", 2000)
            return None

        result = self.prompt_manager.undo()
        if not result:
            return None

        prompt_path, old_text = result

        try:
            prompt_path.write_text(old_text, encoding="utf-8")

            # Find the affected item
            for orig_item in original_items:
                if orig_item.prompt_path == prompt_path:
                    self.status_bar.showMessage(f"Undone: {prompt_path.name}", 3000)
                    return prompt_path, old_text, orig_item

            return prompt_path, old_text, None

        except Exception as e:
            if parent_widget:
                QMessageBox.critical(
                    parent_widget,
                    "Undo Error",
                    f"Failed to undo changes to {prompt_path}:\n{e}"
                )
            return None

    def update_item_prompt(
        self,
        item: OriginalItem,
        new_text: str,
        all_items: List[OriginalItem],
        displayed_items: List[OriginalItem]
    ) -> OriginalItem:
        """
        Update an item's prompt text in all relevant lists.

        Args:
            item: The item to update
            new_text: New prompt text
            all_items: List of all original items
            displayed_items: List of currently displayed items

        Returns:
            Updated OriginalItem
        """
        updated_item = OriginalItem(
            image_path=item.image_path,
            prompt_path=item.prompt_path,
            prompt_text=new_text
        )

        # Update in all items list
        for idx, orig_item in enumerate(all_items):
            if orig_item.image_path == item.image_path:
                all_items[idx] = updated_item
                break

        # Update in displayed items list
        for idx, disp_item in enumerate(displayed_items):
            if disp_item.image_path == item.image_path:
                displayed_items[idx] = updated_item
                break

        return updated_item

    def perform_search_replace(
        self,
        item: OriginalItem,
        search_text: str,
        replace_text: str,
        case_sensitive: bool
    ) -> Tuple[str, int]:
        """
        Perform search and replace on a prompt.

        Args:
            item: OriginalItem to perform replace on
            search_text: Text to search for
            replace_text: Text to replace with
            case_sensitive: Whether search is case sensitive

        Returns:
            Tuple of (new_prompt_text, occurrence_count)
        """
        old_prompt = item.prompt_text or ""

        if case_sensitive:
            new_prompt = old_prompt.replace(search_text, replace_text)
            count = old_prompt.count(search_text)
        else:
            new_prompt = self.prompt_manager.case_insensitive_replace(
                old_prompt,
                search_text,
                replace_text
            )
            count = old_prompt.lower().count(search_text.lower())

        return new_prompt, count

    def add_to_history(self, prompt_path: Path, old_text: str) -> None:
        """
        Add a prompt change to the undo history.

        Args:
            prompt_path: Path to the prompt file
            old_text: Original text before the change
        """
        self.prompt_manager.add_to_history(prompt_path, old_text)
