# SPDX-License-Identifier: GPL-3.0-or-later

bl_info = {
    "name": "Precision Shrinkwrap",
    "author": "lionjazz15",
    "version": (1, 0, 0),
    "blender": (4, 1, 0),
    "location": "View3D > Sidebar > PrecisionSW",
    "description": "High precision shrinkwrap for tight clothing: even offset in valleys, "
                   "no stretching, exact topology transfer (subdivision aware)",
    "category": "Mesh",
}

if "bpy" in locals():
    import importlib
    for _m in (solver, mesh_utils, fitting, rough_cage, properties, operators, ui, translations):  # noqa: F821
        importlib.reload(_m)

import bpy

from . import solver, mesh_utils, fitting, rough_cage, properties, operators, ui, translations

_classes = (properties.PSW_Settings, *operators.classes, *ui.classes)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.precision_shrinkwrap = bpy.props.PointerProperty(type=properties.PSW_Settings)
    bpy.app.translations.register(__package__, translations.translations_dict)


def unregister():
    bpy.app.translations.unregister(__package__)
    del bpy.types.Scene.precision_shrinkwrap
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
