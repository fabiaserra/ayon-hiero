"""
Host specific functions where host api is connected
"""

from collections import OrderedDict
from copy import deepcopy
import os
import re
import platform
import functools
import warnings
import json
import ast
import secrets
import hiero

import opentimelineio as otio
from qtpy import QtWidgets, QtCore
import ayon_api
try:
    from PySide import QtXml
except ImportError:
    from PySide2 import QtXml

from ayon_core.settings import get_project_settings
from ayon_core.pipeline import (
    Anatomy,
    get_current_project_name,
    get_current_folder_path,
    AYON_INSTANCE_ID,
    AVALON_INSTANCE_ID,
)
from ayon_core.pipeline.load import filter_containers
from ayon_core.lib import Logger
from . import tags
from .constants import (
    OPENPYPE_TAG_NAME,
    DEFAULT_SEQUENCE_NAME,
    DEFAULT_BIN_NAME
)
from ayon_core.pipeline.context_tools import (
    get_hierarchy_env,
)


class _CTX:
    has_been_setup = False
    has_menu = False
    parent_gui = None


class DeprecatedWarning(DeprecationWarning):
    pass


def deprecated(new_destination):
    """Mark functions as deprecated.

    It will result in a warning being emitted when the function is used.
    """

    func = None
    if callable(new_destination):
        func = new_destination
        new_destination = None

    def _decorator(decorated_func):
        if new_destination is None:
            warning_message = (
                " Please check content of deprecated function to figure out"
                " possible replacement."
            )
        else:
            warning_message = " Please replace your usage with '{}'.".format(
                new_destination
            )

        @functools.wraps(decorated_func)
        def wrapper(*args, **kwargs):
            warnings.simplefilter("always", DeprecatedWarning)
            warnings.warn(
                (
                    "Call to deprecated function '{}'"
                    "\nFunction was moved or removed.{}"
                ).format(decorated_func.__name__, warning_message),
                category=DeprecatedWarning,
                stacklevel=4
            )
            return decorated_func(*args, **kwargs)
        return wrapper

    if func is None:
        return _decorator
    return _decorator(func)


log = Logger.get_logger(__name__)


def flatten(list_):
    for item_ in list_:
        if isinstance(item_, (list, tuple)):
            for sub_item in flatten(item_):
                yield sub_item
        else:
            yield item_


def get_current_project(remove_untitled=False):
    projects = hiero.core.projects()
    if not remove_untitled:
        return projects[0]

    # if remove_untitled
    for proj in projects:
        if "Untitled" in proj.name():
            proj.close()
        else:
            return proj


def get_current_sequence(name=None, new=False):
    """
    Get current sequence in context of active project.

    Args:
        name (str)[optional]: name of sequence we want to return
        new (bool)[optional]: if we want to create new one

    Returns:
        hiero.core.Sequence: the sequence object
    """
    sequence = None
    project = get_current_project()
    root_bin = project.clipsBin()

    if new:
        # create new
        name = name or DEFAULT_SEQUENCE_NAME
        sequence = hiero.core.Sequence(name)
        root_bin.addItem(hiero.core.BinItem(sequence))
    elif name:
        # look for sequence by name
        sequences = project.sequences()
        for _sequence in sequences:
            if _sequence.name() == name:
                sequence = _sequence
        if not sequence:
            # if nothing found create new with input name
            sequence = get_current_sequence(name, True)
    else:
        # if name is none and new is False then return current open sequence
        sequence = hiero.ui.activeSequence()

    return sequence


def get_timeline_selection():
    active_sequence = hiero.ui.activeSequence()
    timeline_editor = hiero.ui.getTimelineEditor(active_sequence)
    return list(timeline_editor.selection())


def get_current_track(sequence, name, audio=False):
    """
    Get current track in context of active project.

    Creates new if none is found.

    Args:
        sequence (hiero.core.Sequence): hiero sequence object
        name (str): name of track we want to return
        audio (bool)[optional]: switch to AudioTrack

    Returns:
        hiero.core.Track: the track object
    """
    tracks = sequence.videoTracks()

    if audio:
        tracks = sequence.audioTracks()

    # get track by name
    track = None
    for _track in tracks:
        if _track.name() == name:
            track = _track

    if not track:
        if not audio:
            track = hiero.core.VideoTrack(name)
        else:
            track = hiero.core.AudioTrack(name)

        sequence.addTrack(track)

    return track


def get_track_items(
        selection=False,
        sequence_name=None,
        track_item_name=None,
        track_name=None,
        track_type=None,
        check_enabled=True,
        check_locked=True,
        check_tagged=False):
    """Get all available current timeline track items.

    Attribute:
        selection (list)[optional]: list of selected track items
        sequence_name (str)[optional]: return only clips from input sequence
        track_item_name (str)[optional]: return only item with input name
        track_name (str)[optional]: return only items from track name
        track_type (str)[optional]: return only items of given type
                                    (`audio` or `video`) default is `video`
        check_enabled (bool)[optional]: ignore disabled if True
        check_locked (bool)[optional]: ignore locked if True

    Return:
        list or hiero.core.TrackItem: list of track items or single track item
    """
    track_type = track_type or "video"
    selection = selection or []
    return_list = []

    # get selected track items or all in active sequence
    if selection:
        try:
            for track_item in selection:
                log.info("___ track_item: {}".format(track_item))
                # make sure only trackitems are selected
                if not isinstance(track_item, hiero.core.TrackItem):
                    continue

                if _validate_all_atrributes(
                    track_item,
                    track_item_name,
                    track_name,
                    track_type,
                    check_enabled,
                    check_tagged
                ):
                    log.info("___ valid trackitem: {}".format(track_item))
                    return_list.append(track_item)
        except AttributeError:
            pass

    # collect all available active sequence track items
    if not return_list:
        sequence = get_current_sequence(name=sequence_name)
        tracks = []
        if sequence is not None:
            # get all available tracks from sequence
            tracks.extend(sequence.audioTracks())
            tracks.extend(sequence.videoTracks())

        # loop all tracks
        for track in tracks:
            if check_locked and track.isLocked():
                continue
            if check_enabled and not track.isEnabled():
                continue
            # and all items in track
            for track_item in track.items():
                # make sure no subtrackitem is also track items
                if not isinstance(track_item, hiero.core.TrackItem):
                    continue

                if _validate_all_atrributes(
                    track_item,
                    track_item_name,
                    track_name,
                    track_type,
                    check_enabled,
                    check_tagged
                ):
                    return_list.append(track_item)

    return return_list


def _validate_all_atrributes(
    track_item,
    track_item_name,
    track_name,
    track_type,
    check_enabled,
    check_tagged
):
    def _validate_correct_name_track_item():
        if track_item_name and track_item_name in track_item.name():
            return True
        elif not track_item_name:
            return True

    def _validate_tagged_track_item():
        if check_tagged and track_item.tags():
            return True
        elif not check_tagged:
            return True

    def _validate_enabled_track_item():
        if check_enabled and track_item.isEnabled():
            return True
        elif not check_enabled:
            return True

    def _validate_parent_track_item():
        if track_name and track_name in track_item.parent().name():
            # filter only items fitting input track name
            return True
        elif not track_name:
            # or add all if no track_name was defined
            return True

    def _validate_type_track_item():
        if track_type == "video" and isinstance(
                track_item.parent(), hiero.core.VideoTrack):
            # only video track items are allowed
            return True
        elif track_type == "audio" and isinstance(
                track_item.parent(), hiero.core.AudioTrack):
            # only audio track items are allowed
            return True

    # check if track item is enabled
    return all([
        _validate_enabled_track_item(),
        _validate_type_track_item(),
        _validate_tagged_track_item(),
        _validate_parent_track_item(),
        _validate_correct_name_track_item()
    ])


def get_track_item_tags(track_item):
    """
    Get track item tags excluded openpype tag

    Attributes:
        trackItem (hiero.core.TrackItem): hiero object

    Returns:
        hiero.core.Tag: hierarchy, orig clip attributes
    """
    returning_tag_data = []
    # get all tags from track item
    _tags = track_item.tags()
    if not _tags:
        return []

    # collect all tags which are not openpype tag
    returning_tag_data.extend(
        tag for tag in _tags
        if tag.name() != OPENPYPE_TAG_NAME
    )

    return returning_tag_data


def _get_tag_unique_hash():
    # sourcery skip: avoid-builtin-shadow
    return secrets.token_hex(nbytes=4)


def set_track_openpype_tag(track, data=None):
    """
    Set openpype track tag to input track object.

    Attributes:
        track (hiero.core.VideoTrack): hiero object

    Returns:
        hiero.core.Tag
    """
    data = data or {}

    # basic Tag's attribute
    tag_data = {
        "editable": "0",
        "note": "OpenPype data container",
        "icon": "openpype_icon.png",
        "metadata": dict(data.items())
    }
    # get available pype tag if any
    _tag = get_track_openpype_tag(track)

    if _tag:
        # it not tag then create one
        tag = tags.update_tag(_tag, tag_data)
    else:
        # if pype tag available then update with input data
        tag = tags.create_tag(
            "{}_{}".format(
                OPENPYPE_TAG_NAME,
                _get_tag_unique_hash()
            ),
            tag_data
        )
        # add it to the input track item
        track.addTag(tag)

    return tag


def get_track_openpype_tag(track):
    """
    Get pype track item tag created by creator or loader plugin.

    Attributes:
        trackItem (hiero.core.TrackItem): hiero object

    Returns:
        hiero.core.Tag: hierarchy, orig clip attributes
    """
    # get all tags from track item
    _tags = track.tags()
    if not _tags:
        return None
    for tag in _tags:
        # return only correct tag defined by global name
        if OPENPYPE_TAG_NAME in tag.name():
            return tag


def get_track_openpype_data(track, container_name=None):
    """
    Get track's openpype tag data.

    Attributes:
        trackItem (hiero.core.VideoTrack): hiero object

    Returns:
        dict: data found on pype tag
    """
    return_data = {}
    # get pype data tag from track item
    tag = get_track_openpype_tag(track)

    if not tag:
        return None

    # get tag metadata attribute
    tag_data = deepcopy(dict(tag.metadata()))

    for obj_name, obj_data in tag_data.items():
        obj_name = obj_name.replace("tag.", "")

        if obj_name in ["applieswhole", "note", "label"]:
            continue
        return_data[obj_name] = json.loads(obj_data)

    return (
        return_data[container_name]
        if container_name
        else return_data
    )


@deprecated("ayon_hiero.api.lib.get_trackitem_openpype_tag")
def get_track_item_pype_tag(track_item):
    # backward compatibility alias
    return get_trackitem_openpype_tag(track_item)


@deprecated("ayon_hiero.api.lib.set_trackitem_openpype_tag")
def set_track_item_pype_tag(track_item, data=None):
    # backward compatibility alias
    return set_trackitem_openpype_tag(track_item, data)


@deprecated("ayon_hiero.api.lib.get_trackitem_openpype_data")
def get_track_item_pype_data(track_item):
    # backward compatibility alias
    return get_trackitem_openpype_data(track_item)


def get_trackitem_openpype_tag(track_item):
    """
    Get pype track item tag created by creator or loader plugin.

    Attributes:
        trackItem (hiero.core.TrackItem): hiero object

    Returns:
        hiero.core.Tag: hierarchy, orig clip attributes
    """
    # get all tags from track item
    _tags = track_item.tags()
    if not _tags:
        return None
    for tag in _tags:
        # return only correct tag defined by global name
        if OPENPYPE_TAG_NAME in tag.name():
            return tag


def set_trackitem_openpype_tag(track_item, data=None):
    """
    Set openpype track tag to input track object.

    Attributes:
        track (hiero.core.VideoTrack): hiero object

    Returns:
        hiero.core.Tag
    """
    data = data or {}

    # basic Tag's attribute
    tag_data = {
        "editable": "0",
        "note": "OpenPype data container",
        "icon": "openpype_icon.png",
        "metadata": dict(data.items())
    }
    # get available pype tag if any
    _tag = get_trackitem_openpype_tag(track_item)
    if _tag:
        # it not tag then create one
        tag = tags.update_tag(_tag, tag_data)
    else:
        # if pype tag available then update with input data
        tag = tags.create_tag(
            "{}_{}".format(
                OPENPYPE_TAG_NAME,
                _get_tag_unique_hash()
            ),
            tag_data
        )
        # add it to the input track item
        track_item.addTag(tag)

    return tag


def get_trackitem_openpype_data(track_item):
    """
    Get track item's pype tag data.

    Attributes:
        trackItem (hiero.core.TrackItem): hiero object

    Returns:
        dict: data found on pype tag
    """
    data = {}
    # get pype data tag from track item
    tag = get_trackitem_openpype_tag(track_item)

    if not tag:
        return None

    # get tag metadata attribute
    tag_data = deepcopy(dict(tag.metadata()))
    # convert tag metadata to normal keys names and values to correct types
    for k, v in tag_data.items():
        key = k.replace("tag.", "")

        try:
            # capture exceptions which are related to strings only
            if re.match(r"^[\d]+$", v):
                value = int(v)
            elif re.match(r"^True$", v):
                value = True
            elif re.match(r"^False$", v):
                value = False
            elif re.match(r"^None$", v):
                value = None
            elif re.match(r"^[\w\d_]+$", v):
                value = v
            else:
                value = ast.literal_eval(v)
        except (ValueError, SyntaxError) as msg:
            log.warning(msg)
            value = v

        data[key] = value

    return data


def imprint(track_item, data=None):
    """
    Adding `Avalon data` into a hiero track item tag.

    Also including publish attribute into tag.

    Arguments:
        track_item (hiero.core.TrackItem): hiero track item object
        data (dict): Any data which needst to be imprinted

    Examples:
        data = {
            'folderPath': '/shots/sq020sh0280',
            'productType': 'render',
            'productName': 'productMain'
        }
    """
    data = data or {}

    tag = set_trackitem_openpype_tag(track_item, data)

    # add publish attribute
    set_publish_attribute(tag, True)


def set_publish_attribute(tag, value):
    """ Set Publish attribute in input Tag object

    Attribute:
        tag (hiero.core.Tag): a tag object
        value (bool): True or False
    """
    tag_data = tag.metadata()
    # set data to the publish attribute
    tag_data.setValue("tag.publish", str(value))


def get_publish_attribute(tag):
    """ Get Publish attribute from input Tag object

    Attribute:
        tag (hiero.core.Tag): a tag object
        value (bool): True or False
    """
    tag_data = tag.metadata()
    # get data to the publish attribute
    value = tag_data.value("tag.publish")
    # return value converted to bool value. Atring is stored in tag.
    return ast.literal_eval(value)


def sync_avalon_data_to_workfile():
    # import session to get project dir
    project_name = get_current_project_name()

    anatomy = Anatomy(project_name)
    work_template = anatomy.get_template_item(
        "work", "default", "path"
    )
    work_root = anatomy.root_value_for_template(work_template)
    active_project_root = (
        os.path.join(work_root, project_name)
    ).replace("\\", "/")
    # getting project
    project = get_current_project()

    if "Tag Presets" in project.name():
        return

    log.debug("Synchronizing Pype metadata to project: {}".format(
        project.name()))

    # set project root with backward compatibility
    try:
        project.setProjectDirectory(active_project_root)
    except Exception:
        # old way of setting it
        project.setProjectRoot(active_project_root)

    # get project data from avalon db
    project_entity = ayon_api.get_project(project_name)
    project_attribs = project_entity["attrib"]

    log.debug("project attributes: {}".format(project_attribs))

    # get format and fps property from avalon db on project
    width = project_attribs["resolutionWidth"]
    height = project_attribs["resolutionHeight"]
    pixel_aspect = project_attribs["pixelAspect"]
    fps = project_attribs["fps"]
    format_name = project_entity["code"]

    # create new format in hiero project
    format = hiero.core.Format(width, height, pixel_aspect, format_name)
    project.setOutputFormat(format)

    # set fps to hiero project
    project.setFramerate(fps)

    # TODO: add auto colorspace set from project drop
    log.info("Project property has been synchronised with Avalon db")


def launch_workfiles_app(event):
    """
    Event for launching workfiles after hiero start

    Args:
        event (obj): required but unused
    """
    from . import launch_workfiles_app
    launch_workfiles_app()


### Starts Alkemy-x Override ###
def set_favorites():
    from ayon_nuke.api.utils import set_context_favorites

    work_dir = os.getenv("AYON_WORKDIR")
    asset = get_current_folder_path()
    favorite_items = OrderedDict()

    project_code = os.getenv("SHOW")

    # project
    # get project's root and split to parts
    projects_root = os.path.normpath(work_dir.split(
        project_code)[0])
    # add project name
    project_dir = os.path.join(projects_root, project_code).replace("\\", "/")
    # No need to add project to hiero favorites as that is a default favorite

    # incoming
    incoming_dir = os.path.join(project_dir, "io/incoming")
    favorite_items.update({"Incoming dir": incoming_dir})

    # outgoing
    outgoing_dir = os.path.join(project_dir, "io/outgoing")
    favorite_items.update({"Outgoing dir": outgoing_dir})

    # asset
    asset_dir = os.path.normpath(work_dir.split(work_dir.split(asset)[-1])[0])
    # add to favorites
    favorite_items.update({"Shot dir": asset_dir.replace("\\", "/")})

    # workdir
    favorite_items.update({"Work dir": work_dir.replace("\\", "/")})

    set_context_favorites(favorite_items)
### Ends Alkemy-x Override ###


def setup(console=False, port=None, menu=True):
    """Setup integration

    Registers Pyblish for Hiero plug-ins and appends an item to the File-menu

    Arguments:
        console (bool): Display console with GUI
        port (int, optional): Port from which to start looking for an
            available port to connect with Pyblish QML, default
            provided by Pyblish Integration.
        menu (bool, optional): Display file menu in Hiero.
    """
    ### Starts Alkemy-x Override ###
    # Add Favorites
    set_favorites()
    ### Ends Alkemy-x Override ###

    if _CTX.has_been_setup:
        teardown()

    add_submission()

    if menu:
        add_to_filemenu()
        _CTX.has_menu = True

    _CTX.has_been_setup = True
    log.debug("pyblish: Loaded successfully.")


def teardown():
    """Remove integration"""
    if not _CTX.has_been_setup:
        return

    if _CTX.has_menu:
        remove_from_filemenu()
        _CTX.has_menu = False

    _CTX.has_been_setup = False
    log.debug("pyblish: Integration torn down successfully")


def remove_from_filemenu():
    raise NotImplementedError("Implement me please.")


def add_to_filemenu():
    PublishAction()


class PyblishSubmission(hiero.exporters.FnSubmission.Submission):

    def __init__(self):
        hiero.exporters.FnSubmission.Submission.__init__(self)

    def addToQueue(self):
        from . import publish
        # Add submission to Hiero module for retrieval in plugins.
        hiero.submission = self
        publish(hiero.ui.mainWindow())


def add_submission():
    registry = hiero.core.taskRegistry
    registry.addSubmission("Pyblish", PyblishSubmission)


class PublishAction(QtWidgets.QAction):
    """
    Action with is showing as menu item
    """

    def __init__(self):
        QtWidgets.QAction.__init__(self, "Publish", None)
        self.triggered.connect(self.publish)

        for interest in ["kShowContextMenu/kTimeline",
                         "kShowContextMenukBin",
                         "kShowContextMenu/kSpreadsheet"]:
            hiero.core.events.registerInterest(interest, self.eventHandler)

        self.setShortcut("Ctrl+Alt+P")

    def publish(self):
        from . import publish
        # Removing "submission" attribute from hiero module, to prevent tasks
        # from getting picked up when not using the "Export" dialog.
        if hasattr(hiero, "submission"):
            del hiero.submission
        publish(hiero.ui.mainWindow())

    def eventHandler(self, event):
        # Add the Menu to the right-click menu
        event.menu.addAction(self)


# def CreateNukeWorkfile(nodes=None,
#                        nodes_effects=None,
#                        to_timeline=False,
#                        **kwargs):
#     ''' Creating nuke workfile with particular version with given nodes
#     Also it is creating timeline track items as precomps.
#
#     Arguments:
#         nodes(list of dict): each key in dict is knob order is important
#         to_timeline(type): will build trackItem with metadata
#
#     Returns:
#         bool: True if done
#
#     Raises:
#         Exception: with traceback
#
#     '''
#     import hiero.core
#     from ayon_nuke.api.lib import (
#         BuildWorkfile,
#         imprint
#     )
#
#     # check if the file exists if does then Raise "File exists!"
#     if os.path.exists(filepath):
#         raise FileExistsError("File already exists: `{}`".format(filepath))
#
#     # if no representations matching then
#     #   Raise "no representations to be build"
#     if len(representations) == 0:
#         raise AttributeError("Missing list of `representations`")
#
#     # check nodes input
#     if len(nodes) == 0:
#         log.warning("Missing list of `nodes`")
#
#     # create temp nk file
#     nuke_script = hiero.core.nuke.ScriptWriter()
#
#     # create root node and save all metadata
#     root_node = hiero.core.nuke.RootNode()
#
#     anatomy = Anatomy(get_current_project_name())
#     work_template = anatomy.get_template_item("work", "default", "path")
#     root_path = anatomy.root_value_for_template(work_template)
#
#     nuke_script.addNode(root_node)
#
#     script_builder = BuildWorkfile(
#         root_node=root_node,
#         root_path=root_path,
#         nodes=nuke_script.getNodes(),
#         **kwargs
#     )


def create_nuke_workfile_clips(nuke_workfiles, seq=None):
    '''
    nuke_workfiles is list of dictionaries like:
    [{
        'path': 'P:/Jakub_testy_pipeline/test_v01.nk',
        'name': 'test',
        'handleStart': 15, # added asymmetrically to handles
        'handleEnd': 10, # added asymmetrically to handles
        "clipIn": 16,
        "frameStart": 991,
        "frameEnd": 1023,
        'task': 'Comp-tracking',
        'work_dir': 'VFX_PR',
        'shot': '00010'
    }]
    '''

    proj = hiero.core.projects()[-1]
    root = proj.clipsBin()

    if not seq:
        seq = hiero.core.Sequence('NewSequences')
        root.addItem(hiero.core.BinItem(seq))
    # todo will need to define this better
    # track = seq[1]  # lazy example to get a destination#  track
    clips_lst = []
    for nk in nuke_workfiles:
        task_path = '/'.join([nk['work_dir'], nk['shot'], nk['task']])
        bin = create_bin(task_path, proj)

        if nk['task'] not in seq.videoTracks():
            track = hiero.core.VideoTrack(nk['task'])
            seq.addTrack(track)
        else:
            track = seq.tracks(nk['task'])

        # create clip media
        media = hiero.core.MediaSource(nk['path'])
        media_in = int(media.startTime() or 0)
        media_duration = int(media.duration() or 0)

        handle_start = nk.get("handleStart")
        handle_end = nk.get("handleEnd")

        if media_in:
            source_in = media_in + handle_start
        else:
            source_in = nk["frameStart"] + handle_start

        if media_duration:
            source_out = (media_in + media_duration - 1) - handle_end
        else:
            source_out = nk["frameEnd"] - handle_end

        source = hiero.core.Clip(media)

        name = os.path.basename(os.path.splitext(nk['path'])[0])
        split_name = split_by_client_version(name)[0] or name

        # add to bin as clip item
        items_in_bin = [b.name() for b in bin.items()]
        if split_name not in items_in_bin:
            binItem = hiero.core.BinItem(source)
            bin.addItem(binItem)

        new_source = [
            item for item in bin.items() if split_name in item.name()
        ][0].items()[0].item()

        # add to track as clip item
        trackItem = hiero.core.TrackItem(
            split_name, hiero.core.TrackItem.kVideo)
        trackItem.setSource(new_source)
        trackItem.setSourceIn(source_in)
        trackItem.setSourceOut(source_out)
        trackItem.setTimelineIn(nk["clipIn"])
        trackItem.setTimelineOut(nk["clipIn"] + (source_out - source_in))
        track.addTrackItem(trackItem)
        clips_lst.append(trackItem)

    return clips_lst


def create_bin(path=None, project=None):
    '''
    Create bin in project.
    If the path is "bin1/bin2/bin3" it will create whole depth
    and return `bin3`

    '''
    # get the first loaded project
    project = project or get_current_project()

    path = path or DEFAULT_BIN_NAME

    path = path.replace("\\", "/").split("/")

    root_bin = project.clipsBin()

    done_bin_lst = []
    for i, b in enumerate(path):
        if i == 0 and len(path) > 1:
            if b in [bin.name() for bin in root_bin.bins()]:
                bin = [bin for bin in root_bin.bins() if b in bin.name()][0]
                done_bin_lst.append(bin)
            else:
                create_bin = hiero.core.Bin(b)
                root_bin.addItem(create_bin)
                done_bin_lst.append(create_bin)

        elif i >= 1 and i < len(path) - 1:
            if b in [bin.name() for bin in done_bin_lst[i - 1].bins()]:
                bin = [
                    bin for bin in done_bin_lst[i - 1].bins()
                    if b in bin.name()
                ][0]
                done_bin_lst.append(bin)
            else:
                create_bin = hiero.core.Bin(b)
                done_bin_lst[i - 1].addItem(create_bin)
                done_bin_lst.append(create_bin)

        elif i == len(path) - 1:
            if b in [bin.name() for bin in done_bin_lst[i - 1].bins()]:
                bin = [
                    bin for bin in done_bin_lst[i - 1].bins()
                    if b in bin.name()
                ][0]
                done_bin_lst.append(bin)
            else:
                create_bin = hiero.core.Bin(b)
                done_bin_lst[i - 1].addItem(create_bin)
                done_bin_lst.append(create_bin)

    return done_bin_lst[-1]


def split_by_client_version(string):
    regex = r"[/_.]v\d+"
    try:
        matches = re.findall(regex, string, re.IGNORECASE)
        return string.split(matches[0])
    except Exception as error:
        log.error(error)
        return None


def get_selected_track_items(sequence=None):
    _sequence = sequence or get_current_sequence()

    # Getting selection
    timeline_editor = hiero.ui.getTimelineEditor(_sequence)
    return timeline_editor.selection()


def set_selected_track_items(track_items_list, sequence=None):
    _sequence = sequence or get_current_sequence()

    # make sure only trackItems are in list selection
    only_track_items = [
        i for i in track_items_list
        if isinstance(i, hiero.core.TrackItem)]

    # Getting selection
    timeline_editor = hiero.ui.getTimelineEditor(_sequence)
    return timeline_editor.setSelection(only_track_items)


def _read_doc_from_path(path):
    # reading QtXml.QDomDocument from HROX path
    hrox_file = QtCore.QFile(path)
    if not hrox_file.open(QtCore.QFile.ReadOnly):
        raise RuntimeError("Failed to open file for reading")
    doc = QtXml.QDomDocument()
    doc.setContent(hrox_file)
    hrox_file.close()
    return doc


def _write_doc_to_path(doc, path):
    # write QtXml.QDomDocument to path as HROX
    hrox_file = QtCore.QFile(path)
    if not hrox_file.open(QtCore.QFile.WriteOnly):
        raise RuntimeError("Failed to open file for writing")
    stream = QtCore.QTextStream(hrox_file)
    doc.save(stream, 1)
    hrox_file.close()


def _set_hrox_project_knobs(doc, **knobs):
    # set attributes to Project Tag
    proj_elem = doc.documentElement().firstChildElement("Project")
    for k, v in knobs.items():
        if "ocioconfigpath" in k:
            paths_to_format = v[platform.system().lower()]
            for _path in paths_to_format:
                v = _path.format(**os.environ)
                if not os.path.exists(v):
                    continue
        log.debug("Project colorspace knob `{}` was set to `{}`".format(k, v))
        if isinstance(v, dict):
            continue
        proj_elem.setAttribute(str(k), v)


def apply_colorspace_project():
    """Apply colorspaces from settings.

    Due to not being able to set the project settings through the Python API,
    we need to do use some dubious code to find the widgets and set them. It is
    possible to set the project settings without traversing through the widgets
    but it involves reading the hrox files from disk with XML, so no in-memory
    support. See https://community.foundry.com/discuss/topic/137771/change-a-project-s-default-color-transform-with-python  # noqa
    for more details.
    """
    ### Starts Alkemy-X Override ###
    # we rely on setting $OCIO
    return
    ### Ends Alkemy-X Override ###

    # get presets for hiero
    project_name = get_current_project_name()
    imageio = get_project_settings(project_name)["hiero"]["imageio"]
    presets = imageio.get("workfile")

    # Open Project Settings UI.
    for act in hiero.ui.registeredActions():
        if act.objectName() == "foundry.project.settings":
            act.trigger()

    # Find widgets from their sibling label.
    labels = {
        "Working Space:": "workingSpace",
        "Viewer:": "viewerLut",
        "Thumbnails:": "thumbnailLut",
        "Monitor Out:": "monitorOutLut",
        "8 Bit Files:": "eightBitLut",
        "16 Bit Files:": "sixteenBitLut",
        "Log Files:": "logLut",
        "Floating Point Files:": "floatLut"
    }
    widgets = {x: None for x in labels.values()}

    def _recursive_children(widget, labels, widgets):
        children = widget.children()
        for count, child in enumerate(children):
            if isinstance(child, QtWidgets.QLabel):
                if child.text() in labels.keys():
                    widgets[labels[child.text()]] = children[count + 1]
            _recursive_children(child, labels, widgets)

    app = QtWidgets.QApplication.instance()
    title = "Project Settings"
    for widget in app.topLevelWidgets():
        if isinstance(widget, QtWidgets.QMainWindow):
            if widget.windowTitle() != title:
                continue
            _recursive_children(widget, labels, widgets)
            widget.close()

    msg = "Setting value \"{}\" is not a valid option for \"{}\""
    for key, widget in widgets.items():
        options = [widget.itemText(i) for i in range(widget.count())]
        setting_value = presets[key]
        assert setting_value in options, msg.format(setting_value, key)
        widget.setCurrentText(presets[key])

    # This code block is for setting up project colorspaces for files on disk.
    # Due to not having Python API access to set the project settings, the
    # Foundry recommended way is to modify the hrox files on disk with XML. See
    # this forum thread for more details;
    # https://community.foundry.com/discuss/topic/137771/change-a-project-s-default-color-transform-with-python  # noqa
    '''
    # backward compatibility layer
    # TODO: remove this after some time
    config_data = get_current_context_imageio_config_preset()

    if config_data:
        presets.update({
            "ocioConfigName": "custom"
        })

    # get path the the active projects
    project = get_current_project()
    current_file = project.path()

    msg = "The project needs to be saved to disk to apply colorspace settings."
    assert current_file, msg

    # save the workfile as subversion "comment:_colorspaceChange"
    split_current_file = os.path.splitext(current_file)
    copy_current_file = current_file

    if "_colorspaceChange" not in current_file:
        copy_current_file = (
            split_current_file[0]
            + "_colorspaceChange"
            + split_current_file[1]
        )

    try:
        # duplicate the file so the changes are applied only to the copy
        shutil.copyfile(current_file, copy_current_file)
    except shutil.Error:
        # in case the file already exists and it want to copy to the
        # same filewe need to do this trick
        # TEMP file name change
        copy_current_file_tmp = copy_current_file + "_tmp"
        # create TEMP file
        shutil.copyfile(current_file, copy_current_file_tmp)
        # remove original file
        os.remove(current_file)
        # copy TEMP back to original name
        shutil.copyfile(copy_current_file_tmp, copy_current_file)
        # remove the TEMP file as we dont need it
        os.remove(copy_current_file_tmp)

    # use the code from below for changing xml hrox Attributes
    presets.update({"name": os.path.basename(copy_current_file)})

    # read HROX in as QDomSocument
    doc = _read_doc_from_path(copy_current_file)

    # apply project colorspace properties
    _set_hrox_project_knobs(doc, **presets)

    # write QDomSocument back as HROX
    _write_doc_to_path(doc, copy_current_file)

    # open the file as current project
    hiero.core.openProject(copy_current_file)
    '''


def apply_colorspace_clips():
    project_name = get_current_project_name()
    project = get_current_project(remove_untitled=True)
    clips = project.clips()

    # get presets for hiero
    imageio = get_project_settings(project_name)["hiero"]["imageio"]

    presets = imageio.get("regexInputs", {}).get("inputs", {})
    for clip in clips:
        clip_media_source_path = clip.mediaSource().firstpath()
        clip_name = clip.name()
        clip_colorspace = clip.sourceMediaColourTransform()

        if "default" in clip_colorspace:
            continue

        # check if any colorspace presets for read is matching
        preset_clrsp = None
        for k in presets:
            if not bool(re.search(k["regex"], clip_media_source_path)):
                continue
            preset_clrsp = k["colorspace"]

        if preset_clrsp:
            log.debug("Changing clip.path: {}".format(clip_media_source_path))
            log.info("Changing clip `{}` colorspace {} to {}".format(
                clip_name, clip_colorspace, preset_clrsp))
            # set the found preset to the clip
            clip.setSourceMediaColourTransform(preset_clrsp)

    # save project after all is changed
    project.save()


def is_overlapping(ti_test, ti_original, strict=False):
    covering_exp = (
        (ti_test.timelineIn() <= ti_original.timelineIn())
        and (ti_test.timelineOut() >= ti_original.timelineOut())
    )

    if strict:
        return covering_exp

    inside_exp = (
        (ti_test.timelineIn() >= ti_original.timelineIn())
        and (ti_test.timelineOut() <= ti_original.timelineOut())
    )
    overlaying_right_exp = (
        (ti_test.timelineIn() < ti_original.timelineOut())
        and (ti_test.timelineOut() >= ti_original.timelineOut())
    )
    overlaying_left_exp = (
        (ti_test.timelineOut() > ti_original.timelineIn())
        and (ti_test.timelineIn() <= ti_original.timelineIn())
    )

    return any((
        covering_exp,
        inside_exp,
        overlaying_right_exp,
        overlaying_left_exp
    ))


def get_sequence_pattern_and_padding(file):
    """ Return sequence pattern and padding from file

    Attributes:
        file (string): basename form path

    Example:
        Can find file.0001.ext, file.%02d.ext, file.####.ext

    Return:
        string: any matching sequence pattern
        int: padding of sequence numbering
    """
    foundall = re.findall(
        r"(#+)|(%\d+d)|(?<=[^a-zA-Z0-9])(\d+)(?=\.\w+$)", file)
    if not foundall:
        return None, None
    found = sorted(list(set(foundall[0])))[-1]

    padding = int(
        re.findall(r"\d+", found)[-1]) if "%" in found else len(found)
    return found, padding


def sync_clip_name_to_data_asset(track_items_list):
    # loop through all selected clips
    for track_item in track_items_list:
        # ignore if parent track is locked or disabled
        if track_item.parent().isLocked():
            continue
        if not track_item.parent().isEnabled():
            continue
        # ignore if the track item is disabled
        if not track_item.isEnabled():
            continue

        # get name and data
        ti_name = track_item.name()
        data = get_trackitem_openpype_data(track_item)

        # ignore if no data on the clip or not publish instance
        if not data:
            continue
        if data.get("id") not in {
            AYON_INSTANCE_ID, AVALON_INSTANCE_ID
        }:
            continue

        # fix data if wrong name
        if data["asset"] != ti_name:
            data["asset"] = ti_name
            # remove the original tag
            tag = get_trackitem_openpype_tag(track_item)
            track_item.removeTag(tag)
            # create new tag with updated data
            set_trackitem_openpype_tag(track_item, data)
            print("asset was changed in clip: {}".format(ti_name))


def set_track_color(track_item, color):
    track_item.source().binItem().setColor(color)


def check_inventory_versions(track_items=None):
    """
    Actual version color identifier of Loaded containers

    Check all track items and filter only
    Loader nodes for its version. It will get all versions from database
    and check if the node is having actual version. If not then it will color
    it to red.
    """
    from . import parse_container

    track_items = track_items or get_track_items()
    # presets
    clip_color_last = "green"
    clip_color = "red"

    containers = []
    # Find all containers and collect it's node and representation ids
    for track_item in track_items:
        container = parse_container(track_item)
        if container:
            containers.append(container)

    # Skip if nothing was found
    if not containers:
        return

    project_name = get_current_project_name()
    filter_result = filter_containers(containers, project_name)
    for container in filter_result.latest:
        set_track_color(container["_item"], clip_color_last)

    for container in filter_result.outdated:
        set_track_color(container["_item"], clip_color)


def selection_changed_timeline(event):
    """Callback on timeline to check if asset in data is the same as clip name.

    Args:
        event (hiero.core.Event): timeline event
    """
    timeline_editor = event.sender
    selection = timeline_editor.selection()

    track_items = get_track_items(
        selection=selection,
        track_type="video",
        check_enabled=True,
        check_locked=True,
        check_tagged=True
    )

    # run checking function
    sync_clip_name_to_data_asset(track_items)


def before_project_save(event):
    track_items = get_track_items(
        track_type="video",
        check_enabled=True,
        check_locked=True,
        check_tagged=True
    )

    # run checking function
    sync_clip_name_to_data_asset(track_items)

    # also mark old versions of loaded containers
    check_inventory_versions(track_items)


def get_main_window():
    """Acquire Nuke's main window"""
    if _CTX.parent_gui is None:
        top_widgets = QtWidgets.QApplication.topLevelWidgets()
        name = "Foundry::UI::DockMainWindow"
        main_window = next(widget for widget in top_widgets if
                           widget.inherits("QMainWindow") and
                           widget.metaObject().className() == name)
        _CTX.parent_gui = main_window
    return _CTX.parent_gui


### Starts Alkemy-X Override ###
def regex_parse_edl_events(path, color_edits_only=False):
    """
    EDL is parsed using OTIO and then placed into a data struture for output "edl"
    Data is stored under the understanding that it will be used for identifying plate names which are linked to CDLs

    EDL LOC metadata is parsed with OTIO under markers and then regexed to find the main bit of information which
    will link it to a plate name further down the line

    Underscores are not counted ({0,}) but are left greedy incase naming doesn't follow normal shot convention
    but instead follows plate pull naming convention.
    Example: abc_101_010_010_element
    Example: abc_101_010_010_element_fire

    Examples of targeted matches:
    abc_101_010_010_element
    abc_101_010_010
    abc_101_010
    101_010_010

    Examples of what does not match:
    abc_101
    101_001
    _abc_101_010
    abc_101_010_
    """
    # Define regex patterns
    edit_pattern = r"(?<=[\n\r])(?P<edit>\d+\s+[\s\S]*?)(?=([\n\r]+\d+)|\Z)"
    sop_pattern = r"[*]\s?ASC[_]SOP\s+[(]\s?(?P<sR>[-]?\d+[.]\d{4,6})\s+(?P<sG>[-]?\d+[.]\d{4,6})\s+(?P<sB>[-]?\d+[.]\d{4,6})\s?[)]\s?[(]\s?(?P<oR>[-]?\d+[.]\d{4,6})\s+(?P<oG>[-]?\d+[.]\d{4,6})\s+(?P<oB>[-]?\d+[.]\d{4,6})\s?[)]\s?[(]\s?(?P<pR>[-]?\d+[.]\d{4,6})\s+(?P<pG>[-]?\d+[.]\d{4,6})\s+(?P<pB>[-]?\d+[.]\d{4,6})\s?[)]\s?"
    sat_pattern = r"[*]\s?ASC_SAT\s+(?P<sat>\d+[.]*\d*)"
    tape_pattern = r"\d+\s*(?P<source>[\S]*)(?=\s*)"
    clip_name_pattern = r"[*]\s?FROM[ ]*CLIP[ ]*NAME:\s*(?P<clip_name>.+)"
    loc_pattern = r"[*]\s?LOC:\s?.+\b(?<!_)(?P<LOC>[\w]{3,4}_((?<=_)[\w]{3,4}_){1,2}[\w]{3,4}(?<!_)(_[\w]{1,}){0,})\b"

    with open(path, "r") as f:
        edl_data = f.read()

    # Need to find first entry in edit list for range
    first_match = re.search(edit_pattern, edl_data)
    first_entry = int(first_match.group().split(" ", 1)[0]) if first_match else 1

    edl = {"events": {}}
    for edit_match in re.finditer(edit_pattern, edl_data):
        slope, offset, power, sat = None, None, None, None

        edit_value = edit_match.group("edit")
        # Determine if color data is present in event and store it
        sop_match = re.search(sop_pattern, edit_value)
        if sop_match:
            slope, offset, power = (
                tuple(map(float, (sop_match.group("sR"), sop_match.group("sG"), sop_match.group("sB")))),
                tuple(map(float, (sop_match.group("oR"), sop_match.group("oG"), sop_match.group("oB")))),
                tuple(map(float, (sop_match.group("pR"), sop_match.group("pG"), sop_match.group("pB")))),
            )

        # Always record even numbers
        entry = str(int(edit_value.split(" ", 1)[0]))
        # edl["entries"].append(entry)

        # Clip Name value
        clip_name_match = re.search(clip_name_pattern, edit_value)
        clip_name_value = clip_name_match.group("clip_name") if clip_name_match else ""

        # Tape value
        tape_match = re.search(tape_pattern, edit_value)
        tape_value = tape_match.group("source") if tape_match else ""

        # LOC value
        loc_match = re.search(loc_pattern, edit_value)
        loc_value = loc_match.group("LOC") if loc_match else ""


        # Do rest of regex find if color data was found.
        if not (slope is None and offset is None and power is None):
            # Sat value doesn't need to be found. If not found default to 1
            sat_match = re.search(sat_pattern, edit_value)
            sat = sat_match.group("sat") if sat_match else 1

            edl["events"][entry] = {
                "tape": tape_value,
                "clip_name": clip_name_value,
                "LOC": loc_value,
                "slope": slope,
                 "offset": offset,
                 "power": power,
                 "sat": sat,
                 }
        else:
            if not color_edits_only:
                edl["events"][entry] = {
                    "tape": tape_value,
                    "clip_name": clip_name_value,
                    "LOC": loc_value,
                }

    # Add last found entry from edit list iteration
    last_entry = int(edit_value.split(" ", 1)[0])

    # Finish EDL info
    edl["first_entry"] = first_entry
    edl["last_entry"] = last_entry

    return edl


def otio_parse_edl_events(path, color_edits_only=False):
    """EDL is parsed using OTIO and then placed into a dictionary

    Data is stored under the understanding that it will be used for identifying
    plate names which are linked to CDLs.

    EDL LOC metadata is parsed with OTIO under markers and then regexed to find
    the main bit of information which will link it to a plate name further down
    the line.

    Underscores are not counted ({0,}) but are left greedy incase naming
    doesn't follow normal shot convention but instead follows plate pull naming
    convention.

    Plate pull naming convention:
    Example: abc_101_010_010
    Example: abc_101_010_010_element
    Example: abc_101_010_010_element_fire

    Examples of targeted matches:
    abc_101_010_010_element
    abc_101_010_010
    abc_101_010
    101_010_010

    Examples of what does not match:
    abc_101
    101_001
    _abc_101_010
    abc_101_010_

    Args:
        path (str): The path of the EDL file.
        color_edits_only (bool, optional): Whether to include only color edits.
            Defaults to False.

    Returns:
        dict: A dictionary containing information about the events in the EDL
            file.
            - "events" (dict): where the key is the event number and the value
                is a dictionary containing information about the event.
                - "clip_name" (str): clip name
                - "tape" (str): tape name,
                - "LOC" (str): metadata,

                CDL information (if applicable)
                - "slope" (tuple): CDL Slope.
                - "offset (tuple): CDL Offset.
                - "power" (tuple): CDL Power.
                - "sat" (float): CDL Saturation.

            - "first_entry" (int): The number of the first event in the EDL.
            - "last_entry" (int): The number of the last event in the EDL.

    Raises:
        Exception: If the EDL file contains more than one track.
    """
    shot_pattern = r"(?<!_)(?P<LOC>[a-zA-Z0-9]{3,4}_((?<=_)[a-zA-Z0-9]{3,4}_)"\
        "{1,2}[a-zA-Z0-9]{3,4}(?<!_)(_[a-zA-Z0-9]{1,}){0,})\b"

    # ignore_timecode_mismatch is set to True to ensure that OTIO doesn't get
    # confused by TO CLIP NAME and FROM CLIP NAME timecode ranges
    timeline = otio.adapters.read_from_file(
        path, ignore_timecode_mismatch=True
    )

    if len(timeline.tracks) > 1:
        raise Exception(
            "EDL '%s' can not contain more than one track. Something went wrong" % path
        )

    edl = {"events": {}}

    # There is a possibility that the entry count doesn't start at 1.
    # However it's extremely rare and it's purpose is to keep track of order
    entry_count = 0
    for clip in timeline.tracks[0].each_child():
        if not isinstance(clip, otio.schema.Clip):
            continue

        entry_count += 1
        loc_value = ""
        tape_value = ""
        entry = {"clip_name": clip.name}
        cdl = clip.metadata.get("cdl")
        if cdl:
            entry.update(
                {
                    "slope": tuple(cdl["asc_sop"]["slope"]),
                    "offset": tuple(cdl["asc_sop"]["offset"]),
                    "power": tuple(cdl["asc_sop"]["power"]),
                    "sat": cdl.get("asc_sat") or 1.0,
                }
            )
        elif color_edits_only:
            continue

        if clip.markers:
            # Join markers (*LOC). The data is strictly being stored to parse
            # shot name Space is added to make parsing much easier and
            # predictable
            full_clip_loc = " ".join([" "] + [m.name for m in clip.markers])
            loc_match = re.search(shot_pattern, full_clip_loc)
            loc_value = loc_match.group("LOC") if loc_match else ""

        # Capture tape and source name
        cmx_3600 = clip.metadata.get("cmx_3600")
        if cmx_3600:
            tape_value = cmx_3600.get("reel", "")

        entry.update(
            {
                "tape": tape_value,
                "LOC": loc_value,
            }
        )
        edl["events"][entry_count] = entry

    # Finish EDL info
    edl["first_entry"] = 1
    edl["last_entry"] = entry_count

    return edl


def parse_edl_events(color_file, color_edits_only=True):
    try:
        edl = otio_parse_edl_events(color_file, color_edits_only)

    except UnicodeDecodeError:
        return False

    except:
        # Try using regex to parse if OTIO fails
        try:
            edl = regex_parse_edl_events(color_file, color_edits_only)
        except:
            return False

    return edl


def parse_cdl(path):
    """Parses cdl formatted xml using regex to find Slope, Offset, Power, and
        Saturation.

    Args:
        path (str): The path to a grade file that follows ASC xml formatting.

    Returns:
        dict: A dictionary containing the parsed slope, offset, power, and
            saturation values, as well as the path to the file.
    """

    with open(path, "r") as f:
        cdl_data = f.read().lower()

    slope_pattern = r"<slope>(?P<sR>[-,\d,.]*)[ ]{1}(?P<sG>[-,\d,.]+)[ ]{1}(?P<sB>[-,\d,.]*)</slope>"
    offset_pattern = r"<offset>(?P<oR>[-,\d,.]*)[ ]{1}(?P<oG>[-,\d,.]+)[ ]{1}(?P<oB>[-,\d,.]*)</offset>"
    power_pattern = r"<power>(?P<pR>[-,\d,.]*)[ ]{1}(?P<pG>[-,\d,.]+)[ ]{1}(?P<pB>[-,\d,.]*)</power>"
    sat_pattern = r"<saturation\>(?P<sat>[-,\d,.]+)</saturation\>"

    slope_match = re.search(slope_pattern, cdl_data)
    if slope_match:
        slope = tuple(map(float, slope_match.groups()))
    else:
        slope = None

    offset_match = re.search(offset_pattern, cdl_data)
    if offset_match:
        offset = tuple(map(float, offset_match.groups()))
    else:
        offset = None

    power_match = re.search(power_pattern, cdl_data)
    if power_match:
        power = tuple(map(float, power_match.groups()))
    else:
        power = None

    sat_match = re.search(sat_pattern, cdl_data)
    sat = float(sat_match.group("sat")) if sat_match else None

    cdl = {
        "slope": slope,
        "offset": offset,
        "power": power,
        "sat": sat,
        "file": path,
    }

    return cdl


def get_main_ref_track(track_item=None):
    if track_item:
        sequence = track_item.sequence()
    else:
        sequence = hiero.ui.activeSequence()

    video_tracks = sequence.videoTracks()
    for track in video_tracks:
        if track.name() == "edit_ref":
            return track

    return None


class MainPlate():
    def sequence(function):
        """Decorator class funtion that ensures that the conditions needed to
        operate main plate changes are met
        """
        def wrapper(self, *args, **kwargs):
            # Need to know the main track in order to perform operations
            if not self.main_track and self.track_items:
                return None

            result = function(self, *args, **kwargs)

            return result

        return wrapper

    def get_track_index(self=None, track=None):
        video_tracks = hiero.ui.activeSequence().videoTracks()
        track_index = video_tracks.index(track)

        return track_index

    def get_main_plate_track(self=None):
        main_plate_track = None
        sequence = hiero.ui.activeSequence()
        for track in sequence.videoTracks():
            if not "ref" in track.name():
                main_plate_track = track
                break

        return main_plate_track

    def get_plate_tracks(self=None):
        if not self:
            main_plate = MainPlate.get_main_plate_track()
        else:
            main_plate = self.main_track

        video_tracks = hiero.ui.activeSequence().videoTracks()
        main_plate_index = MainPlate.get_track_index(track=main_plate)
        plate_tracks = video_tracks[main_plate_index:]

        return plate_tracks

    def get_ref_tracks(self=None):
        if not self:
            main_plate = MainPlate.get_main_plate_track()
        else:
            main_plate = self.main_track

        video_tracks = hiero.ui.activeSequence().videoTracks()
        main_plate_index = MainPlate.get_track_index(track=main_plate)
        plate_tracks = video_tracks[:main_plate_index]

        return plate_tracks

    def __init__(self):
        # Should main grade be a track index instead?
        self.main_track = self.get_main_plate_track()
        self.track_plate_items = self.get_plate_track_items()
        self.track_ref_items = self.get_ref_track_items()

    def get_plate_track_items(self):
        track_items = []
        for track in self.get_plate_tracks():
            track_items.extend(track.items())

        return track_items

    def get_ref_track_items(self):
        track_items = []
        for track in self.get_ref_tracks():
            track_items.extend(track.items())

        return track_items

    @sequence
    def set_track_item_main_plate(self, track_item):
        shot_name = track_item.name()
        shot_items = [track_item for track_item in self.track_plate_items if track_item.name() == shot_name]
        main_plate_tags_to_remove = []
        has_main_track = False
        # Check to see if a item of the shot is on main grade track
        # If so then if no override set that track item as main
        if self.main_track.name() == shot_items[0].parent().name():
            has_main_track = True

        remove_default_tags = False
        # track items need to be sorted by track index
        # sorted(shot_items, key=lambda item: item MainPlate.get_track_index(item))
        for shot_item in shot_items:
            main_plate_tag = shot_item.get_main_plate()
            # If override found leave shot altogether
            if not main_plate_tag:
                continue
            if "override" in main_plate_tag.metadata():
                remove_default_tags = True
            else:
                # Default tag list. Main track will never be added
                main_plate_tags_to_remove.append((shot_item, main_plate_tag))


        # Remove default tags needs to inlude potentially old plate tracks
        for track_ref_item in self.track_ref_items:
            if track_ref_item.name() == shot_name:
                main_plate_tag = track_ref_item.get_main_plate()
                if not main_plate_tag:
                    continue
                main_plate_tags_to_remove.append((track_ref_item, main_plate_tag))

        # Set default tag on main track item
        if has_main_track and not remove_default_tags:
            main_track_tag = (shot_items[0], shot_items[0].get_main_plate())

            # Remove main grade from remove list and add tag if not found and no override
            tagged_tracks = [tag[0].parent() for tag in main_plate_tags_to_remove]
            if shot_items[0].parent() in tagged_tracks:
                main_plate_tags_to_remove.pop(0)
            else:
                shot_items[0].set_main_plate(default_tag=True, spreadsheet=False)

        for track_item, tag in main_plate_tags_to_remove:
            # Sometimes the track doesn't update
            try:
                track_item.removeTag(tag)
            except RuntimeError:
                pass

    @sequence
    def set_track_main_plates(self):
        # Main grades will only ever be set on main grade track
        for track_item in self.main_track.items():
            self.set_track_item_main_plate(track_item)


def is_valid_folder(track_item):
    """Check if the given folder name is valid for the current project.

    Args:
        asset_name (str): The name of the asset to validate.

    Returns:
        dict: The folder entity if found, otherwise an empty dictionary.
    """
    # Track item may not have ran through callback to is valid attr
    if "hierarchy_env" in track_item.__dir__():
        return track_item.hierarchy_env

    project_name = get_current_project_name()
    current_folder = ayon_api.get_folder_by_name(project_name, track_item.name())
    if current_folder:
        return True
    
    return False


def get_entity_hierarchy(folder_entity, project_name):
    """Retrieve entity links for the given asset.

    This function creates a dictionary of linked entities for the specified
    asset. The linked entities may include:
    - episode
    - sequence
    - shot
    - folder

    Args:
        asset_name (str): The name of the asset.

    Returns:
        dict: A dictionary containing linked entities, including episode,
                sequence, shot, and folder information.
    """
    project_entity = ayon_api.get_project(project_name)
    hierarchy_env = get_hierarchy_env(project_entity, folder_entity)

    folder_entities = {}
    episode = hierarchy_env.get("EPISODE")
    if episode:
        folder_entities["episode"] = episode

    sequence = hierarchy_env.get("SEQ")
    if sequence:
        folder_entities["sequence"] = sequence

    asset = hierarchy_env.get("SHOT")
    if asset:
        folder_entities["shot"] = asset

    if hierarchy_env.get("ASSET_TYPE"):
        folder = "asset"
    else:
        folder = "shots"
    folder_entities["folder"] = folder

    return folder_entities


def get_hierarchy_data(folder_entity, project_name, track_name):
    hierarchy_data = get_entity_hierarchy(folder_entity, project_name)
    hierarchy_data["track"] = track_name

    return hierarchy_data


def get_hierarchy_path(folder_entity):
    """Asset path is always the joining of the asset parents"""
    hierarchy_path = os.sep.join(folder_entity["data"]["parents"])

    return hierarchy_path


def get_hierarchy_parents(hierarchy_data):
    parents = []
    parents_types = ["folder", "episode", "sequence"]
    for key, value in hierarchy_data.items():
        if key in parents_types:
            entity = {"folder_type": key, "entity_name": value}
            parents.append(entity)

    return parents


def set_framing_info(cut_info, track_item):
    # Only reference will update cut info to SG
    cut_info["cut_in"] = int(cut_info["cut_in"])
    # cut info will almost always exist except for old style tags
    if cut_info.get("cut_range", "True") == "False":
        cut_info["head_handles"] = int(track_item.handleInLength())
        cut_info["tail_handles"] = int(track_item.handleOutLength())
    else:
        cut_info["head_handles"] = int(cut_info["head_handles"])
        cut_info["tail_handles"] = int(cut_info["tail_handles"])

    # Cut out is always cut_in + duration - 1
    cut_out = cut_info["cut_in"] + track_item.duration() - 1
    cut_info["cut_out"] = cut_out

    return cut_info


def create_ayon_instance(track_item):
    """
    Only one key of the tag can be modified at a time for items that already
    have a tag.
    """
    track_item_name = track_item.name()
    track_name = track_item.parentTrack().name()

    ingest_instance_data = track_item.ingest_instance_data()
    cut_info_data = track_item.cut_info_data()
    if not cut_info_data:
        log.info(
            f"{track_item.parent().name()}.{track_item.name()}: "
            "No cut info tag!"
        )
        return "No cut info tag"

    if not ingest_instance_data:
        log.info(
            f"{track_item.parent().name()}.{track_item.name()}: "
            "No ingest data tag!"
        )

        return "No ingest data tag"

    # Check if asset has valid name
    if not is_valid_folder(track_item):
        log.info(
            f"{track_item.parent().name()}.{track_item.name()}: "
            "Track item name not found in DB!"
        )

        return "Shot not found in DB"

    else:
        project_name = get_current_project_name()
        folder_entity = ayon_api.get_folder_by_name(project_name, track_item.name())

    instance_data = {}

    family = ingest_instance_data["family"]
    families = ["clip"]
    if family == "plate":
        families.append("review")

    use_nuke = ingest_instance_data["use_nuke"]

    hierarchy_data = get_hierarchy_data(
        folder_entity, project_name, track_name
    )
    hierarchy_parents = get_hierarchy_parents(hierarchy_data)
    hierarchy_path = folder_entity["path"]

    # Framing comes from tag
    cut_info = set_framing_info(cut_info_data, track_item)
    instance_data["cut_info_data"] = cut_info

    frame_start = cut_info["cut_in"] - cut_info["head_handles"]
    handle_start = cut_info["head_handles"]
    handle_end = cut_info["tail_handles"]

    main_plate = "True" if track_item.get_main_plate() else "False"
    main_ref = "True" if get_main_ref_track(track_item) == track_item.parent() else "False"

    instance_data["hierarchyData"] = hierarchy_data
    instance_data["hierarchy"] = hierarchy_path
    instance_data["parents"] = hierarchy_parents
    instance_data["folderPath"] = hierarchy_path
    instance_data["folder"] = track_item_name
    instance_data["asset_name"] = track_item_name
    instance_data["productName"] = track_name
    instance_data["productType"] = family
    instance_data["family"] = family
    instance_data["families"] = str(families)
    instance_data["workfileFrameStart"] = frame_start
    instance_data["handleStart"] = handle_start
    instance_data["handleEnd"] = handle_end
    instance_data["main_plate"] = main_plate
    instance_data["main_ref"] = main_ref
    instance_data["use_nuke"] = use_nuke

    # Constants
    instance_data["audio"] = "True"
    instance_data["heroTrack"] = "True"
    instance_data["id"] = "pyblish.avalon.instance"
    instance_data["publish"] = "True"
    instance_data["reviewTrack"] = "None"
    instance_data["sourceResolution"] = "False"
    instance_data["variant"] = "Main"
    instance_data["ingested_grade"] = "None"

    tag = set_trackitem_openpype_tag(track_item, instance_data)
    return True if tag else "AYON tag couldn't be added"
### Ends Alkemy-X Override ###
