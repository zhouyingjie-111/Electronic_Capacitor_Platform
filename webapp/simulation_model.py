"""Read the saved ANN function without starting MATLAB or evaluating model content."""

import xml.etree.ElementTree as ET
from zipfile import ZipFile


def ann_function(path):
    with ZipFile(path) as model:
        machine = ET.fromstring(model.read("simulink/stateflow/machine.xml"))
        for instance in machine.findall("instance"):
            fields = {item.get("Name"): item.text for item in instance.findall("P")}
            if fields.get("name") == "MATLAB Function":
                chart = ET.fromstring(
                    model.read(f"simulink/stateflow/chart_{fields['chart']}.xml")
                )
                script = next(
                    item.text
                    for item in chart.iter("P")
                    if item.get("Name") == "script"
                )
                return {"name": "MATLAB Function", "code": script}
    raise ValueError("ANN function not found in the supplied model")
