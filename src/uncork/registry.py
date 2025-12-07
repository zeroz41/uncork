"""
Wine registry file processing.

Wine registry files use a custom format similar to .reg files but with
some differences (UTF-8 with special escapes, different header format).
"""

from __future__ import annotations

import re
from pathlib import Path


class RegistryProcessor:
    """
    Process Wine registry files for path tokenization.
    
    Wine registry files contain paths that need to be made portable:
    - C:\\users\\username\\... -> C:\\users\\__WINE_USER__\\...
    - Z:\\home\\username\\... -> Z:\\__USER_HOME__\\username\\...
    """

    def __init__(self, user_token: str = "__WINE_USER__", home_token: str = "__USER_HOME__"):
        self.user_token = user_token
        self.home_token = home_token

    def tokenize_file(self, reg_path: Path, original_user: str) -> None:
        """
        Tokenize paths in a registry file in-place.
        
        Args:
            reg_path: Path to .reg file
            original_user: Username to replace with token
        """
        try:
            content = reg_path.read_text(encoding="utf-8", errors="surrogateescape")
        except Exception:
            # Try latin-1 as fallback
            content = reg_path.read_text(encoding="latin-1")
        
        modified = self._tokenize_content(content, original_user)
        
        reg_path.write_text(modified, encoding="utf-8", errors="surrogateescape")

    def _tokenize_content(self, content: str, original_user: str) -> str:
        """Replace hardcoded paths with tokens."""
        
        # Escape special regex chars in username
        escaped_user = re.escape(original_user)
        
        # Windows-style paths in registry values (double backslash in .reg format)
        # C:\\users\\username -> C:\\users\\__WINE_USER__
        content = re.sub(
            rf'(C:\\\\users\\\\){escaped_user}\\b',
            rf'\1{self.user_token}',
            content,
            flags=re.IGNORECASE
        )
        
        # Also handle single backslash variants (some entries)
        content = re.sub(
            rf'(C:\\users\\){escaped_user}\\b',
            rf'\1{self.user_token}',
            content,
            flags=re.IGNORECASE
        )
        
        # Z: drive paths to home directory
        # Z:\\home\\username -> Z:\\__USER_HOME__
        content = re.sub(
            rf'(Z:\\\\home\\\\){escaped_user}\\b',
            rf'\1{self.user_token}',
            content,
            flags=re.IGNORECASE
        )
        
        content = re.sub(
            rf'(Z:\\home\\){escaped_user}\\b',
            rf'\1{self.user_token}',
            content,
            flags=re.IGNORECASE
        )
        
        # Unix-style paths that might appear (less common but possible)
        # /home/username -> __USER_HOME__
        content = re.sub(
            rf'/home/{escaped_user}\\b',
            f'/home/{self.user_token}',
            content
        )
        
        return content

    def detokenize_content(self, content: str, actual_user: str, actual_home: str) -> str:
        """Replace tokens with actual values (for install-time)."""
        
        content = content.replace(self.user_token, actual_user)
        content = content.replace(self.home_token, actual_home)
        
        return content


class RegistryParser:
    """
    Parse Wine registry files to extract information.
    
    Wine registry files have this format:
    
        WINE REGISTRY Version 2
        ;; All keys relative to \\\\Machine or \\\\User\\\\<sid>
        
        [key\\path] timestamp
        "value_name"="string_value"
        "dword_value"=dword:00000001
        
    Keys are in square brackets, values follow.
    """

    def __init__(self, reg_path: Path):
        self.reg_path = reg_path
        self._content: str | None = None

    @property
    def content(self) -> str:
        if self._content is None:
            try:
                self._content = self.reg_path.read_text(encoding="utf-8", errors="surrogateescape")
            except Exception:
                self._content = self.reg_path.read_text(encoding="latin-1")
        return self._content

    def get_value(self, key_path: str, value_name: str) -> str | None:
        """
        Get a string value from the registry.
        
        Args:
            key_path: Registry key path (e.g., "Software\\\\Wine\\\\DllOverrides")
            value_name: Value name to retrieve
            
        Returns:
            Value string or None if not found
        """
        # Find the key section
        escaped_key = re.escape(key_path)
        key_pattern = rf'\[{escaped_key}\][^\[]*'
        
        match = re.search(key_pattern, self.content, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        
        key_section = match.group(0)
        
        # Find the value in this section
        escaped_name = re.escape(value_name)
        value_pattern = rf'"{escaped_name}"="([^"]*)"'
        
        value_match = re.search(value_pattern, key_section, re.IGNORECASE)
        if value_match:
            return value_match.group(1)
        
        return None

    def get_dll_overrides(self) -> dict[str, str]:
        """Get DLL override settings."""
        overrides = {}
        
        # Find DllOverrides key
        pattern = r'\[Software\\\\Wine\\\\DllOverrides\][^\[]*'
        match = re.search(pattern, self.content, re.IGNORECASE | re.DOTALL)
        
        if not match:
            return overrides
        
        section = match.group(0)
        
        # Parse all values
        value_pattern = r'"([^"]+)"="([^"]*)"'
        for m in re.finditer(value_pattern, section):
            dll_name = m.group(1)
            override_type = m.group(2)
            
            # Clean up dll name (remove leading * if present)
            if dll_name.startswith("*"):
                dll_name = dll_name[1:]
            
            overrides[dll_name] = override_type
        
        return overrides


def get_dll_overrides(prefix_path: Path) -> dict[str, str]:
    """Convenience function to get DLL overrides from a prefix."""
    user_reg = prefix_path / "user.reg"
    if not user_reg.exists():
        return {}
    
    parser = RegistryParser(user_reg)
    return parser.get_dll_overrides()
