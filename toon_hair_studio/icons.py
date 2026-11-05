# SPDX-License-Identifier: GPL-3.0-or-later
"""Icon names differ between Blender versions: fall back to no icon instead
of raising when a name does not exist in the running Blender."""

import bpy

_valid = None


def ic(name):
    global _valid
    if _valid is None:
        try:
            items = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items
            _valid = {i.identifier for i in items}
        except Exception:  # pragma: no cover
            _valid = set()
    return name if (not _valid or name in _valid) else 'NONE'
