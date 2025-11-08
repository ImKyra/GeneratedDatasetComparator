from pathlib import Path
from typing import List, Optional, Tuple

from items import OriginalItem


class PromptManager:
    def __init__(self, max_history_size: int = 50):
        self.history: List[Tuple[Path, str]] = []
        self.max_history_size = max_history_size

    def add_to_history(self, prompt_path: Path, old_text: str) -> None:
        self.history.append((prompt_path, old_text))
        if len(self.history) > self.max_history_size:
            self.history.pop(0)

    def undo(self) -> Optional[Tuple[Path, str]]:
        if not self.history:
            return None
        return self.history.pop()

    def has_history(self) -> bool:
        return bool(self.history)

    @staticmethod
    def save_prompt(item: OriginalItem, new_text: str) -> None:
        item.prompt_path.parent.mkdir(parents=True, exist_ok=True)
        item.prompt_path.write_text(new_text, encoding="utf-8")

    @staticmethod
    def case_insensitive_replace(text: str, search: str, replace: str) -> str:
        if not search:
            return text

        result = []
        text_lower = text.lower()
        search_lower = search.lower()
        start = 0

        while True:
            pos = text_lower.find(search_lower, start)
            if pos == -1:
                result.append(text[start:])
                break

            result.append(text[start:pos])
            result.append(replace)
            start = pos + len(search)

        return ''.join(result)
