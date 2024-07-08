import pyblish.api

from ayon_hiero.api.constants import OPENPYPE_TAG_NAME


class CollectFrameRange(pyblish.api.InstancePlugin):
    """A Pyblish plugin for collecting the frame range of a plate instance that
    will be used in extract frames. The media frame range is checked to ensure
    that it is within the expected extract frame range. The plugin bundles
    frame range into a pair of tuples which describe media frame and target
    output frame.

    This plugin should not be confused with the
    'collect_frame_tag_instances.py' plugin.

    Attributes:
        order (float): The order of this plugin in the Pyblish instance
            processing pipeline.
        label (str): A human-readable label for this plugin.
        hosts (list): A list of DCC applications where this plugin is
            supported.
        families (list): A list of families to which this plugin applies.
    """

    order = pyblish.api.CollectorOrder + 0.49
    label = "Collect Frame Range"
    hosts = ["hiero"]
    families = ["plate"]

    def process(self, instance):
        """Collect the media and output frame ranges of the given instance.

        It collects the media and output frame ranges and saves them as two
        separate tuples (frame_start, last_frame) on the instance.data.

        Args:
            instance (pyblish.api.Instance): The instance to process.

        Raises:
            Exception: If the output frame range is out of the source frame
                range of the media.

        Returns:
            None
        """
        self.track_item = instance.data["item"]

        # handleStart and handleEnd are overriden to reflect media range and
        # not absolute handles
        # Solution is to take the handle values directly from the tag instead
        # of instance data
        handle_start, handle_end = self.get_tag_handles()
        frame_start = instance.data["frameStart"] - handle_start
        frame_end = instance.data["frameEnd"] + handle_end

        # Need clip source in and original clip source media in and out to
        # calculate matching input frame
        clip_source_in = self.track_item.sourceIn()
        source_start = self.track_item.source().sourceIn()
        source_end = self.track_item.source().sourceOut()

        # Calculate frame offset between media and source frame range
        frame_offset = (
            source_start + clip_source_in - handle_start - frame_start
        )
        media_frame_start = int(frame_offset + frame_start)
        media_frame_end = int(frame_offset + frame_end)
        instance.data["srcFrameRange"] = (
            media_frame_start,
            media_frame_end,
        )
        self.log.info("Collected media frame range: %d - %d",
            media_frame_start, media_frame_end
        )

        instance.data["outFrameRange"] = (
            frame_start,
            frame_end,
        )
        self.log.info("Collected output frame range: %d - %d",
            frame_start, frame_end
        )

    def openpype_publish_tag(self):
        """Find the tag that was used to publish the given track item.

        This function iterates through all the tags associated with the given
        self.track_item and returns the metadata of the tag that belongs to the
        'plate' family.

        Returns:
            dict: The metadata of the tag belonging to the 'plate' family, or
                an empty dictionary if no such tag is found.
        """
        for item_tag in self.track_item.tags():
            tag_metadata = item_tag.metadata().dict()
            tag_label = tag_metadata.get("tag.label")
            if OPENPYPE_TAG_NAME in tag_label:
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
