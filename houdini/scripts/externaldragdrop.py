"""
External drag-and-drop hook for Houdini.
Place this file at:
  $HOUDINI_USER_PREF_DIR/scripts/externaldragdrop.py

When a USD file is dragged from the OS file browser (or the
Asset Manager gallery) and dropped onto the Network Editor,
this hook intercepts the event and creates a Reference LOP.
"""


def dropAccept(files):
    """
    Called by Houdini to determine if the dropped files are acceptable.
    Return True to accept USD files.
    """
    usd_extensions = (".usd", ".usda", ".usdc", ".usdz")
    for f in files:
        if f.lower().endswith(usd_extensions):
            return True
    return False


def dropPerform(files):
    """
    Called by Houdini when files are dropped and accepted.
    Creates Reference LOP nodes for each dropped USD file.
    """
    import os
    import hou

    usd_extensions = (".usd", ".usda", ".usdc", ".usdz")
    usd_files = [f for f in files if f.lower().endswith(usd_extensions)]

    if not usd_files:
        return False

    # Find the active network editor and its current context
    net_editor = None
    for pane in hou.ui.paneTabs():
        if (pane.type() == hou.paneTabType.NetworkEditor
                and pane.isCurrentTab()):
            net_editor = pane
            break

    if net_editor is None:
        # Fallback: any network editor
        for pane in hou.ui.paneTabs():
            if pane.type() == hou.paneTabType.NetworkEditor:
                net_editor = pane
                break

    if net_editor is None:
        hou.ui.displayMessage(
            "No Network Editor found. Cannot create nodes.",
            severity=hou.severityType.Warning
        )
        return False

    parent = net_editor.pwd()

    # Check if we're in a LOP context
    if parent.childTypeCategory() != hou.lopNodeTypeCategory():
        # Not in LOPs — try to find /stage
        stage = hou.node("/stage")
        if stage is not None:
            parent = stage
        else:
            hou.ui.displayMessage(
                "Please navigate to a LOP network (Solaris /stage) "
                "before dropping USD files.",
                severity=hou.severityType.Warning
            )
            return False

    created_nodes = []
    try:
        cursor_pos = net_editor.cursorPosition()
    except Exception:
        cursor_pos = hou.Vector2(0, 0)

    for i, usd_path in enumerate(usd_files):
        usd_path = usd_path.replace("\\", "/")
        asset_name = os.path.splitext(os.path.basename(usd_path))[0]

        try:
            node = parent.createNode("reference", asset_name)
            node.parm("filepath1").set(usd_path)
            node.parm("primpath1").set(f"/{asset_name}")

            # Offset each node vertically
            pos = hou.Vector2(cursor_pos[0], cursor_pos[1] - i * 1.5)
            node.setPosition(pos)

            if i == len(usd_files) - 1:
                # Last node gets display/render flags
                node.setDisplayFlag(True)
                node.setRenderFlag(True)

            created_nodes.append(node)

        except Exception as e:
            print(f"[AssetManager] Failed to create node for {usd_path}: {e}")

    if created_nodes:
        # Select all created nodes
        for n in created_nodes:
            n.setCurrent(True, clear_all_selected=(n == created_nodes[0]))

        # Wire them in sequence if multiple
        if len(created_nodes) > 1:
            for i in range(1, len(created_nodes)):
                try:
                    merge = parent.createNode("merge", "merge_assets")
                    for j, n in enumerate(created_nodes):
                        merge.setInput(j, n, 0)
                    merge.setPosition(
                        hou.Vector2(cursor_pos[0],
                                    cursor_pos[1] - len(created_nodes) * 1.5)
                    )
                    merge.setDisplayFlag(True)
                    merge.setRenderFlag(True)
                except Exception:
                    pass
                break

        parent.layoutChildren(created_nodes)

    return True
