"""
import_gate.py — Lazy Import Gate for Watermark Modules
========================================================
Provides lazy loading mechanism for heavy dependencies to avoid circular imports
and improve startup performance.
"""

import sys
import importlib

class ImportGate:
    """Lazy import gate for watermark module dependencies."""
    
    _cache = {}
    
    @classmethod
    def get(cls, module_name: str):
        """
        Get a module by name, loading it lazily if not already cached.
        
        Args:
            module_name: Name of the module to import (e.g., "gemini_enhance")
            
        Returns:
            The imported module or None if import fails
        """
        if module_name in cls._cache:
            return cls._cache[module_name]
        
        try:
            module = importlib.import_module(module_name)
            cls._cache[module_name] = module
            return module
        except ImportError as e:
            # Log but don't fail - allow graceful degradation
            print(f"⚠️ ImportGate: Failed to import {module_name}: {e}")
            return None
        except Exception as e:
            print(f"⚠️ ImportGate: Error importing {module_name}: {e}")
            return None
    
    @classmethod
    def reset(cls):
        """Clear the import cache."""
        cls._cache.clear()
