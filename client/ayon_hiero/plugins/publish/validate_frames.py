import glob
import os
import pyblish.api

from ayon_core.pipeline import (
    registered_root
)


class ValidateFrames(pyblish.api.InstancePlugin):
    """A Pyblish plugin for validating the frame range of a plate or reference instance.

    Attributes:
        order (float): The order of this plugin in the Pyblish instance processing pipeline.
        label (str): A human-readable label for this plugin.
        hosts (list): A list of DCC applications where this plugin is supported.
        families (list): A list of families to which this plugin applies.
    """

    order = pyblish.api.ValidatorOrder
    label = "Validate Frames"
    hosts = ["hiero"]
    families = ["plate", "reference"]

    def process(self, instance):
        """Validates the frame range of a ingest media.
        Three checks are performed:
            - Zero byte frames
            - Frames are sequential
            - Clip media is present for render range

        Args:
            instance (pyblish.api.Instance): The instance to process.

        Raises:
            Exception: The status of the three checks

        Returns:
            None
        """

        self.track_item = instance.data["item"]

        media_source = self.track_item.source().mediaSource()
        input_path = media_source.fileinfos()[0].filename()
        input_dir = os.path.dirname(input_path)
        padding_length = media_source.filenamePadding()
        self.frame_head = media_source.filenameHead()
        source_ext = os.path.splitext(input_path)[1][1:]
        self.media_start = media_source.fileinfos()[0].startFrame()
        self.media_end = media_source.fileinfos()[0].endFrame()
        # Have to look to disk for actual range of frames
        self.frame_paths = sorted(glob.glob(
            f"{input_dir}/{self.frame_head}{'[0-9]' * padding_length}.{source_ext}"))

        # If the clip extends beyond where media actually starts or ends
        missing_media_frames = self.get_missing_media_frames(instance)
        for missing_output_frame, missing_input_frame in missing_media_frames:
            self.log.critical(
                "Frame out of range of source: '%d' - Source frame '%d'" % (
                    missing_output_frame, missing_input_frame
                )
            )

        # Frame padding will be -1 if source is not an image sequence. In this
        # case we want to ignore anything other than frame sequence
        fragmented_ranges = []
        empty_frames = []
        if not padding_length == -1:
            # If the clip has missing frames inbetween media start and end
            fragmented_ranges = self.get_fragmented_ranges()
            for fragment_range_start, fragment_range_end in fragmented_ranges:
                self.log.critical(
                    "Missing frames between %d-%d" % (
                        fragment_range_start, fragment_range_end)
                )

            # If the frames are empty on disk
            empty_frames = self.get_empty_frames()
            for empty_frame in empty_frames:
                self.log.critical("Zero byte frame %s" % empty_frame)

        # In case of exception need to raise exception to stop publish
        if missing_media_frames or fragmented_ranges or empty_frames:
            raise Exception("Frame validation not passed!")

        project_path = self.get_project_path()
        if not project_path in input_dir:
            exception_msg = (f"Clip media path is not in project path '{project_path}'\n"
                 f"    Clip name: {self.track_item.name()}\n"
                 f"    Media path: {input_path}")
            raise Exception(exception_msg)

    def get_project_path(self):
        project_root = registered_root()["work"].__str__()
        return os.path.join(project_root, os.getenv("SHOW"))

    def openpype_publish_tag(self):
        """Find the tag that was used to publish the given track item.

        This function iterates through all the tags associated with the given
        self.track_item and returns the metadata of the tag that belongs to the
        plate and reference family.

        Returns:
            dict: The metadata of the tag belonging to the 'plate' family, or
            an empty dictionary if no such tag is found.
        """
        for item_tag in self.track_item.tags():
            tag_metadata = item_tag.metadata().dict()
            tag_family = tag_metadata.get("tag.family")
            if tag_family in ["plate", "reference"]:
                return tag_metadata

        return None

    def get_tag_handles(self):
        """Get the handles of the tag used for publishing the given track item.

        This function retrieves the 'handleStart' and 'handleEnd' values from
        the metadata of the tag associated with the given self.track_item, and
        returns them as a tuple.

        Args:
            self.track_item (hiero.core.TrackItem): The track item for which to
            retrieve the handles.

        Raises:
            Exception: If the 'handleStart' or 'handleEnd' field in the tag
            metadata contains non-numeric characters.

        Returns:
            tuple: A tuple containing the handle start and handle end values as
             integers.
        """
        tag = self.openpype_publish_tag()
        if tag is None:
            raise Exception("No OpenPype Publish tag found")

        try:
            handle_start = int(tag.get("tag.handleStart", "0"))
            handle_end = int(tag.get("tag.handleEnd", "0"))
        except ValueError:
            raise ValueError("Handle field should only contain numbers")

        return handle_start, handle_end

    def get_missing_media_frames(self, instance):
        # Define frame output range
        # handleStart and handleEnd are overridden to reflect media range and not absolute handles
        # Solution is to take the handle values directly from the tag instead of instance data
        # Temp solution to references not have handles in tags that match clip
        handle_start, handle_end = self.get_tag_handles()

        first_frame = instance.data["frameStart"] - handle_start
        end_frame = instance.data["frameEnd"] + handle_end

        # Need clip source in and original clip source media in and out to calculate matching input frame
        clip_source_in = self.track_item.sourceIn()
        source_start = self.track_item.source().sourceIn()
        source_end = self.track_item.source().sourceOut()

        frames = range(first_frame, end_frame + 1)
        missing_media_frames = []
        for output_frame in frames:
            # Calculate input_frame for output by normalizing input media to first frame
            input_frame = source_start + clip_source_in - handle_start + output_frame - first_frame
            if input_frame - source_start < 0 or input_frame > source_end:
                missing_media_frames.append((output_frame, input_frame))

        return missing_media_frames

    def get_empty_frames(self):
        empty_frames = []
        for frame_path in self.frame_paths:
            frame = int(frame_path.split(self.frame_head)[-1].split(".", 1)[0])
            # Only test for frames within track item frame rang
            if not (self.media_start <= frame <= self.media_end):
                continue

            if os.stat(frame_path).st_size == 0:
                empty_frames.append(frame_path)

        return empty_frames

    def get_fragmented_ranges(self):
        first_media_frame = int(
            self.frame_paths[0].split(self.frame_head)[-1].split(".", 1)[0])
        fragmented_ranges = []
        for frame_path in self.frame_paths:
            frame = int(frame_path.split(self.frame_head)[-1].split(".", 1)[0])
            # Only test for frames within track item frame rang
            if not (self.media_start <= frame <= self.media_end):
                continue

            # If first frame then only set previous_frame and continue
            if frame == first_media_frame:
                previous_frame = frame
                continue

            if frame - previous_frame != 1:
                fragmented_ranges.append((previous_frame, frame))

            previous_frame = frame

        return fragmented_ranges
