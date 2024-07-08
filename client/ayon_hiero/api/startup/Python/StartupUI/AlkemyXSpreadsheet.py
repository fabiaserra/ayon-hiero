import bisect
import glob
import nuke
import os
import pathlib
import PyOpenColorIO

from qtpy.QtWidgets import *
from qtpy.QtCore import *
from qtpy.QtGui import *

import hiero

import ayon_api

from ayon_core.lib import Logger
from ayon_core.pipeline import get_current_project_name
from ayon_core.pipeline.context_tools import (
    get_current_project_name,
    get_hierarchy_env,
)
from ayon_shotgrid.lib import credentials

from ayon_hiero.api.lib import MainPlate
from ayon_hiero.api.constants import OPENPYPE_TAG_NAME


def tile_to_qrgb(tile):
    return qRgb(0xFF & tile >> 24, 0xFF & tile >> 16, 0xFF & tile >> 8)

SHOTGRID = credentials.get_shotgrid_session()

FORMATS = {
    fmt.name(): {
        "width": fmt.width(),
        "height": fmt.height(),
        "format": fmt.toString(),
        "pixelAspect": fmt.pixelAspect(),
    }
    for fmt in hiero.core.formats()
}

TAG_DATA_KEY_CONVERT = {
    OPENPYPE_TAG_NAME: {
        "tag.workfileFrameStart": "frame_start",
        "tag.handleStart": "handle_start",
        "tag.handleEnd": "handle_end",
    }
}

SG_TAG_ICONS = {
    "screen insert": "icons:SyncPush.png",
    "re-time": "icons:TagKronos.png",
    "repo": "icons:ExitFullScreen.png",
    "split screen": "icons:TimelineToolSlide.png",
    "flip/flop": "icons:TimelineToolSlide.png",
    "insert element": "icons:SyncPush.png",
}

INGEST_EFFECTS = ["flip", "flop"]
SG_TAGS = [
    "screen insert",
    "re-time",
    "repo",
    "split screen",
    "flip/flop",
    "insert element"
]

HIERO_PREFERENCES = nuke.toNode("preferences")
HIGHLIGHT_COLOR = QColor(
    tile_to_qrgb(HIERO_PREFERENCES["UIHighlightColor"].value())
)
MEDIA_OFFLINE_COLOR = QColor(
    tile_to_qrgb(HIERO_PREFERENCES["projectItemOfflineColor"].value())
)
TEXT_HIGHLIGHTED_COLOR = QColor(
    tile_to_qrgb(HIERO_PREFERENCES["UIHighlightedTextColor"].value())
)
TEXT_COLOR = QColor(
    tile_to_qrgb(HIERO_PREFERENCES["UILabelColor"].value())
)

EVEN_COLUMN_COLOR = QColor(61, 61, 66)
ODD_COLUMN_COLOR = QColor(53, 53, 57)

NO_OP_TRANSLATE = {43: None, 45: None, 42: None, 47: None}

log = Logger.get_logger(__name__)


def get_active_ocio_config():
    """Get the active OCIO configuration.

    This function retrieves the OCIO configuration from the environment
    variable 'OCIO' if available. Otherwise, it checks the current Hiero
    session's project settings for the OCIO configuration. If no active
    sequence is loaded, a default OCIO configuration is used.

    Returns:
        PyOpenColorIO.Config: The active OCIO configuration.
    """
    env_ocio_path = os.getenv("OCIO")

    if env_ocio_path:
        # Returning now. No need to search other places for config
        return PyOpenColorIO.Config.CreateFromFile(env_ocio_path)

    # If not OCIO found in environ then check project OCIO
    active_seq = hiero.ui.activeSequence()
    configs_path = __file__.split("plugins")[0] + "plugins/OCIOConfigs/configs"
    if active_seq:
        project = active_seq.project()
        if project.ocioConfigPath():
            ocio_path = project.ocioConfigPath()
        # Use default config path from sw
        elif project.ocioConfigName():
            hiero_configs = glob.glob(
                configs_path + "/**/*.ocio", recursive=True
            )
            for config in hiero_configs:
                config_name = pathlib.Path(config).parent.name
                if project.ocioConfigName() == config_name:
                    ocio_path = config
                    break

    # Else statement is a catch for when the spreadsheet runs without sequence
    # loaded
    else:
        ocio_path = os.path.join(configs_path, "nuke-default/config.ocio")

    ocio_config = PyOpenColorIO.Config.CreateFromFile(ocio_path)

    return ocio_config


class CheckboxMenu(QMenu):
    mouse_in_view = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def accept(self, event):
        if self.can_close:
            event.accept()
        else:
            event.ignore()

    def enterEvent(self, event):
        self.mouse_in_view = True

    def leaveEvent(self, event):
        self.mouse_in_view = False

    def keyPressEvent(self, event):
        if event.key() in [Qt.Key_Return, Qt.Key_Enter]:
            self.can_close = True
            self.close()

    def mousePressEvent(self, event):
        if not self.mouse_in_view:
            self.can_close = True
            self.close()

        if event.button() == Qt.LeftButton:
            action = self.activeAction()
            if action:
                if action.isChecked():
                    action.setChecked(False)
                else:
                    action.setChecked(True)

                # Suppress the event to prevent the menu from closing
                event.accept()
                return

        super().mouseReleaseEvent(event)


class ColorspaceWidget(QMainWindow):
    def __init__(self, ocio_config, parent=None):
        super().__init__(parent)

        # Change how roles are added - add them to the base menu using the
        # getRoles method
        self.colorspace_button = QPushButton("Colorspaces")
        # Menu must be stored on self. Button won't react properly without
        self.root_menu = QMenu("Main")

        menu_dict = {}
        color_roles = [f"{x[0]} ({x[1]})" for x in ocio_config.getRoles()]
        color_spaces = []
        for color_space in ocio_config.getColorSpaces():
            color_spaces.append(
                (color_space.getName(), color_space.getFamily())
            )

        for role in color_roles:
            role_action = QAction(role, self.root_menu)
            self.root_menu.addAction(role_action)

        # Create menu_dict which stores the hierarchy and associated colorspace
        for name, family in color_spaces:
            parts = family.split("/")
            current_dict = menu_dict
            for part in parts:
                current_dict = current_dict.setdefault(part, {})
            current_dict[name] = None

        self.colorspace_menu = QMenu("Colorspaces")
        self.root_menu.addMenu(self.colorspace_menu)
        for key, value in menu_dict.items():
            submenu = self.build_menu(value, key)
            self.colorspace_menu.addMenu(submenu)

        self.colorspace_button.setMenu(self.root_menu)
        self.setCentralWidget(self.colorspace_button)

    def menu_insertion_target(self, actions, menu_text):
        """Determine the insertion point for a menu or action within a list of
        actions.

        Args:
            actions (list): List of actions, where each item is a tuple
                            containing an action and a boolean indicating
                            whether it's a menu.
            menu_text (str): The text of the menu to insert.

        Returns:
            tuple: A tuple containing the action to insert before and its
                            index.
        """
        menu_actions = []
        normal_actions = []

        for action, is_menu in actions:
            if is_menu:
                menu_actions.append((action, is_menu))
            else:
                normal_actions.append((action, is_menu))

        if menu_actions:
            # Sort menus alphabetically
            index = bisect.bisect_left(
                [x[0].text() for x in menu_actions], menu_text
            )
            if index == len(menu_actions):
                if normal_actions:
                    action_index = actions.index(normal_actions[0])

                    return (normal_actions[0][0], action_index)
                else:
                    return (None, None)

            action_index = actions.index(menu_actions[index])

            return (menu_actions[index][0], action_index)

        elif normal_actions:
            # Otherwise place before first action
            return (normal_actions[0][0], 0)
        else:
            return (None, None)

    def action_insert_target(self, actions, action_text):
        """Determine the insertion point for an action within a list of
        actions.

        Args:
            actions (list): List of actions, where each item is a tuple
                            containing an action and a boolean indicating
                            whether it's a menu.

            action_text (str): The text of the action to insert.

        Returns:
            tuple: A tuple containing the action to insert before and its
                            index.
        """
        normal_actions = []
        for action, is_menu in actions:
            if not is_menu:
                normal_actions.append((action, is_menu))

        if normal_actions:
            # Sort actions alphabetically
            index = bisect.bisect_left(
                [x[0].text() for x in normal_actions], action_text
            )
            if index == len(normal_actions):
                return (None, None)
            else:
                action_index = actions.index(normal_actions[index])

                return (normal_actions[index][0], action_index)

        else:
            return (None, None)

    def build_menu(self, menu_data, family_name):
        """Build a hierarchical menu from the given menu data.

        Args:
            menu_data (dict): The hierarchical menu data.
            family_name (str): The name of the menu.

        Returns:
            QMenu: The constructed menu.
        """
        menu = QMenu(family_name)
        # Can't rely on widget children since the menu is built recursively
        prev_items = []
        for key, value in menu_data.items():
            if value is None:
                action = QAction(key, menu)
                target_action, insert_index = self.action_insert_target(
                    prev_items, key
                )
                if target_action:
                    menu.insertAction(target_action, action)
                    prev_items.insert(insert_index, (action, False))
                else:
                    menu.addAction(action)
                    prev_items.append((action, False))
            else:
                # Since value is not None then this is a submenu
                # Need to place submenu at beginning of current submenu
                submenu = self.build_menu(value, key)
                target_submenu, insert_index = self.menu_insertion_target(
                    prev_items, key
                )
                if target_submenu:
                    menu.insertMenu(target_submenu, submenu)
                    prev_items.insert(
                        insert_index, (submenu.menuAction(), True)
                    )
                else:
                    menu.addMenu(submenu)
                    prev_items.append((submenu.menuAction(), True))

        return menu


class IngestResWidget(QComboBox):
    def __init__(self, item, current_format):
        super().__init__()

        default_working_resolution = self.get_default_working_resolution(item.name())
        if default_working_resolution:
            default_format_width, default_format_height = \
                default_working_resolution
        elif "x" in current_format:
            default_format_width, default_format_height = current_format.split(
                "x"
            )
        else:
            default_format = item.source().format()
            default_format_width = default_format.width()
            default_format_height = default_format.height()

        self.setEditable(True)
        validator = QRegExpValidator(r"^\d+[x]\d+$", self.lineEdit())
        self.setValidator(validator)
        self.lineEdit().setText("--")

        # Use base settings from current combobox defaults
        completer = self.completer()
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setFilterMode(Qt.MatchContains)
        self.addItem("--")

        # Move current resolution to the top
        proper_res = ""
        for res in sorted(FORMATS, key=lambda x: FORMATS[x]["width"]):
            width = FORMATS[res]["width"]
            height = FORMATS[res]["height"]
            if (
                width == default_format_width
                and height == default_format_height
            ):
                proper_res = res
            else:
                self.addItem("{0}x{1} - {2}".format(width, height, res))

        # Move current resolution to the top
        if proper_res:
            width = FORMATS[proper_res]["width"]
            height = FORMATS[proper_res]["height"]
            default_format_string = f"{width}x{height} - {proper_res}"
        else:
            default_format_string = (
                f"{default_format_width}x{default_format_height}"
            )

        self.insertItem(0, default_format_string)

        # Will need to add current format if found as tag on clip
        if not current_format:
            self.setCurrentIndex(1)
        else:
            self.setCurrentIndex(0)

        # Select all for easy editing
        self.lineEdit().selectAll()


    def get_default_working_resolution(self, asset_name):
        """Set resolution to project resolution."""
        # If Asset has working resolution pull from asset
        # If not pull from Project default working res
        project_name = get_current_project_name()
        folder_entity = ayon_api.get_folder_by_name(project_name, asset_name)

        if folder_entity:
            asset_data = folder_entity["data"]
            width = asset_data.get("resolutionWidth", "")
            height = asset_data.get("resolutionHeight", "")
            if width and height:

                return (width, height)

        else:
            filters = [
                [
                    "name",
                    "is",
                    project_name,
                ],
            ]
            fields = [
                "sg_project_resolution",
                "sg_resolution_width",
                "sg_resolution_height",
            ]
            sg_project = SHOTGRID.find_one("Project", filters, fields)
            if not sg_project:
                return None

            show_resolution = sg_project["sg_project_resolution"]
            if "x" in show_resolution:
                width, height = show_resolution.split("x")

                return (width, height)

        return None


class IngestEffectsWidget(QMainWindow):
    can_close = False
    effects_data = {}
    effect_actions = {}

    def __init__(self, tag_state):
        super().__init__()

        self.effects_button = QPushButton("Effects")

        # Menu must be stored on self. Button won't react properly without
        self.root_menu = CheckboxMenu("Main")

        # set default state to effect data that exists
        for effect_type in INGEST_EFFECTS:
            effect_action = QAction(effect_type)
            effect_action.setObjectName(effect_type)
            effect_type_state = (
                True
                if tag_state.get(effect_type, "False") == "True"
                else False
            )
            effect_action.setCheckable(True)
            effect_action.setChecked(effect_type_state)

            self.effect_actions[effect_type] = effect_action
            self.root_menu.addAction(effect_action)

        self.effects_button.setMenu(self.root_menu)
        self.setCentralWidget(self.effects_button)

    def set_effects_data(self):
        for key, widget in self.effect_actions.items():
            self.effects_data[key] = widget.isChecked()


class CurrentGradeDialog(QDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def leaveEvent(self, event):
        self.close()


# QT widget type doesn't matter. Only used for the show event
class CurrentGradeWidget(QLabel):
    def __init__(self, text):
        super().__init__()
        self.ingest_grade = text

    def showEvent(self, event):
        # On show pop out separate dialog widget
        dialog = CurrentGradeDialog()

        line_edit = QLineEdit()
        line_edit.setReadOnly(True)
        line_edit.setText(self.ingest_grade)
        line_edit.editingFinished.connect(dialog.close)
        line_edit.returnPressed.connect(dialog.close)
        line_edit.setFrame(False)

        layout_widget = QVBoxLayout()
        layout_widget.addWidget(line_edit)
        dialog.setLayout(layout_widget)
        dialog.move(self.mapToGlobal(self.rect().topLeft()))
        dialog.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)

        metrics = line_edit.fontMetrics()
        margin = line_edit.textMargins()
        content = line_edit.contentsMargins()
        width = (
            metrics.width(self.ingest_grade)
            + margin.left()
            + margin.right()
            + content.left()
            + content.right()
        )
        # 32 is the dialog window margin
        dialog.setFixedWidth(width + 32)
        dialog.exec()


class SGTagsWidget(QMainWindow):
    can_close = False
    tag_data = {}
    tag_actions = {}

    def __init__(self, tag_state):
        super().__init__()

        self.sg_tags_button = QPushButton("SG Tags")

        # Menu must be stored on self. Button won't react properly without
        self.root_menu = CheckboxMenu("Main")

        # set default state to tag data that exists
        for tag_type in SG_TAGS:
            tag_action = QAction(tag_type)
            tag_action.setObjectName(tag_type)
            tag_type_state = (
                True if tag_state.get(tag_type, "False") == "True" else False
            )
            tag_action.setCheckable(True)
            tag_action.setChecked(tag_type_state)

            self.tag_actions[tag_type] = tag_action
            self.root_menu.addAction(tag_action)

        self.sg_tags_button.setMenu(self.root_menu)
        self.setCentralWidget(self.sg_tags_button)

    def set_tag_data(self):
        for key, widget in self.tag_actions.items():
            self.tag_data[key] = widget.isChecked()


def get_track_item_env(track_item):
    """
    Get the asset environment from an asset stored in the Avalon database.

    Args:
        track_item (str): Track item.

    Returns:
        dict: The asset environment if found, otherwise an empty dictionary.
    """
    if "hierarchy_env" in track_item.__dir__():
        return track_item.hierarchy_env

    project_name = get_current_project_name()
    project_entity = ayon_api.get_project(project_name)
    folder_entity = ayon_api.get_folder_by_name(project_name, track_item.name())
    if not folder_entity:
        return {}

    hierarchy_env = get_hierarchy_env(project_entity, folder_entity)

    return hierarchy_env


# The Custom Spreadsheet Columns
class CustomSpreadsheetColumns(QObject):
    """A class defining custom columns for Hiero's spreadsheet view. This has a
    similar, but slightly simplified, interface to the QAbstractItemModel and
    QItemDelegate classes.
    """

    # Decorator function for widget callbacks
    def column_widget_callback(callback):
        def wrapper(self, *args, **kwargs):
            view = hiero.ui.activeView()
            selection = [
                item
                for item in view.selection()
                if isinstance(item.parent(), hiero.core.VideoTrack)
            ]
            project = selection[0].project()

            result = callback(self, selection, project, *args, **kwargs)

            sequence = hiero.ui.activeSequence()
            # There may not be an active sequence
            if sequence:
                # Force sequence update
                sequence.editFinished()

            return result

        return wrapper

    currentView = hiero.ui.activeView()

    # This is the list of Columns that will be added
    column_list = [
        {"name": "FileType", "cellType": "readonly"},
        {"name": "Tags", "cellType": "readonly"},
        {"name": "Colorspace", "cellType": "custom"},
        {"name": "WidthxHeight", "cellType": "readonly"},
        {"name": "Pixel Aspect", "cellType": "readonly"},
        {"name": "ingest_res", "cellType": "custom", "size": QSize(100, 25)},
        {"name": "resize_type", "cellType": "custom"},
        {"name": "ingest_effects", "cellType": "custom"},
        {"name": "cur_version", "cellType": "readonly"},
        {"name": "cur_grade", "cellType": "custom"},
        {"name": "sg_tags", "cellType": "custom"},
        {"name": "edit_note", "cellType": "custom"},
        {"name": "head_handles", "cellType": "text"},
        {"name": "cut_in", "cellType": "text"},
        {"name": "cut_out", "cellType": "readonly"},
        {"name": "tail_handles", "cellType": "text"},
        {"name": "cut_range", "cellType": "text"},
        {
            "name": "valid_entity",
            "cellType": "readonly",
        },
        {"name": "main_plate", "cellType": "custom"},
        {"name": "family", "cellType": "dropdown", "size": QSize(10, 25)},
        {"name": "use_nuke", "cellType": "custom"}, # -> use_nuke
        # If use nuke is True then turn ingest template from read only
        # {"name": "ingest_template", "cellType": "custom"}, -> ingest_template
    ]

    def numColumns(self):
        """Return the number of custom columns in the spreadsheet view"""

        return len(self.column_list)

    def columnName(self, column):
        """Return the name of a custom column"""

        return self.column_list[column]["name"]

    def get_tags_string(self, item):
        """Convenience method for returning all the Notes in a Tag as a
        string
        """
        tag_names = []
        tags = item.tags()
        for tag in tags:
            tag_names += [tag.name()]
        tag_name_string = ",".join(tag_names)

        return tag_name_string

    def get_notes(self, item):
        """Convenience method for returning all the Notes in a Tag as a
        string
        """
        notes = []
        for tag in item.tags():
            # Skip OpenPype notes
            if "openpypeData" in tag.name():
                continue
            note = tag.note()
            if note:
                notes.append(note)

        return ", ".join(notes)

    def getData(self, row, column, item):
        """Return the data in a cell"""
        current_column = self.column_list[column]
        current_column_name = current_column["name"]
        if current_column_name == "Tags":
            return self.get_tags_string(item)

        elif current_column_name == "Colorspace":
            # RuntimeError: Clip must be added to a project before accessing color transforms
            try:
                colorspace = item.sourceMediaColourTransform()
            except RuntimeError:
                colorspace = ""

            return colorspace

        elif current_column_name == "Notes":
            return self.get_notes(item)

        elif current_column_name == "FileType":
            fileType = "--"
            item_metadata = item.source().mediaSource().metadata()
            if item_metadata.hasKey("foundry.source.type"):
                fileType = item_metadata.value("foundry.source.type")
            elif item_metadata.hasKey("media.input.filereader"):
                fileType = item_metadata.value("media.input.filereader")
            return fileType

        elif current_column_name == "WidthxHeight":
            width = str(item.source().format().width())
            height = str(item.source().format().height())
            return f"{width}x{height}"

        elif current_column_name == "Pixel Aspect":
            return str(item.source().format().pixelAspect())

        elif current_column_name == "Artist":
            if item.artist():
                name = item.artist()["artistName"]
                return name
            else:
                return "--"

        elif current_column_name == "Department":
            if item.artist():
                dep = item.artist()["artistDepartment"]
                return dep
            else:
                return "--"

        elif current_column_name == "cur_version":
            instance_data = item.ingest_instance_data()
            if not instance_data:
                return "--"

            project_name = get_current_project_name()
            # Asset is track item name
            folder_name = item.name()
            folder_entity = ayon_api.get_folder_by_name(
                project_name, folder_name
            )
            if not folder_entity:
                return "--"

            # Subset is track name
            product_name = item.parentTrack().name()
            last_version_doc = ayon_api.get_last_version_by_product_name(
                project_name, product_name, folder_entity["id"]
            )

            if last_version_doc:
                last_version = last_version_doc["name"]
                return str(last_version)
            else:
                return "--"

        elif current_column_name == "ingest_res":
            return item.ingest_res_data().get("resolution", "--")

        elif current_column_name == "resize_type":
            return item.ingest_res_data().get("resize", "--")

        elif current_column_name == "edit_note":
            return item.edit_note_data().get("note", "--")

        elif current_column_name == "ingest_effects":
            effects_data = item.ingest_effects_data()

            effects = []
            for key in sorted(effects_data, key=INGEST_EFFECTS.index):
                if effects_data[key] == "True":
                    effects.append(key)

            if effects:
                return ", ".join(effects)
            else:
                return "--"

        elif current_column_name in [
            "cut_in",
        ]:
            if not isinstance(item.parent(), hiero.core.VideoTrack):
                return "--"

            tag_key = current_column_name
            current_tag_text = item.cut_info_data().get(tag_key, "--")

            return current_tag_text


        elif current_column_name == "head_handles":
            cut_info_data = item.cut_info_data()
            if not cut_info_data:
                return "--"

            cut_range = cut_info_data.get("cut_range")
            if cut_range == "False":
                return str(item.handleInLength())
            else:
                return cut_info_data.get("head_handles", "--")

        elif current_column_name == "tail_handles":
            cut_info_data = item.cut_info_data()
            if not cut_info_data:
                return "--"

            cut_range = cut_info_data.get("cut_range")
            if cut_range == "False":
                return str(item.handleOutLength())
            else:
                return cut_info_data.get("tail_handles", "--")

        elif current_column_name == "cut_out":
            cut_in = item.cut_info_data().get("cut_in")
            if str(cut_in) == "None":
                return "--"
            else:
                cut_out = str(int(cut_in) + item.duration() - 1)

                return cut_out

        elif current_column_name == "cut_range":
            if not isinstance(item.parent(), hiero.core.VideoTrack):
                return "--"

            tag_key = current_column_name
            cut_info_data = item.cut_info_data()
            current_tag_text = cut_info_data.get("cut_range", "--")

            return current_tag_text

        elif current_column_name == "main_plate":
            if not isinstance(item.parent(), hiero.core.VideoTrack):
                return "--"

            return "True" if item.get_main_plate() else "False"

        elif current_column_name in [
                "family",
                "use_nuke",
                # "template"
            ]:
            if not isinstance(item.parent(), hiero.core.VideoTrack):
                return "--"

            instance_key = current_column_name
            current_tag_text = item.ingest_instance_data().get(
                f"{instance_key}", "--"
            )

            return current_tag_text

        return ""

    def setData(self, row, column, item, data):
        """Set the data in a cell - unused in this example"""

        return None

    def getTooltip(self, row, column, item):
        """Return the tooltip for a cell"""
        current_column = self.column_list[column]

        if current_column["name"] == "Tags":
            return str([item.name() for item in item.tags()])

        elif current_column["name"] == "ingest_res":
            return (
                "When provided overrides the default resolution value from "
                "both Plate resolution and Shotgrid ingest resolution.\n\n"
                "Text input format:\n{width}x{height}\ni.e. 1920x1080"
            )

        elif current_column["name"] == "resize_type":
            return (
                "Nuke like resize types that is used for determining how to "
                "perform the reformating action when aspect ratio differs"
            )

        elif current_column["name"] == "ingest_effects":
            return "Effects to apply to track item on ingest"

        elif current_column["name"] == "cur_version":
            return "Current ingested items latest current published version"

        elif current_column["name"] == "cur_grade":
            return (
                "After ingesting media the grade (if one was used) will show "
                "up here.\nDouble click to see full path"
            )

        elif current_column["name"] == "sg_tags":
            return (
                "Shot tags that you'd like applied to the items Shotgrid Shot"
            )

        elif current_column["name"] == "edit_note":
            return (
                "Editorial Note that gets applied to the items Shotgrid Shot\n"
                "If this note was already made it be created again"
            )

        elif current_column["name"] == "cut_in":
            return (
                "Shot 'cut in' frame. This is meant to be ground truth and can"
                " be used to sync to SG.\n\n Operators are supported "
                "i.e:\n'+20' -> 1001+20=1021\n'-10' -> 1010-10=1000\n'*2' -> "
                "8*2=16\n'/2' -> 16/2=8\n\nWhen written in 1001-10 form the "
                "expression will evaluate first. For multi-select updates that"
                " may yield unintended results"
            )

        elif current_column["name"] == "cut_out":
            return (
                "Shot 'cut out' frame. A calculated field: Cut In + Duration - 1"
            )

        elif current_column["name"] == "head_handles":
            return (
                "Shot 'head handle' duration. This is meant to be ground truth"
                " and can be used to sync to SG.\n\n Operators are supported "
                "i.e:\n'+20' -> 1001+20=1021\n'-10' -> 1010-10=1000\n'*2' -> "
                "8*2=16\n'/2' -> 16/2=8\n\nWhen written in 1001-10 form the "
                "expression will evaluate first. For multi-select updates that"
                " may yield unintended results"
            )

        elif current_column["name"] == "tail_handles":
            return (
                "Shot 'tail handle' duration. This is meant to be ground truth"
                " and can be used to sync to SG.\n\n Operators are supported "
                "i.e:\n'+20' -> 1001+20=1021\n'-10' -> 1010-10=1000\n'*2' -> "
                "8*2=16\n'/2' -> 16/2=8\n\nWhen written in 1001-10 form the "
                "expression will evaluate first. For multi-select updates that"
                " may yield unintended results"
            )

        elif current_column["name"] == "valid_entity":
            return (
                "Whether this track items name is found as a valid"
                " entity in Avalon DB."
            )

        elif current_column["name"] == "family":
            return "Ingest family."

        elif current_column["name"] == "use_nuke":
            return (
                "Ingest can use two different methods depending on media type "
                "Nuke or OIIO. If you need to force a Nuke ingest toggle "
                "use_nuke to True"
            )

        return ""

    def getFont(self, row, column, item):
        """Return the font for a cell"""

        return None

    def getBackground(self, row, column, item):
        """Return the background color for a cell"""
        if not item.source().mediaSource().isMediaPresent():
            return MEDIA_OFFLINE_COLOR

        if self.column_list[column]["name"] in [
                "valid_entity",
                "family",
                "main_plate",
                "use_nuke",
            ]:
            if row % 2 == 0:
                # For reference default even row is 61, 61, 61
                return EVEN_COLUMN_COLOR
            else:
                # For reference default odd row is 53, 53, 53
                return ODD_COLUMN_COLOR

        return None

    def getForeground(self, row, column, item):
        """Return the text color for a cell"""

        return None

    def getIcon(self, row, column, item):
        """Return the icon for a cell"""
        current_column = self.column_list[column]
        if current_column["name"] == "Colorspace":
            return QIcon("icons:LUT.png")

        elif current_column["name"] == "valid_entity":
            project_name = get_current_project_name()
            try:
                folder_entity = ayon_api.get_folder_by_name(project_name, item.name())
            except ayon_api.exceptions.GraphQlQueryFailed:
                icon_name = "icons:status/TagOmitted.png"
                
            if folder_entity:
                icon_name = "icons:status/TagFinal.png"
            else:
                icon_name = "icons:status/TagOmitted.png"

            return QIcon(icon_name)

        return None

    def getSizeHint(self, row, column, item):
        """Return the size hint for a cell"""

        return self.column_list[column].get("size", None)

    def make_readonly_widget(self):
        readonly_widget = QLabel()
        readonly_widget.setEnabled(False)
        readonly_widget.setVisible(False)

        return readonly_widget

    def paintCell(self, row, column, item, painter, option):
        """Paint a custom cell. Return True if the cell was painted, or False
        to continue with the default cell painting.
        """
        # Save the painter so it can restored later
        painter.save()

        # Set highlight for selected items
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            painter.setPen(TEXT_HIGHLIGHTED_COLOR)
        elif not item.isMediaPresent():
            painter.fillRect(option.rect, MEDIA_OFFLINE_COLOR)
            painter.setPen(TEXT_COLOR)
        else:
            painter.setPen(TEXT_COLOR)

        current_column = self.column_list[column]
        if current_column["name"] == "Tags":
            iconSize = 20
            rectangle = QRect(
                option.rect.x(),
                option.rect.y() + (option.rect.height() - iconSize) / 2,
                iconSize,
                iconSize,
            )
            tags = item.tags()
            if len(tags) > 0:
                painter.setClipRect(option.rect)
                for tag in item.tags():
                    QIcon(tag.icon()).paint(painter, rectangle, Qt.AlignCenter)
                    rectangle.translate(rectangle.width() + 2, 0)

                painter.restore()
                return True

        elif current_column["name"] == "cur_grade":
            ingest_grade = item.openpype_instance_data().get("ingested_grade")

            painter.setClipRect(option.rect)

            if not ingest_grade or ingest_grade == "None":
                painter.drawText(option.rect, Qt.AlignLeft, "--")

                painter.restore()
                return True

            margin = QMargins(0, 0, 5, 0)
            text = option.fontMetrics.elidedText(
                ingest_grade, Qt.ElideLeft, option.rect.width()
            )
            painter.drawText(
                option.rect - margin, Qt.AlignRight | Qt.AlignVCenter, text
            )

            painter.restore()
            return True

        elif current_column["name"] == "sg_tags":
            painter.setClipRect(option.rect)
            sg_tag_data = item.sg_tags_data()
            if not sg_tag_data:
                painter.drawText(option.rect, Qt.AlignLeft, "--")

                painter.restore()
                return True

            iconSize = 20
            rectangle = QRect(
                option.rect.x(),
                option.rect.y() + (option.rect.height() - iconSize) / 2,
                iconSize,
                iconSize,
            )

            # Need to make sure the icons are sorted for easy readability
            for key in sorted(sg_tag_data, key=SG_TAGS.index):
                if sg_tag_data[key] == "True":
                    QIcon(SG_TAG_ICONS[key]).paint(
                        painter, rectangle, Qt.AlignCenter
                    )
                    rectangle.translate(rectangle.width() + 2, 0)

            painter.restore()
            return True

        painter.restore()
        return False

    def createEditor(self, row, column, item, view):
        """Create an editing widget for a custom cell"""
        self.currentView = view
        current_column = self.column_list[column]
        current_column_name = current_column["name"]

        if current_column["cellType"] == "readonly" or not isinstance(
            item.parent(), hiero.core.VideoTrack
        ):
            # readonly is done by removing visibility and useability of the
            # returned widget to the widget viewer
            return self.make_readonly_widget()

        elif current_column_name == "Colorspace":
            ocio_config = get_active_ocio_config()
            edit_widget = ColorspaceWidget(ocio_config)
            edit_widget.root_menu.triggered.connect(self.colorspace_changed)

            return edit_widget

        elif current_column_name == "ingest_res":
            current_format = item.ingest_res_data().get("resolution", "")

            resolution_combo = IngestResWidget(item, current_format)

            resolution_combo.currentIndexChanged.connect(
                lambda: self.ingest_res_changed(resolution_combo, "index")
            )
            resolution_combo.lineEdit().returnPressed.connect(
                lambda: self.ingest_res_changed(
                    resolution_combo.lineEdit(), "return"
                )
            )

            return resolution_combo

        elif current_column_name == "resize_type":
            # Let user know that ingest format must exist first
            current_resize_type = item.ingest_res_data().get("resize")
            if not current_resize_type:
                QMessageBox.warning(
                    hiero.ui.mainWindow(),
                    "Critical",
                    "No Ingest Resolution found\n"
                    "Please assign an Ingest Resolution first",
                )

            resize_type = QComboBox()
            resize_type.addItem("none")
            resize_type.addItem("width")
            resize_type.addItem("height")
            resize_type.addItem("fit")
            resize_type.addItem("fill")
            resize_type.addItem("distort")

            resize_index = resize_type.findText(current_resize_type)
            resize_type.setCurrentIndex(resize_index)
            resize_type.currentIndexChanged.connect(
                lambda: self.ingest_res_type_changed(resize_type)
            )

            return resize_type

        elif current_column_name == "ingest_effects":
            ingest_effects_state = item.ingest_effects_data()
            ingest_effects_edit_widget = IngestEffectsWidget(
                ingest_effects_state
            )
            ingest_effects_edit_widget.root_menu.aboutToHide.connect(
                lambda: self.ingest_effect_changed(ingest_effects_edit_widget)
            )

            return ingest_effects_edit_widget

        elif current_column_name == "cur_grade":
            # If user double clicks on current grade. Show the full path and
            # disable editing
            ingest_grade = item.openpype_instance_data().get("ingested_grade")
            if not ingest_grade or ingest_grade == "None":
                edit_widget = QLabel()
                edit_widget.setEnabled(False)
                edit_widget.setVisible(False)

                return edit_widget

            widget = CurrentGradeWidget(ingest_grade)

            return widget

        elif current_column_name == "sg_tags":
            sg_tag_state = item.sg_tags_data()
            sg_tag_edit_widget = SGTagsWidget(sg_tag_state)
            sg_tag_edit_widget.root_menu.aboutToHide.connect(
                lambda: self.sg_tags_changed(sg_tag_edit_widget)
            )

            return sg_tag_edit_widget

        elif current_column_name == "edit_note":
            current_edit_note = item.edit_note_data().get("note", "")

            edit_widget = QLineEdit()
            edit_widget.setText(current_edit_note)

            edit_widget.returnPressed.connect(
                lambda: self.edit_note_changed(edit_widget)
            )

            return edit_widget

        elif current_column_name in [
            "head_handles",
            "tail_handles",
        ]:
            cut_info_data = item.cut_info_data()
            if not cut_info_data:
                return self.make_readonly_widget()

            if cut_info_data.get("cut_range") == "False":
                return self.make_readonly_widget()

            current_text = cut_info_data.get(current_column_name)
            edit_widget = QLineEdit(current_text)
            edit_widget.setObjectName(current_column_name)
            edit_widget.returnPressed.connect(
                lambda: self.cut_info_changed(edit_widget)
            )

            return edit_widget

        elif current_column_name == "cut_in":
            current_text = item.cut_info_data().get("cut_in")
            edit_widget = QLineEdit(current_text)
            edit_widget.setObjectName("cut_in")
            edit_widget.returnPressed.connect(
                lambda: self.cut_info_changed(edit_widget)
            )

            return edit_widget

        elif current_column_name == "cut_range":
            check_state = item.cut_info_data().get("cut_range")
            combo_widget = QComboBox()
            combo_widget.setObjectName("cut_range")
            # Since trigger is on index change. Need to make sure valid options
            # will also be a change of index
            combo_widget.addItem("--")
            combo_widget.addItem("True")
            combo_widget.addItem("False")
            combo_widget.setCurrentText(check_state)
            combo_widget.currentIndexChanged.connect(
                lambda: self.cut_info_changed(combo_widget)
            )

            return combo_widget

        elif current_column_name == "family":
            instance_tag = item.get_ingest_instance()
            if not instance_tag:
                combo_text = "--"
            else:
                combo_text = instance_tag.metadata().value("family")

            combo_widget = QComboBox()
            combo_widget.setObjectName("family")
            # Since trigger is on index change. Need to make sure valid options
            # will also be a change of index
            combo_widget.addItem("--")
            combo_widget.addItem("plate")
            combo_widget.addItem("reference")
            combo_widget.setCurrentText(combo_text)
            combo_widget.currentIndexChanged.connect(
                lambda: self.ingest_instance_changed(combo_widget)
            )

            return combo_widget

        elif current_column_name == "main_plate":
            instance_tag = item.get_main_plate()
            check_state = "True"
            if not instance_tag:
                check_state = "False"

            combo_widget = QComboBox()
            combo_widget.addItem("True")
            combo_widget.addItem("False")
            combo_widget.setCurrentText(check_state)
            combo_widget.currentIndexChanged.connect(
                lambda: self.main_plate_changed(combo_widget)
            )

            return combo_widget

        elif current_column_name == "use_nuke":
            instance_tag = item.get_ingest_instance()
            if not instance_tag:
                check_state = "--"
            else:
                # For Openpype tags already made they won't have use nuke
                # instance data
                try:
                    check_state = instance_tag.metadata().value("use_nuke")
                except RuntimeError:
                    check_state = "--"

            instance_key = current_column_name
            combo_widget = QComboBox()
            combo_widget.setObjectName(instance_key)
            # Since trigger is on index change. Need to make sure valid options
            # will also be a change of index
            combo_widget.addItem("--")
            combo_widget.addItem("True")
            combo_widget.addItem("False")
            combo_widget.setCurrentText(check_state)
            combo_widget.currentIndexChanged.connect(
                lambda: self.ingest_instance_changed(combo_widget)
            )

            return combo_widget

        return None

    def setModelData(self, row, column, item, editor):
        return False

    def dropMimeData(self, row, column, item, data, drop_items):
        """Handle a drag and drop operation - adds a Dragged Tag to the shot"""
        for drop_item in drop_items:
            if isinstance(drop_item, hiero.core.Tag):
                item.addTag(drop_item)

        return None

    @column_widget_callback
    def colorspace_changed(self, selection, project, action):
        """This method is called when Colorspace widget changes index."""
        colorspace = action.text()

        with project.beginUndo("Set Colorspace"):
            track_item = None
            for track_item in selection:
                track_item.setSourceMediaColourTransform(colorspace)

    @column_widget_callback
    def ingest_res_changed(self, selection, project, sender, signal_type):
        if signal_type == "index":
            # Don't want current text incase it's from line edit?
            ingest_resolution = sender.currentText()
        else:
            ingest_resolution = sender.text().strip()

        key = "resolution"

        with project.beginUndo("Set Ingest Resolution"):
            if ingest_resolution != "--":
                ingest_resolution = ingest_resolution.split(" - ")[0]
                for track_item in selection:
                    track_item.set_ingest_res(key, ingest_resolution)

            else:
                for track_item in selection:
                    ingest_res_tag = track_item.get_ingest_res()
                    if ingest_res_tag:
                        log.info(
                            f"{track_item.parent().name()}."
                            f"{track_item.name()}: "
                            "Removing 'Ingest Resolution' tag"
                        )
                        track_item.removeTag(ingest_res_tag)

    @column_widget_callback
    def ingest_res_type_changed(self, selection, project, sender):
        resize_type = sender.currentText()
        key = "resize"

        with project.beginUndo("Set Ingest Resolution Resize Type"):
            for track_item in selection:
                track_item.set_ingest_res(key, resize_type)

    @column_widget_callback
    def ingest_effect_changed(
        self, selection, project, ingest_effects_edit_widget
    ):
        ingest_effects_edit_widget.set_effects_data()
        effect_states = ingest_effects_edit_widget.effects_data

        with project.beginUndo("Update Ingest Effects"):
            for track_item in selection:
                track_item.set_ingest_effects(effect_states)

    @column_widget_callback
    def sg_tags_changed(self, selection, project, sg_tag_edit_widget):
        sg_tag_edit_widget.set_tag_data()
        tag_states = sg_tag_edit_widget.tag_data

        with project.beginUndo("Update SG Tag Toggle"):
            for track_item in selection:
                track_item.set_sg_tags(tag_states)

    @column_widget_callback
    def edit_note_changed(self, selection, project, sender):
        text = sender.text()

        with project.beginUndo("Set Edit Note"):
            if text != "--":
                for track_item in selection:
                    track_item.set_edit_note(text)

            # If value is -- this is used as an easy to remove Edit Note tag
            else:
                for track_item in selection:
                    edit_note_tag = track_item.get_edit_note()
                    if edit_note_tag:
                        log.info(
                            f"{track_item.parent().name()}."
                            f"{track_item.name()}: "
                            "Removing 'Edit Note' tag"
                        )
                        track_item.removeTag(edit_note_tag)

    @column_widget_callback
    def cut_info_changed(self, selection, project, sender):
        key = sender.objectName()
        if isinstance(sender, QComboBox):
            value = sender.currentText()
        else:
            value = sender.text().strip()

        value_no_operators = value.translate(NO_OP_TRANSLATE)
        # Only pass on edit if user unintentionally erased value from column
        if value not in ["--", "", "False", "True"] and not value_no_operators.isdigit():
            return
        else:
            # Remove preceding zeros
            value = value if value == "0" else value.lstrip("0")

        with project.beginUndo("Set Cut Info"):
            operate = value != value_no_operators
            if value != "--":
                for track_item in selection:
                    track_item.set_cut_info(key, value, operate)

            # If value is -- this is used as an easy to remove Cut Info tag
            else:
                for track_item in selection:
                    cut_info_tag = track_item.get_cut_info()
                    if cut_info_tag:
                        log.info(
                            f"{track_item.parent().name()}."
                            f"{track_item.name()}: "
                            "Removing 'Cut Info' tag"
                        )
                        track_item.removeTag(cut_info_tag)

    @column_widget_callback
    def main_plate_changed(self, selection, project, sender):
        text = sender.currentText()

        with project.beginUndo("Set Main Plate"):
            if text == "True":
                for track_item in selection:
                    track_item.set_main_plate()
            else:
                for track_item in selection:
                    main_plate_tag = track_item.get_main_plate()
                    if main_plate_tag:
                        log.info(
                            f"{track_item.parent().name()}."
                            f"{track_item.name()}: "
                            "Removing 'Main Plate' tag"
                        )
                        track_item.removeTag(main_plate_tag)

    @column_widget_callback
    def ingest_instance_changed(self, selection, project, sender):
        key = sender.objectName()
        if isinstance(sender, QComboBox):
            value = sender.currentText()
        else:
            value = sender.text()

        with project.beginUndo("Set AYON Instance"):
            # If value is -- this is used as an easy to remove openpype tag
            if value.strip() == "--":
                for track_item in selection:
                    ingest_instance = track_item.get_ingest_instance()
                    if ingest_instance:
                        log.info(
                            f"{track_item.parent().name()}."
                            f"{track_item.name()}: "
                            "Removing 'Cut Info' tag"
                        )
                        track_item.removeTag(ingest_instance)
            else:
                for track_item in selection:
                    track_item.set_ingest_instance(key, value)


def get_tag(self, name, contains=False):
    tags = self.tags()
    for tag in tags:
        if contains:
            if name in tag.name():
                return tag
        else:
            if name == tag.name():
                return tag

    return None


def get_tag_data(self, name, contains=False):
    tag = get_tag(self, name, contains)
    tag_data = {}

    if not tag:
        return tag_data

    convert_keys = TAG_DATA_KEY_CONVERT.get(name, {})
    tag_meta_data = tag.metadata().dict()
    for key, value in tag_meta_data.items():
        # Convert data from column names into tag key names
        if key in convert_keys:
            tag_data[convert_keys[key]] = value
        else:
            tag_data[key.split("tag.")[-1]] = value

    # Remove default keys
    for key in ("label", "applieswhole"):
        if key in tag_data:
            del tag_data[key]

    return tag_data


def get_frame_defaults(project_name):
    # Grab handle infos from SG
    filters = [
        [
            "name",
            "is",
            project_name,
        ],
    ]
    fields = [
        "sg_show_handles",
        "sg_default_start_frame",
    ]
    sg_project = SHOTGRID.find_one("Project", filters, fields)

    if not sg_project:
        return 1001, 8, 8

    frame_start_default = sg_project.get("sg_default_start_frame", 1001)
    handle_start_default = sg_project.get("sg_show_handles", 8)

    return (frame_start_default, handle_start_default, handle_start_default)


def _set_cut_info(self, key, value, operate):
    """Empty value is allowed incase editor wants to create a cut tag with
    default values
    """
    # Cut tag can be set from a variety of columns
    # Need to logic for each case
    cut_tag = self.get_cut_info()

    # Can't do operations on an empty value
    if not cut_tag and operate:
        return

    # Grab OP tag if found - this change into a new tag once OP tag is removed.
    family = self.ingest_instance_data().get("family")
    if not family:
        track_name = self.parentTrack().name()
        if "ref" in track_name:
            family = "reference"
        else:
            family = "plate"

    if family == "plate":
        cut_range = False
    else:
        cut_range = True

    if not cut_tag:
        # get default handles
        cut_tag = hiero.core.Tag("Cut Info")
        cut_tag.setIcon("icons:TagKeylight.png")
        project_name = get_current_project_name()
        frame_start, handle_start, handle_end = get_frame_defaults(
            project_name
        )

        if not cut_range:
            handle_start = self.handleInLength()
            handle_end = self.handleOutLength()

        if None not in [frame_start, handle_start]:
            frame_offset = frame_start + handle_start
            if value:
                if key == "cut_in":
                    frame_offset = int(value)

            cut_in = frame_offset

        else:
            cut_in = None

        cut_data = {}
        cut_data["cut_in"] = cut_in
        cut_data["head_handles"] = handle_start
        cut_data["tail_handles"] = handle_end
        cut_data["cut_range"] = cut_range

        for cut_key, cut_value in cut_data.items():
            if not isinstance(cut_value, str):
                cut_value = str(cut_value)
            cut_tag.metadata().setValue(f"tag.{cut_key}", cut_value)

        self.sequence().editFinished()
        self.addTag(cut_tag)

        _set_cut_info(self, key, value, False)

    # Cut range might not exist
    if "tag.cut_range" in cut_tag.metadata():
        current_cut_range = cut_tag.metadata().value("tag.cut_range")
    else:
        current_cut_range = cut_range
    if key == "cut_range":
        cut_range = value
    else:
        cut_range = current_cut_range

    if value:
        if operate:
            # Leave operation as is if value has valid expression
            if value[0].isdigit():
                operation = value
            else:
                current_value = cut_tag.metadata().value(f"tag.{key}")
                operation = f"{current_value}{value}"

            try:
                # Frames must be integers
                value = str(int(eval(operation)))
            except SyntaxError:
                log.info(
                    f"{self.parent().name()}.{self.name()}: "
                    f"{value} must be properly formatted. Read"
                    "tooltip for more information"
                )
                return

        if cut_range == "False":
            handle_start = str(self.handleInLength())
            handle_end = str(self.handleOutLength())
            cut_tag.metadata().setValue("tag.head_handles", handle_start)
            cut_tag.metadata().setValue("tag.tail_handles", handle_end)
        cut_tag.metadata().setValue(f"tag.{key}", value)

    self.sequence().editFinished()


def _set_ingest_instance(self, key, value, update=True):
    """
    Only one key of the tag can be modified at a time for items that already
    have a tag.
    """
    value = value if value == "0" else value.strip().lstrip("0")

    ingest_tag = self.get_ingest_instance()
    track_name = self.parentTrack().name()

    tag_data = {}
    if not ingest_tag:
        # First fill default instance if no tag found and then update with
        # data parameter
        if "ref" in track_name:
            family = "reference"
        else:
            family = "plate"

        tag_data["family"] = family
        tag_data["use_nuke"] = "True"

        ingest_tag = hiero.core.Tag("Ingest Data")
        ingest_tag.setIcon("icons:EffectsTiny.png")

        for key, value in tag_data.items():
            if not isinstance(value, str):
                value = str(value)
            ingest_tag.metadata().setValue(f"tag.{key}", value)

        self.sequence().editFinished()
        self.addTag(ingest_tag)

        # Need this here because the correct tag on run is always the next one
        # _set_ingest_instance(self, key, value)

    if update:
        ingest_tag.metadata().setValue(f"tag.{key}", value)

    self.sequence().editFinished()


def _set_ingest_effects(self, states):
    effect_tag = self.get_ingest_effects()

    if not effect_tag:
        effect_tag = hiero.core.Tag("Ingest Effects")
        effect_tag.setIcon("icons:TimelineToolSoftEffect.png")
        # Add default resize type
        self.sequence().editFinished()
        self.addTag(effect_tag)
        # Need this here because the correct tag on run is always the next one
        _set_ingest_effects(self, states)
    else:
        # Remove tag if all states are False
        if not [
            effect_tag for effect_tag in states if states[effect_tag] == True
        ]:
            self.removeTag(effect_tag)
            return

    effect_tag_meta = effect_tag.metadata()
    for key, value in states.items():
        # Meta will always have all SG tag states
        # Convert to string to match tag metadata needs
        value = "True" if value else "False"
        effect_tag_meta.setValue(f"tag.{key}", value)


def _set_sg_tags(self, states):
    sg_tag = self.get_sg_tags()

    if not sg_tag:
        sg_tag = hiero.core.Tag("SG Tags")
        sg_tag.setIcon("icons:EffectsTiny.png")
        # Add default resize type
        self.sequence().editFinished()
        self.addTag(sg_tag)
        # Need this here because the correct tag on run is always the next one
        _set_sg_tags(self, states)
    else:
        # Remove tag if all states are False
        if not [sg_tag for sg_tag in states if states[sg_tag] == True]:
            self.removeTag(sg_tag)
            return

    sg_tag_meta = sg_tag.metadata()
    for key, value in states.items():
        # Meta will always have all SG tag states
        # Convert to string to match tag metadata needs
        value = "True" if value else "False"
        sg_tag_meta.setValue(f"tag.{key}", value)


def _set_ingest_res(self, key, value):
    ingest_res_tag = self.get_ingest_res()

    if not ingest_res_tag:
        ingest_res_tag = hiero.core.Tag("Ingest Resolution")
        ingest_res_tag.setIcon("icons:PPResolution.png")
        ingest_res_data = {}
        ingest_res_data["resize"] = "width"

        if value:
            ingest_res_data.update({key: value})

        for key, value in ingest_res_data.items():
            if not isinstance(value, str):
                value = str(value)
            ingest_res_tag.metadata().setValue(f"tag.{key}", value)

        self.sequence().editFinished()
        self.addTag(ingest_res_tag)

        # Need this here because the correct tag on run is always the next one
        _set_ingest_res(self, key, value)

    ingest_res_tag.metadata().setValue(f"tag.{key}", value)

    self.sequence().editFinished()


def _set_edit_note(self, note):
    edit_note_tag = self.get_edit_note()

    if not edit_note_tag:
        edit_note_tag = hiero.core.Tag("Edit Note")
        edit_note_tag.setIcon("icons:SyncMessage.png")

        self.sequence().editFinished()
        self.addTag(edit_note_tag)

        # Need this here because the correct tag on run is always the next one
        _set_edit_note(self, note)

    edit_note_tag.metadata().setValue(f"tag.note", note)

    self.sequence().editFinished()


def _set_main_plate(self, default_tag=False, spreadsheet=True):
    main_plate_tag = self.get_main_plate()
    if not main_plate_tag:
        main_plate_tag = hiero.core.Tag("Main Plate")
        main_plate_tag.setIcon("/pipe/resources/icons/main_plate.png")

        self.addTag(main_plate_tag)
        # Need this here because the correct tag on run is always the next one
        _set_main_plate(self, default_tag)

    if not default_tag:
        main_plate_tag.metadata().setValue("override", "True")

    if spreadsheet:
        # Run cleanup on default tags
        main_plate_utility = MainPlate()
        main_plate_utility.set_track_item_main_plate(self)

    self.sequence().editFinished()


def unique_track_items(track_items):
        # Create a set to store unique names
    unique_names = set()

    # Create a new list to store the filtered track items
    filtered_track_items = []

    for track_item in track_items:
        name = track_item.name()

        # Check if the name is unique (not in the set)
        if name not in unique_names:
            # Add the name to the set
            unique_names.add(name)
            # Add the track item to the filtered list
            filtered_track_items.append(track_item)

    return filtered_track_items


def _update_track_main_plates(event):

    main_plate_track = MainPlate.get_main_plate_track()
    if not main_plate_track:
        return

    project = main_plate_track.project()

    if event.type == "kTrackItemUpdate":
        track_items = event.track_items
        from_plate_plate = event.main_track_switch
        with project.beginUndo('Set Main Grades'):
            if from_plate_plate:
                # Make sure that only one item per a track is sent. no need to run more than once
                main_track_items = unique_track_items(track_items)
            else:
                main_track_items = [item for item in track_items if item.parent() == main_plate_track]
            if main_track_items:
                main_plate_utility = MainPlate()
                for track_item in main_track_items:
                    main_plate_utility.set_track_item_main_plate(track_item)
                    return

    else: # Event type will be kTrackUpdate
        with project.beginUndo('Set Main Grades'):
            main_plate_utility = MainPlate()
            main_plate_utility.set_track_main_plates()


class TrackUpdateEvent:
    # previous_track_item = ()
    plate_tracks = []

    def __init__(self):
        # Register the custom event type in Hiero
        hiero.core.events.registerEventType("kTrackUpdate")
        self.set_plate_tracks()

        hiero.core.events.registerInterest(
            # Event is restricted to timeline as video track selections only happen on
            # timeline
            "kSelectionChanged/kTimeline", self.new_main_plate_track
        )

    def set_plate_tracks(self):
        sequence = hiero.ui.activeSequence()
        if not sequence:
            self.plate_tracks = {}
            return

        self.plate_tracks = MainPlate.get_plate_tracks()

    # There is more overhead when being less specific. Therefore only run
    # when rename has for sure occurred
    def new_main_plate_track(self, event):
        """Rename event encapsulates all track updates that deal with name
        updates. That includes new tracks
        """
        if not self.plate_tracks:
            self.set_plate_tracks()
            return

        old_plate_tracks = self.plate_tracks
        self.set_plate_tracks()

        if old_plate_tracks[0] == self.plate_tracks[0]:
            self.previous_track_item = event.sender.selection()
            return

        hiero.core.events.sendEvent( "kTrackUpdate", None,
            renamed_track=self.plate_tracks[0],
            timeline=event.sender,
            )

        self.previous_track_item = event.sender.selection()


class TrackItemUpdateEvent():
    track_items = set()

    def __init__(self):
        # Register the custom event type in Hiero
        hiero.core.events.registerEventType("kTrackItemUpdate")
        hiero.core.events.registerInterest(
            # Event is restricted to timeline as video track selections only happen on
            # timeline
            "kSelectionChanged/kTimeline", self.track_item_update_event
        )
        hiero.core.events.registerInterest(
            "kSequenceEdited", self.track_item_update_event
        )

    def set_track_items(self):
        track_items = set()
        seq = hiero.ui.activeSequence()
        if seq:
            for track in seq.videoTracks():
                # The original track item is used to track if it was a rename or a new
                # track item object
                track_items.update([(item, item.name(), item.parent().name()) for item in track.items()])

        self.track_items = track_items

    def track_item_update_event(self, event):
        previous_track_items = self.track_items
        self.set_track_items()

        item_differences = self.track_items.difference(previous_track_items)

        # If item was simple rename or addition to main plate then always event
        # If item was moved from other track make sure that track was main track then event
        # Find out if track item was previously on main track
        main_plate_track = MainPlate.get_main_plate_track()
        main_track_switch = False
        previously_changed_item = []
        for item_difference in item_differences:
            for previous_track_item in previous_track_items:
                # If same item
                if previous_track_item[0] in item_difference:
                    previously_changed_item.append(previous_track_item[0])
                    # If different track
                    if previous_track_item[-1] != item_difference[-1]:
                        # If was main track
                        if previous_track_item[-1] == main_plate_track.name():
                            main_track_switch = True
                            break

        # Track item update event
        if item_differences:
            hiero.core.events.sendEvent("kTrackItemUpdate", None,
                track_items=tuple(item[0] for item in item_differences),
                main_track_switch=main_track_switch,
                )

        # # New track item event
        # if self.track_items > previous_track_items:
        #     # previously_changed_item stores previous items in difference.
        #     # Removing these values from difference will give new items
        #     new_items = []
        #     for item_difference in item_differences:
        #         if item_difference[0] not in  previously_changed_item:
        #             new_items.append(item_difference[0])
        #     if new_items:
        #         hiero.core.events.sendEvent("kTrackItemNew", None,
        #             track_items=new_items,
        #             )


def _add_default_tags(event):
    track_items = event.track_items

    for track_item in track_items:
        # If has context then add tags
        if get_track_item_env(track_item):
            # Check for ingest tag
            if not track_item.get_ingest_instance():
                track_item.set_ingest_instance("", "", update=False)

            # Check for cut info tag
            if not track_item.get_cut_info():
                _set_cut_info(track_item, "", "", False)


# Attach tag setters, getters and tag data get into hiero.core.TrackItem
hiero.core.TrackItem.set_ingest_res = _set_ingest_res
hiero.core.TrackItem.get_ingest_res = lambda self: get_tag(
    self, "Ingest Resolution"
)
hiero.core.TrackItem.ingest_res_data = lambda self: get_tag_data(
    self, "Ingest Resolution"
)

hiero.core.TrackItem.set_ingest_effects = _set_ingest_effects
hiero.core.TrackItem.get_ingest_effects = lambda self: get_tag(
    self, "Ingest Effects"
)
hiero.core.TrackItem.ingest_effects_data = lambda self: get_tag_data(
    self, "Ingest Effects"
)

hiero.core.TrackItem.set_sg_tags = _set_sg_tags
hiero.core.TrackItem.get_sg_tags = lambda self: get_tag(self, "SG Tags")
hiero.core.TrackItem.sg_tags_data = lambda self: get_tag_data(self, "SG Tags")

hiero.core.TrackItem.set_edit_note = _set_edit_note
hiero.core.TrackItem.get_edit_note = lambda self: get_tag(self, "Edit Note")
hiero.core.TrackItem.edit_note_data = lambda self: get_tag_data(
    self, "Edit Note"
)

hiero.core.TrackItem.set_cut_info = _set_cut_info
hiero.core.TrackItem.get_cut_info = lambda self: get_tag(self, "Cut Info")
hiero.core.TrackItem.cut_info_data = lambda self: get_tag_data(
    self, "Cut Info"
)

hiero.core.TrackItem.set_ingest_instance = _set_ingest_instance
hiero.core.TrackItem.get_ingest_instance = lambda self: get_tag(self, "Ingest Data")
hiero.core.TrackItem.ingest_instance_data = lambda self: get_tag_data(
    self, "Ingest Data"
)

hiero.core.TrackItem.set_main_plate = _set_main_plate
hiero.core.TrackItem.get_main_plate = lambda self: get_tag(self, "Main Plate")
hiero.core.TrackItem.main_plate_data = lambda self: get_tag_data(
    self, "Main Plate"
)

hiero.core.TrackItem.openpype_instance_data = lambda self: get_tag_data(
    self, OPENPYPE_TAG_NAME, contains=True
)

# Register custom events
track_update = TrackUpdateEvent()
track_item_update = TrackItemUpdateEvent()

hiero.core.events.registerInterest(
    "kTrackUpdate", _update_track_main_plates
)

hiero.core.events.registerInterest(
    "kTrackItemUpdate", _update_track_main_plates
)

hiero.core.events.registerInterest(
    "kTrackItemUpdate", _add_default_tags
)

# Register our custom columns
hiero.ui.customColumn = CustomSpreadsheetColumns()
