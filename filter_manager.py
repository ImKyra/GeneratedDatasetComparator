"""Manages filtering and searching of dataset items."""

import shlex
import re
from typing import List, Tuple

from items import OriginalItem


class FilterManager:
    """Handles filtering and search/replace operations for dataset items."""

    def __init__(self):
        """Initialize the filter manager."""
        self.filter_text: str = ""
        self.exclude_text: str = ""

    def parse_filter_text(self, filter_text: str) -> Tuple[List[str], List[str], List[str]]:
        """
        Parse filter text into include words, exclude words, and exact phrases.

        Supports:
        - Regular words: included in search
        - -word: excluded from search
        - "exact phrase": must match exactly

        Args:
            filter_text: The filter text to parse

        Returns:
            Tuple of (include_words, exclude_words, phrases)
        """
        include_words = []
        exclude_words = []
        phrases = []

        # Extract quoted phrases first
        phrase_pattern = r'"([^"]+)"'
        for match in re.finditer(phrase_pattern, filter_text):
            phrases.append(match.group(1).lower())

        # Remove phrases from text to process remaining tokens
        remaining_text = re.sub(phrase_pattern, '', filter_text)

        # Tokenize remaining text
        try:
            tokens = shlex.split(remaining_text.lower())
        except ValueError:
            tokens = remaining_text.lower().split()

        # Classify tokens
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

    def should_include_item(
        self,
        item: OriginalItem,
        filter_text: str,
        exclude_text: str
    ) -> bool:
        """
        Determine if an item should be included based on filter criteria.

        Args:
            item: The OriginalItem to check
            filter_text: Positive filter text
            exclude_text: Negative filter text

        Returns:
            True if item should be included, False otherwise
        """
        prompt_lower = (item.prompt_text or "").lower()

        # Apply positive filters
        if filter_text:
            include_words, exclude_words, phrases = self.parse_filter_text(filter_text)

            # Check exclude words (from positive filter with -)
            if any(word in prompt_lower for word in exclude_words):
                return False

            # Check include words (all must be present)
            if include_words and not all(word in prompt_lower for word in include_words):
                return False

            # Check phrases (all must be present)
            if phrases and not all(phrase in prompt_lower for phrase in phrases):
                return False

        # Apply negative filters
        if exclude_text:
            exclude_include_words, _, exclude_phrases = self.parse_filter_text(exclude_text)

            # If any exclude word is found, exclude the item
            if exclude_include_words and any(word in prompt_lower for word in exclude_include_words):
                return False

            # If any exclude phrase is found, exclude the item
            if exclude_phrases and any(phrase in prompt_lower for phrase in exclude_phrases):
                return False

        return True

    def filter_items(
        self,
        items: List[OriginalItem],
        filter_text: str,
        exclude_text: str
    ) -> List[OriginalItem]:
        """
        Filter a list of items based on filter criteria.

        Args:
            items: List of OriginalItem objects to filter
            filter_text: Positive filter text
            exclude_text: Negative filter text

        Returns:
            Filtered list of items
        """
        if not filter_text and not exclude_text:
            return items

        return [
            item for item in items
            if self.should_include_item(item, filter_text, exclude_text)
        ]
