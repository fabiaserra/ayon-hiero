import os
import pyblish.api

from ayon_core.pipeline import publish


class ExtractThumbnail(publish.Extractor):
    """
    Extractor for track item's tumbnails
    """

    label = "Extract Thumbnail"
    order = pyblish.api.ExtractorOrder
    families = ["plate", "take", "reference"]
    hosts = ["hiero"]

    def process(self, instance):
        # create representation data
        if "representations" not in instance.data:
            instance.data["representations"] = []

        ### Starts Alkemy-X Override ###
        # Set staging dir to a shared disk location instead of temp local disk
        # so we can run the extraction and publish in the farm
        staging_dir = os.path.join(
            os.getenv("AYON_WORKDIR"), "temp_transcode"
        )

        # Create staging dir if it doesn't exist
        try:
            if not os.path.isdir(staging_dir):
                os.makedirs(staging_dir, exist_ok=True)
        except OSError:
            # directory is not available
            self.log.warning("Path is unreachable: `{}`".format(staging_dir))
        ### Ends Alkemy-X Override ###

        self.create_thumbnail(staging_dir, instance)

    def create_thumbnail(self, staging_dir, instance):
        track_item = instance.data["item"]
        track_item_name = track_item.name()

        # frames
        duration = track_item.sourceDuration()
        frame_start = track_item.sourceIn()
        self.log.debug(
            "__ frame_start: `{}`, duration: `{}`".format(
                frame_start, duration))

        # get thumbnail frame from the middle
        thumb_frame = int(frame_start + (duration / 2))

        thumb_file = "{}thumbnail{}{}".format(
            track_item_name, thumb_frame, ".png")
        thumb_path = os.path.join(staging_dir, thumb_file)

        thumbnail = track_item.thumbnail(thumb_frame, "colour").save(
            thumb_path,
            format='png'
        )
        self.log.debug(
            "__ thumb_path: `{}`, frame: `{}`".format(thumbnail, thumb_frame))

        self.log.info("Thumbnail was generated to: {}".format(thumb_path))
        thumb_representation = {
            'files': thumb_file,
            'stagingDir': staging_dir,
            'name': "thumbnail",
            'thumbnail': True,
            'ext': "png"
        }
        instance.data["representations"].append(
            thumb_representation)
