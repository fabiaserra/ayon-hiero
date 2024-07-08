import os
import glob
import pyblish.api

import hiero

from ayon_deadline.lib import submit
from ayon_deadline import constants as dl_constants

from ayon_core.pipeline import publish


class TranscodeFrames(publish.Extractor):
    """Transcode Hiero media to the right colorspace using OIIO or Nuke"""

    order = pyblish.api.ExtractorOrder - 0.1
    label = "Extract Transcode Frames"
    hosts = ["hiero"]
    families = ["plate"]

    movie_extensions = {"mov", "mp4", "mxf"}
    nuke_specific_extensions = {"braw"}
    output_ext = "exr"
    dst_media_color_transform = "scene_linear"

    # TODO: Replace these with published Templates workflow
    nuke_transcode_py = "/pipe/hiero/templates/nuke_transcode.py"
    default_nuke_transcode_script = "/pipe/hiero/templates/ingest_transcode.nk"

    # WARNING: Need to be very careful about the length of the overall command
    # Anything around 490-505 will cause ffmpeg to through an error
    # OIIO args we want to run to convert colorspaces
    oiio_args = [
        "--frames",
        "<STARTFRAME>-<ENDFRAME>",
        '"{input_path}"',  # Escape input path in case there's whitespaces
        "--eraseattrib",
        '"Exif*"',  # Image history is too long and not needed
        "-v",
        "--compression",
        "zips",
        "-d",
        "half",
        "--scanline",
        "{input_args}",
        # "--sattrib",  # Can't add this meta until farm OIIO supports it
        # "original_meta",
        # '"{{TOP.META}}"',  # Add meta from current input for pass through
        "--attrib:subimages=1",
        "framesPerSecond",
        '"{fps}"',
        "--colorconfig",  # Add color config as an arg so that it can be traced
        '"{ocio_path}"',
        "--colorconvert",
        '"{src_media_color_transform}"',
        '"{dst_media_color_transform}"',
        "--sansattrib",  # Remove attrib/sattrib from command in software/exif
        "-o",
        "{output_path}",
    ]


    def process(self, instance):
        """Submit a job to the farm to transcode the video frames"""
        instance.data["toBeRenderedOn"] = "deadline"

        context = instance.context

        track_item = instance.data["item"]
        media_source = track_item.source().mediaSource()

        # Define source path along with extension
        input_path = media_source.fileinfos()[0].filename()
        source_ext = os.path.splitext(input_path)[1][1:]

        # Output variables
        staging_dir = os.path.join(os.getenv("AYON_WORKDIR"), "temp_transcode")
        instance.data["stagingDir"] = staging_dir

        try:
            # Ensure staging folder exists
            os.makedirs(staging_dir)
        except OSError:
            pass

        # Determine color transformation
        src_media_color_transform = track_item.sourceMediaColourTransform()

        # Define extra metadata variables
        ocio_path = os.getenv("OCIO")

        # TODO: skip transcoding if source colorspace matches destination
        # if src_media_color_transform == self.dst_media_color_transform:

        src_frame_start, src_frame_end = instance.data["srcFrameRange"]
        out_frame_start, out_frame_end = instance.data["outFrameRange"]
        self.log.info(
            f"Processing frames {out_frame_start} - {out_frame_end}"
        )

        # Create some useful variables
        anatomy = instance.context.data["anatomy"]
        padding = anatomy.templates.get("frame_padding", 4)
        hiero_version = "{}.{}".format(
            hiero.core.env["VersionMajor"], hiero.core.env["VersionMinor"]
        )
        app_name = f"hiero/{hiero_version}"

        # Check what resolutions we are asking to ingest
        ingest_resolution = instance.data.get("ingest_resolution")

        # By default, we only ingest a single resolution (WR) unless
        # we have an ingest_resolution on the data stating a different
        # resolution
        ingest_resolutions = ["wr"]
        if ingest_resolution:
            width = int(ingest_resolution["width"])
            height = int(ingest_resolution["height"])
            fr_width = int(ingest_resolution["fr_width"])
            fr_height = int(ingest_resolution["fr_height"])
            if width != fr_width and height != fr_height:
                self.log.debug(
                    "Working resolution %sx%s differs from full resolution %sx%s, "
                    "ingesting as separate representation 'fr'.",
                    width, height, fr_width, fr_height
                )
                ingest_resolutions.append("fr")
            else:
                self.log.debug(
                    "Working resolution %sx%s matches full resolution %sx%s, "
                    "ingesting only a single resolution.",
                    width, height, fr_width, fr_height
                )
        else:
            self.log.error(
                "No ingest resolution found, please set it"
            )
            raise AssertionError("Ingest resolution missing")

        # Name to use for batch grouping Deadline tasks
        batch_name = "Ingest - {}".format(
            context.data.get("currentFile", "")
        )
        instance.data["deadlineBatchName"] = batch_name

        # For each output resolution we create a job in the farm
        submission_jobs = []
        for resolution in ingest_resolutions:
            nuke_transcode_script = self.get_show_nuke_transcode_script(resolution)
            if not nuke_transcode_script:
                nuke_transcode_script = self.default_nuke_transcode_script
                self.log.debug("Show transcode script not found, using default '%s'", nuke_transcode_script)

            representation_name = instance.data["name"]
            if resolution == "fr":
                representation_name += "_fr"

            # Create staging directory if it doesn't exist
            try:
                if not os.path.isdir(staging_dir):
                    os.makedirs(staging_dir, exist_ok=True)
            except OSError:
                # directory is not available
                self.log.error("Path is unreachable: `{}`".format(staging_dir))
                continue

            output_path = os.path.join(
                staging_dir,
                f"{representation_name}.%0{padding}d.{self.output_ext}"
            )

            self.log.debug("Source ext: %s", source_ext.lower())
            self.log.debug("Output path: %s", output_path)
            self.log.debug("Output ext: %s", self.output_ext)

            # Create names for Deadline batch job and tasks
            task_name = "Transcode frames - {} - {} - {} ({})".format(
                os.path.basename(output_path),
                staging_dir,
                os.getenv("AYON_PROJECT_NAME"),
                os.getenv("SHOW")
            )

            # If either source or output is a video format, transcode using Nuke
            if (self.output_ext.lower() in self.movie_extensions or
                    source_ext.lower() in self.movie_extensions or
                    source_ext.lower() in self.nuke_specific_extensions) or \
                    instance.data.get("use_nuke", False):
                # No need to raise error as Nuke raises an error exit value if
                # something went wrong
                self.log.info("Submitting Nuke transcode")

                # Add environment variables required to run Nuke script
                extra_env = {}
                extra_env["_AX_TRANSCODE_NUKESCRIPT"] = nuke_transcode_script
                extra_env["_AX_TRANSCODE_FRAMES"] = "{0}_{1}_{2}".format(
                    int(out_frame_start), int(out_frame_end), int(src_frame_start)
                )
                extra_env["_AX_TRANSCODE_READTYPE"] = self.output_ext.lower()
                extra_env["_AX_TRANSCODE_READPATH"] = input_path
                extra_env["_AX_TRANSCODE_WRITEPATH"] = output_path
                extra_env["_AX_TRANSCODE_READCOLORSPACE"] = src_media_color_transform
                extra_env["_AX_TRANSCODE_TARGETCOLORSPACE"] = self.dst_media_color_transform

                extra_env["AYON_RENDER_JOB"] = 1
                extra_env["AYON_FOLDER_PATH"] = instance.data["folderPath"]
                extra_env["AYON_APP_NAME"] = app_name

                # Create dictionary of data specific to Nuke plugin for payload submit
                plugin_data = {
                    "ScriptJob": True,
                    "SceneFile": self.nuke_transcode_py,
                    "ScriptFilename": self.nuke_transcode_py,
                    "Version": hiero_version,
                    "UseGpu": False,
                    "OutputFilePath": staging_dir,
                }

                response = submit.payload_submit(
                    plugin="AxNuke",
                    plugin_data=plugin_data,
                    batch_name=batch_name,
                    task_name=task_name,
                    frame_range="{0}-{1}".format(out_frame_start, out_frame_end),
                    department="Editorial",
                    group=dl_constants.NUKE_CPU_GROUP.format(
                        hiero.core.env["VersionMajor"], hiero.core.env["VersionMinor"]
                    ),
                    comment=context.data.get("comment", ""),
                    extra_env=extra_env,
                )
            else:
                self.log.info("Submitting OIIO transcode")

                input_args = ""

                if ingest_resolution and resolution == "wr":
                    width = int(ingest_resolution["width"])
                    height = int(ingest_resolution["height"])
                    fr_width = int(ingest_resolution["fr_width"])
                    fr_height = int(ingest_resolution["fr_height"])
                    width_offset = (fr_width - width) / 2
                    height_offset = (fr_height - height) / 2
                    resize_crop = f"--cut {width}x{height}+{width_offset}+{height_offset}"

                    # TODO: add different reformat operations
                    # if ingest_resolution["reformat"].get("resize"):
                    #     if ingest_resolution["reformat"]["resize"] == "fit":
                    #         resize = "--fit:fillmode=letterbox " + wxh + resize_crop
                    #     elif ingest_resolution["reformat"]["resize"] == "fill":
                    #         if float(READ_NODE.width())/READ_NODE.height() < float(WRITE_NODE.width())/WRITE_NODE.height():
                    #             resize = "--fit:fillmode=width " + wxh + resize_crop
                    #         else:
                    #             resize = "--fit:fillmode=height " + wxh + resize_crop
                    #     elif ingest_resolution["reformat"]["resize"] == "width":
                    #         resize = "--fit:fillmode=width " + wxh + resize_crop
                    #     elif ingest_resolution["reformat"]["resize"] == "height":
                    #         resize = "--fit:fillmode=height " + wxh + resize_crop
                    #     elif ingest_resolution["reformat"]["resize"] == "distort":
                    #         resize = "--resize " + wxh + resize_crop
                    #     elif ingest_resolution["reformat"]["resize"] == "none":
                    #         print("WARNING: "none" OIIO resize is not current supported in finalmaker")
                    #     else:
                    #         print("WARNING: {} OIIO resize is not current supported in finalmaker".format(output_settings["reformat"].get('resize')))
                    input_args = resize_crop

                self.log.info("Submitting OIIO transcode")
                oiio_args = " ".join(self.oiio_args).format(
                    input_path=input_path,
                    src_media_color_transform=src_media_color_transform,
                    dst_media_color_transform=self.dst_media_color_transform,
                    input_args=input_args,
                    output_path=output_path,
                    fps=round(instance.data["fps"], 2),
                    ocio_path=ocio_path,
                )

                # Normalize path
                render_dir = os.path.normpath(os.path.dirname(output_path))

                # Create dictionary of data specific to Nuke plugin for payload submit
                plugin_data = {
                    "Executable": "/sw/bin/oiiotool",
                    "Arguments": oiio_args,
                    "UseGpu": False,
                    "WorkingDirectory": render_dir,
                }

                # NOTE: We use src frame start/end because oiiotool doesn't support
                # writing out a different frame range than input
                response = submit.payload_submit(
                    plugin="CommandLine",
                    plugin_data=plugin_data,
                    batch_name=batch_name,
                    task_name=task_name,
                    department="Editorial",
                    frame_range="{0}-{1}".format(src_frame_start, src_frame_end),
                    group=dl_constants.OP_GROUP,
                    comment=context.data.get("comment", ""),
                )

            # adding expected files to instance.data
            self.expected_files(
                instance,
                output_path,
                src_frame_start,
                src_frame_end
            )

            submission_jobs.append(response)

        # Store output dir for unified publisher (filesequence)
        instance.data["deadlineSubmissionJobs"] = submission_jobs
        instance.data["outputDir"] = staging_dir

        # Remove source representation as its replaced by the transcoded frames
        ext_representations = [
            rep
            for rep in instance.data["representations"]
            if rep["ext"] == source_ext
        ]
        if ext_representations:
            self.log.info(
                "Removing source representation and replacing with transcoded frames"
            )
            instance.data["representations"].remove(ext_representations[0])
        else:
            self.log.info("No source ext to remove from representation")

    def expected_files(
        self,
        instance,
        path,
        out_frame_start,
        out_frame_end,
    ):
        """Create expected files in instance data"""
        if not instance.data.get("expectedFiles"):
            instance.data["expectedFiles"] = []

        dirname = os.path.dirname(path)
        filename = os.path.basename(path)

        if "#" in filename:
            pparts = filename.split("#")
            padding = "%0{}d".format(len(pparts) - 1)
            filename = pparts[0] + padding + pparts[-1]

        if "%" not in filename:
            instance.data["expectedFiles"].append(path)
            return

        for i in range(out_frame_start, (out_frame_end + 1)):
            instance.data["expectedFiles"].append(
                os.path.join(dirname, (filename % i)).replace("\\", "/"))

        # Set destination colorspace to instance data so the transcode plugin
        # that runs on these files knows what input colorspace to use
        self.log.debug(
            "Setting colorspace '%s' to instance", self.dst_media_color_transform
        )
        instance.data["colorspace"] = self.dst_media_color_transform

        # Set frame start/end handles as it's used in integrate to map
        # the frames to the correct frame range
        instance.data["frameStartHandle"] = out_frame_start
        instance.data["frameEndHandle"] = out_frame_end

    def get_show_nuke_transcode_script(self, resolution):
        """Get the template Nuke script to use for ingest the given resolution type."""
        ingest_template = None
        ingest_template_path = os.path.join(
            os.getenv("AX_PROJ_ROOT"),
            os.getenv("SHOW"),
            "resources",
            "ingest_template",
            f"{resolution}*"
        )
        self.log.debug(
            "Looking for show ingest template scripts for '%s' at '%s'",
            resolution, ingest_template_path
        )
        ingest_templates = sorted(glob.glob(ingest_template_path))
        if ingest_templates:
            ingest_template = ingest_templates[-1]
            self.log.debug("Found show ingest template script at '%s'", ingest_template)

        return ingest_template
