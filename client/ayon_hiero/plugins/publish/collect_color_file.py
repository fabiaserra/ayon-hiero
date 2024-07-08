import re
import os.path
from glob import glob
from pathlib import Path
from datetime import datetime

import hiero
import pyblish.api
from qtpy import QtWidgets, QtCore, QtGui

from ayon_hiero import api as phiero

LUTS_3D = ["cube", "3dl", "csp", "lut"]
LUTS_2D = ["ccc", "cc", "cdl"]

# EDL is not by nature a color file nor a LUT. For this reason it's seperated
# from previous categories
COLOR_FILE_EXTS = LUTS_2D + LUTS_3D + ["edl"]


class MissingColorFile(QtWidgets.QDialog):
    """Constructs a dialog that allows user to locate a grade file on disk
    using any of the valid extensions in the global variable COLOR_FILE_EXTS.
    If the path leads to an edl file the user can select which event in the edl
    they would like to target.

    Caveat: No ability to select CDL ID from CCC file. What happens if CCC is
    found and has more than one ID.

    Parameters:
        - shot_name (str): The name of the shot to locate the color file for.
        - source_name (str): The name of the source to locate the color file
            for.
        - main_grade (dict): A dictionary containing the SOPS values for the
            main grade.

    Attributes:
        - data (dict): Is utilized to store information gathered from UI.
            - "cdl" (dict): CDL information (if applicable)
                - "slope" (tuple): CDL Slope.
                - "offset (tuple): CDL Offset.
                - "power" (tuple): CDL Power.
                - "sat" (float): CDL Saturation.
            - "path" (str): Path to file that user selected.
            - "ignore" (bool): Stores whether the grade will be ignored during
                color file integration.
            - "type" (str): File type that was selected by user.
        - default_browser_path (str): The default path for the file browser.
        - prev_file_path_input (str): The previous file path input.
        - prev_edl_entries_path (str): The previous EDL entries path.
        - edl (dict): A dictionary containing EDL data.
        - ignore_grade (bool): A flag indicating whether to ignore the grade.

    returns:
        QT signal for close or accept dialog
    """

    data = {}
    prev_file_path_input = ""
    prev_edl_entries_path = ""
    edl = {}
    ignore_grade = False

    def __init__(self, shot_name, source_name, source_path, main_grade, parent=None):
        super(MissingColorFile, self).__init__(parent)
        self.shot_name = shot_name
        self.source_name = source_name
        self.main_grade = main_grade
        self.default_browser_path = os.path.dirname(source_path)

        self.setWindowTitle("Locate Color File")
        width = 519
        height = 386
        self.setFixedSize(width, height)

        # Fonts
        header_font = QtGui.QFont()
        header_font.setPointSize(20)

        # All layouts are added to widgets. This allows all content to be added
        # as widgets
        self.content_widget = [QtWidgets.QWidget(self)]

        # Info Layout
        info_widget = QtWidgets.QWidget(self)
        info_layout = QtWidgets.QVBoxLayout(info_widget)
        info_msg_1 = "Grade not located!"
        self.information_label_1 = QtWidgets.QLabel(info_msg_1)
        self.information_label_1.setFont(header_font)
        info_layout.addWidget(self.information_label_1)
        info_layout.setAlignment(
            self.information_label_1, QtCore.Qt.AlignCenter
        )
        info_msg_2 = (
            "Shot: {0}\nPlate: {1}\n\nPlease locate Grade File and "
            "determine if it's the shot Main Grade".format(
                self.shot_name, self.source_name
            )
        )
        self.information_label_2 = QtWidgets.QLabel(info_msg_2)
        info_layout.addWidget(self.information_label_2)
        self.content_widget.append(info_widget)

        # Input Layout
        input_widget = QtWidgets.QWidget(self)
        input_layout = QtWidgets.QGridLayout(input_widget)
        self.file_path_label = QtWidgets.QLabel("Grade Path:")
        self.file_path_input = QtWidgets.QLineEdit("")
        self.file_path_browse = QtWidgets.QPushButton("")

        file_browse_pixmap = QtWidgets.QStyle.SP_DialogOpenButton
        file_browse_icon = self.style().standardIcon(file_browse_pixmap)
        self.file_path_browse.setIcon(file_browse_icon)

        self.file_path_browse.setMaximumWidth(22)
        self.main_grade_checkbox = QtWidgets.QCheckBox("Main Grade")
        self.main_grade_checkbox.setChecked(self.main_grade)
        input_layout.addWidget(self.file_path_label, 0, 0)
        input_layout.addWidget(self.file_path_input, 0, 1)
        input_layout.addWidget(self.file_path_browse, 0, 2)
        input_layout.addWidget(self.main_grade_checkbox, 1, 1)
        self.content_widget.append(input_widget)

        # Layout for EDL and SOPS
        info_display_widget = QtWidgets.QWidget(self)
        info_display_layout = QtWidgets.QHBoxLayout(info_display_widget)

        # EDL Layout
        self.edl_widget = QtWidgets.QWidget(self)
        self.edl_widget.setStyleSheet("background-color: rgb(43, 43, 43)")
        self.edl_widget.hide()
        edl_layout = QtWidgets.QGridLayout(self.edl_widget)
        # EDL event-viewing
        event_and_view = QtWidgets.QHBoxLayout()
        self.entry_label = QtWidgets.QLabel("Event #:")
        self.entry_number = QtWidgets.QSpinBox()
        self.open_file = QtWidgets.QPushButton("Open EDL")
        self.open_file.setStyleSheet("background-color: rgb(55, 55, 55)")
        self.open_file.setMinimumWidth(70)
        self.open_file.setMaximumWidth(70)
        event_and_view.addWidget(self.entry_number)
        event_and_view.addWidget(self.open_file)
        self.entry_number.setMinimumWidth(55)
        self.entry_number.setMaximumWidth(55)
        self.entry_number.setMinimumHeight(20)
        self.entry_number.setMaximumHeight(20)
        self.tape_label = QtWidgets.QLabel("Tape Name:")
        self.tape_name = QtWidgets.QLineEdit()
        self.tape_name.setReadOnly(True)
        self.tape_name.setMinimumWidth(149)
        self.tape_name.setMaximumWidth(149)
        self.clip_name_label = QtWidgets.QLabel("Clip Name:")
        self.clip_name = QtWidgets.QLineEdit()
        self.clip_name.setReadOnly(True)
        self.clip_name.setMinimumWidth(149)
        self.clip_name.setMaximumWidth(149)
        self.loc_name_label = QtWidgets.QLabel("LOC {shot}:")
        self.loc_name = QtWidgets.QLineEdit()
        self.loc_name.setReadOnly(True)
        self.loc_name.setMinimumWidth(149)
        self.loc_name.setMaximumWidth(149)
        edl_layout.addWidget(self.entry_label, 0, 0)
        edl_layout.addLayout(event_and_view, 0, 1)
        edl_layout.addWidget(self.tape_label, 1, 0)
        edl_layout.addWidget(self.tape_name, 1, 1)
        edl_layout.addWidget(self.clip_name_label, 2, 0)
        edl_layout.addWidget(self.clip_name, 2, 1)
        edl_layout.addWidget(self.loc_name_label, 3, 0)
        edl_layout.addWidget(self.loc_name, 3, 1)

        # SOPS Layout
        sops_widget = QtWidgets.QWidget(self)
        sops_widget.setStyleSheet("background-color: rgb(43, 43, 43)")
        sops_layout = QtWidgets.QGridLayout(sops_widget)

        # Slope Layout
        slope_layout = QtWidgets.QHBoxLayout()
        self.slope_label = QtWidgets.QLabel("Slope:")
        self.slope_r_input = QtWidgets.QLineEdit("NA")
        self.slope_r_input.setReadOnly(True)
        self.slope_r_input.setMinimumWidth(50)
        self.slope_r_input.setMaximumWidth(50)
        self.slope_g_input = QtWidgets.QLineEdit("NA")
        self.slope_g_input.setReadOnly(True)
        self.slope_g_input.setMinimumWidth(50)
        self.slope_g_input.setMaximumWidth(50)
        self.slope_b_input = QtWidgets.QLineEdit("NA")
        self.slope_b_input.setReadOnly(True)
        self.slope_b_input.setMinimumWidth(50)
        self.slope_b_input.setMaximumWidth(50)
        slope_layout.addWidget(self.slope_r_input)
        slope_layout.addWidget(self.slope_g_input)
        slope_layout.addWidget(self.slope_b_input)
        sops_layout.addWidget(self.slope_label, 0, 0)
        sops_layout.addLayout(slope_layout, 0, 1)

        # Offset Layout
        offset_layout = QtWidgets.QHBoxLayout()
        self.offset_label = QtWidgets.QLabel("Offset:")
        self.offset_r_input = QtWidgets.QLineEdit("NA")
        self.offset_r_input.setReadOnly(True)
        self.offset_r_input.setMinimumWidth(50)
        self.offset_r_input.setMaximumWidth(50)
        self.offset_g_input = QtWidgets.QLineEdit("NA")
        self.offset_g_input.setReadOnly(True)
        self.offset_g_input.setMinimumWidth(50)
        self.offset_g_input.setMaximumWidth(50)
        self.offset_b_input = QtWidgets.QLineEdit("NA")
        self.offset_b_input.setReadOnly(True)
        self.offset_b_input.setMinimumWidth(50)
        self.offset_b_input.setMaximumWidth(50)
        offset_layout.addWidget(self.offset_r_input)
        offset_layout.addWidget(self.offset_g_input)
        offset_layout.addWidget(self.offset_b_input)
        sops_layout.addWidget(self.offset_label, 1, 0)
        sops_layout.addLayout(offset_layout, 1, 1)

        # Power Layout
        power_layout = QtWidgets.QHBoxLayout()
        self.power_label = QtWidgets.QLabel("Power:")
        self.power_r_input = QtWidgets.QLineEdit("NA")
        self.power_r_input.setReadOnly(True)
        self.power_r_input.setMinimumWidth(50)
        self.power_r_input.setMaximumWidth(50)
        self.power_g_input = QtWidgets.QLineEdit("NA")
        self.power_g_input.setReadOnly(True)
        self.power_g_input.setMinimumWidth(50)
        self.power_g_input.setMaximumWidth(50)
        self.power_b_input = QtWidgets.QLineEdit("NA")
        self.power_b_input.setReadOnly(True)
        self.power_b_input.setMinimumWidth(50)
        self.power_b_input.setMaximumWidth(50)
        power_layout.addWidget(self.power_r_input)
        power_layout.addWidget(self.power_g_input)
        power_layout.addWidget(self.power_b_input)
        sops_layout.addWidget(self.power_label, 2, 0)
        sops_layout.addLayout(power_layout, 2, 1)

        # Saturation Layout
        sat_layout = QtWidgets.QHBoxLayout()
        self.sat_label = QtWidgets.QLabel("Sat:")
        self.sat_input = QtWidgets.QLineEdit("NA")
        self.sat_input.setReadOnly(True)
        self.sat_input.setMinimumWidth(42)
        self.sat_input.setMaximumWidth(42)
        sat_layout.addWidget(self.sat_input)
        sops_layout.addWidget(self.sat_label, 3, 0)
        sops_layout.addLayout(sat_layout, 3, 1)

        # Continue building EDL and SOPS display info layout
        info_h_spacer_item_1 = QtWidgets.QSpacerItem(
            10, 8, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        info_display_layout.addItem(info_h_spacer_item_1)
        info_display_layout.addWidget(self.edl_widget)
        self.edl_sops_separator = QtWidgets.QFrame()
        self.edl_sops_separator.setFrameShape(QtWidgets.QFrame.VLine)
        self.edl_sops_separator.setFrameShadow(QtWidgets.QFrame.Sunken)
        self.edl_sops_separator.setSizePolicy(
            QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding
        )
        self.edl_sops_separator.hide()
        info_display_layout.addWidget(self.edl_sops_separator)
        info_display_layout.addWidget(sops_widget)
        info_h_spacer_item_3 = QtWidgets.QSpacerItem(
            10, 8, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        info_display_layout.addItem(info_h_spacer_item_3)
        self.content_widget.append(info_display_widget)

        # Buttons Layout
        buttons_widget = QtWidgets.QWidget(self)
        buttons_layout = QtWidgets.QHBoxLayout(buttons_widget)
        buttons_h_spacer_item_1 = QtWidgets.QSpacerItem(
            10, 8, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        self.blank_grade_button = QtWidgets.QPushButton("Blank Grade")
        self.ignore_grade_button = QtWidgets.QPushButton("Ignore Grade")
        buttons_h_spacer_item_2 = QtWidgets.QSpacerItem(
            10, 8, QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed
        )
        self.accept_button = QtWidgets.QPushButton("Accept")
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        buttons_layout.addItem(buttons_h_spacer_item_1)
        buttons_layout.addWidget(self.blank_grade_button)
        buttons_layout.addWidget(self.ignore_grade_button)
        buttons_layout.addItem(buttons_h_spacer_item_2)
        buttons_layout.addWidget(self.accept_button)
        buttons_layout.addWidget(self.cancel_button)
        self.content_widget.append(buttons_widget)

        # Main layout of the dialog
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(0)

        # adding content widget
        for w in self.content_widget:
            main_layout.addWidget(w)

        # Connections
        self.file_path_browse.pressed.connect(self.open_color_file_browser)
        self.blank_grade_button.pressed.connect(self.blank_grade)
        self.ignore_grade_button.pressed.connect(self.set_ignore)
        self.accept_button.pressed.connect(self.check_and_accept)
        self.cancel_button.pressed.connect(self.cancel)
        self.file_path_input.textChanged.connect(self.set_sops_and_edl)
        self.entry_number.valueChanged.connect(self.set_edl_info)
        self.open_file.pressed.connect(self.open_text_file)

    def set_edl_info(self):
        event_number = self.entry_number.value()
        if self.edl:
            event_info = self.edl["events"][event_number]
            self.tape_name.setText(event_info["tape"])
            self.clip_name.setText(event_info["clip_name"])
            self.loc_name.setText(event_info["LOC"])
            if event_info.get("slope"):
                self.set_sops_widgets(
                    event_info.get("slope"),
                    event_info.get("offset"),
                    event_info.get("power"),
                    event_info.get("sat"),
                )

            else:
                self.set_sops_widgets(None, None, None, None)
        else:
            return

    def set_sops_from_cdl(self):
        """
        Driven by connection. Based on the change of file path.
        """
        file_path = self.file_path_input.text()
        file_extension = file_path.lower().rsplit(".", 1)[-1]
        if file_extension in ["ccc", "cc", "cdl"] and os.path.isfile(
            file_path
        ):
            cdl = phiero.parse_cdl(file_path)
            self.set_sops_widgets(
                cdl.get("slope"),
                cdl.get("offset"),
                cdl.get("power"),
                cdl.get("sat"),
            )
        else:
            return

    def set_sops_and_edl(self):
        file_path = self.file_path_input.text()
        file_extension = file_path.lower().rsplit(".", 1)[-1]

        if file_extension == "edl":
            self.edl_widget.show()
            self.edl_sops_separator.show()
        else:
            self.edl_widget.hide()
            self.edl_sops_separator.hide()

        color_file_path = file_path
        if self.prev_file_path_input == color_file_path:
            return

        if file_extension == "edl":
            edl = phiero.parse_edl_events(color_file_path)
            self.edl = edl
            self.entry_number.setMinimum(edl["first_entry"])
            self.entry_number.setMaximum(edl["last_entry"])

            # Right after parse update edl info
            self.set_edl_info()

        elif file_extension in ["ccc", "cc", "cdl"] and os.path.isfile(
            file_path
        ):
            cdl = phiero.parse_cdl(file_path)
            self.set_sops_widgets(
                cdl.get("slope"),
                cdl.get("offset"),
                cdl.get("power"),
                cdl.get("sat"),
            )

        if not (
            file_extension in ["ccc", "cc", "cdl", "edl"]
            and os.path.isfile(file_path)
        ):
            self.set_sops_widgets(None, None, None, None)
        else:
            self.prev_file_path_input = color_file_path

            return

    def set_sops_widgets(self, slope, offset, power, sat):
        if not slope:
            slope = ("NA", "NA", "NA")
        if not offset:
            offset = ("NA", "NA", "NA")
        if not power:
            power = ("NA", "NA", "NA")
        if sat is None:
            sat = "NA"

        # Set slope
        self.slope_r_input.setText(str(slope[0]))
        self.slope_g_input.setText(str(slope[1]))
        self.slope_b_input.setText(str(slope[2]))

        # Set Offset
        self.offset_r_input.setText(str(offset[0]))
        self.offset_g_input.setText(str(offset[1]))
        self.offset_b_input.setText(str(offset[2]))

        # Set Power
        self.power_r_input.setText(str(power[0]))
        self.power_g_input.setText(str(power[1]))
        self.power_b_input.setText(str(power[2]))

        # Set Saturation
        self.sat_input.setText(str(sat))

    def open_color_file_browser(self):
        # hiero.menu browse
        path_result = hiero.ui.openFileBrowser(
            caption="Color path",
            mode=1,
            initialPath=self.default_browser_path,
            multipleSelection=False,
        )
        if path_result:
            if path_result[0].replace("\\", "/").endswith("/"):
                path_result = path_result[0][:-1]
            else:
                path_result = path_result[0]
            folder_path = path_result
        else:
            return

        self.file_path_input.setText(folder_path)

    def blank_grade(self):
        cdl = {
            "slope": (1, 1, 1),
            "offset": (0, 0, 0),
            "power": (1, 1, 1),
            "sat": 1,
        }
        self.data["cdl"] = cdl
        self.data["path"] = ""
        self.data["ignore"] = False
        self.data["type"] = "ccc"

        self.accept()

    def set_ignore(self):
        self.ignore_grade = True
        self.data["ignore"] = self.ignore_grade
        self.accept()

    def open_text_file(self):
        path = self.file_path_input.text()
        if os.path.isfile(path):
            os.system("code {}".format(path))
        else:
            QtWidgets.QMessageBox.information(
                hiero.ui.mainWindow(),
                "Info",
                "Can't open file as it doesn't exist on disk",
            )

    def set_data(self):
        file_path = self.file_path_input.text()
        color_ext = file_path.rsplit(".", 1)[-1]
        data = {}
        if color_ext == "edl":
            cdl = {
                "slope": (
                    self.slope_r_input.text(),
                    self.slope_g_input.text(),
                    self.slope_b_input.text(),
                ),
                "offset": (
                    self.offset_r_input.text(),
                    self.offset_g_input.text(),
                    self.offset_b_input.text(),
                ),
                "power": (
                    self.power_r_input.text(),
                    self.power_g_input.text(),
                    self.power_b_input.text(),
                ),
                "sat": self.sat_input.text(),
            }
            data["cdl"] = cdl

        data.update(
            {
                "path": file_path,
                "event": self.entry_number.value(),
                "type": color_ext,
                "ignore": self.ignore_grade,
            }
        )
        self.data = data

    def check_and_accept(self):
        # Test whether or not the new file path gives the proper result and if
        # not then warn user
        color_file_path = self.file_path_input.text()
        color_file_path_ext = color_file_path.rsplit(".", 1)[-1]

        if not os.path.isfile(color_file_path):
            # Make sure that color file is a file on disk
            QtWidgets.QMessageBox.information(
                hiero.ui.mainWindow(),
                "Info",
                "Please make sure the file you selected exists",
            )
            return

        incoming_pattern = r"\/proj\/.*\/incoming"
        incoming_match = re.match(incoming_pattern, color_file_path)
        if not incoming_match:
            # Make sure that color file is a file on disk
            QtWidgets.QMessageBox.information(
                hiero.ui.mainWindow(),
                "Info",
                "Please make sure the file you selected is in show incoming",
            )
            return

        if not color_file_path_ext in COLOR_FILE_EXTS:
            # Make sure that color file is correct
            QtWidgets.QMessageBox.information(
                hiero.ui.mainWindow(),
                "Info",
                "Please make sure the file you selected is a color file type\n"
                "\n'{0}'".format(", ".join(COLOR_FILE_EXTS)),
            )
            return

        self.set_data()
        if self.data["type"] == "edl":
            if (
                self.data["cdl"]["slope"][0] == "NA"
                and color_file_path_ext == "edl"
            ):
                QtWidgets.QMessageBox.information(
                    hiero.ui.mainWindow(),
                    "Info",
                    "No color data found!\n\nIf this shot needs a blank grade "
                    "press 'Blank Grade'",
                )
                return

        self.accept()

    def cancel(self):
        self.data = {}

        self.close()


def get_files(package_path, filters):
    files = {}
    for path in Path(package_path).glob("**/*"):
        # path.suffix has a prefixed . that needs to be matched
        if path.suffix in [f".{f}" for f in filters]:
            files.setdefault(
                path.suffix.replace(".", ""), [path.resolve().__str__()]
            ).append(path.resolve().__str__())

    return files


class CollectColorFile(pyblish.api.InstancePlugin):
    """Collect Color File for plate."""

    order = pyblish.api.CollectorOrder
    label = "Collect Color File"
    families = ["plate"]

    optional = True

    def process(self, instance):
        track_item = instance.data["item"]
        item_name = track_item.name()
        source_name = track_item.source().name()
        source_path = track_item.source().mediaSource().firstpath()
        main_grade = False
        color_file = ""

        # TODO: Check to make sure that the source_path is in incoming
        incoming_pattern = r"\/proj\/.*\/incoming"
        incoming_match = re.match(incoming_pattern, source_path)
        color_info = {}
        if incoming_match:
            priority, cdl, color_file = self.get_color_file(
                source_path, item_name, source_name
            )
            if color_file:
                color_ext = color_file.rsplit(".", 1)[-1]

                if color_ext == "edl":
                    color_info["cdl"] = cdl

                color_info["path"] = color_file
                color_info["type"] = color_ext
                color_info["ignore"] = False

        if not color_file:
            dialog = MissingColorFile(item_name, source_name, source_path, main_grade)
            dialog_result = dialog.exec()
            if dialog_result:
                color_info = dialog.data
            else:
                self.log.critical(
                    "No color file found for plate '{0}'-'{1}'".format(
                        item_name, source_name
                    )
                )

        if not color_info:
            raise Exception("User canceled Color File Collect")

        instance.data["shot_grade"] = color_info
        self.log.info("Collected Color File: {0}".format(color_info))

    def get_color_file(self, source_path, item_name, source_name):
        """Find best guess color file for a given source path"""
        incoming_split = re.split("/incoming/\d+/", source_path)
        # There is no split which means no incoming directory found
        if len(incoming_split) == 1:
            return None, None, None

        package_path_end = incoming_split[1]
        package_name = package_path_end.split("/")[0]
        dated_incoming = source_path.split(package_name)[0]
        package_path = "{0}{1}".format(
            dated_incoming,
            package_name,
        )

        incoming_path = os.path.dirname(
            source_path.split("/" + package_name)[0]
        )
        incoming_packages = [
            d for d in sorted(glob(incoming_path + "/*")) if os.path.isdir(d)
        ]

        # Create Package list
        sorted_incoming = sorted(
            incoming_packages,
            key=lambda x: int(os.path.basename(x))
            if os.path.basename(x).isdigit()
            else int(
                datetime.fromtimestamp(os.path.getctime(x)).strftime("%Y%m%d")
            ),
        )

        # Add current package directory
        if os.path.isfile(package_path):
            package_path = os.path.dirname(package_path)
            if package_path in incoming_packages:
                sorted_incoming.remove(package_path)
            sorted_incoming.insert(0, package_path)

        priority, cdl, color_file = None, None, None
        for path in sorted_incoming:
            color_files = get_files(path, COLOR_FILE_EXTS)

            # Prioritize which color files will be used
            priority, cdl, color_file = self.priority_color_file(
                color_files, item_name, source_name
            )
            if color_file:
                break

        return priority, cdl, color_file

    def priority_color_file(self, color_files, item_name, source_name):
        """Returns the closest matching file from a list of color files given
        an item and a source name.

        Priority is given to the closest match of source_name then item_name as
        well as found file type. The function searches for non-EDL files first,
        followed by EDL files.

        Args:
            color_files (dict): A dictionary containing color files organized
                by type.
            item_name (str): The name of the item to match.
            source_name (str): The name of the source to match.

        Returns:
            tuple or None: A tuple containing the priority level, CDL
                information, and path of the matching color file, or None if no
                matches are found. Priority level is determined based on the
                match between the item and source names as well as the file
                type. CDL information is extracted from the matching file if
                it's not an EDL file.
        """

        source_name = source_name.lower()
        source_name_no_color = source_name
        # Color file may be wack but source name may also be wack
        if (
            source_name_no_color.endswith("_ccc")
            or source_name_no_color.endswith("_cc")
            or source_name_no_color.endswith("_cdl")
        ):
            source_name_no_color = (
                source_name_no_color.replace("_ccc", "")
                .replace("_cc", "")
                .replace("_cdl", "")
            )

        matches = []
        for color_ext in COLOR_FILE_EXTS:
            ext_color_files = color_files.get(color_ext)
            if not ext_color_files:
                continue

            for color_file in ext_color_files:
                # Check non edls first. Sometimes edls don't carry ground truth
                # SOPS
                if color_ext != "edl":
                    # Name match priority
                    priority = None
                    # Remove extension and ignore case
                    color_file_name = os.path.splitext(
                        os.path.basename(color_file)
                    )[0].lower()

                    if source_name == color_file_name:
                        priority = 0
                    elif item_name == color_file_name:
                        priority = 8
                    else:
                        # Incase file name is wack
                        if (
                            color_file_name.endswith("_ccc")
                            or color_file_name.endswith("_cc")
                            or color_file_name.endswith("_cdl")
                        ):
                            color_file_name = (
                                color_file_name.replace("_ccc", "")
                                .replace("_cc", "")
                                .replace("_cdl", "")
                            )
                        if source_name_no_color == color_file_name:
                            priority = 4

                    # Need to compare to None since priority can be 0
                    if priority is not None:
                        # Distinguish type priority
                        if color_ext == "cc":
                            priority += 0
                        elif color_ext == "ccc":
                            priority += 1
                        # EDL is priority += 2
                        elif color_ext == "cdl":
                            priority += 3

                        cdl = phiero.parse_cdl(color_file)
                        matches.append((priority, cdl, color_file))
                else:
                    edits = phiero.parse_edl_events(
                        color_file, color_edits_only=True
                    )
                    if edits is False:
                        self.log.warning("UnicodeDecodeError error. Color "
                                        f"file not be parsed: {color_file}")
                        continue

                    for edit, edl_event in edits["events"].items():
                        priority = None
                        edl_event = edits["events"][edit]
                        cdl = {
                            "slope": edl_event["slope"],
                            "offset": edl_event["offset"],
                            "power": edl_event["power"],
                            "sat": edl_event["sat"],
                        }
                        loc_name = edl_event["LOC"].lower()
                        if source_name == loc_name:
                            priority = 2
                        elif item_name == loc_name:
                            priority = 10

                        if priority:
                            matches.append((priority, cdl, color_file))

        if matches:
            return sorted(matches, key=lambda x: x[0])[0]

        return None, None, None
