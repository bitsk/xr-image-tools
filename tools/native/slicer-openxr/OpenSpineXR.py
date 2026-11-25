import json
import os
from pathlib import Path
import traceback

import qt
import slicer
state = {}


def stop():
    try:
        state["widget"].setVirtualRealityActive(False)
        state["widget"].setVirtualRealityConnected(False)
        print("SLICER_OPENXR_STOPPED", flush=True)
        slicer.util.exit(0)
    except Exception:
        traceback.print_exc()
        slicer.util.exit(1)


def check():
    try:
        result = {
            "version": slicer.app.applicationVersion,
            "revision": str(slicer.app.repositoryRevision),
            "spine_segments": state["segments"],
        }
        print("SLICER_OPENXR_RENDERING " + json.dumps(result), flush=True)
        path = os.environ.get("SLICER_XR_RESULT")
        if path:
            Path(path).write_text(json.dumps(result, indent=2) + "\n")
        seconds = int(os.environ.get("SLICER_XR_TEST_SECONDS", "0"))
        if seconds:
            qt.QTimer.singleShot(seconds * 1000, stop)
    except Exception:
        traceback.print_exc()
        slicer.util.exit(1)


def start():
    try:
        module = slicer.modules.virtualreality
        assert slicer.util.loadScene("/usr/share/xr-slicer-spine/Spine-CT-Review.mrb")
        segmentation = slicer.util.getNodesByClass("vtkMRMLSegmentationNode")[0]
        state["segments"] = segmentation.GetSegmentation().GetNumberOfSegments()
        assert state["segments"] == 10
        module.setMRMLScene(slicer.mrmlScene)
        widget = module.widgetRepresentation()
        widget.setMRMLScene(slicer.mrmlScene)
        widget.setVirtualRealityXRBackend(2)
        state["widget"] = widget
        print("SLICER_OPENXR_CONNECTING", flush=True)
        widget.setVirtualRealityConnected(True)
        module.updateViewFromReferenceViewCamera()
        widget.setVirtualRealityActive(True)
        qt.QTimer.singleShot(3000, check)
    except Exception:
        traceback.print_exc()
        slicer.util.exit(1)


qt.QTimer.singleShot(1000, start)
