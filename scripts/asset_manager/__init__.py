"""
Houdini Solaris LOP Asset Manager
=================================
A complete USD pipeline tool for Houdini Solaris that:
- Scans directories for untextured 3D models
- Auto-assigns MaterialX materials & textures based on naming conventions
- Populates a Gallery Manager with rendered thumbnails
- Enables drag-and-drop asset placement into Solaris scenes
- Uses PDG (TOPs) for recursive batch processing
- Generates proxy & simulation files for each asset
- Supports Karma, Arnold, and Redshift (all via MaterialX)
"""

__version__ = "1.0.0"
__author__ = "Asset Manager"

import os
import sys

# Ensure subpackages are importable
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, os.path.dirname(PACKAGE_DIR))
